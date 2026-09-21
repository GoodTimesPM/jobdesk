"""What can I apply to right now.

Primary source is Job Radar's candidate cache (`data/radar/candidates.json`),
which carries the JD body and the scoring context. If that file doesn't exist
yet -- Job Radar hasn't run since this sub-project was built, or it's paused --
the newest digest is parsed instead, which gives everything except the JD text.
That fallback matters: the failure mode to avoid is "the apply tool is useless
because the discovery tool didn't run today".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import config


@dataclass
class Candidate:
    title: str
    company: str
    url: str
    source: str = "manual"
    location: str = ""
    description: str = ""
    score: int = 0
    tier: str = ""
    uid: str = ""
    dedupe_key: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    remote: bool = False
    posted_at: str = ""
    first_seen: str = ""
    last_seen: str = ""
    required_years: int | None = None
    job_family: str = ""
    flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    also_on: list[str] = field(default_factory=list)
    origin: str = "cache"            # cache | digest | manual

    # The employer inside a shared job board -- "Dept of Accounts" under
    # "Commonwealth of Virginia". Written by the radar, read by the
    # concurrency guard, empty whenever the company is already the employer.
    division: str = ""

    @property
    def partial_description(self) -> bool:
        """Is the cached body a snippet the aggregator cut, not the posting?

        Read off the flag the radar wrote, so `apply/` learns this without
        importing `radar/`. It matters because the cutoff for a usable JD is
        400 characters and an aggregator snippet is 500: without this, the
        shortest possible read of a posting clears the bar for the longest
        piece of work in the app.
        """
        return "partial-description" in self.flags

    @property
    def salary_text(self) -> str:
        if self.salary_min and self.salary_max:
            return f"${self.salary_min:,.0f}-${self.salary_max:,.0f}"
        if self.salary_min:
            return f"${self.salary_min:,.0f}+"
        if self.salary_max:
            return f"up to ${self.salary_max:,.0f}"
        return ""

    @property
    def age_days(self) -> int | None:
        for value in (self.posted_at, self.first_seen):
            if not value:
                continue
            try:
                when = datetime.fromisoformat(value)
            except ValueError:
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return int((datetime.now(timezone.utc) - when).total_seconds() // 86400)
        return None

    @property
    def found_on(self) -> str:
        """The date Job Radar first saw this posting (YYYY-MM-DD), or "".

        `first_seen` is the run that discovered it; `last_seen` only says the
        posting was still up this morning, which every live posting is. Only
        the former answers "is this new today".
        """
        return (self.first_seen or "")[:10]

    def one_line(self) -> str:
        bits = [self.location or ("Remote" if self.remote else "")]
        if self.salary_text:
            bits.append(self.salary_text)
        age = self.age_days
        if age is not None:
            bits.append(f"{age}d old")
        bits.append(self.source)
        return " - ".join(b for b in bits if b)


def _from_cache() -> list[Candidate]:
    path = config.RADAR_CANDIDATES
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    known = set(Candidate.__dataclass_fields__)
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        data = {k: v for k, v in row.items() if k in known}
        data["origin"] = "cache"
        out.append(Candidate(**data))
    return out


# Digest headings look like:
#   ### [84] [FP&A Analyst](https://...)
#   *MyFitnessPal - United States - $70,000-$110,000 - 0d old - himalayas*
_HEAD = re.compile(r"^### \[(\d+)\] \[(.+?)\]\((\S+)\)\s*$")
_META = re.compile(r"^\*(.+?)\*\s*$")
_TIER = re.compile(r"^## ([A-F]) - ")


def _from_digest() -> list[Candidate]:
    folder = config.RADAR_DIGESTS
    if not folder.exists():
        return []
    digests = sorted(folder.glob("digest_*.md"))
    if not digests:
        return []
    lines = digests[-1].read_text(encoding="utf-8", errors="replace").splitlines()

    out: list[Candidate] = []
    tier = ""
    pending: Candidate | None = None
    for line in lines:
        tier_match = _TIER.match(line)
        if tier_match:
            tier = tier_match.group(1)
            continue
        head = _HEAD.match(line)
        if head:
            pending = Candidate(
                title=head.group(2), company="", url=head.group(3),
                score=int(head.group(1)), tier=tier, source="digest",
                origin="digest",
            )
            out.append(pending)
            continue
        if pending is not None:
            meta = _META.match(line)
            if meta:
                parts = [p.strip() for p in meta.group(1).split(" - ")]
                if parts:
                    pending.company = parts[0]
                if len(parts) > 1:
                    pending.location = parts[1]
                if parts and parts[-1] != pending.company:
                    pending.source = parts[-1]
                pending = None
    return [c for c in out if c.company]


def load(*, exclude_uids: set[str] | None = None,
         min_score: int = 0, limit: int = 40,
         found_on: str = "") -> tuple[list[Candidate], str]:
    """The apply queue, best first.

    Returns the list and a one-line note about where it came from, because
    "there are no candidates" and "Job Radar hasn't run" look identical from
    the menu and need completely different responses.

    `found_on` (YYYY-MM-DD) narrows to postings Job Radar *first* saw that day.
    The cache is cumulative -- it carries every live posting from every past
    run -- so without this the list is a permanent leaderboard of old 100s, and
    the thing actually found this morning is buried below the cut.
    """
    rows, origin = _from_cache(), "cache"
    if not rows:
        rows, origin = _from_digest(), "digest"
    exclude = exclude_uids or set()
    rows = [r for r in rows if r.score >= min_score
            and not (r.uid and r.uid in exclude)]
    if found_on:
        rows = [r for r in rows if r.found_on == found_on]
    rows.sort(key=lambda c: (-c.score, c.age_days if c.age_days is not None else 99))

    if origin == "cache" and found_on:
        note = (f"{len(rows)} posting(s) Job Radar first saw on {found_on} "
                f"(every score, JD text included)")
    elif origin == "cache":
        note = (f"{len(rows)} candidate(s) from Job Radar's cache "
                f"(JD text included)")
    elif rows:
        note = (f"{len(rows)} candidate(s) parsed from the newest digest -- "
                f"Job Radar hasn't written a candidate cache yet, so JD text "
                f"will be fetched or pasted per job")
    else:
        note = ("No candidates found. Run Job Radar (menu option 8) or build "
                "a packet from a URL instead (option 2).")
    return rows[:limit], note


def run_dates() -> list[str]:
    """Every date the cache has a first-sighting for, newest first.

    Used when today's list is empty: "Job Radar hasn't run yet today" and
    "it ran and found nothing new" need different responses, and the newest
    date in the cache is the one that tells them apart.
    """
    seen = {c.found_on for c in _from_cache() if c.found_on}
    return sorted(seen, reverse=True)
