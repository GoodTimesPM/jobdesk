"""Deduplication and the seen-posting store.

Two separate jobs:

  1. Collapse duplicates WITHIN a run. The same req legitimately appears on
     the company's Workday board, on RemoteOK, and via three staffing
     agencies. Keeping the company-direct copy is what turns "apply through
     the agency" into "apply direct" -- which matters, because a duplicate
     agency submission is one of the few things that gets a candidate
     genuinely blacklisted (plan item 13).

  2. Remember postings ACROSS runs, so the daily digest only ever shows what
     is actually new. `first_seen` also gives real posting-age data for
     boards that don't publish a date, and doubles as the raw material for
     the Richmond Job Market Dashboard (plan item 11).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import config
from .models import Job

# Preference order when the same req shows up on several sources: the
# company's own ATS always wins over an aggregator.
SOURCE_PRIORITY = {
    "workday": 0, "greenhouse": 0, "lever": 0, "ashby": 0,
    "smartrecruiters": 1, "workable": 1, "recruitee": 1,
    "usajobs": 2, "adzuna": 3, "hiringcafe": 3,
    "remoteok": 4, "remotive": 4, "himalayas": 4, "weworkremotely": 4,
    "hackernews": 5,
}


def collapse(jobs: list[Job]) -> tuple[list[Job], int]:
    """Collapse same-req duplicates within one run.

    Returns (kept, n_collapsed). The surviving copy records which other
    sources also carried it, so a posting seen on five boards is visibly
    a real, widely-syndicated req rather than five separate opportunities.
    """
    best: dict[str, Job] = {}
    also: dict[str, set[str]] = {}
    collapsed = 0

    for job in jobs:
        key = job.dedupe_key
        if not key:
            continue
        also.setdefault(key, set()).add(job.source)
        incumbent = best.get(key)
        if incumbent is None:
            best[key] = job
            continue
        collapsed += 1
        if SOURCE_PRIORITY.get(job.source, 9) < SOURCE_PRIORITY.get(incumbent.source, 9):
            # Keep the better source but don't lose a description we already
            # paid an HTTP call for.
            if not job.description and incumbent.description:
                job.description = incumbent.description
            if not job.posted_at:
                job.posted_at = incumbent.posted_at
            best[key] = job

    for key, job in best.items():
        others = also[key] - {job.source}
        if others:
            # Structured, not just a flag string: scoring reads this as a
            # competition proxy (see score._syndication_points). The flag
            # stays for the human-readable surfaces.
            job.also_on = sorted(others)
            job.flags.append("also on: " + ", ".join(job.also_on))
    return list(best.values()), collapsed


class SeenStore:
    """Persistent record of every posting the radar has ever surfaced."""

    def __init__(self, path=None):
        self.path = path or config.SEEN_FILE
        self._data: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8-sig")
            self._data = json.loads(raw) if raw.strip() else {}
        except (OSError, ValueError):
            self._data = {}

    def save(self) -> None:
        self.prune()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def prune(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=config.SEEN_RETENTION_DAYS)
        for uid in [u for u, rec in self._data.items()
                    if _parse(rec.get("first_seen")) and
                    _parse(rec["first_seen"]) < cutoff]:
            del self._data[uid]

    def is_new(self, job: Job) -> bool:
        return job.uid not in self._data

    def record(self, job: Job) -> None:
        now = datetime.now(timezone.utc)
        rec = self._data.get(job.uid)
        if rec is None:
            self._data[job.uid] = {
                "first_seen": now.isoformat(),
                "last_seen": now.isoformat(),
                "times_seen": 1,
                "title": job.title,
                "company": job.company,
                "url": job.url,
                "score": job.score,
            }
            job.first_seen = now
        else:
            rec["last_seen"] = now.isoformat()
            rec["times_seen"] = rec.get("times_seen", 1) + 1
            rec["score"] = job.score
            job.first_seen = _parse(rec.get("first_seen")) or now

    def settle_posted(self, job: Job) -> None:
        """Refuse a post date later than the run that first saw the posting.

        `posted_at` is whatever the source said it was, and some sources say
        `<lastmod>`: the day the page last changed, not the day the job went
        up. A statewide board that regenerates a posting bumps it to today,
        and a req the radar has been carrying for a week arrives looking
        brand new -- scored as fresh, sent to Discord as fresh, and listed as
        posted today beside the ones that really were.

        The store knows one thing the source does not: we already had this on
        a day it now claims to predate. That day is the latest it can
        honestly be. A ceiling, not a guess at the real date.
        """
        rec = self._data.get(job.uid)
        if not rec:
            return                      # first sighting, nothing to contradict
        first = _parse(rec.get("first_seen"))
        if first and job.posted_at and job.posted_at > first:
            job.posted_at = first

    def times_seen(self, job: Job) -> int:
        return (self._data.get(job.uid) or {}).get("times_seen", 0)

    def ghost_check(self, job: Job) -> None:
        """Flag postings that keep reappearing over a long window.

        A req the radar has seen on 40+ separate runs spanning two months is
        almost always an evergreen pipeline-filler rather than a live opening
        -- worth knowing before spending 20 minutes tailoring for it.
        """
        rec = self._data.get(job.uid)
        if not rec:
            return
        first = _parse(rec.get("first_seen"))
        if not first:
            return
        span = (datetime.now(timezone.utc) - first).days
        if span >= 45 and rec.get("times_seen", 0) >= 20:
            job.flags.append("ghost-suspect")
            job.reasons.append(
                f"seen {rec['times_seen']}x over {span}d - likely evergreen")
            job.score = max(0, job.score - 15)

    def __len__(self) -> int:
        return len(self._data)


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
