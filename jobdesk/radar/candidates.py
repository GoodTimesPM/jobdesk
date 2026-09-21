"""Hand-off cache for Assisted Apply (plan items 5 and 6, sub-project 4).

Job Radar already pays for the expensive part: it pulls the posting, fetches
the JD body on anything plausible, scores it, and knows the flags. Assisted
Apply needs exactly that -- the JD text plus the scoring context -- to build an
application packet without re-fetching a page Job Radar already has.

So this writes it down once per run instead of making the other sub-project go
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
        path.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    except OSError as exc:
        log(f"candidates: write failed, run continues ({exc})")
        return 0
    log(f"candidates: {kept} above threshold this run -> {len(rows)} in cache")
    return len(rows)


# What one WAF cooldown costs, and how many are worth sitting through before
# concluding the host has simply stopped talking to us. Seven minutes is what
# jobs.virginia.gov measured at; the extra minute is slack.
COOLDOWN_SECONDS = 480
MAX_COOLDOWNS = 4


def repair_partials(path=None, log: Callable[[str], None] = print) -> int:
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
    tried again, and a row whose posting has expired stays exactly as it was.
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

    todo = [r for r in rows if wants_body(r) and r.get("url")]
    if not todo:
        log("candidates: every row already has its posting")
        return 0

    fixed = 0
    blocked: dict[str, str] = {}
    cooldowns: Counter[str] = Counter()
    queue = list(todo)
    while queue:
        row = queue.pop(0)
        host = http._host(row["url"])
        if host in blocked:
            continue
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
            if cooldowns[host] > MAX_COOLDOWNS:
                blocked[host] = str(why)
                continue
            log(f"  {why} - waiting {COOLDOWN_SECONDS}s "
                f"({cooldowns[host]} of {MAX_COOLDOWNS})")
            time.sleep(COOLDOWN_SECONDS)
            queue.insert(0, row)
            continue
        except Exception:
            continue
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

    if not fixed:
        log(f"candidates: none of the {len(todo)} posting(s) could be fetched")
        return 0

    rows = [r for r in rows if (r.get("score") or 0) >= config.MIN_SCORE_TO_REPORT]
    rows.sort(key=lambda r: (-(r.get("score") or 0), str(r.get("last_seen") or "")))
    try:
        path.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    except OSError as exc:
        log(f"candidates: write failed ({exc})")
        return 0
    log(f"candidates: repaired {fixed} of {len(todo)} posting(s) that "
        f"reached the board without one")
    return fixed


if __name__ == "__main__":       # pragma: no cover
    import sys
    if "--repair-partials" in sys.argv:
        raise SystemExit(0 if repair_partials() >= 0 else 1)
    print("usage: py -m jobdesk.radar.candidates --repair-partials")
