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
import re
from functools import lru_cache
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


# A staffing-agency repost farm: one requisition, advertised by a crowd of
# contract shops, each filing under its own name so `collapse` -- which keys
# on company plus title -- sees a crowd of separate jobs. One Virginia SCC
# Power BI contract arrived from twenty shops in a single Richmond pull and
# reached the digest as twenty three A-tier jobs.
#
# Both numbers are measured. Grouped by the title with cosmetic decoration
# removed, over a full Richmond pull:
#
#   23 posts  20 companies  43% agency-flagged  power bi developer
#   17 posts   9 companies  47% agency-flagged  business analyst
#    7 posts   6 companies   0% agency-flagged  board certified behavior analyst
#    3 posts   3 companies  33% agency-flagged  data analyst
#
# The bottom two are real. Six separate ABA clinics do each want a behaviour
# analyst, and Cardinal Health and Guild Mortgage really are both hiring a
# data analyst. What separates them is not the size of the crowd but who is
# in it: a farm arrives with contract shops already flagged by the body
# language in scoring, and a genuine collision of ordinary job titles arrives
# with none at all.
#
# Four is high enough that four unrelated employers posting a title that
# normalizes identically is not worth worrying about. A quarter is low enough
# to catch a farm where most members write nothing incriminating, which is
# most of them, because the aggregator truncates the body long before the
# giveaway.
REPOST_MIN_COMPANIES = 4
REPOST_MIN_AGENCY_SHARE = 0.25

# The company's own ATS carries its own reqs, so a farm cannot form there.
_OWN_BOARDS = frozenset(SOURCE_PRIORITY) - {
    "usajobs", "adzuna", "hiringcafe", "remoteok", "remotive",
    "himalayas", "weworkremotely", "hackernews",
}

# Words that describe the arrangement, the commute or the paperwork rather
# than the job. A shop decorates the same req a dozen different ways --
# "Power BI Developer (Hybrid)", "SCC - Power BI Developer", "Power BI
# Developer | W2/1099 | Applicant Must Be Current VA Resident" -- and an
# exact-title match sees a dozen jobs. Stripping these leaves the title.
_DECOR = {
    "hybrid", "remote", "onsite", "on", "site", "w2", "1099", "c2c",
    "local", "locals", "candidate", "candidates", "resident", "residents",
    "applicant", "applicants", "must", "be", "current", "only", "in",
    "usa", "us", "contract", "fulltime", "full", "time", "position",
    "immediate", "urgent", "hiring", "job", "id", "no", "and", "or", "the",
}

_BRACKETED = re.compile(r"[(\[{][^)\]}]*[)\]}]")
_SEGMENT = re.compile(r"\||\s+-+\s+")
_NON_ALNUM_RUN = re.compile(r"[^a-z0-9]+")


def _words(text: str) -> list[str]:
    return [w for w in _NON_ALNUM_RUN.sub(" ", (text or "").lower()).split() if w]


@lru_cache(maxsize=1)
def _decoration() -> frozenset[str]:
    """`_DECOR`, plus wherever this profile is looking.

    "Power BI Developer in Richmond, VA" is one more way of writing the same
    title, but "Richmond" belongs to the user's profile and not to a
    deduplication module. Reading the places out of the targeting file is
    what keeps this from being a rule that only works in one city.
    """
    from . import profile as targeting
    places: set[str] = set()
    for term in (list(targeting.LOCAL_TERMS) + list(targeting.STATE_TERMS)
                 + [targeting.HOME_METRO]):
        places.update(_words(str(term)))
    return frozenset(_DECOR | places)


def _is_decoration(text: str) -> bool:
    words = _words(text)
    decor = _decoration()
    return bool(words) and all(w in decor or w.isdigit() for w in words)


def _title_key(title: str) -> str:
    """The job, with the sales copy taken off.

    Only decoration comes off, and only when the whole span is decoration.
    "(Hybrid)" goes; "(Federal Grants & eRA Systems)" stays, because GovCIO's
    business analyst really is a different job from the nine that a crowd of
    shops were advertising under the bare title, and merging them would lose
    a real posting to a farm it had nothing to do with.
    """
    text = _BRACKETED.sub(
        lambda m: " " if _is_decoration(m.group()) else m.group(), title or "")
    parts = [p for p in _SEGMENT.split(text) if p.strip()]
    while len(parts) > 1 and _is_decoration(parts[-1]):
        parts.pop()
    if len(parts) > 1 and len(_NON_ALNUM_RUN.sub("", parts[0].lower())) <= 4:
        parts = parts[1:]           # an org acronym: "SCC - Power BI Developer"
    words = _words(" ".join(parts))
    decor = _decoration()
    while words and (words[-1] in decor
                     or (words[-1].isdigit() and len(words[-1]) > 2)):
        words.pop()                 # trailing city, arrangement, requisition no.
    return " ".join(words)


def collapse_reposts(jobs: list[Job],
                     known: set[str] | None = None) -> tuple[list[Job], int]:
    """Collapse one requisition farmed out across many staffing agencies.

    `collapse` above cannot do this: it keys on company plus title, and the
    whole point of a farm is that the company differs on every copy.

    A qualifying group is not emptied down to one row, because these groups
    are mixtures. Nine shops advertising the same contract under the bare
    title "Business Analyst" sat in a group with two Markel reqs that were
    nothing to do with them. So a company already on the watch list is never
    dropped -- it is there because a person put it there or a probe confirmed
    its ATS -- and the rest of the group is represented by its best-scoring
    posting.
    """
    if known is None:
        from . import learn          # local: keeps dedupe free of the import
        known = learn._known_names()

    groups: dict[tuple[str, str], list[Job]] = {}
    for job in jobs:
        if job.source in _OWN_BOARDS:
            continue
        key = _title_key(job.title)
        if len(key.split()) >= 2:   # a one-word key is too blunt to group on
            groups.setdefault((job.source, key), []).append(job)

    dropped: set[int] = set()
    for members in groups.values():
        companies = {j.company.strip().lower() for j in members}
        if len(companies) < REPOST_MIN_COMPANIES:
            continue
        flagged = sum(1 for j in members if "staffing-agency" in j.flags)
        if flagged / len(members) < REPOST_MIN_AGENCY_SHARE:
            continue

        rest = [j for j in members if j.company.strip().lower() not in known]
        if not rest:
            continue
        keeper = max(rest, key=lambda j: (j.score, bool(j.description)))
        for job in rest:
            if job is not keeper:
                dropped.add(id(job))
        shops = len({j.company.strip().lower() for j in rest})
        if "staffing-agency" not in keeper.flags:
            keeper.flags.append("staffing-agency")
        keeper.flags.append(f"reposted by {shops} firms")
        keeper.reasons.append(
            f"one requisition advertised by {shops} staffing firms")

    if not dropped:
        return jobs, 0
    return [j for j in jobs if id(j) not in dropped], len(dropped)


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
            # paid an HTTP call for. Longest wins, rather than any-vs-none:
            # an aggregator's 500-character snippet counts as a description
            # under a truth test, so a bare `not job.description` handed the
            # snippet to a posting whose own board had sent the whole thing.
            #
            # The flag travels with the text. Copying a snippet across and
            # leaving `partial` false is the worse half of the same bug: the
            # About Us paragraph then reads as a complete posting that
            # happens to require no years and name no tools.
            if len(incumbent.description) > len(job.description):
                job.description = incumbent.description
                job.partial = incumbent.partial
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
                # Kept for the seeder, which needs to know which of the
                # thousands of companies in here were hiring in this metro.
                # Rows written before this line exist without it.
                "location": job.location,
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
