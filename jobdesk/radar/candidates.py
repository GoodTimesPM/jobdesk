"""Hand-off cache for Assisted Apply.

Job Radar already pays for the expensive part: it pulls the posting, fetches
the JD body on anything plausible, scores it, and knows the flags. Assisted
Apply needs exactly that -- the JD text plus the scoring context -- to build an
application packet without re-fetching a page Job Radar already has.

So this writes it down once per run instead of making the apply package go
back to the network. It is a DATA hand-off, not shared code: the file is plain
JSON with no radar types in it, and nothing here imports or is imported by
the apply package.

Deliberately separate from the snapshot in `main.run`. That one is the market
dataset -- newly-seen only, JD bodies stripped, committed, grows forever. This
one is a short-lived working set: everything currently worth applying to, JD
bodies kept, git-ignored, rewritten every run.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import config
from .models import Job, parse_date

# A JD body past this is boilerplate (benefits, EEO, legal). Truncating keeps
# the file a few MB rather than a few dozen; the required/preferred sections a
# tailoring run reads are always near the top.
MAX_DESCRIPTION_CHARS = 12_000

# The working set, not the archive. A ceiling on file size, and nothing else.
#
# This was 300, and 300 was wrong in a way that took a while to see. The rows
# are sorted by score before the slice, so once more than 300 postings sat
# above the floor the cap stopped being a size limit and became a second,
# invisible score floor -- the 300th row scored 68, so C-tier (45-59) and the
# bottom of B never reached the Jobs tab at all, and the tab quietly claimed
# there were no C-tier postings when there were 98 of them.
#
# 1500 is chosen against the real number: 508 postings currently sit above the
# floor in the retention window, and the file is ~6KB a row, so the ceiling is
# a few tens of MB in the worst case and three times the headroom in practice.
# `write()` says so out loud when the cap actually bites, because a silent cut
# is what made the first one hard to find.
MAX_ENTRIES = 1500

# A posting Job Radar hasn't seen in a month is usually filled or pulled.
RETENTION_DAYS = 30


def _fresh(row: dict, cutoff: datetime) -> bool:
    seen = parse_date(row.get("last_seen"))
    return seen is None or seen >= cutoff


def _rescore_carried(by_uid: dict[str, dict], touched: set[str],
                     log: Callable[[str], None] = print) -> None:
    """Re-score the rows this run did not see, in place.

    A row stays in the cache for RETENTION_DAYS after the last run that found
    it, and until now it kept whatever number the code of the day gave it.
    Two things go wrong with that, and the file being sorted by score makes
    both of them visible in the Jobs tab rather than merely untidy.

    A scoring change only ever reached the postings found after it shipped.
    The 0.3.0 rescale is the clean example: it took the top of the board from
    100 to 96, and the morning after, 46 rows scored by the old code were
    still claiming a perfect 100 and sitting above every posting found that
    day. The cache was showing two scales at once, with the obsolete one on
    top.

    And nothing ever aged. Freshness is worth 10 points inside FRESH_DAYS and
    -18 past 90 days, but a row scored on the day it appeared carried its +10
    for the next month whether or not the posting was still young, so the
    cache slowly filled with rows flattering themselves. Re-scoring is also
    how a posting that today's rules would disqualify finally leaves.

    Costs no network: everything the scorer reads is already in the row.
    """
    from . import score  # local import, same reason as in write()

    changed = 0
    for uid, row in by_uid.items():
        if uid in touched:
            continue
        try:
            job = score.score_job(Job.from_dict(row))
        except Exception:
            continue    # a malformed row is dropped by the floor check below
        was = row.get("score")
        for field in ("score", "tier", "reasons", "flags", "job_family",
                      "salary_min", "salary_max"):
            row[field] = getattr(job, field)
        row["required_years"] = score.required_years(job)
        if row["score"] != was:
            changed += 1
    if changed:
        log(f"candidates: re-scored {changed} carried-over posting(s)")


def save(rows: list[dict], path=None) -> None:
    """Write the cache through a temp file, so a reader never sees half of it.

    The window reads this file while the radar and the Criteria tab write it.
    """
    path = path or config.CANDIDATES
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def write(jobs: list[Job], log: Callable[[str], None] = print) -> int:
    """Rewrite the candidate cache from this run's scored postings.

    Best-effort in both directions: a broken cache file is replaced rather
    than crashing the run, and a failure to write is logged and swallowed.
    Discovery must never go down because the apply side's convenience file
    couldn't be updated.
    """
    from . import score  # local import: score imports profile, nothing here

    path = config.CANDIDATES
    try:
        previous = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else []
    except (OSError, ValueError) as exc:
        log(f"candidates: unreadable cache, starting fresh ({exc})")
        previous = []

    by_uid: dict[str, dict] = {
        row["uid"]: row for row in previous if isinstance(row, dict) and row.get("uid")
    }
    now = datetime.now(timezone.utc)
    stamp = now.isoformat()
    kept = 0
    touched: set[str] = set()

    for job in jobs:
        if job.score < config.MIN_SCORE_TO_REPORT:
            continue
        kept += 1
        prior = by_uid.get(job.uid, {})
        row = job.to_dict()
        row["uid"] = job.uid
        row["dedupe_key"] = job.dedupe_key
        row["required_years"] = score.required_years(job)
        # A later run can lose the body -- detail-fetch budget is capped, and
        # aggregator copies carry less text than the ATS original. Never trade
        # a JD we already have for an empty one.
        body = job.description or prior.get("description") or ""
        row["description"] = body[:MAX_DESCRIPTION_CHARS]
        row["first_seen"] = prior.get("first_seen") or row.get("first_seen") or stamp
        row["last_seen"] = stamp
        touched.add(job.uid)
        by_uid[job.uid] = row

    _rescore_carried(by_uid, touched, log)

    cutoff = now - timedelta(days=RETENTION_DAYS)
    # The score floor is applied to every row, not just this run's. A carried
    # posting that has aged out of the bands, or that the current rules now
    # disqualify, has no business in a working set of things worth applying to.
    rows = [r for r in by_uid.values()
            if _fresh(r, cutoff)
            and (r.get("score") or 0) >= config.MIN_SCORE_TO_REPORT]
    rows.sort(key=lambda r: (-(r.get("score") or 0), str(r.get("last_seen") or "")))
    if len(rows) > MAX_ENTRIES:
        cut = rows[MAX_ENTRIES:]
        log(f"candidates: cap dropped {len(cut)} posting(s); the working set "
            f"now starts at score {rows[MAX_ENTRIES - 1].get('score')}, not "
            f"{config.MIN_SCORE_TO_REPORT}")
        rows = rows[:MAX_ENTRIES]

    try:
        save(rows, path)
    except OSError as exc:
        log(f"candidates: write failed, run continues ({exc})")
        return 0
    log(f"candidates: {kept} above threshold this run -> {len(rows)} in cache")
    return len(rows)


def reuse_bodies(jobs: list[Job], path=None,
                 log: Callable[[str], None] = print) -> int:
    """Give this run's postings the bodies earlier runs already read.

    A sitemap or Workday list carries no body, so without this every run
    spent its detail budget re-reading the same top postings. On
    jobs.virginia.gov the WAF allows about 36 reads a run, and the same 17
    were read every time while 50 newer rows never got one.
    """
    path = path or config.CANDIDATES
    try:
        rows = json.loads(path.read_text(encoding="utf-8-sig") or "[]")
    except (OSError, ValueError):
        return 0
    # Keyed by URL as well as uid, because the uid is company and title, and
    # two state agencies can both post an "Energy Analyst".
    bodies = {(r["uid"], r.get("url")): r["description"] for r in rows
              if isinstance(r, dict) and r.get("uid") and r.get("description")
              and not r.get("partial")
              and "partial-description" not in (r.get("flags") or [])}
    reused = 0
    for job in jobs:
        if job.description:
            continue
        body = bodies.get((job.uid, job.url))
        if body:
            job.description = body
            reused += 1
    if reused:
        log(f"  reused {reused} job description(s) from the last run")
    return reused


# What one WAF cooldown costs, and how many are worth sitting through before
# concluding the host has simply stopped talking to us. Seven minutes is what
# jobs.virginia.gov measured at; the extra minute is slack.
COOLDOWN_SECONDS = 480
MAX_COOLDOWNS = 4

# Rows the end-of-run sweep rechecks. Each is one or two page reads at five
# seconds apiece, so twenty adds two to three minutes to a run, and a closed
# ad leaves the board within a day or two instead of sitting there a month.
SWEEP_BUDGET = 20


def repair_partials(path=None, log: Callable[[str], None] = print,
                    budget: int | None = None, wait: bool = True) -> int:
    """Fetch the real posting for every row here whose body never arrived.

    Two kinds of row qualify, and they got here by different routes:

    * An aggregator snippet -- 500 characters and an ellipsis, flagged
      `partial-description`.
    * No body at all, because the detail fetch never reached it. The per-run
      budget in `fetch_details` is finite and used to be spent in collection
      order, so a source collected late got nothing regardless of how well its
      postings scored.

    Both end the same way: the years requirement and the tool list are sitting
    in text nobody read, and the salary is an estimate over a page that states
    the band. The fix in `sources.fill_partials` and the sort in
    `fetch_details` only reach postings a future run collects, and a row lives
    here for RETENTION_DAYS after the last run that saw it, so today's board
    would stay as it is until it aged out. This repairs the rows in place,
    rescores them, and saves.

    Safe to run twice: a row that fills is no longer missing a body and is not
    tried again.

    A row whose posting answers 404 everywhere is dropped, but only if the
    latest run did not see it. Adzuna keeps listing an ad for a while after
    its page goes, and a posting still being advertised stays until the feed
    lets go of it too. Before this, closed ads sat on the board as snippets
    for the whole RETENTION_DAYS: 42 of them on 2026-10-01.

    `budget` is the radar's own call, made at the end of every run (see
    SWEEP_BUDGET). It checks only rows the run did not see, since
    `fill_partials` already tried the rest, least recently checked first so
    a few unreadable rows cannot take the budget every time. It does not
    wait out a bot check either.
    """
    import time
    from collections import Counter

    from . import http, score
    from .sources import ats

    path = path or config.CANDIDATES
    try:
        rows = json.loads(path.read_text(encoding="utf-8-sig") or "[]")
    except (OSError, ValueError) as exc:
        log(f"candidates: cannot read the cache ({exc})")
        return 0

    def wants_body(r: dict) -> bool:
        if r.get("partial") or "partial-description" in (r.get("flags") or []):
            return True
        return not (r.get("description") or "").strip()

    latest = max((str(r.get("last_seen") or "") for r in rows), default="")

    def carried(r: dict) -> bool:
        return str(r.get("last_seen") or "") < latest

    todo = [r for r in rows if wants_body(r) and r.get("url")]
    if budget is not None:
        todo = [r for r in todo if carried(r)]
        todo.sort(key=lambda r: (str(r.get("checked_at") or ""),
                                 -(r.get("score") or 0)))
    if not todo:
        if budget is None:
            log("candidates: every row already has its posting")
        return 0

    stamp = datetime.now(timezone.utc).isoformat()
    fixed = 0
    tried = 0
    closed: set[str] = set()
    blocked: dict[str, str] = {}
    cooldowns: Counter[str] = Counter()
    queue = list(todo)
    while queue:
        row = queue.pop(0)
        host = http._host(row["url"])
        if host in blocked:
            continue
        if budget is not None and tried >= budget:
            break
        tried += 1
        job = Job.from_dict(row)
        job.partial = bool(row.get("partial")
                           or "partial-description" in (row.get("flags") or []))
        try:
            got = ats.partial_detail(job)
        except ats.Challenged as why:
            # Not a dead link, and on this host not permanent either. Measured
            # on 2026-09-19: jobs.virginia.gov's WAF held for about seven
            # minutes of one-request-per-45-seconds probing and then answered
            # 200 with the whole page. A run cannot afford that wait, which is
            # why `fill_partials` drops the host and moves on. This is a
            # command someone typed on purpose to fix the board they are
            # looking at, so here it waits, puts the row back, and carries on.
            #
            # Before this was caught at all, a repair that met the WAF on its
            # fifth request reported 78 live postings as expired.
            cooldowns[host] += 1
            if not wait or cooldowns[host] > MAX_COOLDOWNS:
                blocked[host] = str(why)
                continue
            log(f"  {why} - waiting {COOLDOWN_SECONDS}s "
                f"({cooldowns[host]} of {MAX_COOLDOWNS})")
            time.sleep(COOLDOWN_SECONDS)
            queue.insert(0, row)
            continue
        except ats.Gone:
            row["checked_at"] = stamp
            if carried(row):
                closed.add(row["uid"])
                log(f"  {row.get('company')} | {str(row.get('title'))[:40]} "
                    f"-> taken down, off the board")
            continue
        except Exception:
            row["checked_at"] = stamp
            continue
        row["checked_at"] = stamp
        if not got:
            continue
        fixed += 1
        job = score.score_job(job)
        row.update(job.to_dict())
        row["description"] = job.description[:MAX_DESCRIPTION_CHARS]
        row["required_years"] = score.required_years(job)
        log(f"  {job.company} | {job.title[:40]} -> "
            f"{len(job.description)} chars, score {job.score}")

    for host, why in sorted(blocked.items()):
        log(f"  {why} - the postings on {host} are live, we just cannot read "
            f"them; open one and use the paste button")

    if not (fixed or closed):
        if budget is None:
            log(f"candidates: none of the {len(todo)} posting(s) could be fetched")
        else:
            # The checked_at stamps still matter: they move the next run on
            # to rows it has not tried.
            try:
                save(rows, path)
            except OSError:
                pass
        return 0

    rows = [r for r in rows if r.get("uid") not in closed
            and (r.get("score") or 0) >= config.MIN_SCORE_TO_REPORT]
    rows.sort(key=lambda r: (-(r.get("score") or 0), str(r.get("last_seen") or "")))
    try:
        save(rows, path)
    except OSError as exc:
        log(f"candidates: write failed ({exc})")
        return 0
    log(f"candidates: repaired {fixed} and removed {len(closed)} taken down, "
        f"of {len(todo)} posting(s) without a description")
    return fixed


if __name__ == "__main__":       # pragma: no cover
    import sys
    if "--repair-partials" in sys.argv:
        raise SystemExit(0 if repair_partials() >= 0 else 1)
    print("usage: py -m jobdesk.radar.candidates --repair-partials")
