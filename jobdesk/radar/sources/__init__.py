"""Source registry.

`collect()` is the only thing the rest of the pipeline calls. Each source is
isolated: one raising or timing out costs its own jobs and nothing else, the
same way a dead feed in the news bot drops one section rather than the run.
"""

from __future__ import annotations

import traceback
from collections import Counter
from typing import Callable

from .. import companies, config, http
from ..models import Job
from . import ats, boards

# Aggregator/board sources, in the order they run. Disable one by setting it
# False here -- no other file needs to change.
BOARD_SOURCES: dict[str, bool] = {
    "remoteok": True,
    "remotive": True,
    "himalayas": True,
    "weworkremotely": True,
    "adzuna": True,        # no-ops without ADZUNA_APP_ID / ADZUNA_APP_KEY
    "usajobs": True,       # no-ops without USAJOBS_API_KEY / USAJOBS_EMAIL
    "hackernews": True,
    "hiringcafe": False,   # see the note in boards.py -- private API, off by default
}


def collect(log: Callable[[str], None] = print,
            only: str | None = None) -> tuple[list[Job], dict]:
    """Run every enabled source. Returns (jobs, stats)."""
    jobs: list[Job] = []
    stats: dict = {"sources": {}, "errors": [], "rate_limited": []}

    # -- company ATS boards first: freshest, most direct ---------------------
    for entry in companies.active():
        name = f"{entry['ats']}:{entry['name']}"
        if only and only.lower() not in name.lower():
            continue
        handler = ats.HANDLERS.get(entry["ats"])
        if handler is None:
            stats["errors"].append(f"{name}: no handler for '{entry['ats']}'")
            continue
        try:
            found = handler(entry)
        except http.RateLimited as exc:
            stats["rate_limited"].append(str(exc))
            log(f"  {name}: rate-limited, skipped")
            continue
        except Exception:
            stats["errors"].append(f"{name}: {traceback.format_exc(limit=2)}")
            log(f"  {name}: FAILED")
            continue
        for job in found:
            job.__dict__["_company_entry"] = entry   # for the detail fetch
        jobs.extend(found)
        stats["sources"][name] = len(found)
        log(f"  {name}: {len(found)}")

    # -- public boards ------------------------------------------------------
    for key, enabled in BOARD_SOURCES.items():
        if not enabled:
            continue
        if only and only.lower() not in key.lower():
            continue
        handler = boards.HANDLERS.get(key)
        if handler is None:
            continue
        try:
            found = handler(None)
        except http.RateLimited as exc:
            stats["rate_limited"].append(str(exc))
            log(f"  {key}: rate-limited, skipped")
            continue
        except Exception:
            stats["errors"].append(f"{key}: {traceback.format_exc(limit=2)}")
            log(f"  {key}: FAILED")
            continue
        jobs.extend(found)
        stats["sources"][key] = len(found)
        log(f"  {key}: {len(found)}")

    return jobs, stats


# Sources whose list endpoint carries no job description, and the call that
# fetches one posting's body.
DETAIL_FETCHERS = {
    "workday": ats.workday_detail,
    "sitemap": ats.sitemap_detail,
}


