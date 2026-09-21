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

from . import http, resolve

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
    # What the board says it is, when it says anything. Greenhouse,
    # SmartRecruiters, Workable and Recruitee all name their own company;
    # Lever and Ashby name nobody, which is why the Ashby `solstice`
    # collision was possible in the first place. Empty means "did not say".
    company: str = ""
    # Where its current postings are. The only corroboration available for a
    # board whose ATS will not say whose it is.
    locations: list[str] = field(default_factory=list)

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

def _field(items, *path) -> list[str]:
    """Pull one nested field off every item, skipping the ones that lack it.

    Every ATS spells a location differently and some nest it: Greenhouse
    says `location.name`, SmartRecruiters `location.city`, Ashby a bare
    `location`. One walker rather than six.
    """
    out = []
    for item in items or []:
        value = item
        for step in path:
            value = value.get(step) if isinstance(value, dict) else None
            if value is None:
                break
        if value:
            out.append(str(value))
    return out


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
                     len(j["jobs"]),
                     locations=_field(j["jobs"], "location", "name"))
    return None


def try_lever(slug: str) -> Board | None:
    j = http.get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if isinstance(j, list) and j:
        return Board("lever", slug, _titles(j, "text"), len(j),
                     locations=_field(j, "categories", "location"))
    return None


def try_ashby(slug: str) -> Board | None:
    j = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if isinstance(j, dict) and j.get("jobs"):
        return Board("ashby", slug, _titles(j["jobs"], "title"), len(j["jobs"]),
                     locations=_field(j["jobs"], "location"))
    return None


def try_smartrecruiters(slug: str) -> Board | None:
    j = http.get_json(
        f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=50")
    if isinstance(j, dict) and j.get("totalFound"):
        content = j.get("content") or []
        return Board("smartrecruiters", slug, _titles(content, "name"),
                     int(j["totalFound"]),
                     company=(_field(content, "company", "name") or [""])[0],
                     locations=_field(content, "location", "city"))
    return None


def try_recruitee(slug: str) -> Board | None:
    j = http.get_json(f"https://{slug}.recruitee.com/api/offers/")
    if isinstance(j, dict) and j.get("offers"):
        return Board("recruitee", slug, _titles(j["offers"], "title"),
                     len(j["offers"]),
                     company=(_field(j["offers"], "company_name") or [""])[0],
                     locations=_field(j["offers"], "location"))
    return None


def try_workable(slug: str) -> Board | None:
    j = http.get_json(
        f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    if isinstance(j, dict) and j.get("jobs"):
        return Board("workable", slug, _titles(j["jobs"], "title"),
                     len(j["jobs"]), company=str(j.get("name") or ""),
                     locations=_field(j["jobs"], "location"))
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
                     {"host": host, "tenant": tenant, "site": site},
                     locations=_field(postings, "locationsText"))
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


# Words that sit on the end of a company name without distinguishing the
# company. "Genworth" and "Genworth Financial" are one employer; so are
# "Capital One" and "Capital One Bank". Compare names with these off the
# end and the comparison can be exact, which is what it needs to be.
_GENERIC = ("financial", "health", "healthcare", "group", "systems", "system",
            "technologies", "technology", "bank", "holdings", "industries",
            "partners", "services", "solutions", "company", "companies",
            "enterprises", "international", "global", "usa", "us", "america",
            "american", "national")


def _name_words(name: str) -> list[str]:
    """A company name reduced to the words that identify it."""
    words = [w for w in _PUNCT.sub(" ", (name or "").lower()).split() if w]
    while len(words) > 1 and (words[-1] in _SUFFIXES or words[-1] in _GENERIC):
        words.pop()
    return words


def same_company(a: str, b: str) -> bool:
    """Whether two company names are the same company.

    Exact, once the words that distinguish nobody are off the end. A prefix
    rule was written first and thrown out on the case this project already
    got burned by: it called "Solstice" and "Solstice Advanced Materials"
    the same company, and telling those apart is the entire reason the
    confirmation step exists. "Advanced Materials" is what the company is;
    "Financial" on the end of "Genworth" is not.
    """
    x, y = _name_words(a), _name_words(b)
    return bool(x) and x == y


def board_company(board: Board, budget: Budget) -> str:
    """What the board says it is, buying the answer if it is one call away.

    SmartRecruiters, Workable and Recruitee say so in the response the probe
    already paid for. Greenhouse says so at a different endpoint, so that
    one costs a call -- spent only when it is about to decide something, and
    only on a board that already answered. Lever and Ashby never say, and
    that silence is why the Ashby `solstice` board could ever have been
    mistaken for Solstice Advanced Materials of Chesterfield.
    """
    if board.company:
        return board.company
    if board.ats == "greenhouse" and budget.spend():
        j = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{board.slug}")
        if isinstance(j, dict):
            board.company = str(j.get("name") or "")
    return board.company


_STATES = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
    "california": "ca", "colorado": "co", "connecticut": "ct",
    "delaware": "de", "district of columbia": "dc", "florida": "fl",
    "georgia": "ga", "hawaii": "hi", "idaho": "id", "illinois": "il",
    "indiana": "in", "iowa": "ia", "kansas": "ks", "kentucky": "ky",
    "louisiana": "la", "maine": "me", "maryland": "md",
    "massachusetts": "ma", "michigan": "mi", "minnesota": "mn",
    "mississippi": "ms", "missouri": "mo", "montana": "mt",
    "nebraska": "ne", "nevada": "nv", "new hampshire": "nh",
    "new jersey": "nj", "new mexico": "nm", "new york": "ny",
    "north carolina": "nc", "north dakota": "nd", "ohio": "oh",
    "oklahoma": "ok", "oregon": "or", "pennsylvania": "pa",
    "puerto rico": "pr", "rhode island": "ri", "south carolina": "sc",
    "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut",
    "vermont": "vt", "virginia": "va", "washington": "wa",
    "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy",
}


