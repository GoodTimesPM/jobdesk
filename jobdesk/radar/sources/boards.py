"""Public job boards and aggregators with real, free APIs.

Everything here is either an officially documented API or a public RSS feed.
Nothing scrapes rendered HTML.

Deliberately NOT here: LinkedIn and Indeed. Neither has a public jobs API
(Indeed's Publisher API has been closed to new applicants for years), both
ban scraping in their terms and enforce it with IP blocks, and getting an
account restricted costs you the network itself -- far more damage than
the listings are worth. The clean route to their inventory is their own
email alerts, parsed over IMAP (plan item 12), which is a separate build.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

from .. import http, profile as targeting
from ..models import Job, parse_date

TIMEOUT = 30

# What to ask the keyword-driven boards for. Kept narrow on purpose -- these
# boards are national, so a broad query buries the Richmond signal.
def queries(limit: int = 8) -> list[str]:
    """The search phrases sent to the keyword boards.

    Adzuna and USAJOBS want a phrase, not a taxonomy, and the full target list
    would be forty round trips per cycle for one metro. `search_queries` in
    targeting.toml is the answer when the user has one; otherwise the top of
    the tier-1 list stands in, which covers the work they said they want.
    Everything else is caught by the company feeds, which are not
    keyword-limited.
    """
    declared = [q for q in targeting.SEARCH_QUERIES if q]
    if declared:
        return declared[:limit]
    titles = [t for t in targeting.TIER_1_TITLES if t]
    return titles[:limit] or ["analyst"]


def where() -> str:
    """The location string for boards that take one. `home_metro` is written
    as "Denver, CO" or "Richmond, VA", which is what these APIs expect.
    """
    return targeting.HOME_METRO or "United States"


# --------------------------------------------------------------------------
# RemoteOK -- free, no key. Returns a list whose first element is a legal
# notice rather than a job; that entry is skipped by checking for `position`.
# --------------------------------------------------------------------------

def remoteok(_cfg: dict | None = None) -> list[Job]:
    data = http.get_json("https://remoteok.com/api", timeout=TIMEOUT)
    if not isinstance(data, list):
        return []
    out = []
    for j in data:
        if not isinstance(j, dict) or not j.get("position"):
            continue
        out.append(Job(
            title=j.get("position", ""),
            company=j.get("company", ""),
            url=j.get("url", ""),
            source="remoteok",
            location=j.get("location", "") or "Remote",
            description=j.get("description", ""),
            posted_at=parse_date(j.get("date") or j.get("epoch")),
            salary_min=_num(j.get("salary_min")),
            salary_max=_num(j.get("salary_max")),
            remote=True,
            external_id=str(j.get("id", "")),
        ))
    return out


# --------------------------------------------------------------------------
# Remotive -- free, no key, documented.
# --------------------------------------------------------------------------

def remotive(_cfg: dict | None = None) -> list[Job]:
    out: list[Job] = []
    for q in queries():
        data = http.get_json(
            "https://remotive.com/api/remote-jobs",
            params={"search": q, "limit": 25}, timeout=TIMEOUT)
        if not isinstance(data, dict):
            continue
        for j in data.get("jobs", []):
            out.append(Job(
                title=j.get("title", ""),
                company=j.get("company_name", ""),
                url=j.get("url", ""),
                source="remotive",
                location=j.get("candidate_required_location", "") or "Remote",
                description=j.get("description", ""),
                posted_at=parse_date(j.get("publication_date")),
                department=j.get("category", ""),
                remote=True,
                external_id=str(j.get("id", "")),
            ))
    return out


# --------------------------------------------------------------------------
# Himalayas -- free, no key.
# --------------------------------------------------------------------------

# The API caps a page at 20 no matter what `limit` says -- asking for 100 got
# 20 and silently lost the rest. Paginate instead.
HIMALAYAS_PAGE = 20
HIMALAYAS_PAGES = 5

# Some responses come back with the API's *schema* in the value slots:
# companyName "name", companyLogo "thumbnail_url". It is intermittent and
# whole-page (all 20 rows or none), and it is what put seven postings into the
# candidate cache under the company "name" -- through scoring, into Notion, and
# out to Discord, where the embed dutifully read "Company: name". Titles,
# salaries and URLs in those rows are real, so the page is worth repairing
# rather than dropping: the slug is right there in the URL.
_PLACEHOLDER_COMPANY = {"name", "companyname", "company name", "string",
                        "thumbnail_url", "null", "none"}


def _deslug(slug: str) -> str:
    """"one-call" -> "One Call". Loses internal caps (StarRez -> Starrez).

    A near-miss on capitalisation is recoverable -- a human reads it and fixes
    the packet. "name" is not: it defeats the dedupe key, the per-company
    application cap, and every guard rule that matches on employer.
    """
    return " ".join(part.capitalize() for part in slug.replace("_", "-").split("-")
                    if part)


def _himalayas_company(j: dict) -> str:
    name = (j.get("companyName") or "").strip()
    if name and name.lower() not in _PLACEHOLDER_COMPANY:
        return name
    return _deslug((j.get("companySlug") or "").strip())


def himalayas(_cfg: dict | None = None) -> list[Job]:
    out: list[Job] = []
    for page in range(HIMALAYAS_PAGES):
        data = http.get_json("https://himalayas.app/jobs/api",
                             params={"limit": HIMALAYAS_PAGE,
                                     "offset": page * HIMALAYAS_PAGE},
                             timeout=TIMEOUT)
        if not isinstance(data, dict):
            break
        jobs = data.get("jobs") or []
        if not jobs:
            break
        for j in jobs:
            locs = j.get("locationRestrictions") or []
            out.append(Job(
                title=j.get("title", ""),
                company=_himalayas_company(j),
                url=j.get("applicationLink") or j.get("guid", ""),
                source="himalayas",
                location=", ".join(locs) if locs else "Remote",
                description=j.get("description", ""),
                posted_at=parse_date(j.get("pubDate")),
                salary_min=_num(j.get("minSalary")),
                salary_max=_num(j.get("maxSalary")),
                remote=True,
                external_id=str(j.get("guid", "")),
            ))
    return out


# --------------------------------------------------------------------------
# We Work Remotely -- RSS, no key.
# --------------------------------------------------------------------------

WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-business-exec-management-jobs.rss",
    "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
]


def weworkremotely(_cfg: dict | None = None) -> list[Job]:
    out: list[Job] = []
    for url in WWR_FEEDS:
        resp = http.get(url, timeout=TIMEOUT)
        if resp is None or resp.status_code >= 400:
            continue
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            continue
        for item in root.iter("item"):
            raw_title = _txt(item, "title")
            # WWR formats titles as "Company: Role"
            company, _, title = raw_title.partition(": ")
            if not title:
                company, title = "", raw_title
            out.append(Job(
                title=title.strip(),
                company=company.strip(),
                url=_txt(item, "link"),
                source="weworkremotely",
                location=_txt(item, "region") or "Remote",
                description=_txt(item, "description"),
                posted_at=parse_date(_txt(item, "pubDate")),
                remote=True,
            ))
    return out


# --------------------------------------------------------------------------
# Adzuna -- free tier, needs an app id + key (adzuna.com/developer).
# Worth having: it is the only free source here that reliably carries salary
# data, which feeds both the salary filter and the market dashboard later.
# --------------------------------------------------------------------------

def adzuna(_cfg: dict | None = None) -> list[Job]:
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        return []
    out: list[Job] = []
    for q in queries():
        data = http.get_json(
            "https://api.adzuna.com/v1/api/jobs/us/search/1",
            params={
                "app_id": app_id, "app_key": app_key,
                "what": q, "where": where(),
                "distance": 40, "results_per_page": 25,
                "max_days_old": 30, "content-type": "application/json",
            }, timeout=TIMEOUT)
        if not isinstance(data, dict):
            continue
        for j in data.get("results", []):
            out.append(Job(
                title=j.get("title", ""),
                company=(j.get("company") or {}).get("display_name", ""),
                url=j.get("redirect_url", ""),
                source="adzuna",
                location=(j.get("location") or {}).get("display_name", ""),
                description=j.get("description", ""),
                posted_at=parse_date(j.get("created")),
                salary_min=_num(j.get("salary_min")),
                salary_max=_num(j.get("salary_max")),
                external_id=str(j.get("id", "")),
            ))
    return out


# --------------------------------------------------------------------------
# USAJobs -- official federal API. Free, but the key is mandatory
# (developer.usajobs.gov, issued by email). Richmond is a state capital with
# federal presence, and the Richmond Fed hires analysts.
# --------------------------------------------------------------------------

def usajobs(_cfg: dict | None = None) -> list[Job]:
    key = os.getenv("USAJOBS_API_KEY")
    email = os.getenv("USAJOBS_EMAIL")
    if not (key and email):
        return []
    headers = {"Host": "data.usajobs.gov", "User-Agent": email,
               "Authorization-Key": key}
    out: list[Job] = []
    for q in queries(limit=2):
        data = http.get_json(
            "https://data.usajobs.gov/api/search",
            params={"Keyword": q, "LocationName": where(),
                    "Radius": 40, "ResultsPerPage": 25},
            headers=headers, timeout=TIMEOUT)
        if not isinstance(data, dict):
            continue
        items = (data.get("SearchResult") or {}).get("SearchResultItems", [])
        for item in items:
            d = item.get("MatchedObjectDescriptor") or {}
            pay = (d.get("PositionRemuneration") or [{}])[0]
            out.append(Job(
                title=d.get("PositionTitle", ""),
                company=d.get("OrganizationName", ""),
                url=d.get("PositionURI", ""),
                source="usajobs",
                location=", ".join(
                    loc.get("LocationName", "")
                    for loc in (d.get("PositionLocation") or [])[:3]),
                description=" ".join(filter(None, [
                    (d.get("UserArea", {}).get("Details", {}) or {}).get("JobSummary", ""),
                    (d.get("QualificationSummary") or ""),
                ])),
                posted_at=parse_date(d.get("PublicationStartDate")),
                salary_min=_num(pay.get("MinimumRange")),
                salary_max=_num(pay.get("MaximumRange")),
                external_id=str(d.get("PositionID", "")),
            ))
    return out


# --------------------------------------------------------------------------
# Hacker News "Who is Hiring" -- free Firebase API, same one the news bot
# already uses. Skews startup/remote and the postings are plain text, so the
# parse is loose on purpose: it is a supplement, never a primary lane.
# --------------------------------------------------------------------------

_HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{}.json"
_REMOTE_RE = re.compile(r"\bremote\b", re.I)


def hackernews(_cfg: dict | None = None) -> list[Job]:
    user = http.get_json(
        "https://hacker-news.firebaseio.com/v0/user/whoishiring.json",
        timeout=TIMEOUT)
    if not isinstance(user, dict):
        return []

    thread = None
    for sid in (user.get("submitted") or [])[:10]:
        item = http.get_json(_HN_ITEM.format(sid), timeout=TIMEOUT)
        if isinstance(item, dict) and "who is hiring" in (item.get("title") or "").lower():
            thread = item
            break
    if not thread:
        return []

    out: list[Job] = []
    for cid in (thread.get("kids") or [])[:120]:
        c = http.get_json(_HN_ITEM.format(cid), timeout=TIMEOUT, spacing=0.2)
        if not isinstance(c, dict) or c.get("deleted") or c.get("dead"):
            continue
        text = c.get("text") or ""
        if not text:
            continue
        # HN convention: the first line is "Company | Role | Location | ..."
        from ..models import clean_text
        flat = clean_text(text)
        head = flat.split(".")[0][:200]
        parts = [p.strip() for p in head.split("|")]
        company = parts[0] if parts else "Unknown"
        title = parts[1] if len(parts) > 1 else head[:80]
        out.append(Job(
            title=title,
            company=company,
            url=f"https://news.ycombinator.com/item?id={cid}",
            source="hackernews",
            location=parts[2] if len(parts) > 2 else "",
            description=flat,
            posted_at=parse_date(c.get("time")),
            remote=bool(_REMOTE_RE.search(flat[:400])),
            external_id=str(cid),
        ))
    return out


# --------------------------------------------------------------------------
# hiring.cafe
#
# Rated honestly, since it was asked about specifically: as a *site* it is
# genuinely good -- it indexes company ATS boards directly (the same reasoning
# behind `ats.py`) and its filters are better than Indeed's. As a *source for
# this pipeline* it is the weakest option on the list, because it has no
# public API:
#
#     GET  /api/search-jobs  -> 401 Unauthorized
#     POST /api/search-jobs  -> 405 Method not allowed
#
# (verified 2026-07-23). Its frontend bundles reference an Elastic backend
# behind Bearer auth, so the only way to query it programmatically is to lift
# a logged-in session token and replay it against a private endpoint. That is
# the same category of thing as scraping LinkedIn -- undocumented, clearly not
# intended for third-party use, and exactly the risk you asked to stay
# clear of. So it is OFF by default and stays off unless you opts in knowing
# that.
#
# The recommended way to use hiring.cafe: keep using it manually, and turn on
# its email alerts so it arrives through the IMAP lane (plan item 12) like
# LinkedIn and Indeed. Same inventory, zero risk, no token needed.
# --------------------------------------------------------------------------

def hiringcafe(_cfg: dict | None = None) -> list[Job]:
    token = os.getenv("HIRINGCAFE_TOKEN")
    if not token:
        return []
    data = http.post_json(
        "https://hiring.cafe/api/search-jobs",
        headers={"Authorization": f"Bearer {token}"},
        json={"size": 50, "page": 0,
              "searchState": {"searchQuery": queries(limit=1)[0],
                              "locations": [{"formatted_address": where()}]}},
        timeout=TIMEOUT)
    if not isinstance(data, dict):
        return []
    out = []
    for j in data.get("results", []) or data.get("hits", []):
        info = j.get("job_information") or j
        out.append(Job(
            title=info.get("title", ""),
            company=(j.get("company") or {}).get("name", "") or info.get("company", ""),
            url=j.get("apply_url") or j.get("url", ""),
            source="hiringcafe",
            location=info.get("location", ""),
            description=info.get("description", ""),
            posted_at=parse_date(j.get("created_at") or info.get("posted_at")),
        ))
    return out


# --------------------------------------------------------------------------

def _txt(node, tag: str) -> str:
    el = node.find(tag)
    return (el.text or "").strip() if el is not None else ""


def _num(v) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


HANDLERS = {
    "remoteok": remoteok,
    "remotive": remotive,
    "himalayas": himalayas,
    "weworkremotely": weworkremotely,
    "adzuna": adzuna,
    "usajobs": usajobs,
    "hackernews": hackernews,
    "hiringcafe": hiringcafe,
}
