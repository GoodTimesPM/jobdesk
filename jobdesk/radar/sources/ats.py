"""Company ATS boards -- the highest-value, least-crowded channel.

These are the applicant tracking systems companies host their own job boards
on. Every one of them serves public JSON that the company's own careers page
consumes, so reading it is using the site as intended -- no scraping, no
parsing HTML, no ToS gray zone.

Why this matters more than the aggregators: a req appears here the moment it
opens, often days before it propagates to Indeed/LinkedIn, and applying here
goes straight into the company's pipeline rather than through an aggregator
redirect. That is the whole speed-to-apply advantage (plan item 3).

Each function takes a company entry from `radar/companies.py` and returns
`list[Job]`. Endpoint shapes verified live 2026-07-23.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from .. import http, profile as targeting
from ..models import Job, clean_text, parse_date

TIMEOUT = 30


# --------------------------------------------------------------------------
# Greenhouse -- boards-api.greenhouse.io
# --------------------------------------------------------------------------

def greenhouse(entry: dict) -> list[Job]:
    slug = entry["slug"]
    data = http.get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
        timeout=TIMEOUT)
    if not isinstance(data, dict):
        return []
    out = []
    for j in data.get("jobs", []):
        out.append(Job(
            title=j.get("title", ""),
            company=entry["name"],
            url=j.get("absolute_url", ""),
            source="greenhouse",
            location=(j.get("location") or {}).get("name", ""),
            description=j.get("content", ""),
            posted_at=parse_date(j.get("first_published") or j.get("updated_at")),
            department=", ".join(d.get("name", "") for d in j.get("departments", [])),
            external_id=str(j.get("id", "")),
        ))
    return out


# --------------------------------------------------------------------------
# Lever -- api.lever.co
# --------------------------------------------------------------------------

def lever(entry: dict) -> list[Job]:
    slug = entry["slug"]
    data = http.get_json(
        f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout=TIMEOUT)
    if not isinstance(data, list):
        return []
    out = []
    for j in data:
        cats = j.get("categories") or {}
        out.append(Job(
            title=j.get("text", ""),
            company=entry["name"],
            url=j.get("hostedUrl") or j.get("applyUrl", ""),
            source="lever",
            location=cats.get("location", ""),
            description=j.get("descriptionPlain") or j.get("description", ""),
            posted_at=parse_date(j.get("createdAt")),
            department=cats.get("team", "") or cats.get("department", ""),
            external_id=str(j.get("id", "")),
            remote=(cats.get("commitment", "") or "").lower() == "remote",
        ))
    return out


# --------------------------------------------------------------------------
# Ashby -- api.ashbyhq.com
# --------------------------------------------------------------------------

def ashby(entry: dict) -> list[Job]:
    slug = entry["slug"]
    data = http.get_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
        timeout=TIMEOUT)
    if not isinstance(data, dict):
        return []
    out = []
    for j in data.get("jobs", []):
        out.append(Job(
            title=j.get("title", ""),
            company=entry["name"],
            url=j.get("jobUrl") or j.get("applyUrl", ""),
            source="ashby",
            location=j.get("location", ""),
            description=j.get("descriptionPlain") or j.get("descriptionHtml", ""),
            posted_at=parse_date(j.get("publishedAt")),
            department=j.get("department", "") or j.get("team", ""),
            external_id=str(j.get("id", "")),
            remote=bool(j.get("isRemote")),
        ))
    return out


# --------------------------------------------------------------------------
# SmartRecruiters -- api.smartrecruiters.com
# The list endpoint has no description, so the body is fetched per posting.
# That is one extra call each; only done for postings that pass a title
# pre-filter, to keep the request count sane.
# --------------------------------------------------------------------------

def smartrecruiters(entry: dict) -> list[Job]:
    slug = entry["slug"]
    data = http.get_json(
        f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100",
        timeout=TIMEOUT)
    if not isinstance(data, dict):
        return []
    out = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        location = ", ".join(
            x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
        # `ref` is the API's own self-link (a string), not a jobAd dict --
        # the public posting always lives at this company-slug/id path.
        out.append(Job(
            title=j.get("name", ""),
            company=entry["name"],
            url=f"https://jobs.smartrecruiters.com/{slug}/{j.get('id','')}",
            source="smartrecruiters",
            location=location,
            description=j.get("jobAdInternal", ""),
            posted_at=parse_date(j.get("releasedDate")),
            department=(j.get("department") or {}).get("label", ""),
            external_id=str(j.get("id", "")),
            remote=bool(loc.get("remote")),
        ))
    return out


# --------------------------------------------------------------------------
# Workable / Recruitee -- smaller shops, same idea
# --------------------------------------------------------------------------

def workable(entry: dict) -> list[Job]:
    slug = entry["slug"]
    data = http.get_json(
        f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
        timeout=TIMEOUT)
    if not isinstance(data, dict):
        return []
    out = []
    for j in data.get("jobs", []):
        out.append(Job(
            title=j.get("title", ""),
            company=entry["name"],
            url=j.get("url") or j.get("application_url", ""),
            source="workable",
            location=", ".join(
                x for x in (j.get("city"), j.get("state"), j.get("country")) if x),
            description=j.get("description", ""),
            posted_at=parse_date(j.get("published_on")),
            department=j.get("department", ""),
            external_id=str(j.get("shortcode", "")),
            remote=bool(j.get("telecommuting")),
        ))
    return out


def recruitee(entry: dict) -> list[Job]:
    slug = entry["slug"]
    data = http.get_json(f"https://{slug}.recruitee.com/api/offers/", timeout=TIMEOUT)
    if not isinstance(data, dict):
        return []
    out = []
    for j in data.get("offers", []):
        out.append(Job(
            title=j.get("title", ""),
            company=entry["name"],
            url=j.get("careers_url") or j.get("careers_apply_url", ""),
            source="recruitee",
            location=", ".join(
                x for x in (j.get("city"), j.get("state_name"), j.get("country")) if x),
            description=j.get("description", ""),
            posted_at=parse_date(j.get("published_at")),
            department=j.get("department", ""),
            external_id=str(j.get("id", "")),
            remote=bool(j.get("remote")),
        ))
    return out


# --------------------------------------------------------------------------
# Workday -- the big one for Richmond.
#
# Capital One, CarMax, VCU Health and most large employers here run Workday.
# The feed is a POST to /wday/cxs/{tenant}/{site}/jobs. Coordinates are
# per-company and documented nowhere; `scripts/discover_ats.py` finds them.
#
# The list response carries title, location and a relative path but NOT the
# body, so years-of-experience parsing needs a second call per posting. That
# is expensive against a 1,190-posting board, so Workday is queried with a
# `searchText` per target title instead of pulled wholesale, and bodies are
# fetched only for what survives.
# --------------------------------------------------------------------------

def workday_queries(limit: int = 6) -> list[str]:
    """`searchText` values for the Workday feeds, one per function family.

    A Workday board is queried per target title rather than pulled wholesale,
    so the query list has to be short. The first term of each function family
    is the right size of word: broad enough that "accountant" catches the
    staff, senior and assistant flavours, narrow enough that the board does
    not return its entire catalogue. Single-word terms only -- Workday's
    search treats a phrase as an AND and quietly returns nothing.

    `workday_search_terms` in targeting.toml overrides the derivation.
    """
    declared = [q for q in targeting.WORKDAY_SEARCH_TERMS if q]
    if declared:
        return declared[:limit]
    seen: set[str] = set()
    out: list[str] = []
    for _points, _label, terms in targeting.FUNCTION_FAMILIES:
        for term in terms:
            word = term.split()[0].strip().lower()
            if len(word) > 3 and word not in seen:
                seen.add(word)
                out.append(word)
                break
    return out[:limit] or ["analyst"]

# The list endpoint dates postings as human text: "Posted Today",
# "Posted 5 Days Ago", "Posted 30+ Days Ago".
_POSTED_ON = re.compile(r"posted\s+(?:(\d+)\+?\s+days?|(today)|(yesterday))", re.I)


def _parse_posted_on(text: str) -> datetime | None:
    if not text:
        return None
    m = _POSTED_ON.search(text)
    if not m:
        return None
    if m.group(2):
        days = 0
    elif m.group(3):
        days = 1
    else:
        days = int(m.group(1))
    return datetime.now(timezone.utc) - timedelta(days=days)


def _workday_search(entry: dict, query: str, limit: int = 20) -> list[dict]:
    url = (f"https://{entry['host']}/wday/cxs/{entry['tenant']}"
           f"/{entry['site']}/jobs")
    data = http.post_json(url, timeout=TIMEOUT, json={
        "appliedFacets": {}, "limit": limit, "offset": 0, "searchText": query,
    })
    if not isinstance(data, dict):
        return []
    return data.get("jobPostings", [])


def workday(entry: dict) -> list[Job]:
    """Search a Workday board for each target keyword.

    Searched rather than pulled wholesale: CarMax alone has 1,190 open reqs,
    and the list response carries no job description, so the only way to
    filter properly is a detail call per posting. Querying by keyword first
    keeps that to a few dozen calls instead of a few thousand.
    """
    base = f"https://{entry['host']}"
    site_path = f"/{entry['site']}"
    seen: set[str] = set()
    out: list[Job] = []

    for query in entry.get("queries", workday_queries()):
        for j in _workday_search(entry, query):
            path = j.get("externalPath", "")
            if not path or path in seen:
                continue
            seen.add(path)
            bullets = j.get("bulletFields") or []
            out.append(Job(
                title=j.get("title", ""),
                company=entry["name"],
                url=base + site_path + path,
                source="workday",
                location=j.get("locationsText", ""),
                posted_at=_parse_posted_on(j.get("postedOn", "")),
                external_id=str(bullets[0]) if bullets else "",
            ))
    return out


def workday_detail(job: Job, entry: dict) -> bool:
    """Fetch the JD body for one Workday posting, in place.

    Only worth calling for postings that already look promising: the body is
    what the years-of-experience filter needs, and it costs one HTTP call
    each. Returns True if the body was filled in.
    """
    base = f"https://{entry['host']}"
    site_path = f"/{entry['site']}"
    prefix = base + site_path
    if not job.url.startswith(prefix):
        return False
    url = (f"{base}/wday/cxs/{entry['tenant']}/{entry['site']}"
           f"{job.url[len(prefix):]}")
    data = http.get_json(url, timeout=TIMEOUT, spacing=0.6)
    if not isinstance(data, dict):
        return False
    info = data.get("jobPostingInfo") or {}
    if info.get("jobDescription"):
        job.description = clean_text(info["jobDescription"])
    # startDate is the real ISO date; the list view only had "N Days Ago".
    job.posted_at = parse_date(info.get("startDate")) or job.posted_at
    if info.get("externalUrl"):
        job.url = info["externalUrl"]
    if info.get("remoteType"):
        job.remote = "remote" in str(info["remoteType"]).lower()
    return bool(info.get("jobDescription"))


# --------------------------------------------------------------------------
# Sitemap + schema.org -- the lane for employers with no ATS JSON at all.
#
# Some careers sites serve no public JSON but do publish two things that exist
# specifically to be read by machines: a sitemap.xml declared in robots.txt,
# and a schema.org JobPosting JSON-LD block on each posting so Google for Jobs
# can index it. Reading those is the documented use of both, which keeps this
# on the same footing as the ATS feeds -- it is still not HTML scraping.
#
# The bar for adding an entry here, checked by scripts/discover_sitemap.py:
#   1. robots.txt does not Disallow the jobs path
#   2. the sitemap actually lists individual postings, not just a jobs index
#   3. a sample posting carries a real JobPosting JSON-LD block
# Fail any of the three and the employer belongs in NO_PUBLIC_FEED instead.
#
# Only jobs.virginia.gov clears all three today (2026-08-17). It matters
# enough on its own: the Commonwealth is the largest white-collar employer in
# the capital region and 154 of its 3,930 open postings are Richmond.
# --------------------------------------------------------------------------

_SITEMAP_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_SITEMAP_ENTRY = re.compile(r"<url>(.*?)</url>", re.S)
_LASTMOD = re.compile(r"<lastmod>\s*([^<\s]+)\s*</lastmod>")
_TRAILING_ID = re.compile(
    r"-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_JSONLD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S | re.I)

# The slug ends "<title>-<city>-<state>-<country>". Stripping the country and
# state suffixes leaves the city as the tail -- imperfect for two-word cities
# ("newport news" survives as "news"), which is why the detail fetch replaces
# this with the JSON-LD address as soon as a posting scores.
_SLUG_TAIL = ("-united-states", "-virginia")


def _deslug(slug: str) -> tuple[str, str]:
    """Split a job URL slug into (title, location), both human-readable."""
    slug = _TRAILING_ID.sub("", slug).strip("/")
    tail = []
    for suffix in _SLUG_TAIL:
        if slug.endswith(suffix):
            slug = slug[: -len(suffix)]
            tail.insert(0, suffix.strip("-").replace("-", " ").title())
    title, _, city = slug.rpartition("-")
    if not title:                      # single-token slug: no city to split off
        title, city = city, ""
    location = ", ".join(x for x in [city.replace("-", " ").title(), *tail] if x)
    return title.replace("-", " ").title(), location


class Challenged(RuntimeError):
    """A site answered without content -- a bot check, not a posting.

    Raised rather than returned because it is not a fact about this posting.
    Every other posting on the same host is about to do the same thing, and
    the caller should stop asking and say so.
    """


def sitemap(entry: dict) -> list[Job]:
    """Read an employer's sitemap and turn its job URLs into Jobs.

    Titles and locations come out of the URL slug -- no HTML is parsed here.
    That is enough to score on, and `sitemap_detail` fills in the real body
    from the posting's JSON-LD for the handful that score well enough to be
    worth a second call.

    Statewide boards are trimmed on the way in: everything in the commute ring
    is kept, plus title-keyword matches from anywhere. Without that, one entry
    would put 3,900 postings a run through the scorer to surface a few dozen.
    """
    resp = http.get(entry["sitemap"], timeout=TIMEOUT)
    if resp is None or resp.status_code >= 400:
        return []
    job_path = entry.get("job_path", "/job")
    keep_local = [t.lower() for t in entry.get("local_terms", [])]
    keywords = [q.lower() for q in entry.get("queries", workday_queries())]

    out: list[Job] = []
    for block in _SITEMAP_ENTRY.findall(resp.text):
        loc_match = _SITEMAP_LOC.search(block)
        if loc_match is None:
            continue
        url = loc_match.group(1)
        if job_path not in url:
            continue
        title, location = _deslug(url.split(job_path, 1)[1])
        hay = f"{title} {location}".lower()
        if keep_local and not (
                any(t in hay for t in keep_local)
                or any(k in title.lower() for k in keywords)):
            continue
        mod = _LASTMOD.search(block)
        out.append(Job(
            title=title,
            company=entry["name"],
            url=url,
            source="sitemap",
            location=location,
            posted_at=parse_date(mod.group(1)) if mod else None,
            external_id=url.rsplit("/", 1)[-1],
        ))
    return out


# schema.org publishes pay as a MonetaryAmount, and a JobPosting that carries
# one is an employer stating a figure rather than a regex inferring it. The
# sitemap lane ignored it until 2026-09-15, which is why every posting from a
# statewide board arrived with a blank salary while the markup beside the
# description said $48,721 to $79,172.
#
# unitText is the whole game: the same shape carries an annual band and an
# hourly rate, and reading one as the other is off by a factor of 2080.
_PERIOD_HOURS = {"HOUR": 2080, "DAY": 260, "WEEK": 52, "MONTH": 12,
                 "YEAR": 1}


def _schema_salary(node: dict) -> tuple[float | None, float | None]:
    """(min, max) annual, from a JobPosting's baseSalary. Blanks on anything odd."""
    base = node.get("baseSalary")
    if isinstance(base, list):
        base = base[0] if base else None
    if not isinstance(base, dict):
        return None, None
    value = base.get("value")
    if isinstance(value, list):
        value = value[0] if value else None

    if isinstance(value, dict):
        period = str(value.get("unitText") or "YEAR").upper()
        raw = (value.get("minValue"), value.get("maxValue"))
        if raw == (None, None):
            raw = (value.get("value"), value.get("value"))
    elif isinstance(value, (int, float, str)):
        period = str(base.get("unitText") or "YEAR").upper()
        raw = (value, value)
    else:
        return None, None

    factor = _PERIOD_HOURS.get(period)
    if not factor:
        return None, None

    out = []
    for item in raw:
        try:
            number = float(str(item).replace(",", "").replace("$", ""))
        except (TypeError, ValueError):
            out.append(None)
            continue
        annual = number * factor
        # The same sanity band the body parser uses. Boards publish zeroes and
        # placeholder ones in this field as readily as they do in prose.
        out.append(annual if 15_000 <= annual <= 900_000 else None)
    low, high = out
    if low and high and high < low:
        low, high = high, low
    return low, high