def state_full_name(code: str) -> str:
    """"VA" -> "virginia". The table above, read the other way.

    Workday writes the state into a job slug by its full name, so a parser
    that wants to strip it needs the name and only has the code. The table is
    already here and is already all fifty, so this is a lookup rather than a
    second list to keep in step.
    """
    code = (code or "").strip().lower()
    for name, short in _STATES.items():
        if short == code:
            return name
    return ""


def state_named(text: str) -> str:
    """The state a location string names, as a two-letter code, or "".

    Written after the sweep put Groundworks on the watch list for hiring in
    Saint Petersburg, Florida, because the Richmond profile knows about
    Petersburg, Virginia. That is not the old substring bug coming back --
    "petersburg" is a whole word inside "saint petersburg" and the phrase
    rule matched it correctly. City names repeat across states, and the only
    thing that separates them is the state the string already carries.

    A full name matches anywhere. A two-letter code has to follow a comma,
    which is how every location string writes one and keeps "or", "in" and
    "me" from turning ordinary words into states. The longest name wins, so
    "West Virginia" is not read as Virginia.
    """
    low = " ".join(_PUNCT.sub(" ", (text or "").lower()).split())
    for name in sorted(_STATES, key=len, reverse=True):
        if f" {name} " in f" {low} ":
            return _STATES[name]
    codes = set(_STATES.values())
    for chunk in (text or "").lower().split(",")[1:]:
        word = chunk.strip().split(" ")[0].strip(" .")
        if word in codes:
            return word
    return ""


def in_places(text: str, places: set[str], state: str = "") -> str | None:
    """Which of `places` the text names, if any.

    Whole words only. A substring test looks like it works and does not: the
    profile knows about New Kent, and "new" is a substring of "New York", so
    a straight `in` reads a New York posting as local. The terms arrive as
    phrases already ("glen allen", "short pump") and are matched as phrases.

    `state` is the profile's own state as a two-letter code. Given one, a
    location that names a different state is refused however well its city
    matches. A location that names no state at all is still allowed through:
    plenty of boards write "Richmond" and stop, and refusing those would
    cost more than the occasional wrong Petersburg. See `state_named`.
    """
    if state:
        said = state_named(text)
        if said and said != state.strip().lower():
            return None
    low = " " + _PUNCT.sub(" ", (text or "").lower()) + " "
    low = " ".join(low.split())
    for place in places:
        needle = " ".join(_PUNCT.sub(" ", place.lower()).split())
        if needle and f" {needle} " in f" {low} ":
            return place
    return None


def confirms_local(board: Board, name: str, places: set[str],
                   budget: Budget, state: str = "") -> str | None:
    """Believe a board for a company we have never seen post anything.

    `confirms` cannot help here. It proves a board by finding a title an
    aggregator already attributed to this company, and a seed company is by
    definition one no aggregator has shown us. Something else has to carry
    the proof, and there are only two honest candidates.

    The proof is that the board is hiring where the company is. It is worth
    something because of what the slug already did: we guessed this slug
    FROM the company name, so the board is either that company or an
    unrelated one that happens to share the name. An unrelated Ashby startup
    in New York does not have four openings in the user's metro. A national
    brand with an office in town passes this and is the right company anyway.

    The board naming itself used to be accepted as proof on its own, and a
    sweep showed why it cannot be. The slug was guessed from the name, so a
    board found at that slug names itself that name by construction. It
    confirms the spelling, not the company. Five entries came in that way and
    all five were somebody else: `greenhouse/universal` is a London design
    agency, not the Richmond tobacco company; `recruitee/ey` is an Amsterdam
    shop with a sample posting still on it, not Ernst & Young.

    So a matching name now buys nothing, and a mismatching one still costs
    everything: a board that names a different company is refused before the
    locations are read. Where the name genuinely proves a board is the other
    path, `from_website`, because there the company itself published the
    link.

    Anything else is a miss. A wrong board pollutes every future run, so the
    bar for a company we know nothing about stays higher than the evidence.
    """
    said = board_company(board, budget)
    if said and not same_company(said, name):
        return None             # it said, and it said somebody else
    for where in board.locations:
        if in_places(where, places, state):
            return f"hiring in {where}"
    return None