def fill_partials(jobs: list[Job], log: Callable[[str], None] = print) -> int:
    """Turn aggregator snippets back into the postings they were cut from.

    `fetch_details` cannot do this. It skips any job that has a description,
    and a snippet is a description by that test -- 500 characters of "Lumen is
    the trusted network for the AI-powered world" and an ellipsis, which is
    the About Us paragraph and none of the req.

    That is not only a scoring problem. The packet builder reads this body and
    sorts it into required, preferred and responsibilities; a body that is all
    boilerplate sorts into nothing, and the tailored resume answers a job
    description it never saw. The snippet is worse than a blank because it
    looks like a JD.

    Run this after `dedupe.collapse` and before the rescore. After collapse,
    because the same req arrives from four aggregators and only the survivor
    is worth an HTTP call. Before the rescore, because the years gate and the
    stack match have to read the body that comes back rather than the one the
    first pass saw.
    """
    budget = config.MAX_PARTIAL_FETCHES
    filled = 0
    tried = 0
    blocked_hosts: set[str] = set()
    # Best first. The budget runs out long before the list does on a full
    # pull, and the posting a person is most likely to open is the one whose
    # body is most worth having.
    for job in sorted(jobs, key=lambda j: -j.score):
        if budget <= 0:
            break
        if not job.partial or not job.url:
            continue
        if job.score < config.DETAIL_FETCH_MIN_SCORE:
            continue
        if http._host(job.url) in blocked_hosts:
            continue
        budget -= 1
        tried += 1
        try:
            if ats.partial_detail(job):
                filled += 1
        except http.RateLimited:
            continue
        except ats.Challenged as blocked:
            # Say it, and stop asking that host. It is not going to change its
            # mind inside one run, and the alternative is a loop that spends
            # the whole budget collecting the same refusal.
            log(f"  {blocked}")
            blocked_hosts.add(http._host(job.url))
            continue
        except Exception:
            continue
    if tried:
        log(f"  filled {filled} of {tried} snippet(s) from the posting itself")
    return filled


def fetch_details(jobs: list[Job], log: Callable[[str], None] = print) -> int:
    """Fill in JD bodies for promising description-less postings.

    Workday's list endpoint returns no description, and a sitemap carries
    nothing but a URL and a date -- which means no years-of-experience parsing
    and no stack matching, the two filters that do the most work. Bodies are
    fetched only for postings whose title already scored, and only up to
    MAX_DETAIL_FETCHES per run.

    Best first, not first come. The budget used to be spent in collection
    order, which meant the order the sources happen to be listed in decided
    who got read. On 2026-09-19 the 120 calls were gone before the loop
    reached the Commonwealth of Virginia sitemap, which is collected second to
    last and is 539 postings of a state government that prints its pay band in
    the body -- so 64 rows reached the Jobs tab with no description, and a Fair
    Housing Investigator req showed an $86k-$131k estimate over a page that
    says "Hiring Range: $57,000 - $72,000". Nothing was broken. The reader just
    never got that far down the list.

    Sorting costs nothing here. A posting that already has a description is
    skipped before the budget is touched, so the budget only ever buys bodies
    for postings that lack one, and it should buy the best ones.

    MAX_DETAIL_FETCHES_PER_HOST keeps best-first from turning into all-one-
    host. The Commonwealth sitemap alone is 304 of the 321 postings that want
    a body, and it is read at one request per five seconds, so without a cap
    it takes the whole budget and twenty minutes with it.
    """
    budget = config.MAX_DETAIL_FETCHES
    per_host: Counter[str] = Counter()
    filled = 0
    blocked_sources: set[str] = set()
    for job in sorted(jobs, key=lambda j: -j.score):
        if budget <= 0:
            break
        fetcher = DETAIL_FETCHERS.get(job.source)
        if fetcher is None or job.description:
            continue
        if job.source in blocked_sources:
            continue
        if job.score < config.DETAIL_FETCH_MIN_SCORE:
            continue
        entry = job.__dict__.get("_company_entry")
        if not entry:
            continue
        host = http._host(job.url)
        if per_host[host] >= config.MAX_DETAIL_FETCHES_PER_HOST:
            continue
        per_host[host] += 1
        budget -= 1
        try:
            if fetcher(job, entry):
                filled += 1
        except http.RateLimited:
            break
        except ats.Challenged as blocked:
            # Every remaining posting on that host will answer the same way, so
            # there is nothing to gain by spending the rest of the budget on it.
            # It gets said out loud: a source that quietly stops returning
            # bodies looks identical to a source whose postings are all short,
            # and the difference is two months of blank descriptions.
            blocked_sources.add(job.source)
            log(f"  {job.source}: {blocked}")
            continue
        except Exception:
            continue
    if filled:
        log(f"  fetched {filled} job descriptions")
    if blocked_sources:
        log(f"  no bodies from {', '.join(sorted(blocked_sources))} "
            f"- the site is serving a bot check, not the posting")
    return filled
