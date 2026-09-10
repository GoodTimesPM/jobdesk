"""Every posting the radar has ever seen, not just the ones it kept.

`candidates.json` is a working set: postings above the score floor, seen in
the last 30 days, capped at 300. That cap is the right one for a queue you
work through, and it is the wrong one for the question "did we ever see a job
at Markel", which is what `seen.json` can answer -- 24,000 postings and
counting, going back to the first sweep.

What the archive has and the queue does not: everything. What the queue has
and the archive does not: the JD body, the location, the salary, the scoring
reasons. `seen.json` is an index the radar keeps so it can tell a new posting
from one it already reported, so it stores six fields and no more, and this
module does not pretend otherwise. A row here is a title, a company, a link,
the score it got, and the dates it was first and last seen.

The file is 8MB, so it is read once and held, and re-read only when its
modification time changes -- which happens exactly once per sweep.
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timezone

from ..radar import config as radar_config

_lock = threading.Lock()
_cached: list[dict] = []
_stamp: float = -1.0


def _load() -> list[dict]:
    """The archive, from memory unless the radar has written since."""
    global _cached, _stamp
    path = radar_config.SEEN_FILE
    if not path.exists():
        return []
    mtime = path.stat().st_mtime
    with _lock:
        if mtime == _stamp and _cached:
            return _cached
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            # A half-written index is a reason to show nothing in one tab, not
            # a reason to break the app. The next sweep rewrites it.
            return []
        rows = []
        for uid, row in (raw.items() if isinstance(raw, dict) else []):
            if not isinstance(row, dict):
                continue
            rows.append({
                "uid": uid,
                "title": row.get("title") or "",
                "company": row.get("company") or "",
                "url": row.get("url") or "",
                "score": int(row.get("score") or 0),
                "times_seen": int(row.get("times_seen") or 0),
                "first_seen": (row.get("first_seen") or "")[:10],
                "last_seen": (row.get("last_seen") or "")[:10],
            })
        rows.sort(key=lambda r: (r["last_seen"], r["score"]), reverse=True)
        _cached, _stamp = rows, mtime
        return rows


def _live_days(row: dict) -> int | None:
    """How long the posting stayed up, when both dates are readable."""
    try:
        first = date.fromisoformat(row["first_seen"])
        last = date.fromisoformat(row["last_seen"])
    except (ValueError, KeyError):
        return None
    return (last - first).days


def summary() -> dict:
    """The numbers above the table: how much has been seen, and since when."""
    rows = _load()
    if not rows:
        return {"total": 0, "companies": 0, "since": "", "until": "",
                "scored": 0}
    firsts = [r["first_seen"] for r in rows if r["first_seen"]]
    last = max((r["last_seen"] for r in rows if r["last_seen"]), default="")
    return {
        "total": len(rows),
        "companies": len({r["company"].lower() for r in rows if r["company"]}),
        "since": min(firsts) if firsts else "",
        "until": last,
        "scored": sum(1 for r in rows if r["score"] > 0),
        # The three that keep "24,000" from being read as "24,000 jobs exist".
        # `readings` is how many times a posting has been pulled off a board,
        # summed, and it runs several times the row count. `last_read` and
        # `last_new` are the most recent day's share of that: how many of these
        # rows the sweep saw again, and how many of them it had never seen.
        "readings": sum(r["times_seen"] for r in rows),
        "last_read": sum(1 for r in rows if r["last_seen"] == last) if last else 0,
        "last_new": sum(1 for r in rows if r["first_seen"] == last) if last else 0,
    }


def search(*, text: str = "", min_score: int = 0, since: str = "",
           limit: int = 200, offset: int = 0) -> dict:
    """Filter the archive server-side.

    Server-side because the whole point of this tab is the rows the queue
    cannot hold: shipping 24,000 of them to the browser to filter there would
    be 5MB of JSON to answer a question that touches forty rows.
    """
    rows = _load()
    needle = text.strip().lower()
    if needle:
        words = needle.split()
        rows = [r for r in rows
                if all(w in (r["title"] + " " + r["company"]).lower()
                       for w in words)]
    if min_score:
        rows = [r for r in rows if r["score"] >= min_score]
    if since:
        rows = [r for r in rows if r["last_seen"] >= since]

    page = rows[offset:offset + max(1, min(limit, 500))]
    return {
        "rows": [{**r, "live_days": _live_days(r)} for r in page],
        "matched": len(rows),
        "offset": offset,
        "total": len(_load()),
    }


def companies(limit: int = 60) -> list[dict]:
    """Who posts the most, and how well their postings score.

    The one genuinely new thing 24,000 rows can tell you that 300 cannot: an
    employer that posts constantly and never scores is one to stop watching,
    and one that posts rarely and scores high is one to put on the watch list.
    """
    tally: dict[str, dict] = {}
    for row in _load():
        name = row["company"].strip()
        if not name:
            continue
        slot = tally.setdefault(name.lower(), {"company": name, "postings": 0,
                                               "best": 0, "scored": 0})
        slot["postings"] += 1
        slot["best"] = max(slot["best"], row["score"])
        if row["score"] > 0:
            slot["scored"] += 1
    ranked = sorted(tally.values(),
                    key=lambda c: (-c["scored"], -c["postings"]))
    return ranked[:limit]
