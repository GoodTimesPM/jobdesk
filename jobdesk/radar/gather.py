"""Fill in seed_companies.toml for a metro, instead of asking for an afternoon.

`seed.py` needs a list of a metro's employers and, beside each one, the
company's website. That list is what makes the whole thing work: the last
sweep confirmed thirteen boards and every single one of them was confirmed by
the `linked from <site>` path, none by the hiring-in-this-metro rule. So the
website column is not a nicety, it is where the yield is.

For Richmond I typed it. 246 names and 228 websites off four public lists,
one afternoon. That is a fine answer for one person and a terrible answer for
everybody else, and this module is the attempt to stop charging a stranger
the same afternoon.

Four sources, weakest first, because the ordering is the honest part:

  1. The map. Nominatim turns "Austin, TX" into a coordinate, Overpass
     returns everything inside a radius of it that carries both a name and a
     website. For Richmond that is 5,102 pins collapsing to 2,979 domains,
     and it is the only source that needs nothing from the user at all.
  2. Wikidata. Organisations whose headquarters is that city, with their
     official website. 172 for Richmond. Small, clean, and it knows about
     head offices the map has no pin for.
  3. Pages the user points at with `--from`. The four lists every metro has
     are named in seed_companies.toml, and a person finds them in a minute.
     What costs the afternoon is reading 60 rows off each one and retyping
     them, and a table of links is the one thing a computer reads perfectly.
  4. Whatever `seed.from_history` already derives from postings on file.
     Untouched here; that half was always free.

What this does NOT do is believe any of it. Measured against the 246 names I
typed by hand, the map and Wikidata together name 66 of them, which is a
start and is nowhere near the list. Ranking is therefore the product, not
filtering: every candidate is scored and the file is written best first, so
the seeder's budget of 300 names is spent on the plausible end and the user
trims a list instead of building one.

Tag filtering was tried and measured and thrown away. Keeping only pins
tagged as an office, a hospital or a university cut the noise from 2,979
domains to 663 and cut the hand-list hits from 62 to 36, because a corporate
head office is very often just a building. Dropping anything carrying OSM's
`brand` tag looked like a clean way to delete the fast-food chains, and it
deletes CarMax, Wegmans and Truist with them. Both are rank penalties now.
Nothing is thrown away for looking wrong; it is sorted down for looking wrong.

    py -m jobdesk.radar.gather
    py -m jobdesk.radar.gather --from https://example.com/largest-employers
    py -m jobdesk.radar.gather --write

Nothing is written without `--write`, and `--write` refuses to clobber a file
that already has entries unless `--merge` says what to do with them.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urljoin, urlparse

from .. import profile as profile_files
from . import config, discover, http, profile as targeting

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = "https://overpass-api.de/api/interpreter"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

# Who is asking. Nominatim and Overpass are volunteer-funded and their usage
# policy asks for an identifying agent rather than a browser string, which is
# the opposite of what http.py sends by default and worth the override.
POLITE = {"User-Agent": "jobdesk-radar (self-hosted job search; one user)"}

# How far out a metro reaches. 40km is roughly a commute, and a commute is
# what this has to be in terms of: the point is the companies a person could
# work at, not the ones inside a city line.
DEFAULT_RADIUS_KM = 40

# Overpass is a shared public endpoint doing a real query. One call, generous
# timeout, and no retries hammering it if it is busy.
OVERPASS_TIMEOUT = 240


class GatherError(RuntimeError):
    """Something a user has to fix before this can run."""


# --------------------------------------------------------------------------
# What a candidate is
# --------------------------------------------------------------------------

@dataclass
class Candidate:
    """One company, and every reason we think it is one."""

    name: str
    site: str = ""
    sources: set[str] = field(default_factory=set)
    pins: int = 0               # how many map pins share this domain
    branded: bool = False       # OSM says this is a chain's outlet
    employer_tag: bool = False  # tagged office/hospital/university/industrial
    listed: bool = False        # named on a page the user pointed at

    def key(self) -> str:
        """What makes two candidates the same company.

        The domain, when there is one. Two rows for bonsecours.com are one
        employer however differently the pins spell the name, and that
        collapse is most of what makes the map usable at all: 5,102 pins are
        2,979 companies.
        """
        return self.site or _slug(self.name)

    def score(self) -> int:
        """How much this looks like somewhere a person works.

        The magnitudes below are not taste. The map's two weights were fitted
        against the 246 names I typed by hand for Richmond, asking one
        question: of the 64 hand-typed companies the map knows about, how
        many land in the first 300 rows, which is all the seeder will read?

            first 100 rows   16 of 64
            first 300 rows   36 of 64
            first 600 rows   40 of 64

        Chance would put 6.5 of them in the first 300, so the ordering is
        worth about five and a half times reading the file in the order
        Overpass happens to return it. It is not worth more than that, and
        pretending otherwise would be the easiest lie in this module.

        Two things that sound like signal measured worse than nothing. A
        penalty for OSM's `brand` tag cost four of the 36, because Wegmans,
        CarMax and Truist are branded and are also three of the metro's
        larger employers. Leaning hard on the office/hospital/university tag
        cost eleven, because it mostly repeats what the pin count already
        says. Both survive as a small nudge and a note on the row.
        """
        points = 0
        if self.listed:
            points += 100      # a human published it on a list of employers
        if "wikidata" in self.sources:
            points += 40       # headquartered here, per an encyclopaedia
        if len(self.sources) > 1:
            points += 30       # two sources that do not talk to each other
        if self.site:
            points += 10       # the column that pays
        if self.employer_tag:
            points += 4
        points += min(self.pins, 6) * 3
        return points


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


# Words that are in a legal name and are not what makes it that company.
_LEGAL = {"inc", "llc", "corp", "corporation", "co", "company", "ltd", "plc",
          "group", "holdings", "incorporated", "limited", "the", "of", "and"}


def core_words(name: str) -> list[str]:
    """The words that distinguish a company from another company."""
    words = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()
    return [w for w in words if w not in _LEGAL] or words


def _fetch(method: str, url: str, **kwargs):
    """A call that treats being cut off as an answer rather than an accident.

    `http.request` raises RateLimited so that a scheduled run stops pestering
    a host that has said no. Here there are four independent sources and the
    right response to one of them refusing is to carry on with the other
    three, so the exception becomes a None like every other failure. These
    are public endpoints run by volunteers; being told to go away is a normal
    outcome and not an error to report.
    """
    try:
        return http.request(method, url, **kwargs)
    except http.RateLimited:
        return None


# Subdomains that are a department of a site rather than a different body.
# Deliberately short and literal. A general "collapse to the last two labels"
# rule is wrong on exactly the domains a metro list is full of: dhr.virginia.gov
# and vdh.virginia.gov are two separate state agencies with separate payrolls,
# and folding them together would lose one of them.
_DEPARTMENT = ("maps.", "map.", "www2.", "jobs.", "careers.", "career.",
               "store.", "shop.", "locations.", "location.", "recruiting.",
               "apply.", "my.", "portal.", "secure.")


def domain_of(url: str) -> str:
    """The bare hostname of a URL, or "" if there is not one in there.

    A university is the one case where every subdomain really is the same
    employer -- maps.vcu.edu, arts.vcu.edu and vcu.edu are one payroll -- so
    a .edu is cut back to its last two labels and nothing else is.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    if "//" not in raw:
        raw = "http://" + raw
    host = (urlparse(raw).netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    for prefix in _DEPARTMENT:
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    parts = host.split(".")
    if len(parts) > 2 and parts[-1] == "edu":
        host = ".".join(parts[-2:])
    return host if "." in host and " " not in host else ""


def _initials(name: str) -> str:
    return "".join(w[0] for w in core_words(name))


def best_name(counts: dict[str, int], site: str) -> str:
    """Which of the names pinned on one domain is the company's.

    The first attempt took the shortest, on the theory that forty pins for a
    hospital system are named after forty buildings and the short one is the
    company. It is not: the shortest name on vcu.edu is "Bowe House", on
    vmfa.museum it is "Amuse", the museum cafe, and on
    keystonetractorworks.com it is "Keystone Grill". Shortness finds the
    coffee shop inside the employer.

    What actually identifies the company is the domain, which the company
    chose and named itself after. So: a name the domain echoes wins, whether
    spelled out (Bon Secours -> bonsecours.com) or as initials (Virginia
    Commonwealth University -> vcu.edu). Failing that, the name the most pins
    agree on, and shortness is only the tie-break it should always have been.
    """
    stem = re.sub(r"[^a-z0-9]", "", site.rsplit(".", 1)[0])
    named = [n for n in counts if _slug(n) and _slug(n) in stem]
    # And the other way round, because a domain is usually the short form of
    # the name rather than all of it: bonsecours.com is where "Bon Secours
    # Health System" lives, and without this the longer, righter name loses to
    # whatever the cafe in the lobby is called. Six characters is the floor,
    # since a three-letter stem is inside half the words in English.
    named += [n for n in counts
              if len(stem) >= 6 and stem in _slug(n) and n not in named]
    named += [n for n in counts if _initials(n) and _initials(n) == stem]
    if named:
        return max(named, key=lambda n: (len(_slug(n)), counts[n]))
    return max(counts, key=lambda n: (counts[n], -len(n), n))


# --------------------------------------------------------------------------
# Source 1: the map
# --------------------------------------------------------------------------

def geocode(metro: str) -> tuple[float, float]:
    """"Austin, TX" -> a coordinate, so that "within 40km" means something.

    Raises rather than guessing. Everything downstream is a radius around
    this point, so a silently wrong centre would fill a user's file with
    another state's companies and look like it had worked.
    """
    query = urllib.parse.urlencode({"q": metro, "format": "json", "limit": 1})
    resp = _fetch("GET", f"{NOMINATIM}?{query}", headers=POLITE, spacing=1.5)
    if resp is None or resp.status_code >= 400:
        raise GatherError(f"could not reach the geocoder for {metro!r}")
    try:
        hits = resp.json()
    except ValueError:
        raise GatherError(f"the geocoder said nothing readable about {metro!r}")
    if not hits:
        raise GatherError(
            f"no place called {metro!r}. home_metro in targeting.toml has to "
            f"be a real place name, written the way a map would write it.")
    return float(hits[0]["lat"]), float(hits[0]["lon"])


# Tags that mean "salaried staff work here" rather than "this is a shopfront".
# Used to rank, never to exclude; see the module docstring for why.
_EMPLOYER_KEYS = ("office", "healthcare", "industrial", "military")
_EMPLOYER_PAIRS = {
    ("amenity", "hospital"), ("amenity", "university"), ("amenity", "college"),
    ("amenity", "clinic"), ("amenity", "research_institute"),
    ("amenity", "school"), ("amenity", "courthouse"), ("amenity", "townhall"),
    ("man_made", "works"), ("landuse", "industrial"),
    ("building", "office"), ("building", "commercial"),
    ("building", "industrial"), ("building", "hospital"),
    ("building", "university"),
}


def looks_like_employer(tags: dict) -> bool:
    if any(key in tags for key in _EMPLOYER_KEYS):
        return True
    return any(tags.get(key) == value for key, value in _EMPLOYER_PAIRS)


def overpass_query(lat: float, lon: float, radius_km: int) -> str:
    """Everything with a name and a website inside the radius.

    Asking for both `website` and `contact:website` is not fussiness. The two
    tags are used interchangeably by mappers and dropping either one loses a
    few hundred real domains.
    """
    metres = int(radius_km * 1000)
    return (f"[out:json][timeout:{OVERPASS_TIMEOUT}];\n(\n"
            f'  nwr["name"]["website"](around:{metres},{lat},{lon});\n'
            f'  nwr["name"]["contact:website"](around:{metres},{lat},{lon});\n'
            f");\nout tags center;\n")


def elements_to_candidates(elements: list[dict]) -> list[Candidate]:
    """Collapse map pins into one row per domain."""
    merged: dict[str, Candidate] = {}
    names: dict[str, dict[str, int]] = {}
    for element in elements:
        tags = element.get("tags", {}) if isinstance(element, dict) else {}
        name = str(tags.get("name") or "").strip()
        site = domain_of(tags.get("website") or tags.get("contact:website") or "")
        if not name or not site:
            continue
        row = merged.get(site)
        if row is None:
            row = merged[site] = Candidate(name=name, site=site)
            row.sources.add("map")
            names[site] = {}
        row.pins += 1
        row.branded = row.branded or ("brand" in tags or "brand:wikidata" in tags)
        row.employer_tag = row.employer_tag or looks_like_employer(tags)
        names[site][name] = names[site].get(name, 0) + 1
    for site, row in merged.items():
        row.name = best_name(names[site], site)
    return list(merged.values())


def from_map(lat: float, lon: float, radius_km: int = DEFAULT_RADIUS_KM,
             log=print) -> list[Candidate]:
    """Ask the map what is inside the radius. One call."""
    resp = _fetch("POST", OVERPASS,
                  data={"data": overpass_query(lat, lon, radius_km)},
                  headers=POLITE, timeout=OVERPASS_TIMEOUT, retries=1,
                  spacing=2.0)
    if resp is None or resp.status_code >= 400:
        log("gather: the map endpoint did not answer; skipping it")
        return []
    try:
        elements = resp.json().get("elements", [])
    except ValueError:
        log("gather: the map endpoint answered with something unreadable")
        return []
    rows = elements_to_candidates(elements)
    log(f"gather: the map has {len(elements)} pin(s) with a website inside "
        f"{radius_km}km, which is {len(rows)} distinct domain(s)")
    return rows


# --------------------------------------------------------------------------
# Source 2: Wikidata
# --------------------------------------------------------------------------

def city_entity(metro: str) -> str:
    """The Wikidata id for a place name, or "".

    Needed because the headquarters query is by entity rather than by string.
    Any metro resolves through the same endpoint, which is the whole point.
    """
    query = urllib.parse.urlencode({
        "action": "wbsearchentities", "search": metro, "language": "en",
        "format": "json", "limit": 1, "type": "item"})
    resp = _fetch("GET", f"{WIKIDATA_API}?{query}", headers=POLITE)
    if resp is None or resp.status_code >= 400:
        return ""
    try:
        hits = resp.json().get("search", [])
    except ValueError:
        return ""
    return str(hits[0]["id"]) if hits else ""


def hq_query(city: str) -> str:
    """Organisations headquartered in a place, with their official website.

    `wdt:P159/wdt:P131*` reads "headquarters located in, or in anything
    administratively inside" -- so a company in a suburb of the named city
    still counts, which is the behaviour a metro wants.
    """
    return ("SELECT ?orgLabel ?site WHERE {\n"
            f"  ?org wdt:P159/wdt:P131* wd:{city} .\n"
            "  ?org wdt:P856 ?site .\n"
            '  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }\n'
            "}")


def from_wikidata(metro: str, log=print) -> list[Candidate]:
    """Head offices in this city.

    Note what this is not: a lookup by company name. Searching Wikidata for a
    name and taking its website was tried and got 2 of 60 known answers
    right. Asking the other way round, by place, is the query the data
    actually supports.
    """
    city = city_entity(metro.split(",")[0].strip() or metro)
    if not city:
        log("gather: no encyclopaedia entry for that place; skipping it")
        return []
    url = (f"{WIKIDATA_SPARQL}?format=json&query="
           + urllib.parse.quote(hq_query(city)))
    resp = _fetch("GET", url, headers=POLITE, timeout=120, spacing=3.0)
    if resp is None or resp.status_code >= 400:
        log("gather: the encyclopaedia did not answer; skipping it")
        return []
    try:
        rows = resp.json()["results"]["bindings"]
    except (ValueError, KeyError):
        log("gather: the encyclopaedia answered with something unreadable")
        return []
    out: list[Candidate] = []
    for row in rows:
        name = str(row.get("orgLabel", {}).get("value") or "").strip()
        site = domain_of(row.get("site", {}).get("value") or "")
        # An unlabelled entity comes back as its own id. That is not a name.
        if not name or not site or re.fullmatch(r"Q\d+", name):
            continue
        candidate = Candidate(name=name, site=site)
        candidate.sources.add("wikidata")
        out.append(candidate)
    log(f"gather: the encyclopaedia names {len(out)} organisation(s) "
        f"headquartered around {metro}")
    return out


# --------------------------------------------------------------------------
# Source 3: a page the user points at
# --------------------------------------------------------------------------

_ANCHOR = re.compile(r"<a\b[^>]*?href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
                     re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_ENTITY = re.compile(r"&(?:nbsp|amp|#39|quot|rsquo|lsquo|ldquo|rdquo|#\d+);")

# Anchor text that is furniture rather than a company.
_FURNITURE = re.compile(
    r"^(?:home|about|contact|menu|search|log ?in|sign ?in|join|more|read more"
    r"|next|prev(?:ious)?|back|top|share|print|subscribe|privacy|terms"
    r"|cookie|advertis|newsletter|facebook|twitter|linkedin|instagram"
    r"|youtube|click here|view all|see all|learn more|apply now|\d+)\b", re.I)

_BOILERPLATE = (
    "facebook.com", "twitter.com", "x.com", "linkedin.com", "instagram.com",
    "youtube.com", "google.com", "goo.gl", "bit.ly", "apple.com",
    "wordpress.com", "pinterest.com", "tiktok.com", "adobe.com",
    "mailchimp.com", "eventbrite.com", "chambermaster.com", "wikipedia.org",
    "paypal.com", "vimeo.com", "flickr.com", "yelp.com", "threads.net",
)


def _clean(text: str) -> str:
    text = _TAGS.sub(" ", text)
    text = _ENTITY.sub(" ", text)
    return " ".join(text.split()).strip(" .,:;|-")


def is_boilerplate_host(site: str) -> bool:
    return any(site == host or site.endswith("." + host)
               for host in _BOILERPLATE)


def links_to_candidates(html: str, page_url: str) -> list[Candidate]:
    """Read a roster of employers as (name, website) pairs.

    The shape this exploits is the same on all four of the lists
    seed_companies.toml names: a table where each company's name is a link to
    the company. Anchor text is the name, the href is the website, and both
    columns come out of one fetch with no typing at all.

    Links back into the publisher's own site are dropped, which removes the
    navigation without needing to understand the page.
    """
    host = domain_of(page_url)
    merged: dict[str, Candidate] = {}
    for href, inner in _ANCHOR.findall(html):
        name = _clean(inner)
        site = domain_of(urljoin(page_url, href))
        if not name or not site or not 3 <= len(name) <= 70:
            continue
        if _FURNITURE.match(name) or not re.search(r"[A-Za-z]{3}", name):
            continue
        if host and (site == host or site.endswith("." + host)):
            continue
        if is_boilerplate_host(site):
            continue
        row = merged.setdefault(site, Candidate(name=name, site=site))
        row.sources.add("listed")
        row.listed = True
        if len(name) < len(row.name):
            row.name = name
    return list(merged.values())


def from_page(url: str, log=print) -> list[Candidate]:
    """Fetch a published list of local employers and read it."""
    resp = _fetch("GET", url,
                  headers={"Accept": "text/html,application/xhtml+xml"})
    if resp is None or resp.status_code >= 400:
        why = "no answer" if resp is None else resp.status_code
        log(f"gather: {url} did not give up a page ({why})")
        return []
    rows = links_to_candidates(resp.text, resp.url or url)
    log(f"gather: {url} names {len(rows)} company website(s)")
    return rows


# --------------------------------------------------------------------------
# The website column, for names that arrived without one
# --------------------------------------------------------------------------

_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


def guesses(name: str) -> list[str]:
    """Domains worth trying for a name, most distinctive first.

    Wider than `resolve.domains`, and that is only allowed because every
    guess here has to be confirmed by the page before it is written down.
    `resolve` guesses in the middle of a run and acts on the answer; this
    guesses once and has to show its work.

    No stem here ever drops a word and leaves one behind. Dropping the last
    word off a two-word name is the bare first word wearing a hat, and a
    single generic word is somebody else's company by default: memorial.com is
    a domain broker, richmond.com is the newspaper rather than the Federal
    Reserve, owens.com redirects to a malware warning, london.com is not The
    London Company and chesapeake.org is not the Chesapeake Corporation.
    Measured over the 246 Richmond names, the one-word guess was right four
    times in twenty-three.

    A one-word stem still comes out of here, because "The London Company" is
    one distinctive word once `core_words` has dropped the furniture, and a
    company named with one word has to be allowed to own the matching domain.
    That case is not made safe here. It is made safe in `confirms`, which
    makes a single-word name prove it is in this metro before it counts.
    """
    words = core_words(name)
    if not words:
        return []
    stems = [
        "".join(words),
        "-".join(words) if len(words) > 1 else "",
        # Only from three words up, so what is left is still two of them.
        "".join(words[:-1]) if len(words) > 2 else "",
        "".join(w[0] for w in words) if len(words) > 2 else "",
    ]
    out: list[str] = []
    for stem in dict.fromkeys(s for s in stems if len(s) > 2):
        out.extend(stem + tld for tld in (".com", ".org", ".net"))
    return out


def home_terms() -> tuple[str, ...]:
    """The words a local company's own website is likely to print.

    Off the profile, so this asks about Austin for a user in Austin. Used
    only by `confirms`, and only for the one risky case it guards.

    Punctuation is stripped and anything under four letters is dropped, which
    is not tidiness. The profile's local terms include ", VA", and a page's
    HTML is full of minified JavaScript, so a plain substring test found ", va"
    inside `, var x` on every page on the internet and cheerfully confirmed a
    Broken Bow cabin rental as a Richmond employer. State abbreviations are two
    letters and cannot be told apart from noise, so they do not get a vote.
    """
    metro = str(targeting.HOME_METRO)
    raw = {metro.split(",")[0], metro.rpartition(",")[2]}
    raw |= {str(t) for t in targeting.LOCAL_TERMS}
    raw |= {str(t) for t in targeting.STATE_TERMS}
    terms = {re.sub(r"[^a-z0-9 ]+", " ", t.lower()).strip() for t in raw}
    return tuple(t for t in terms if len(t) > 3)


def _says_home(body: str) -> bool:
    """Does this page print the name of the user's own metro anywhere?

    Whole words only. `in` would match a term inside a longer word, and the
    whole point of this test is that it is the last thing standing between a
    one-word name and a stranger's homepage.
    """
    return any(re.search(r"\b" + re.escape(term) + r"\b", body)
               for term in home_terms())


def confirms(name: str, title: str, body: str, host: str) -> bool:
    """Does this page belong to this company?

    Every distinctive word of the name has to be in the page title. Half of
    them was the first rule and it was far too kind: it let Cavalier Telephone
    resolve to brandforce.com and Virginia Premier to virginia.org. Requiring
    all of them took the known-bad names from ten wrong to five.

    The other five were one-word names, where the only stem available is that
    one word and a company really does put its own word in its own title. So
    a single-word name additionally has to prove it is in this metro, and
    that took ten wrong down to two. The two left are Riverstone Properties
    and Reliance Ventures, whose names also belong to an Oklahoma cabin
    rental and a Texas oilfield hauler. Nothing reads its way past that, and
    it is why this returns "" rather than a maybe.

    Making every name prove it is in this metro, not only the one-word ones,
    does catch both of those. It was measured over 80 of the hand-verified
    Richmond sites and it is not worth it: right answers fell from 16 to 9 to
    buy those two. Better than half the companies in a metro do not say where
    they are on their own front page, and a rule that reads silence as a no
    throws away more employers than it saves.
    """
    if not title:
        return False
    if title == "__blocked__":
        # Alive, but refusing scripted clients -- CarMax and Bon Secours both
        # do. Nothing can be read, so the hostname has to carry the whole
        # name by itself, which is the only reason the guess was made.
        return "".join(core_words(name)) in host.replace("-", "").replace(".", "")
    lowered = title.lower()
    words = core_words(name)
    strong = [w for w in words if len(w) > 3] or words
    if not all(w in lowered for w in strong):
        return False
    if len(words) == 1:
        return _says_home(body)
    return True


def _page_of(host: str) -> tuple[str, str, str]:
    """The final hostname, page title and lowercased body for a domain.

    The body is read because `confirms` needs it for one-word names, and one
    fetch that returns everything beats two that each return half.
    """
    for prefix in ("https://www.", "https://"):
        resp = _fetch("GET", prefix + host, retries=1, timeout=12,
                      block_on_429=False,
                      headers={"Accept": "text/html,application/xhtml+xml"})
        if resp is None:
            continue
        if resp.status_code in (403, 406, 429):
            return host, "__blocked__", ""
        if resp.status_code >= 400:
            continue
        page = resp.text[:150_000]
        match = _TITLE_TAG.search(page)
        return (domain_of(resp.url or host),
                _clean(match.group(1) if match else ""), page.lower())
    return "", "", ""


def find_site(name: str, budget, log=None) -> str:
    """A website for a bare name, or "" -- and "" is a perfectly good answer.

    Nothing uncertain is returned. A wrong website is worse than none here:
    the seeder confirms a board by finding it linked from the site it was
    given, so a stranger's domain does not fail, it confirms a stranger's
    board and files it under this company's name. That is the one outcome
    this project refuses, so the bar is a page that names the company.
    """
    for host in guesses(name):
        if not budget.spend():
            return ""
        final, title, body = _page_of(host)
        if final and confirms(name, title, body, final):
            if log:
                log(f"    {name} -> {final}")
            return final
    return ""


# A seed entry that is a bare name, e.g.   "Molson Coors",
_BARE = re.compile(r'^(\s*)"([^"]+)"(\s*,\s*)(#.*)?$')


def fill_sites(text: str, budget, log=print, find=find_site) -> tuple[str, int]:
    """Give a website to every entry in a seed file that has only a name.

    This is the other half of the afternoon. Gathering finds companies the
    user never listed; this one takes the list they already have -- pasted
    off a largest-employers PDF, which prints names and no links -- and goes
    and finds each company's website, which is the column the sweep showed
    all the yield comes from.

    Line-based on purpose. Rewriting the file through a TOML parser would
    lose every comment in it, and in a file a person is meant to read and
    prune, the comments saying where each name came from are half the value.
    A bare-name line is replaced; every other line is passed through byte for
    byte.

    `find` is the resolver, and it is a parameter so that the self-check can
    test the rewriting without asking the internet whether Acme Health System
    exists.
    """
    out: list[str] = []
    filled = 0
    for line in text.splitlines(keepends=True):
        match = _BARE.match(line.rstrip("\n"))
        if match is None:
            out.append(line)
            continue
        indent, name, comma, comment = match.groups()
        site = find(name, budget)
        if not site:
            log(f"    {name}: no site found")
            out.append(line)
            continue
        filled += 1
        log(f"    {name} -> {site}")
        tail = f"  {comment}" if comment else ""
        out.append(f'{indent}{{ name = "{name}", '
                   f'site = "https://{site}" }}{comma.rstrip()}{tail}\n')
    return "".join(out), filled


# --------------------------------------------------------------------------
# Putting it together
# --------------------------------------------------------------------------

def merge(groups: list[list[Candidate]]) -> list[Candidate]:
    """One row per company, best first.

    Merging is by domain, so a company the map pinned and the encyclopaedia
    listed becomes one candidate carrying both sources -- and two independent
    sources agreeing is the strongest signal available here, worth more than
    anything either one of them says alone.
    """
    merged: dict[str, Candidate] = {}
    for group in groups:
        for row in group:
            key = row.key()
            held = merged.get(key)
            if held is None:
                merged[key] = row
                continue
            held.sources |= row.sources
            held.pins += row.pins
            held.branded = held.branded or row.branded
            held.employer_tag = held.employer_tag or row.employer_tag
            if not held.site:
                held.site = row.site
            # A name published on a list of employers beats one read off a
            # map pin, which is named after a building.
            if row.listed and not held.listed:
                held.name = row.name
            held.listed = held.listed or row.listed
    rows = list(merged.values())
    rows.sort(key=lambda r: (-r.score(), r.name.lower()))
    return rows


def existing_entries() -> set[str]:
    """What the profile's seed list already names, so a merge does not repeat it."""
    path = profile_files.directory() / "seed_companies.toml"
    if not path.exists():
        return set()
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    out: set[str] = set()
    for item in data.get("company", []):
        if isinstance(item, dict):
            out.add(_slug(str(item.get("name") or "")))
            out.add(domain_of(str(item.get("site") or "")))
        else:
            out.add(_slug(str(item)))
    out.discard("")
    return out


HEADER = """\
# Employers to probe for an ATS board. `py -m jobdesk.radar.seed` reads this.
#
# Gathered by `py -m jobdesk.radar.gather` on {today}, for {metro}.
#
# The comment on each row says where it came from. `map` is OpenStreetMap
# inside {radius}km of the metro centre, `wikidata` is an organisation
# headquartered here, `listed` is a page you pointed the gatherer at with
# --from. Best-looking candidates are first and the seeder stops after the
# first {limit} names, so the order is the useful part of this file.
#
# NOTHING HERE IS VERIFIED. A name on this list is a guess that a company
# exists and hires, and the seeder still has to confirm a board before it
# counts; it records a miss when it cannot. What the gatherer cannot do is
# tell a regional headquarters from a franchise outlet, so read down the list
# once and delete the rows that are obviously a coffee shop. That is a few
# minutes of deleting instead of an afternoon of typing, which was the point.
#
# Then add what it missed. Every metro publishes the same four lists:
#
#   - the regional economic development partnership's largest-employers list
#   - your newspaper's Top Workplaces roster (Energage runs most of them)
#   - the local business journal's Fortune 1000 / top private companies
#   - the chamber of commerce member directory
#
# Point `--from <url>` at any of those and the gatherer reads the names and
# the websites straight off the page, which beats retyping them.

company = [
"""


def _row(candidate: Candidate) -> str:
    why = "+".join(sorted(candidate.sources)) or "?"
    if candidate.branded:
        # Not a penalty -- penalising it measured worse -- but worth saying.
        # A branded pin is usually a franchise outlet, and the rows a user
        # should be deleting are mostly these.
        why += ", chain?"
    name = candidate.name.replace('"', "'")
    if candidate.site:
        return (f'  {{ name = "{name}", site = "https://{candidate.site}" }},'
                f'  # {why}\n')
    return f'  "{name}",  # {why}, no website found\n'


def render(rows: list[Candidate], metro: str, radius_km: int,
           limit: int, today: date) -> str:
    """The seed file, as text, in the order the seeder will read it."""
    head = HEADER.format(today=today.isoformat(), metro=metro,
                         radius=radius_km, limit=limit)
    return head + "".join(_row(r) for r in rows) + "]\n"


def run(*, pages: list[str] | None = None, radius_km: int = DEFAULT_RADIUS_KM,
        limit: int = 400, use_map: bool = True, use_wikidata: bool = True,
        log=print) -> list[Candidate]:
    """Gather candidates for whichever metro the profile names."""
    metro = str(targeting.HOME_METRO).strip()
    if not metro:
        raise GatherError(
            "targeting.toml does not say where you live (home_metro), and "
            "every source here is a radius around that.")
    log(f"gather: {metro}, within {radius_km}km")
    groups: list[list[Candidate]] = []
    for url in pages or []:
        groups.append(from_page(url, log=log))
    if use_wikidata:
        groups.append(from_wikidata(metro, log=log))
    if use_map:
        lat, lon = geocode(metro)
        log(f"gather: {metro} is at {lat:.4f}, {lon:.4f}")
        groups.append(from_map(lat, lon, radius_km, log=log))
    rows = merge(groups)
    log(f"gather: {len(rows)} distinct candidate(s) before trimming")
    return rows[:limit]


def _append(path, rows: list[Candidate]) -> None:
    """Add to a list the user already has, without rewriting what is there.

    The existing file is somebody's afternoon. It gets the last bracket
    replaced and nothing else touched.
    """
    old = path.read_text(encoding="utf-8").rstrip()
    if not old.endswith("]"):
        raise GatherError(f"{path} does not end in a closing bracket, so it "
                          f"is not a list this can safely add to")
    path.write_text(
        f"{old[:-1]}\n  # Added by gather on {date.today().isoformat()}.\n"
        + "".join(_row(r) for r in rows) + "]\n",
        encoding="utf-8")


def _fill(calls: int) -> int:
    """`--fill-sites`: the website column, for a list that has only names."""
    path = profile_files.directory() / "seed_companies.toml"
    if not path.exists():
        print(f"gather: there is no {path} to fill in yet", file=sys.stderr)
        return 2
    budget = discover.Budget(calls)
    text = path.read_text(encoding="utf-8")
    filled_text, filled = fill_sites(text, budget)
    if not filled:
        print("gather: no name in that file was missing a website")
        return 0
    path.write_text(filled_text, encoding="utf-8")
    print(f"gather: found a website for {filled} name(s) in {path}, "
          f"{budget.left} call(s) left")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="jobdesk.radar.gather",
        description="Build this metro's employer list so nobody has to type it.")
    parser.add_argument("--from", dest="pages", action="append", metavar="URL",
                        help="a published list of local employers to read "
                             "names and websites off; repeatable")
    parser.add_argument("--radius", type=int, default=DEFAULT_RADIUS_KM,
                        help="how far from the metro centre to look, in km")
    parser.add_argument("--limit", type=int, default=400,
                        help="how many candidates to keep")
    parser.add_argument("--no-map", action="store_true",
                        help="skip OpenStreetMap")
    parser.add_argument("--no-wikidata", action="store_true",
                        help="skip the headquarters lookup")
    parser.add_argument("--write", action="store_true",
                        help="write profile/seed_companies.toml (refuses to "
                             "overwrite an existing list without --merge)")
    parser.add_argument("--merge", action="store_true",
                        help="with --write, keep what the file already says "
                             "and append only companies it does not name")
    parser.add_argument("--fill-sites", action="store_true",
                        help="gather nothing; instead find a website for "
                             "every name already in seed_companies.toml that "
                             "has none, and write them in")
    parser.add_argument("--calls", type=int, default=600,
                        help="hard cap on HTTP calls for --fill-sites")
    args = parser.parse_args(argv)

    config.load_env()
    if args.fill_sites:
        return _fill(args.calls)
    try:
        rows = run(pages=args.pages, radius_km=args.radius, limit=args.limit,
                   use_map=not args.no_map, use_wikidata=not args.no_wikidata)
    except GatherError as exc:
        print(f"gather: {exc}", file=sys.stderr)
        return 2

    path = profile_files.directory() / "seed_companies.toml"
    if not args.write:
        for row in rows[:40]:
            print(f"  {row.score():4}  {row.name[:38]:40} {row.site:34} "
                  f"{'+'.join(sorted(row.sources))}")
        print(f"\n{len(rows)} candidate(s), 40 shown. Nothing written. "
              f"Add --write to put them in {path}.")
        return 0

    already = existing_entries()
    if already and not args.merge:
        print(f"gather: {path} already names {len(already)} thing(s). Pass "
              f"--merge to add to it, or move it aside first.", file=sys.stderr)
        return 2
    try:
        if args.merge:
            before = len(rows)
            rows = [r for r in rows
                    if _slug(r.name) not in already and r.site not in already]
            print(f"gather: {before - len(rows)} candidate(s) were already there")
            _append(path, rows)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                render(rows, str(targeting.HOME_METRO), args.radius,
                       config.SEED_MAX_PER_SWEEP, date.today()),
                encoding="utf-8")
    except (GatherError, OSError) as exc:
        print(f"gather: {exc}", file=sys.stderr)
        return 2
    print(f"gather: wrote {len(rows)} company(ies) to {path}")
    print("gather: read down it once and delete the coffee shops, then run "
          "`py -m jobdesk.radar.seed`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
