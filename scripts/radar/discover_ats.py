"""Find which ATS a company uses, and its exact feed coordinates.

Run this when adding an employer to `radar/companies.py`. Most Richmond
employers are NOT on Greenhouse/Lever (those skew startup) -- the big ones run
Workday, whose feed URL needs a tenant, a datacenter number, and a site id
that are all different per company and documented nowhere.

    py scripts/discover_ats.py carmax markel genworth

For Workday the probe leans on a useful quirk: hitting a *wrong* site id on
the *right* host returns 404 with `not found: Job_Posting_Site_ID`, which
confirms the tenant and host before the site id is known.

READ THE SAMPLE LINE. A slug is just a string and these APIs will happily
return a *different company's* board for it -- Ashby's `solstice` board is an
unrelated New York AI startup, not Solstice Advanced Materials of Chesterfield.
Every hit prints one real posting's title and location for exactly that check;
if it doesn't look like the company you meant, it isn't.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jobdesk.radar import http  # noqa: E402

WD_DATACENTERS = ["wd1", "wd3", "wd5", "wd12", "wd101", "wd2", "wd103"]

SITE_GUESSES = [
    "{T}", "{T}_Careers", "{T}careers", "Careers", "careers",
    "External", "external", "{T}_External", "External_Careers",
    "{T}_Jobs", "Search", "{T}Careers", "Global", "{T}_Global",
    "External_Career_Site", "ExternalCareerSite", "Careers_External",
    "{T}_External_Career_Site", "{T}_Career_Site", "CareerSite",
    "{T}Jobs", "Jobs", "jobs", "Corporate", "Professional",
    "Search_Jobs", "SearchJobs", "{T}_careers", "{T}_external",
]

WD_PAYLOAD = {"appliedFacets": {}, "limit": 5, "offset": 0, "searchText": ""}


def _sample(title, location) -> str:
    """One posting, rendered for the eyeball check against slug collisions."""
    return f"{title or '?'} | {location or '?'}"


def try_greenhouse(slug: str):
    j = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    if isinstance(j, dict) and "jobs" in j:
        jobs = j["jobs"]
        first = jobs[0] if jobs else {}
        return len(jobs), _sample(first.get("title"),
                                  (first.get("location") or {}).get("name"))
    return None


def try_lever(slug: str):
    j = http.get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if isinstance(j, list):
        first = j[0] if j else {}
        return len(j), _sample(first.get("text"),
                               (first.get("categories") or {}).get("location"))
    return None


def try_ashby(slug: str):
    j = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if isinstance(j, dict) and "jobs" in j:
        jobs = j["jobs"]
        first = jobs[0] if jobs else {}
        return len(jobs), _sample(first.get("title"), first.get("location"))
    return None


def try_smartrecruiters(slug: str):
    j = http.get_json(
        f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=10")
    if isinstance(j, dict) and "totalFound" in j:
        content = j.get("content") or []
        first = content[0] if content else {}
        loc = first.get("location") or {}
        return j["totalFound"], _sample(
            first.get("name"),
            ", ".join(x for x in (loc.get("city"), loc.get("country")) if x))
    return None


def try_recruitee(slug: str):
    j = http.get_json(f"https://{slug}.recruitee.com/api/offers/")
    if isinstance(j, dict) and "offers" in j:
        offers = j["offers"]
        first = offers[0] if offers else {}
        return len(offers), _sample(first.get("title"), first.get("city"))
    return None


def try_workable(slug: str):
    j = http.get_json(
        f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    if isinstance(j, dict) and "jobs" in j:
        jobs = j["jobs"]
        first = jobs[0] if jobs else {}
        return len(jobs), _sample(first.get("title"), first.get("city"))
    return None


def find_workday(tenant: str):
    """Return (host, site, total, sample) or None."""
    host = None
    for dc in WD_DATACENTERS:
        url = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/__probe__/jobs"
        resp = http.post(url, json=WD_PAYLOAD, spacing=0.8)
        if resp is None:
            continue
        # ONLY this error confirms the tenant exists. A 422 means nothing --
        # *.wdN.myworkdayjobs.com is a wildcard, so every unknown tenant
        # answers 422 from every datacenter.
        if "Job_Posting_Site_ID" in resp.text[:400]:
            host = f"{tenant}.{dc}.myworkdayjobs.com"
            print(f"    host confirmed: {host}")
            break
    if not host:
        return None

    for tmpl in SITE_GUESSES:
        site = tmpl.replace("{T}", tenant)
        url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        resp = http.post(url, json=WD_PAYLOAD, spacing=0.8)
        if resp is not None and resp.status_code == 200:
            try:
                data = resp.json()
            except ValueError:
                continue
            postings = data.get("jobPostings") or []
            first = postings[0] if postings else {}
            return (host, site, data.get("total"),
                    _sample(first.get("title"), first.get("locationsText")))
    return None


SIMPLE = [
    ("greenhouse", try_greenhouse),
    ("lever", try_lever),
    ("ashby", try_ashby),
    ("smartrecruiters", try_smartrecruiters),
    ("recruitee", try_recruitee),
    ("workable", try_workable),
]


def discover(name: str) -> None:
    slug = name.strip().lower().replace(" ", "")
    print(f"\n=== {name} (slug guess: {slug}) ===")
    hit = False
    for label, fn in SIMPLE:
        try:
            found = fn(slug)
        except http.RateLimited:
            print(f"  {label:<16} rate-limited, skipped")
            continue
        # A count of 0 means "that board answered but has no such company" --
        # only a non-zero count is real evidence.
        if not found or not found[0]:
            continue
        n, sample = found
        hit = True
        print(f"  {label:<16} FOUND  {n} postings   slug={slug}")
        print(f"      sample: {sample}")
    try:
        wd = find_workday(slug)
    except http.RateLimited:
        wd = None
    if wd:
        hit = True
        host, site, total, sample = wd
        print(f"  workday          FOUND  {total} postings")
        print(f"      host={host}  tenant={slug}  site={site}")
        print(f"      sample: {sample}")
    if not hit:
        print("  nothing found -- try a different slug, or the company is on "
              "iCIMS/Taleo/Oracle (no clean public JSON feed; use the "
              "email-alert lane instead)")


if __name__ == "__main__":
    targets = sys.argv[1:]
    if not targets:
        print(__doc__)
        raise SystemExit(1)
    for t in targets:
        discover(t)
