"""Find which ATS a company uses, and its exact feed coordinates.

`scripts/radar/discover_ats.py` has done this by hand since the beginning:
you type a company name, it probes seven applicant tracking systems, and you
read the sample posting it prints to check the board is really that company's.
That last step is the whole reason it stayed a script. A slug is just a
string, and these APIs will happily hand back a DIFFERENT company's board for
it -- Ashby's `solstice` board is an unrelated New York AI startup, not
Solstice Advanced Materials of Chesterfield. A human glancing at one posting
catches that instantly.

Automating the probe means replacing that glance with a rule, and the rule
here is deliberately strict: a discovered board is accepted only if it is
currently advertising a job title we have already seen this company post
somewhere else. A collision board would have to be running the same req.
Anything short of that is recorded as a miss, because a wrong board is worse
than no board -- it quietly pollutes every future run with somebody else's
postings under your company's name.

Everything below is network probing against public, documented endpoints,
the same ones `sources/ats.py` reads from once a company is on the list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import http

# Datacenter shards Workday tenants live on, most common first.
WD_DATACENTERS = ["wd1", "wd3", "wd5", "wd12", "wd101", "wd2", "wd103"]

# Workday site ids are per-company and documented nowhere, so they are
# guessed. Ordered by how often they hit in practice.
SITE_GUESSES = [
    "{T}", "{T}_Careers", "{T}careers", "Careers", "careers",
    "External", "external", "{T}_External", "External_Careers",
    "{T}_Jobs", "Search", "{T}Careers", "Global", "{T}_Global",
    "External_Career_Site", "ExternalCareerSite", "Careers_External",
    "{T}_External_Career_Site", "{T}_Career_Site", "CareerSite",
    "{T}Jobs", "Jobs", "jobs", "Corporate", "Professional",
    "Search_Jobs", "SearchJobs", "{T}_careers", "{T}_external",
]

WD_PAYLOAD = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}

# Words that are part of a legal name and never part of a board slug.
_SUFFIXES = ("inc", "inc.", "llc", "l.l.c.", "corp", "corp.", "corporation",
             "co", "co.", "company", "ltd", "ltd.", "plc", "group",
             "holdings", "incorporated", "limited")

_PUNCT = re.compile(r"[^a-z0-9]+")


@dataclass
class Board:
    """One discovered board, before anything decides whether to believe it."""

    ats: str
    slug: str
    titles: list[str]
    count: int
    extra: dict = field(default_factory=dict)   # workday host/tenant/site

    def entry(self, name: str) -> dict:
        """The employers.toml row for this board."""
        row = {"ats": self.ats, "name": name}
        row.update(self.extra or {"slug": self.slug})
        return row


class Budget:
    """A hard cap on HTTP calls, shared across one discovery run.

    Discovery is the one part of the radar whose cost is unbounded by the
    data: 30 site guesses times 7 datacenters times however many companies
    turned up today. A budget makes the ceiling a number in config.py
    rather than a property of the morning's job board.
    """

    def __init__(self, calls: int) -> None:
        self.left = calls

    def spend(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


# --------------------------------------------------------------------------
# Slug guessing
# --------------------------------------------------------------------------

def slugs(name: str) -> list[str]:
    """Board slugs worth trying for a company name, best guess first.

    Two forms cover most of them: the name with everything stripped out
    ("Capital One" -> "capitalone") and the hyphenated form some boards use
    ("capital-one"). Legal suffixes come off first, because no board slug
    has ever contained "LLC".
    """
    words = [w for w in _PUNCT.sub(" ", name.lower()).split() if w]
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words.pop()
    if not words:
        return []
    joined = "".join(words)
    out = [joined]
    if len(words) > 1:
        out.append("-".join(words))
    return out


# --------------------------------------------------------------------------
# The probes. Each returns a Board or None. None means "that ATS answered and
# has no such company" -- a count of zero is not evidence, because an empty
# board and a missing one look identical from here.
# --------------------------------------------------------------------------

def _titles(items, key) -> list[str]:
    out = []
    for item in items or []:
        value = item.get(key) if isinstance(item, dict) else None
        if value:
            out.append(str(value))
    return out


def try_greenhouse(slug: str) -> Board | None:
    j = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    if isinstance(j, dict) and j.get("jobs"):
        return Board("greenhouse", slug, _titles(j["jobs"], "title"),
                     len(j["jobs"]))
    return None


def try_lever(slug: str) -> Board | None:
    j = http.get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if isinstance(j, list) and j:
        return Board("lever", slug, _titles(j, "text"), len(j))
    return None


def try_ashby(slug: str) -> Board | None:
    j = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if isinstance(j, dict) and j.get("jobs"):
        return Board("ashby", slug, _titles(j["jobs"], "title"), len(j["jobs"]))
    return None


def try_smartrecruiters(slug: str) -> Board | None:
    j = http.get_json(
        f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=50")
    if isinstance(j, dict) and j.get("totalFound"):
        content = j.get("content") or []
        return Board("smartrecruiters", slug, _titles(content, "name"),
                     int(j["totalFound"]))
    return None


def try_recruitee(slug: str) -> Board | None:
    j = http.get_json(f"https://{slug}.recruitee.com/api/offers/")
    if isinstance(j, dict) and j.get("offers"):
        return Board("recruitee", slug, _titles(j["offers"], "title"),
                     len(j["offers"]))
    return None


def try_workable(slug: str) -> Board | None:
    j = http.get_json(
        f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    if isinstance(j, dict) and j.get("jobs"):
        return Board("workable", slug, _titles(j["jobs"], "title"),
                     len(j["jobs"]))
    return None


SIMPLE = (try_greenhouse, try_lever, try_ashby, try_smartrecruiters,
          try_recruitee, try_workable)


def try_workday(tenant: str, budget: Budget) -> Board | None:
    """Find a Workday tenant's datacenter and site id, or give up.

    Leans on a quirk worth knowing: hitting a *wrong* site id on the *right*
    host answers 404 with `not found: Job_Posting_Site_ID`, which confirms
    the tenant before the site id is known. A 422 confirms nothing --
    *.wdN.myworkdayjobs.com is a wildcard, so every unknown tenant answers
    422 from every datacenter.

    Much the most expensive probe here, which is why it runs last and why it
    stops the moment the budget does.
    """
    host = None
    for dc in WD_DATACENTERS:
        if not budget.spend():
            return None
        url = (f"https://{tenant}.{dc}.myworkdayjobs.com"
               f"/wday/cxs/{tenant}/__probe__/jobs")
        resp = http.post(url, json=WD_PAYLOAD, spacing=0.8)
        if resp is not None and "Job_Posting_Site_ID" in resp.text[:400]:
            host = f"{tenant}.{dc}.myworkdayjobs.com"
            break
    if not host:
        return None

    for tmpl in SITE_GUESSES:
        if not budget.spend():
            return None
        site = tmpl.replace("{T}", tenant)
        resp = http.post(f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                         json=WD_PAYLOAD, spacing=0.8)
        if resp is None or resp.status_code != 200:
            continue
        try:
            data = resp.json()
        except ValueError:
            continue
        postings = data.get("jobPostings") or []
        return Board("workday", tenant, _titles(postings, "title"),
                     int(data.get("total") or len(postings)),
                     {"host": host, "tenant": tenant, "site": site})
    return None


# --------------------------------------------------------------------------
# Believing it
# --------------------------------------------------------------------------

def _key(title: str) -> str:
    """A title reduced to the part two boards would spell the same way."""
    return _PUNCT.sub(" ", title.lower()).strip()


def confirms(board: Board, seen_titles: list[str]) -> str | None:
    """The title that proves this board belongs to the company, if one does.

    The whole safety rule, in one comparison. `seen_titles` are titles an
    aggregator attributed to this company; if the discovered board is
    currently advertising one of them, it is the same employer. A board that
    belongs to somebody else with a similar name is not running your req.

    Exact match after normalizing punctuation and case. Substring matching
    was tried and is too loose: half the boards on the internet have a req
    called "Analyst".
    """
    if not seen_titles:
        return None
    theirs = {_key(t) for t in board.titles if t}
    for title in seen_titles:
        if _key(title) in theirs:
            return title
    return None


def find(name: str, seen_titles: list[str], budget: Budget) -> tuple[Board | None, str]:
    """Probe for `name`'s board and return it only if a title confirms it.

    Returns (board, note). A None board with a note is a miss worth
    remembering, so the same company is not re-probed tomorrow.
    """
    candidates = slugs(name)
    if not candidates:
        return None, "no usable slug"

    unconfirmed: list[str] = []
    for slug in candidates:
        for probe in SIMPLE:
            if not budget.spend():
                return None, "budget exhausted"
            try:
                board = probe(slug)
            except http.RateLimited:
                continue
            if board is None:
                continue
            if confirms(board, seen_titles):
                return board, f"{board.ats}/{slug}"
            unconfirmed.append(f"{board.ats}/{slug}")

    try:
        board = try_workday(candidates[0], budget)
    except http.RateLimited:
        board = None
    if board is not None:
        if confirms(board, seen_titles):
            return board, f"workday/{board.extra.get('site')}"
        unconfirmed.append("workday/" + str(board.extra.get("site")))

    if unconfirmed:
        # Found something, believed none of it. Worth saying out loud: this
        # is either a slug collision, or the right board with the posting
        # already filled, and the two are not distinguishable from here.
        return None, "found but unconfirmed: " + ", ".join(unconfirmed[:3])
    return None, "no public board found"
