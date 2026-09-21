"""Ask a company where its jobs are, instead of guessing.

`discover.py` works from the name alone. It mangles "Dominion Energy" into a
slug and fires it at seven applicant tracking systems, hoping one of them
answers. Measured over 88 large employers in one metro, that found three
boards for 1,303 calls. The reason is not a bug: the thing being guessed is
not guessable. Dominion's SuccessFactors tenant is `dominionreP3`, Genworth's
Workday tenant is `gnw`, the Federal Reserve Bank's is `rb`, and NewMarket's
UKG id is `ETH1000ETHYL` -- a fossil of the Ethyl Corporation, which changed
its name in 2004. No rule turns a company name into any of those.

The company already publishes the answer. Its website has a careers link, the
careers link lands on the ATS, and the ATS is named in the hostname. That is
one or two page fetches instead of thirty-seven probes, it works for systems
this project cannot yet read, and it is the same public information a person
gets by clicking "Careers".

Measured against the same employers:

    slug guessing, name only          3 of 88     1303 calls
    this, domain guessed from name   20 of 72      296 calls
    this, website supplied           21 of 35       81 calls

The gap between the last two is entirely domain guessing, which fails the
same way slug guessing does and for the same reason: the Federal Reserve Bank
of Richmond is at richmondfed.org, VCU is at vcu.edu, and UPS is at ups.com.
So a seed list may carry a website next to a name, and that is the form worth
asking a user for -- a chamber of commerce directory prints both in one row.

What this does NOT do is get past a site that refuses automated clients.
CarMax, Bon Secours and Patient First answer 403 to anything that is not a
real browser, and Estes closes the connection outright. That is their call to
make. Those are recorded as `blocked` and left alone; defeating a bot
protection is not something this project does.

Nothing here confirms anything. It reports coordinates. Whether a board is
really this company's is still `discover`'s question to answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

from . import http

# Hostnames that name an applicant tracking system. The capture group, where
# there is one, is the tenant id -- the part no amount of guessing produces.
# Ordered most specific first: the pattern that also yields a tenant should
# win over the bare-hostname fallback for the same vendor.
SIGNATURES: list[tuple[str, str]] = [
    ("workday",         r"([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com"),
    ("successfactors",  r"successfactors\.(?:com|eu)[^\"'\s]*?company=([A-Za-z0-9]+)"),
    ("successfactors",  r"[a-z0-9-]+\.successfactors\.(?:com|eu)"),
    ("phenom",          r"cdn\.phenompeople\.com/CareerConnectResources/([A-Z0-9]+)/"),
    ("phenom",          r"phenompeople\.com"),
    ("icims",           r"([a-z0-9-]+)\.icims\.com"),
    ("taleo",           r"([a-z0-9-]+)\.taleo\.net"),
    ("brassring",       r"([a-z0-9-]+)\.brassring\.com"),
    ("cornerstone",     r"([a-z0-9-]+)\.csod\.com"),
    ("neogov",          r"governmentjobs\.com/careers/([a-z0-9]+)"),
    ("neogov",          r"governmentjobs\.com|neogov\.com"),
    ("applitrack",      r"applitrack\.com/([a-z0-9]+)/"),
    ("applitrack",      r"([a-z0-9]+)\.tedk12\.com"),
    ("eightfold",       r"([a-z0-9-]+)\.eightfold\.ai"),
    ("avature",         r"([a-z0-9-]+)\.avature\.net"),
    ("radancy",         r"([a-z0-9-]+)\.talentbrew\.com"),
    ("greenhouse",      r"(?:boards|job-boards)\.greenhouse\.io/([a-z0-9_-]+)"),
    ("greenhouse",      r"greenhouse\.io"),
    ("lever",           r"jobs\.lever\.co/([a-z0-9-]+)"),
    ("ashby",           r"jobs\.ashbyhq\.com/([a-z0-9-]+)"),
    ("smartrecruiters", r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9]+)"),
    ("workable",        r"apply\.workable\.com/([a-z0-9-]+)"),
    ("recruitee",       r"([a-z0-9-]+)\.recruitee\.com"),
    ("paylocity",       r"recruiting\.paylocity\.com/recruiting/jobs/List/(\d+)"),
    ("ultipro",         r"recruiting\d*\.ultipro\.com/([A-Za-z0-9]+)"),
    ("dayforce",        r"([a-z0-9-]+)\.dayforcehcm\.com"),
    ("oracle",          r"([a-z0-9-]+)\.fa\.[a-z0-9]+\.oraclecloud\.com"),
    ("jobvite",         r"jobs\.jobvite\.com/([a-z0-9-]+)"),
    ("bamboohr",        r"([a-z0-9-]+)\.bamboohr\.com"),
    ("jazzhr",          r"([a-z0-9-]+)\.applytojob\.com"),
    ("adp",             r"(?:workforcenow|myjobs)\.adp\.com"),
    ("paycom",          r"([a-z0-9-]+)\.paycomonline\.net"),
    ("paycor",          r"([a-z0-9-]+)\.paycor\.com"),
    ("clearcompany",    r"([a-z0-9-]+)\.clearcompany\.com"),
    ("isolved",         r"([a-z0-9-]+)\.myisolved\.com"),
    ("peopleadmin",     r"([a-z0-9-]+)\.peopleadmin\.com"),
]

_SIGS = [(ats, re.compile(pat, re.I)) for ats, pat in SIGNATURES]

# Which of those `sources/ats.py` can actually read a feed from. The rest are
# recorded anyway: a company whose ATS is known but unreadable is a different
# thing from one we failed to find, and only the first is a handler request.
READABLE = frozenset({"greenhouse", "lever", "ashby", "smartrecruiters",
                      "recruitee", "workable", "workday"})

# Words that are part of a legal name and never part of a domain.
_SUFFIXES = ("inc", "inc.", "llc", "l.l.c.", "corp", "corp.", "corporation",
             "co", "co.", "company", "ltd", "ltd.", "plc", "group",
             "holdings", "incorporated", "limited")

_LINK = re.compile(r"<a\b[^>]*?href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>",
                   re.I | re.S)
_TAG = re.compile(r"<[^>]+>")

# A link worth following to find a jobs portal.
_CAREERS = re.compile(
    r"career|jobs|join[- ]?us|work[- ]?(?:with|for|at)[- ]?us|employment"
    r"|opportunit", re.I)

# ...and the subset that is obviously the portal itself rather than a page
# about how nice it is to work here. Altria's homepage carries six careers
# links; only "Open Jobs Portal" goes anywhere useful.
_PORTAL = re.compile(r"^(?:careers?|jobs)\.|search[- ]?jobs|job[- ]?search"
                     r"|open[- ]?jobs|apply", re.I)

# A browser asking for a page, not a script asking for JSON. The shared
# session in http.py advertises JSON, which is the wrong thing to say on an
# HTML fetch and gets a few CDNs to hand back something unhelpful.
_HTML = {"Accept":
         "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

# How much of a page to scan. The signature is a hostname in a link or a
# script tag, so it is in the head or the early body; a megabyte of inlined
# JSON-LD at the bottom is not worth the regex passes.
_SCAN_BYTES = 400_000


@dataclass
class Found:
    """What a resolution attempt turned up.

    `outcome` is the honest one of:
      hit       an ATS hostname was found, with a tenant where the URL had one
      no-ats    the site answered and was walked, and named no ATS
      blocked   the site refused a non-browser client
      no-site   nothing answered at any address tried
    """

    outcome: str
    ats: str = ""
    tenant: str = ""
    url: str = ""                       # where the ATS was named
    evidence: str = ""
    calls: int = 0
    # Full feed coordinates when the page gave them away. Workday only so
    # far, because Workday is the one whose coordinates are otherwise
    # expensive to find -- see `workday_coords`.
    coords: dict | None = None

    @property
    def readable(self) -> bool:
        """Whether this is an ATS `sources/ats.py` can read a feed from."""
        return self.ats in READABLE


def domains(name: str) -> list[str]:
    """Domains worth trying for a company name, best guess first.

    Deliberately short. Guessing is what this module exists to replace, and a
    long guess list is only a slower way to be wrong -- "University of
    Richmond" guessed as universityofrichmond.com lands on a parked domain
    that redirects to a malware warning. Two tries and stop.

    There used to be a third, the first word on its own, and measuring it
    across 246 Richmond employers is what took it out. Twenty-three names
    reached it and four of those landed on the right company. The rest went
    to strangers, and the strangers are the problem rather than the misses:
    memorial.com is a domain broker's sale page, wall.com a lease-to-own
    parking page, richmond.com the Times-Dispatch rather than the Federal
    Reserve, draper.com the Massachusetts laboratory rather than Draper Aden,
    and solstice.com is the exact collision `same_company` was written for.
    A single generic word is somebody else's company by default, and the four
    it got right -- Anthem, Enhabit, Sheehy, Altria -- are names where the
    first word IS the company, which is precisely what the `site =` field in
    seed_companies.toml is for.

    The cost of a bad guess is not only the wasted call. It points this
    radar's HTTP client at whatever squats on a generic domain, and some of
    them bite: owens.com, guessed for Owens & Minor, answered with a redirect
    to a malware warning. That one is NOT fixed here. It comes from the
    second guess, which the numbers below say is worth keeping, so a bad
    domain is still reachable and the HTTP client is what has to be safe
    about it. Narrowing the guesses makes it rarer, not impossible.

    The second guess was measured the same way and kept, because it turns out
    to be a different animal. Of 64 two-word names the first guess already
    answered 51, the fallback was reached 8 times, and 4 of those were right:
    verizon.com, elephant.com, sentara.com and maymont.com. The other 4 were
    General Insurance, a Texas homebuilder, a German textile consultancy and
    a domain for sale, and every one of them dies at confirmation. Four in
    eight is worth two calls; four in twenty-three was not.
    """
    words = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words.pop()
    if not words:
        return []
    out = ["".join(words)]
    if len(words) > 1:
        out.append("".join(words[:-1]))     # estes express lines -> estesexpress
    seen: set[str] = set()
    uniq: list[str] = []
    for dom in out:
        if len(dom) > 2 and dom not in seen:
            seen.add(dom)
            uniq.append(f"{dom}.com")
    return uniq


def scan(text: str, url: str = "") -> tuple[str, str]:
    """Which ATS this page points at, and its tenant id if a URL carries one.

    The final URL is scanned alongside the body, because a careers link often
    redirects straight onto the ATS, and then the answer is in the address
    bar rather than anywhere in the HTML.
    """
    hay = (url or "") + "\n" + (text or "")
    for ats, pattern in _SIGS:
        match = pattern.search(hay)
        if match:
            tenant = ""
            for group in match.groups():
                if group:
                    tenant = group
                    break
            return ats, tenant
    return "", ""


_WD_URL = re.compile(
    r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/([^\"'\s<>\\?#]*)",
    re.I)

# Workday career-site URLs are often prefixed with a locale that is not part
# of the site id: gnw.wd1.myworkdayjobs.com/en-US/GNW.
_LOCALE = re.compile(r"^[a-z]{2}(?:[-_][A-Za-z]{2})?$")


def workday_coords(text: str, url: str = "") -> dict | None:
    """Full Workday coordinates, when a page links to the board outright.

    Worth a function of its own because of what it saves. `discover` finds a
    Workday board by trying seven datacenters and then up to twenty-nine site
    ids, which is the most expensive probe in the project. A careers page
    that links to gnw.wd1.myworkdayjobs.com/GNW has just given away the
    datacenter and the site id together, and the guessing loop can be skipped
    entirely.
    """
    match = _WD_URL.search((url or "") + "\n" + (text or ""))
    if not match:
        return None
    tenant, datacenter, path = match.group(1), match.group(2), match.group(3)
    parts = [p for p in path.split("/") if p and not _LOCALE.match(p)]
    if not parts:
        return None
    return {"host": f"{tenant}.{datacenter}.myworkdayjobs.com",
            "tenant": tenant, "site": parts[0]}


def careers_links(html: str, base: str) -> list[str]:
    """Links on a page that might lead to its jobs, portal-looking ones first."""
    ranked: list[tuple[int, str]] = []
    for href, text in _LINK.findall(html or ""):
        label = " ".join(_TAG.sub(" ", text).split())[:60]
        if not (_CAREERS.search(href) or _CAREERS.search(label)):
            continue
        url = urljoin(base, href.strip())
        if not url.startswith("http"):
            continue
        parts = url.split("/", 3)
        if len(parts) < 3:
            continue
        host = parts[2]
        rank = 0
        if _PORTAL.search(host) or _PORTAL.search(url.split(host, 1)[-1]):
            rank -= 2
        if re.search(r"career|job", host, re.I):
            rank -= 1
        ranked.append((rank, url))
    seen: set[str] = set()
    out: list[str] = []
    for _, url in sorted(ranked, key=lambda pair: pair[0]):
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def find(name: str, site: str | None = None, *, max_calls: int = 10) -> Found:
    """Resolve a company to its ATS coordinates.

    `site` is the company's website when the user supplied one, which is the
    case worth optimising for: it more than doubles the hit rate and costs a
    third of the calls, because every fetch lands somewhere real.

    Walks at most three careers links from the homepage and two from each of
    those. Two hops covered every hit measured; a third only ever reached
    press releases.
    """
    spent = [0]
    refused = [False]

    def fetch(url: str):
        if spent[0] >= max_calls:
            return None
        spent[0] += 1
        try:
            resp = http.get(url, headers=_HTML, spacing=0.4, timeout=15,
                            retries=1)
        except http.RateLimited:
            refused[0] = True
            return None
        if resp is None or resp.status_code in (403, 406, 429):
            # None is a refused connection as often as a dead host, and the
            # two are not worth telling apart: either way it is not a miss to
            # re-probe tomorrow.
            refused[0] = True
            return None
        if resp.status_code >= 400:
            return None
        return resp

    def hit(resp, evidence: str) -> Found | None:
        body = resp.text[:_SCAN_BYTES]
        ats, tenant = scan(body, resp.url)
        if not ats:
            return None
        coords = workday_coords(body, resp.url) if ats == "workday" else None
        return Found("hit", ats, tenant, resp.url, evidence, spent[0], coords)

    roots = [site] if site else [f"https://www.{d}" for d in domains(name)]
    for root in roots:
        home = fetch(root)
        if home is None:
            continue

        found = hit(home, f"named on {home.url}")
        if found:
            return found

        first = careers_links(home.text, home.url)[:3]
        for link in first:
            page = fetch(link)
            if page is None:
                continue
            found = hit(page, f"via {link}")
            if found:
                return found
            for deeper in careers_links(page.text, page.url)[:2]:
                if deeper == link:
                    continue
                last = fetch(deeper)
                if last is None:
                    continue
                found = hit(last, f"via {link} -> {deeper}")
                if found:
                    return found

        return Found("no-ats", url=home.url, calls=spent[0],
                     evidence=f"{home.url} walked, {len(first)} careers link(s)")

    return Found("blocked" if refused[0] else "no-site", calls=spent[0],
                 evidence=", ".join(roots) or "no usable domain")