def sitemap_detail(job: Job, entry: dict) -> bool:
    """Fill in one posting's body from its schema.org JSON-LD.

    Same contract as `workday_detail`: one HTTP call, only worth spending on a
    posting that already scored. The JSON-LD also carries the authoritative
    title, city and post date, all of which beat what the slug guessed.
    """
    resp = http.get(job.url, timeout=TIMEOUT, spacing=entry.get("spacing", 5.0))
    if resp is None or resp.status_code >= 400:
        return False
    # A bot challenge answers 2xx with nothing in it, which is indistinguishable
    # from a posting that simply has no markup unless you look at the length.
    # jobs.virginia.gov started returning 202 and an empty body at some point
    # before 2026-09-15, and because the only signal was "no JSON-LD found",
    # 52 Commonwealth postings sat in the candidate set with no description,
    # no salary and nothing anywhere saying why. Silence is the bug.
    if not resp.text.strip():
        raise Challenged(f"{entry.get('name', job.company)} returned "
                         f"{resp.status_code} with an empty body")
    for raw in _JSONLD.findall(resp.text):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if not isinstance(node, dict) or node.get("@type") != "JobPosting":
                continue
            if node.get("description"):
                job.description = clean_text(node["description"])
            low, high = _schema_salary(node)
            if low or high:
                job.salary_min, job.salary_max = low, high
            job.title = node.get("title") or job.title
            job.posted_at = parse_date(node.get("datePosted")) or job.posted_at
            place = node.get("jobLocation")
            place = place[0] if isinstance(place, list) and place else place
            address = (place or {}).get("address") or {}
            city = address.get("addressLocality")
            if city:
                job.location = ", ".join(
                    x for x in (city, address.get("addressRegion")) if x)
            return bool(node.get("description"))
    return False


HANDLERS = {
    "greenhouse": greenhouse,
    "sitemap": sitemap,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "workable": workable,
    "recruitee": recruitee,
    "workday": workday,
}
