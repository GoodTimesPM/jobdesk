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
        by_uid[job.uid] = row

    cutoff = now - timedelta(days=RETENTION_DAYS)
    rows = [r for r in by_uid.values() if _fresh(r, cutoff)]
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