PROBES = {
    "greenhouse": try_greenhouse,
    "lever": try_lever,
    "ashby": try_ashby,
    "smartrecruiters": try_smartrecruiters,
    "recruitee": try_recruitee,
    "workable": try_workable,
}


def board_at(found, budget: Budget) -> Board | None:
    """Fetch the board `resolve` pointed at, without guessing anything.

    The coordinates came off the company's own page, so there is one address
    to try rather than a slug list. Workday is the case that pays: when the
    page linked to the board outright, the datacenter and site id came with
    it and the twenty-nine-guess loop never runs.
    """
    if found.ats == "workday":
        coords = found.coords
        if not coords:
            return try_workday(found.tenant, budget)
        if not budget.spend():
            return None
        url = (f"https://{coords['host']}/wday/cxs/{coords['tenant']}"
               f"/{coords['site']}/jobs")
        resp = http.post(url, json=WD_PAYLOAD, spacing=0.8)
        if resp is None or resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        postings = data.get("jobPostings") or []
        return Board("workday", coords["tenant"], _titles(postings, "title"),
                     int(data.get("total") or len(postings)), dict(coords),
                     locations=_field(postings, "locationsText"))

    probe = PROBES.get(found.ats)
    if probe is None or not found.tenant or not budget.spend():
        return None
    try:
        return probe(found.tenant)
    except http.RateLimited:
        return None


def from_website(name: str, site: str | None, seen_titles: list[str],
                 budget: Budget, places: set[str] | None,
                 max_calls: int, state: str = "") -> tuple[Board | None, str]:
    """Find the board by reading the company's website. See `resolve.py`.

    Two things here are worth more than the hit rate. A resolved ATS this
    project cannot read is a definite answer -- the company's jobs are on
    Phenom, and no amount of probing will change that -- so it ends the
    search instead of falling through to the expensive guessing that was
    never going to work either.

    And where the link came from decides how much it proves. If the user gave
    us the website, a careers link on it is the company telling us where its
    jobs are, and nothing more is needed. If the domain was guessed from the
    name, the whole chain rests on that guess, so the ordinary confirmation
    still has to pass -- an unrelated Acme's careers page is exactly as
    convincing as an unrelated Acme's board, which is to say not at all.
    """
    room = min(max_calls, budget.left)
    if room <= 0:
        return None, "budget exhausted"
    found = resolve.find(name, site, max_calls=room)
    for _ in range(found.calls):
        budget.spend()
    if found.outcome != "hit":
        return None, ""
    if not found.readable:
        return None, f"uses {found.ats}, which this radar cannot read yet"

    board = board_at(found, budget)
    if board is None:
        return None, f"{found.ats} named on the site but its board is empty"
    where = f"{found.ats}/{board.slug}"
    if site:
        return board, f"{where} (linked from {site})"
    if confirms(board, seen_titles):
        return board, where
    local = places and confirms_local(board, name, places, budget, state)
    if local:
        return board, f"{where} ({local})"
    return None, f"found via website but unconfirmed: {where}"


def find(name: str, seen_titles: list[str], budget: Budget,
         places: set[str] | None = None, site: str | None = None,
         resolve_calls: int = 0, state: str = "") -> tuple[Board | None, str]:
    """Probe for `name`'s board and return it only if something confirms it.

    Returns (board, note). A None board with a note is a miss worth
    remembering, so the same company is not re-probed tomorrow.

    `places` opens the second route, for a company with no posting history
    to confirm against -- see `confirms_local`. Leave it out and the title
    rule is the only way in, which is what every caller wanted until the
    seeder started probing companies before they had posted anything.
    `state` is the profile's own two-letter state code, which keeps a city
    of the same name in another state from passing -- see `state_named`.

    `resolve_calls` above zero reads the company's website first and only
    guesses if that fails, which is both likelier to work and cheaper -- 21
    of 35 against 3 of 88, at a twelfth of the calls. `site` is the company's
    website when it is known, and roughly doubles the hit rate again.
    """
    if resolve_calls > 0:
        board, note = from_website(name, site, seen_titles, budget, places,
                                   resolve_calls, state)
        if board is not None:
            return board, note
        if note:
            # A definite answer, including "its ATS is one we cannot read".
            # Guessing after this only spends the budget to learn nothing.
            return None, note

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
            local = places and confirms_local(board, name, places, budget, state)
            if local:
                return board, f"{board.ats}/{slug} ({local})"
            unconfirmed.append(f"{board.ats}/{slug}")

    try:
        board = try_workday(candidates[0], budget)
    except http.RateLimited:
        board = None
    if board is not None:
        site = board.extra.get("site")
        if confirms(board, seen_titles):
            return board, f"workday/{site}"
        local = places and confirms_local(board, name, places, budget, state)
        if local:
            return board, f"workday/{site} ({local})"
        unconfirmed.append("workday/" + str(site))

    if unconfirmed:
        # Found something, believed none of it. Worth saying out loud: this
        # is either a slug collision, or the right board with the posting
        # already filled, and the two are not distinguishable from here.
        return None, "found but unconfirmed: " + ", ".join(unconfirmed[:3])
    return None, "no public board found"
