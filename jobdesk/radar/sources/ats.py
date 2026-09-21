"""Company ATS boards -- the highest-value, least-crowded channel.

These are the applicant tracking systems companies host their own job boards
on. Every one of them serves public JSON that the company's own careers page
consumes, so reading it is using the site as intended -- no scraping, no
parsing HTML, no ToS gray zone.

Why this matters more than the aggregators: a req appears here the moment it
opens, often days before it propagates to Indeed/LinkedIn, and applying here
goes straight into the company's pipeline rather than through an aggregator
redirect. That is the whole speed-to-apply advantage (plan item 3).

Each function takes a company entry from `radar/companies.py` and returns
`list[Job]`. Endpoint shapes verified live 2026-07-23.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from .. import discover, http, profile as targeting, terms
from ..models import Job, clean_text, parse_date

TIMEOUT = 30


# --------------------------------------------------------------------------
# Greenhouse -- boards-api.greenhouse.io
# --------------------------------------------------------------------------

def greenhouse(entry: dict) -> list[Job]:
    slug = entry["slug"]
    data = http.get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
        timeout=TIMEOUT)
    if not isinstance(data, dict):
        return []
    out = []
    for j in data.get("jobs", []):
        out.append(Job(
            title=j.get("title", ""),
            company=entry["name"],
            url=j.get("absolute_url", ""),
            source="greenhouse",
            location=(j.get("location") or {}).get("name", ""),
            description=j.get("content", ""),
            posted_at=parse_date(j.get("first_published") or j.get("updated_at")),
            department=", ".join(d.get("name", "") for d in j.get("departments", [])),
            external_id=str(j.get("id", "")),
        ))
    return out


# --------------------------------------------------------------------------
# Lever -- api.lever.co
# --------------------------------------------------------------------------

def lever(entry: dict) -> list[Job]:
    slug = entry["slug"]
    data = http.get_json(
        f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout=TIMEOUT)
    if not isinstance(data, list):
        return []
    out = []
    for j in data:
        cats = j.get("categories") or {}
        out.append(Job(
            title=j.get("text", ""),
            company=entry["name"],
            url=j.get("hostedUrl") or j.get("applyUrl", ""),
            source="lever",
            location=cats.get("location", ""),
            description=j.get("descriptionPlain") or j.get("description", ""),
            posted_at=parse_date(j.get("createdAt")),
            department=cats.get("team", "") or cats.get("department", ""),
            external_id=str(j.get("id", "")),
            remote=(cats.get("commitment", "") or "").lower() == "remote",
        ))
    return out


# --------------------------------------------------------------------------
# Ashby -- api.ashbyhq.com
# --------------------------------------------------------------------------

def ashby(entry: dict) -> list[Job]:
    slug = entry["slug"]
    data = http.get_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
        timeout=TIMEOUT)
    if not isinstance(data, dict):
        return []
    out = []
    for j in data.get("jobs", []):
        out.append(Job(
            title=j.get("title", ""),
            company=entry["name"],
            url=j.get("jobUrl") or j.get("applyUrl", ""),
            source="ashby",
            location=j.get("location", ""),
            description=j.get("descriptionPlain") or j.get("descriptionHtml", ""),
            posted_at=parse_date(j.get("publishedAt")),
            department=j.get("department", "") or j.get("team", ""),
            external_id=str(j.get("id", "")),
            remote=bool(j.get("isRemote")),
            **_ashby_salary(j.get("compensation")),
        ))
    return out


def _ashby_salary(comp) -> dict:
    """Ashby's published band, as salary_min/salary_max kwargs.

    The fetcher has asked for `includeCompensation=true` since the day it was
    written and then dropped the answer on the floor. A Ramp posting showed
    $128K - $180K on its own page while the radar listed an estimate of
    $91k-$125k beside it, because nothing had ever read this field.

    Three things in here are traps. A tier lists several components and only
    the Salary one is pay -- the others are equity, bonus, commission, and
    summing them invents a number the employer never published. `interval`
    carries the period as "1 YEAR" or "1 HOUR", the same factor-of-2080
    mistake `_schema_salary` guards against. And a band in euros is not a band
    in dollars, so anything that names another currency is left blank rather
    than relabelled.

    `summaryComponents` is the flattened view across tiers and is what to read.
    A posting with per-location tiers gives the widest honest band that way,
    which is the right answer for a listing that has not picked a location yet.
    """
    if not isinstance(comp, dict):
        return {}
    lows, highs = [], []
    for part in comp.get("summaryComponents") or []:
        if not isinstance(part, dict):
            continue
        if str(part.get("compensationType") or "") != "Salary":
            continue
        currency = str(part.get("currencyCode") or "USD").upper()
        if currency != "USD":
            continue
        # "1 YEAR" -> YEAR. The count is always one in practice, and a band
        # per two years is not a thing, so only the unit is read.
        period = str(part.get("interval") or "YEAR").upper().split()[-1]
        factor = _PERIOD_HOURS.get(period.rstrip("S"))
        if not factor:
            continue
        for raw, into in ((part.get("minValue"), lows),
                          (part.get("maxValue"), highs)):
            try:
                annual = float(raw) * factor
            except (TypeError, ValueError):
                continue
            if 15_000 <= annual <= 900_000:
                into.append(annual)
    low = min(lows) if lows else None
    high = max(highs) if highs else None
    if low and high and high < low:
        low, high = high, low
    return {"salary_min": low, "salary_max": high}


# --------------------------------------------------------------------------
# SmartRecruiters -- api.smartrecruiters.com
# The list endpoint has no description, so the body is fetched per posting.
# That is one extra call each; only done for postings that pass a title
# pre-filter, to keep the request count sane.
# --------------------------------------------------------------------------

def smartrecruiters(entry: dict) -> list[Job]:
    slug = entry["slug"]
    data = http.get_json(
        f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100",
        timeout=TIMEOUT)
    if not isinstance(data, dict):
        return []
    out = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        location = ", ".join(
            x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
        # `ref` is the API's own self-link (a string), not a jobAd dict --
        # the public posting always lives at this company-slug/id path.
        out.append(Job(
            title=j.get("name", ""),
            company=entry["name"],
            url=f"https://jobs.smartrecruiters.com/{slug}/{j.get('id','')}",
            source="smartrecruiters",
            location=location,
            description=j.get("jobAdInternal", ""),
            posted_at=parse_date(j.get("releasedDate")),
            department=(j.get("department") or {}).get("label", ""),
            external_id=str(j.get("id", "")),
            remote=bool(loc.get("remote")),
        ))
    return out


# --------------------------------------------------------------------------
# Workable / Recruitee -- smaller shops, same idea
# --------------------------------------------------------------------------

def workable(entry: dict) -> list[Job]:
    slug = entry["slug"]
    data = http.get_json(
        f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
        timeout=TIMEOUT)
    if not isinstance(data, dict):
        return []
    out = []
    for j in data.get("jobs", []):
        out.append(Job(
            title=j.get("title", ""),
            company=entry["name"],
            url=j.get("url") or j.get("application_url", ""),
            source="workable",
            location=", ".join(
                x for x in (j.get("city"), j.get("state"), j.get("country")) if x),
            description=j.get("description", ""),
            posted_at=parse_date(j.get("published_on")),
            department=j.get("department", ""),
            external_id=str(j.get("shortcode", "")),
            remote=bool(j.get("telecommuting")),
        ))
    return out


def recruitee(entry: dict) -> list[Job]:
    slug = entry["slug"]
    data = http.get_json(f"https://{slug}.recruitee.com/api/offers/", timeout=TIMEOUT)
    if not isinstance(data, dict):
        return []
    out = []
    for j in data.get("offers", []):
        out.append(Job(
            title=j.get("title", ""),
            company=entry["name"],
            url=j.get("careers_url") or j.get("careers_apply_url", ""),
            source="recruitee",
            location=", ".join(
                x for x in (j.get("city"), j.get("state_name"), j.get("country")) if x),
            description=j.get("description", ""),
            posted_at=parse_date(j.get("published_at")),
            department=j.get("department", ""),
            external_id=str(j.get("id", "")),
            remote=bool(j.get("remote")),
        ))
    return out


# --------------------------------------------------------------------------
# Workday -- the big one for Richmond.
#
# Capital One, CarMax, VCU Health and most large employers here run Workday.
# The feed is a POST to /wday/cxs/{tenant}/{site}/jobs. Coordinates are
# per-company and documented nowhere; `scripts/discover_ats.py` finds them.
#
# The list response carries title, location and a relative path but NOT the
# body, so years-of-experience parsing needs a second call per posting. That
# is expensive against a 1,190-posting board, so Workday is queried with a
# `searchText` per target title instead of pulled wholesale, and bodies are
# fetched only for what survives.
# --------------------------------------------------------------------------

def workday_queries(limit: int = 8, widen: bool = True) -> list[str]:
    """`searchText` values for the Workday feeds.

    A Workday board is queried per target title rather than pulled wholesale,
    so the query list has to stay short. The first term of each function
    family is a good size of word: broad enough that "accountant" catches the
    staff, senior and assistant flavours, narrow enough that the board does
    not return its whole catalogue.

    This used to say single words only, on the grounds that Workday treats a
    phrase as an AND and quietly returns nothing. That is not true, and the
    belief was costing real coverage. Against a live tenant, `searchText`
    "analyst" returns 298 postings, "business intelligence" 223 and "data
    analyst" 209 -- the search is OR-ish and ranks by relevance. Since each
    query takes one page of twenty, a phrase query returns a DIFFERENT top
    twenty than the bare word does, which is exactly the coverage that was
    being left on the table.

    Then the synonym table widens it further, same as the keyword boards.
    One extra query is one extra HTTP call and at most twenty more postings,
    all of which are scored on the title before anything fetches a body.

    `workday_search_terms` in targeting.toml overrides the derivation.
    """
    declared = [q for q in targeting.WORKDAY_SEARCH_TERMS if q]
    if not declared:
        seen: set[str] = set()
        declared = []
        for _points, _label, family in targeting.FUNCTION_FAMILIES:
            for term in family:
                word = term.split()[0].strip().lower()
                if len(word) > 3 and word not in seen:
                    seen.add(word)
                    declared.append(word)
                    break
        declared = declared or ["analyst"]
    base = declared[:limit]
    if not widen:
        return base
    # Half the budget the keyword boards get. This list is sent to EVERY
    # Workday tenant on the employer list -- 27 of them on the live profile --
    # so one extra query here is 27 more calls, not one.
    budget = min(limit // 2, targeting.SEARCH_SYNONYM_LIMIT)
    return base + terms.widen(base, budget)

# The list endpoint dates postings as human text: "Posted Today",
# "Posted 5 Days Ago", "Posted 30+ Days Ago".
_POSTED_ON = re.compile(r"posted\s+(?:(\d+)\+?\s+days?|(today)|(yesterday))", re.I)


def _parse_posted_on(text: str) -> datetime | None:
    if not text:
        return None
    m = _POSTED_ON.search(text)
    if not m:
        return None
    if m.group(2):
        days = 0
    elif m.group(3):
        days = 1
    else:
        days = int(m.group(1))
    return datetime.now(timezone.utc) - timedelta(days=days)


def _workday_search(entry: dict, query: str, limit: int = 20) -> list[dict]:
    url = (f"https://{entry['host']}/wday/cxs/{entry['tenant']}"
           f"/{entry['site']}/jobs")
    data = http.post_json(url, timeout=TIMEOUT, json={
        "appliedFacets": {}, "limit": limit, "offset": 0, "searchText": query,
    })
    if not isinstance(data, dict):
        return []
    return data.get("jobPostings", [])


def workday(entry: dict) -> list[Job]:
    """Search a Workday board for each target keyword.

    Searched rather than pulled wholesale: CarMax alone has 1,190 open reqs,
    and the list response carries no job description, so the only way to
    filter properly is a detail call per posting. Querying by keyword first
    keeps that to a few dozen calls instead of a few thousand.
    """
    base = f"https://{entry['host']}"
    site_path = f"/{entry['site']}"
    seen: set[str] = set()
    out: list[Job] = []

    for query in entry.get("queries", workday_queries()):
        for j in _workday_search(entry, query):
            path = j.get("externalPath", "")
            if not path or path in seen:
                continue
            seen.add(path)
            bullets = j.get("bulletFields") or []
            out.append(Job(
                title=j.get("title", ""),
                company=entry["name"],
                url=base + site_path + path,
                source="workday",
                location=j.get("locationsText", ""),
                posted_at=_parse_posted_on(j.get("postedOn", "")),
                external_id=str(bullets[0]) if bullets else "",
            ))
    return out


def workday_detail(job: Job, entry: dict) -> bool:
    """Fetch the JD body for one Workday posting, in place.

    Only worth calling for postings that already look promising: the body is
    what the years-of-experience filter needs, and it costs one HTTP call
    each. Returns True if the body was filled in.
    """
    base = f"https://{entry['host']}"
    site_path = f"/{entry['site']}"
    prefix = base + site_path
    if not job.url.startswith(prefix):
        return False
    url = (f"{base}/wday/cxs/{entry['tenant']}/{entry['site']}"
           f"{job.url[len(prefix):]}")
    data = http.get_json(url, timeout=TIMEOUT, spacing=0.6)
    if not isinstance(data, dict):
        return False
    info = data.get("jobPostingInfo") or {}
    if info.get("jobDescription"):
        job.description = clean_text(info["jobDescription"])
    # startDate is the real ISO date; the list view only had "N Days Ago".
    job.posted_at = parse_date(info.get("startDate")) or job.posted_at
    if info.get("externalUrl"):
        job.url = info["externalUrl"]
    if info.get("remoteType"):
        job.remote = "remote" in str(info["remoteType"]).lower()
    return bool(info.get("jobDescription"))


# --------------------------------------------------------------------------
# Sitemap + schema.org -- the lane for employers with no ATS JSON at all.
#
# Some careers sites serve no public JSON but do publish two things that exist
# specifically to be read by machines: a sitemap.xml declared in robots.txt,
# and a schema.org JobPosting JSON-LD block on each posting so Google for Jobs
# can index it. Reading those is the documented use of both, which keeps this
# on the same footing as the ATS feeds -- it is still not HTML scraping.
#
# The bar for adding an entry here, checked by scripts/discover_sitemap.py:
#   1. robots.txt does not Disallow the jobs path
#   2. the sitemap actually lists individual postings, not just a jobs index
#   3. a sample posting carries a real JobPosting JSON-LD block
# Fail any of the three and the employer belongs in NO_PUBLIC_FEED instead.
#
# Only jobs.virginia.gov clears all three today (2026-08-17). It matters
# enough on its own: the Commonwealth is the largest white-collar employer in
# the capital region and 154 of its 3,930 open postings are Richmond.
# --------------------------------------------------------------------------

_SITEMAP_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_SITEMAP_ENTRY = re.compile(r"<url>(.*?)</url>", re.S)
_LASTMOD = re.compile(r"<lastmod>\s*([^<\s]+)\s*</lastmod>")
_TRAILING_ID = re.compile(
    r"-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_JSONLD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S | re.I)

# The slug ends "<title>-<city>-<state>-<country>". Stripping the country and
# state suffixes leaves the city as the tail -- imperfect for two-word cities
# ("newport news" survives as "news"), which is why the detail fetch replaces
# this with the JSON-LD address as soon as a posting scores.
#
# The state suffix was the literal "-virginia" until someone asked what this
# app does for a user in Texas. It stripped nothing there, so every Austin
# posting came back titled "Graphic Designer Austin" and located in "Texas".
#
# Reading the state off the profile would fix that user and break the Richmond
# one the moment a remote req is posted out of Austin. The slug is not about
# whose profile is loaded; it names a state and the parser should strip the
# state it names. So: any of the fifty, longest first, so "-west-virginia" is
# not read as a city called West followed by Virginia.
_STATE_TAILS = tuple(sorted(
    ("-" + name.replace(" ", "-") for name in discover._STATES),
    key=len, reverse=True))


def _split_tail(slug: str) -> tuple[str, list[str]]:
    """Peel "-<state>-<country>" off a slug, innermost last.

    Order matters and is the whole reason this is a function. The country
    suffix is outermost, so it comes off first and the state is only then at
    the end of what is left. Checking both against the original slug finds the
    country and never the state.
    """
    tail: list[str] = []
    for suffix in ("-united-states",):
        if slug.endswith(suffix):
            slug = slug[: -len(suffix)]
            tail.insert(0, suffix)
    for suffix in _STATE_TAILS:
        if slug.endswith(suffix):
            slug = slug[: -len(suffix)]
            tail.insert(0, suffix)
            break
    return slug, tail


def _deslug(slug: str) -> tuple[str, str]:
    """Split a job URL slug into (title, location), both human-readable."""
    slug = _TRAILING_ID.sub("", slug).strip("/")
    slug, raw_tail = _split_tail(slug)
    tail = [t.strip("-").replace("-", " ").title() for t in raw_tail]
    title, _, city = slug.rpartition("-")
    if not title:                      # single-token slug: no city to split off
        title, city = city, ""
    location = ", ".join(x for x in [city.replace("-", " ").title(), *tail] if x)
    return title.replace("-", " ").title(), location


class Challenged(RuntimeError):
    """A site answered without content -- a bot check, not a posting.

    Raised rather than returned because it is not a fact about this posting.
    Every other posting on the same host is about to do the same thing, and
    the caller should stop asking and say so.
    """


def challenge_reason(resp) -> str:
    """Name the bot check, or "" if the response is a real page.

    Two signals, and the first one is worth having because it is the site
    saying so in as many words. AWS WAF Bot Control answers a challenged
    request with `x-amzn-waf-action: challenge`, a 202 and zero bytes; passing
    it means executing the JavaScript it would have served a browser, which is
    not something this program does. jobs.virginia.gov sits behind it, which is
    why the Commonwealth's 500-odd postings go quiet in bursts.

    The second is the older guess: any 2xx with nothing in it. Kept because not
    every WAF labels itself, and an empty 200 is never a posting either way.
    """
    if resp is None:
        return ""
    action = (resp.headers.get("x-amzn-waf-action") or "").strip().lower()
    if action:
        return f"AWS WAF answered {resp.status_code} with '{action}'"
    if not resp.text.strip():
        return f"answered {resp.status_code} with an empty body"
    return ""


def sitemap(entry: dict) -> list[Job]:
    """Read an employer's sitemap and turn its job URLs into Jobs.

    Titles and locations come out of the URL slug -- no HTML is parsed here.
    That is enough to score on, and `sitemap_detail` fills in the real body
    from the posting's JSON-LD for the handful that score well enough to be
    worth a second call.

    Statewide boards are trimmed on the way in: everything in the commute ring
    is kept, plus title-keyword matches from anywhere. Without that, one entry
    would put 3,900 postings a run through the scorer to surface a few dozen.
    """
    resp = http.get(entry["sitemap"], timeout=TIMEOUT)
    if resp is None or resp.status_code >= 400:
        return []
    job_path = entry.get("job_path", "/job")
    keep_local = [t.lower() for t in entry.get("local_terms", [])]
    keywords = [q.lower() for q in entry.get("queries", workday_queries())]

    out: list[Job] = []
    for block in _SITEMAP_ENTRY.findall(resp.text):
        loc_match = _SITEMAP_LOC.search(block)
        if loc_match is None:
            continue
        url = loc_match.group(1)
        if job_path not in url:
            continue
        title, location = _deslug(url.split(job_path, 1)[1])
        hay = f"{title} {location}".lower()
        if keep_local and not (
                any(t in hay for t in keep_local)
                or any(k in title.lower() for k in keywords)):
            continue
        mod = _LASTMOD.search(block)
        out.append(Job(
            title=title,
            company=entry["name"],
            url=url,
            source="sitemap",
            location=location,
            posted_at=parse_date(mod.group(1)) if mod else None,
            external_id=url.rsplit("/", 1)[-1],
        ))
    return out


# schema.org publishes pay as a MonetaryAmount, and a JobPosting that carries
# one is an employer stating a figure rather than a regex inferring it. The
# sitemap lane ignored it until 2026-09-15, which is why every posting from a
# statewide board arrived with a blank salary while the markup beside the
# description said $48,721 to $79,172.
#
# unitText is the whole game: the same shape carries an annual band and an
# hourly rate, and reading one as the other is off by a factor of 2080.
_PERIOD_HOURS = {"HOUR": 2080, "DAY": 260, "WEEK": 52, "MONTH": 12,
                 "YEAR": 1}


def _schema_salary(node: dict) -> tuple[float | None, float | None]:
    """(min, max) annual, from a JobPosting's baseSalary. Blanks on anything odd."""
    base = node.get("baseSalary")
    if isinstance(base, list):
        base = base[0] if base else None
    if not isinstance(base, dict):
        return None, None
    value = base.get("value")
    if isinstance(value, list):
        value = value[0] if value else None

    if isinstance(value, dict):
        period = str(value.get("unitText") or "YEAR").upper()
        raw = (value.get("minValue"), value.get("maxValue"))
        if raw == (None, None):
            raw = (value.get("value"), value.get("value"))
    elif isinstance(value, (int, float, str)):
        period = str(base.get("unitText") or "YEAR").upper()
        raw = (value, value)
    else:
        return None, None

    factor = _PERIOD_HOURS.get(period)
    if not factor:
        return None, None

    out = []
    for item in raw:
        try:
            number = float(str(item).replace(",", "").replace("$", ""))
        except (TypeError, ValueError):
            out.append(None)
            continue
        annual = number * factor
        # The same sanity band the body parser uses. Boards publish zeroes and
        # placeholder ones in this field as readily as they do in prose.
        out.append(annual if 15_000 <= annual <= 900_000 else None)
    low, high = out
    if low and high and high < low:
        low, high = high, low
    return low, high


