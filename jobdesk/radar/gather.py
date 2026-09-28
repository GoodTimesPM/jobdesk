"""Fill in seed_companies.toml for a metro, instead of typing it by hand.

`seed.py` needs a metro's employers and each one's website; the website is
where the confirmed boards come from. Four sources, weakest first:

  1. OpenStreetMap. Nominatim finds the metro's centre and Overpass returns
     everything in a radius with a name and a website (Richmond: 5,102 pins,
     2,979 domains). Needs nothing from the user.
  2. Wikidata. Organisations headquartered in the city, with their website.
  3. Pages the user names with `--from`: a table of employer links reads
     straight off the page.
  4. `seed.from_history`, from postings already on file.

None of it is trusted. Every candidate is ranked and the file is written
best first, so the seeder's 300-name budget goes to the plausible end and
the user trims a list instead of building one. Tag filters were measured
and cut real employers (CarMax, Wegmans and Truist carry OSM's `brand` tag),
so they only nudge the rank.

    py -m jobdesk.radar.gather
    py -m jobdesk.radar.gather --from https://example.com/largest-employers
    py -m jobdesk.radar.gather --write

Nothing is written without `--write`, and `--write` refuses to clobber a
file that already has entries unless `--merge` says what to do with them.
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
        """What makes two candidates the same company: the domain, when there is
        one. Collapsing on it is what turns 5,102 pins into 2,979 companies."""
        return self.site or _slug(self.name)

    def score(self) -> int:
        """How much this looks like somewhere a person works.

        The weights were fitted against 246 hand-typed Richmond employers: 36 of
        the 64 the map knows land in the first 300 rows, against 6.5 by chance.
        The `brand` and office/hospital/university tags measured worse as strong
        signals, so each is only a small nudge and a note on the row.
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
    """A call that returns None when the host rate-limits us.

    With four independent sources, one refusing should not stop the others.
    These are volunteer-run public endpoints, so a refusal is not an error.
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
    """The bare hostname of a URL, or "" if there is none.

    A .edu is cut back to its last two labels, because maps.vcu.edu and vcu.edu
    are one payroll. No other domain is.
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

    Shortest was wrong: on vcu.edu it is "Bowe House", on vmfa.museum the cafe.
    A name the domain echoes wins, spelled out (Bon Secours, bonsecours.com) or
    as initials (Virginia Commonwealth University, vcu.edu). Then the name most
    pins agree on, with shortness only as the tie-break.
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
    """"Austin, TX" as a coordinate. Raises instead of guessing, because a
    wrong centre fills the file with another state's companies."""
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
    """Everything with a name and a website inside the radius. Mappers use
    `website` and `contact:website` interchangeably, so both are asked for."""
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
    `wdt:P159/wdt:P131*` also takes a head office in a suburb of the city."""
    return ("SELECT ?orgLabel ?site WHERE {\n"
            f"  ?org wdt:P159/wdt:P131* wd:{city} .\n"
            "  ?org wdt:P856 ?site .\n"
            '  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }\n'
            "}")


def from_wikidata(metro: str, log=print) -> list[Candidate]:
    """Head offices in this city. Asked by place because looking companies up
    by name got 2 of 60 known websites right."""
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

    Employer lists are tables where each name links to the company, so anchor
    text is the name and the href the website. Links back into the publisher's
    own site are dropped, which removes the navigation.
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

    Wider than `resolve.domains`, which is allowed only because `confirms`
    checks every guess against the page. No stem drops words down to one
    generic word: memorial.com is a domain broker and richmond.com a newspaper,
    and that guess was right 4 times in 23. A name that is one distinctive word
    still yields that word, and `confirms` makes it prove it is local.
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
    """The words a local company's own website is likely to print, from the
    profile. Terms under four letters are dropped: ", VA" matched ", var" in
    minified JavaScript on every page on the internet."""
    metro = str(targeting.HOME_METRO)
    raw = {metro.split(",")[0], metro.rpartition(",")[2]}
    raw |= {str(t) for t in targeting.LOCAL_TERMS}
    raw |= {str(t) for t in targeting.STATE_TERMS}
    terms = {re.sub(r"[^a-z0-9 ]+", " ", t.lower()).strip() for t in raw}
    return tuple(t for t in terms if len(t) > 3)


def _says_home(body: str) -> bool:
    """Does this page print the name of the user's own metro anywhere? Whole
    words only, since this is what stands between a one-word name and a
    stranger's homepage."""
    return any(re.search(r"\b" + re.escape(term) + r"\b", body)
               for term in home_terms())


def confirms(name: str, title: str, body: str, host: str) -> bool:
    """Does this page belong to this company?

    Every distinctive word of the name has to be in the page title. Half of
    them let Cavalier Telephone resolve to brandforce.com. A one-word name must
    also show it is in this metro. Requiring that of every name was measured
    and cost more right answers (16 down to 9) than wrong ones it caught, since
    most companies do not print their city on the front page.
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
    """A website for a bare name, or "".

    Nothing uncertain comes back. The seeder confirms a board by finding it
    linked from this site, so a stranger's domain would file a stranger's board
    under this company.
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

    Line-based, so the comments saying where each name came from survive: a
    bare-name line is replaced and every other line passes through unchanged.
    `find` is a parameter so the self-check can run without the internet.
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
    """One row per company, best first. Merged by domain, so two sources
    agreeing on a company become one candidate that carries both."""
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
