"""Probe a careers site for the sitemap + schema.org lane.

`discover_ats.py` answers "does this company run an ATS with a public JSON
API". This answers the fallback question: when it doesn't, does the careers
site still publish its jobs in a machine-readable form the site itself is
asking to be read?

Two signals, both of which are the publisher opting in rather than us working
around them:

  robots.txt     -- a `Sitemap:` directive is the site telling crawlers where
                    its index lives. `Disallow` on the jobs path is the
                    opposite, and it ends the conversation.
  JSON-LD        -- a <script type="application/ld+json"> block typed
                    JobPosting is schema.org markup published so Google for
                    Jobs can index the posting. Reading it is the documented
                    use.

If both are present the site can join the radar as an `ats: "sitemap"` entry.
If neither is, it belongs in NO_PUBLIC_FEED and the email-alert lane.

Usage:
    py scripts/discover_sitemap.py https://careers.example.com [...]
"""

from __future__ import annotations

import json
import re
import sys
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

# Careers sites are full of non-cp1252 characters (en dashes, accented city
# names). Without this the script dies on the first one it prints.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from jobdesk.radar import http  # noqa: E402

TIMEOUT = 25
# Hosts that mean "this careers page is a shell around a real ATS" -- if a
# careers URL redirects to one of these, discover_ats.py is the right tool
# and the coordinates are sitting in the redirected URL.
ATS_HOSTS = {
    "myworkdayjobs.com": "workday",
    "icims.com": "icims (no public JSON)",
    "taleo.net": "taleo (no public JSON)",
    "oraclecloud.com": "oracle (no public JSON)",
    "greenhouse.io": "greenhouse",
    "lever.co": "lever",
    "ashbyhq.com": "ashby",
    "smartrecruiters.com": "smartrecruiters",
    "workable.com": "workable",
    "recruitee.com": "recruitee",
    "phenompeople.com": "phenom (no public JSON)",
    "eightfold.ai": "eightfold",
    "successfactors.com": "successfactors (no public JSON)",
    "brassring.com": "brassring (no public JSON)",
    "jobvite.com": "jobvite",
    "paylocity.com": "paylocity",
    "adp.com": "adp",
    "ultipro.com": "ultipro (no public JSON)",
    "governmentjobs.com": "NEOGOV -- ToS forbids harvesting, do not add",
}

JOBISH = re.compile(r"/(job|jobs|career|careers|opening|position|req)s?[/-]", re.I)
LD_BLOCK = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S | re.I)


def _text(url: str) -> tuple[int, str, str]:
    """Return (status, final_url, body). Empty body on any failure."""
    try:
        resp = http.get(url, timeout=TIMEOUT, spacing=1.0)
    except http.RateLimited:
        return 429, url, ""
    if resp is None:
        return 0, url, ""
    return resp.status_code, resp.url, resp.text


def _ats_host(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    for suffix, label in ATS_HOSTS.items():
        if host.endswith(suffix) or f".{suffix}" in host:
            return label
    return None


def check_robots(base: str) -> tuple[list[str], list[str]]:
    """Return (declared sitemaps, disallow rules for the wildcard agent)."""
    status, _, body = _text(urljoin(base, "/robots.txt"))
    if status != 200 or not body:
        return [], []
    sitemaps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", body)
    # Only the `User-agent: *` group binds us.
    disallows, in_star = [], False
    for line in body.splitlines():
        line = line.strip()
        if re.match(r"(?i)^user-agent:", line):
            in_star = line.split(":", 1)[1].strip() == "*"
        elif in_star and re.match(r"(?i)^disallow:", line):
            rule = line.split(":", 1)[1].strip()
            if rule:
                disallows.append(rule)
    return sitemaps, disallows


def check_sitemap(url: str) -> tuple[int, int, str | None]:
    """Return (total urls, job-looking urls, one sample job url)."""
    status, _, body = _text(url)
    if status != 200 or "<" not in body:
        return 0, 0, None
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body)
    # A sitemap index points at more sitemaps; follow the job-looking ones.
    if "<sitemapindex" in body[:2000].lower():
        jobs, sample = 0, None
        for child in locs[:8]:
            _, child_jobs, child_sample = check_sitemap(child)
            jobs += child_jobs
            sample = sample or child_sample
        return len(locs), jobs, sample
    job_urls = [u for u in locs if JOBISH.search(u)]
    return len(locs), len(job_urls), job_urls[0] if job_urls else None


def check_jsonld(url: str) -> dict | None:
    """Return the first schema.org JobPosting found on the page."""
    status, _, body = _text(url)
    if status != 200 or not body:
        return None
    for raw in LD_BLOCK.findall(body):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and node.get("@type") == "JobPosting":
                return node
    return None


def probe(url: str) -> None:
    print(f"\n=== {url} ===")
    status, final, _ = _text(url)
    if status == 0:
        print("  unreachable")
        return
    if final.rstrip("/") != url.rstrip("/"):
        print(f"  redirects to: {final}")
    label = _ats_host(final)
    if label:
        print(f"  runs on: {label}  <- use discover_ats.py, not this")

    base = f"{urlparse(final).scheme}://{urlparse(final).netloc}"
    sitemaps, disallows = check_robots(base)
    blocking = [d for d in disallows if JOBISH.search(d) or d == "/"]
    if blocking:
        print(f"  robots DISALLOWS the jobs path: {blocking}  <- stop here")
        return
    if not sitemaps:
        sitemaps = [urljoin(base, "/sitemap.xml")]
        print("  robots declares no sitemap; trying /sitemap.xml anyway")
    else:
        print(f"  robots declares sitemap: {sitemaps}")

    sample = None
    for sm in sitemaps[:3]:
        total, jobs, first = check_sitemap(sm)
        if total:
            print(f"  sitemap {sm}: {total} urls, {jobs} job-looking")
        sample = sample or first
    if not sample:
        print("  no job URLs in any sitemap -- NO_PUBLIC_FEED, email-alert lane")
        return

    posting = check_jsonld(sample)
    if not posting:
        print(f"  sample job page has no JobPosting JSON-LD: {sample}")
        print("  -> sitemap alone would mean parsing HTML. NO_PUBLIC_FEED.")
        return
    loc = posting.get("jobLocation")
    loc = loc[0] if isinstance(loc, list) and loc else loc
    city = ((loc or {}).get("address") or {}).get("addressLocality", "?")
    print(f"  JSON-LD JobPosting OK: {posting.get('title','?')!r} -- {city}")
    print(f"  -> ADDABLE as ats='sitemap', sitemap={sitemaps[0]}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    for target in sys.argv[1:]:
        probe(target)