def job_postings_in(html: str) -> list[dict]:
    """Every schema.org JobPosting node on a page, in document order.

    A careers page can carry several: the posting, plus an Organization and a
    BreadcrumbList that are not it. Only JobPosting nodes come back, and a
    `@graph` wrapper is unwrapped, because Yoast and a dozen other plugins
    nest everything under one.
    """
    found: list[dict] = []
    for raw in _JSONLD.findall(html):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        stack = list(data) if isinstance(data, list) else [data]
        while stack:
            node = stack.pop(0)
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "JobPosting" in kinds:
                found.append(node)
    return found


def _apply_posting(job: Job, node: dict) -> bool:
    """Copy a JobPosting node onto a Job. True if it carried a description."""
    if node.get("description"):
        job.description = clean_text(node["description"])
    low, high = _schema_salary(node)
    if low or high:
        job.salary_min, job.salary_max = low, high
    job.title = node.get("title") or job.title
    job.posted_at = parse_date(node.get("datePosted")) or job.posted_at
    place = node.get("jobLocation")
    place = place[0] if isinstance(place, list) and place else place
    address = (place or {}).get("address") or {}
    city = address.get("addressLocality")
    if city:
        job.location = ", ".join(
            x for x in (city, address.get("addressRegion")) if x)
    return bool(node.get("description"))


def sitemap_detail(job: Job, entry: dict) -> bool:
    """Fill in one posting's body from its schema.org JSON-LD.

    Same contract as `workday_detail`: one HTTP call, only worth spending on a
    posting that already scored. The JSON-LD also carries the authoritative
    title, city and post date, all of which beat what the slug guessed.
    """
    resp = http.get(job.url, timeout=TIMEOUT, spacing=entry.get("spacing", 5.0))
    if resp is None or resp.status_code >= 400:
        return False
    # A bot challenge answers 2xx with nothing in it, which is indistinguishable
    # from a posting that simply has no markup unless you look at the length.
    # jobs.virginia.gov started returning 202 and an empty body at some point
    # before 2026-09-15, and because the only signal was "no JSON-LD found",
    # 52 Commonwealth postings sat in the candidate set with no description,
    # no salary and nothing anywhere saying why. Silence is the bug.
    reason = challenge_reason(resp)
    if reason:
        raise Challenged(f"{entry.get('name', job.company)}: {reason}")
    for node in job_postings_in(resp.text):
        return _apply_posting(job, node)
    return False


# The shortest body worth trading a snippet for. Adzuna sends exactly 500
# characters, so anything near that is another summary rather than the
# posting, and a JSON-LD `description` of two lines is a stub some ATS
# templates emit whether or not anyone filled the field in.
MIN_FULL_BODY = 900


def _title_words(title: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", (title or "").lower())
            if len(w) > 3}


_ADZUNA_LAND = re.compile(r"^https?://(?:www\.)?adzuna\.com/land/ad/(\d+)", re.I)


def _urls_to_try(url: str) -> list[str]:
    """The posting's URL, and anywhere else the same posting is readable.

    Adzuna hands out two links to one ad. `redirect_url`, which the API
    returns and the digest links to, is `/land/ad/<id>` -- a paid outbound
    click, and it is guarded: it answers a browser User-Agent with 403 and
    everything else with an interstitial carrying no markup. `/details/<id>`
    is the same ad on Adzuna's own page, is not guarded, and carries the full
    JobPosting markup.

    The outbound link is still tried first, because when it does work it lands
    on the employer's own posting, which is the better copy. The detail page is
    the fallback, and it is worth having: of 21 postings whose outbound link
    gave nothing, 16 read cleanly off `/details/`.
    """
    urls = [url]
    ad = _ADZUNA_LAND.match(url or "")
    if ad:
        urls.append(f"https://www.adzuna.com/details/{ad.group(1)}")
    return urls


def partial_detail(job: Job) -> bool:
    """Replace an aggregator's snippet with the posting it was cut from.

    The aggregator's URL is a redirect into the employer's own board, so one
    GET lands on the real posting and `requests` follows the hops. Most boards
    publish schema.org JobPosting markup there, because Google Jobs reads it,
    which means the full body is sitting in the page as structured data and
    does not have to be scraped out of the layout.

    Two things have to be true before the body is believed, and both exist
    because a wrong JD builds a wrong packet just as surely as a wrong board
    builds a wrong digest:

    The landing page has to be *this* job. A redirect to an expired req serves
    the careers index instead, and that page has JobPosting markup of its own
    for whatever is featured today. So the page's title has to share a real
    word with the title we came in with. "Senior Business Analyst" answering
    for "Business Analyst" is the same req described twice; "Careers at Lumen"
    is not.

    And the body has to be longer than the snippet. Otherwise a short stub
    overwrites 500 characters of real text with nothing, and the posting
    silently gets worse while the `partial` flag says it got better.

    Only the body is taken, which is the difference between this and
    `sitemap_detail`. There the JSON-LD is the company's own and beats a title
    and city guessed out of a URL slug. Here the page is often the
    aggregator's own detail page, and its idea of where the job is has already
    been through a normalizer: every Richmond posting came back located in
    "Capitol, VA, VA", which is not a place. Overwriting a good city with that
    turned "Richmond, VA metro" into "onsite, elsewhere in VA" and took a
    tier-1 posting to zero for a reason that was invented in transit. Salary
    is taken only when we have none, for the same reason.
    """
    want = _title_words(job.title)
    for url in _urls_to_try(job.url):
        # Slower than the default 1.5s, and the same rate `sitemap_detail`
        # reads a careers page at. This is one page a person could have opened
        # by clicking the link in the digest, and reading a few hundred of
        # them at browser speed is what put jobs.virginia.gov's WAF in front
        # of us: a repair at 1.5s got four postings before it started
        # answering 202.
        resp = http.get(url, timeout=TIMEOUT, allow_redirects=True, spacing=5.0)
        if resp is None or resp.status_code >= 400:
            continue
        # Same rule as `sitemap_detail`, and for the same reason: a bot check
        # is not a fact about this posting. Swallowing it here meant a repair
        # over 82 rows read four of them, met a WAF on the fifth, and reported
        # "the links have expired" about 78 postings that were all still live.
        reason = challenge_reason(resp)
        if reason:
            raise Challenged(f"{http._host(url)}: {reason}")
        for node in job_postings_in(resp.text):
            body = clean_text(node.get("description") or "")
            if len(body) < MIN_FULL_BODY or len(body) <= len(job.description):
                continue
            got = _title_words(str(node.get("title") or ""))
            if want and got and not (want & got):
                continue
            job.description = body
            job.partial = False
            if not (job.salary_min or job.salary_max):
                low, high = _schema_salary(node)
                if low or high:
                    job.salary_min, job.salary_max = low, high
            return True
    return False


HANDLERS = {
    "greenhouse": greenhouse,
    "sitemap": sitemap,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "workable": workable,
    "recruitee": recruitee,
    "workday": workday,
}
