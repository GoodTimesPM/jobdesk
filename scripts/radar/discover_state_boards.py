"""Probe every state's central job board and report which ones JobDesk can read.

`discover_sitemap.py` answers the question for one URL. This asks it fifty-one
times, over `state_board_candidates.py`, and prints a table plus a ready-made
TOML block for the ones that clear all three gates.

The point is that JobDesk ships for the whole country and the statewide index
is the single largest employer feed in most metros, but only for somebody
looking for work in that state. So the catalog is built once, here, from
evidence -- and the profile turns on the one or two states a user's commute
ring actually touches.

A state that fails is not a bug and is not worth working around. NEOGOV
(governmentjobs.com) hosts roughly a fifth of them and its terms forbid
harvesting, so those are recorded as refused rather than probed further.

Usage:
    py scripts/radar/discover_state_boards.py            # all of them
    py scripts/radar/discover_state_boards.py VA OH TX   # just these
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from discover_sitemap import (JOBISH, LD_BLOCK, _ats_host, _text,  # noqa: E402
                              check_robots, check_sitemap)
from state_board_candidates import CANDIDATES  # noqa: E402


def _jsonld_with_retries(url: str, tries: int = 4) -> tuple[dict | None, bool]:
    """(the JobPosting, whether the site was challenging us).

    Returns as soon as a posting is found. An empty body on a 2xx, or a 202 or
    403, is a bot challenge rather than an answer, so it is retried with
    growing backoff and reported separately if it never clears.
    """
    challenged = False
    for attempt in range(tries):
        status, _, body = _text(url)
        if status == 200 and body:
            for raw in LD_BLOCK.findall(body):
                try:
                    data = json.loads(raw)
                except ValueError:
                    continue
                for node in data if isinstance(data, list) else [data]:
                    if isinstance(node, dict) and node.get("@type") == "JobPosting":
                        return node, False
            return None, False          # a real page, genuinely unmarked
        if status in (202, 403, 429, 503) or not body:
            challenged = True
            time.sleep(2 ** attempt + random.random())
    return None, challenged


def probe_state(code: str, name: str, url: str) -> dict:
    """Run the three gates. Returns a row with a verdict and the evidence."""
    row = {"code": code, "name": name, "url": url, "verdict": "", "note": "",
           "sitemap": "", "jobs": 0, "sample": ""}

    status, final, _ = _text(url)
    if status == 0:
        row["verdict"] = "unreachable"
        return row
    row["url"] = final

    label = _ats_host(final)
    if label:
        row["note"] = label
        if "do not add" in label or "no public JSON" in label:
            row["verdict"] = "refused" if "do not add" in label else "no-feed"
            return row
        row["verdict"] = "ats"
        return row

    base = f"{urlparse(final).scheme}://{urlparse(final).netloc}"
    sitemaps, disallows = check_robots(base)
    blocking = [d for d in disallows if JOBISH.search(d) or d == "/"]
    if blocking:
        row["verdict"] = "robots-blocked"
        row["note"] = ", ".join(blocking[:3])
        return row

    if not sitemaps:
        sitemaps = [urljoin(base, "/sitemap.xml")]
        row["note"] = "no declared sitemap; tried /sitemap.xml"

    sample = None
    for candidate in sitemaps[:3]:
        total, jobs, first = check_sitemap(candidate)
        if jobs and not sample:
            row["sitemap"], row["jobs"], sample = candidate, jobs, first
    if not sample:
        row["verdict"] = "no-job-urls"
        return row

    row["sample"] = sample
    posting, challenged = _jsonld_with_retries(sample)
    if not posting:
        # A site behind a bot challenge answers 202 or 403 with an empty body,
        # which looks exactly like a page with no markup on it if you only
        # check for the markup. jobs.virginia.gov -- a board this project has
        # read every day for two months -- reported "no JSON-LD" on the first
        # run of this script for precisely that reason. The two verdicts mean
        # opposite things: one says never try again, the other says try later.
        row["verdict"] = "challenged" if challenged else "no-jsonld"
        return row

    row["verdict"] = "OK"
    row["note"] = str(posting.get("title") or "")[:40]
    return row


def _job_path(sample: str, sitemap: str) -> str:
    """The URL segment that marks a posting, for the entry's `job_path`."""
    path = urlparse(sample).path
    for guess in ("/jobs/", "/job/", "/careers/", "/positions/", "/opening/"):
        if guess in path:
            return guess
    return "/" + path.strip("/").split("/", 1)[0] + "/" if path.strip("/") else "/"


def main() -> None:
    wanted = [a.upper() for a in sys.argv[1:]]
    items = [(c, *v) for c, v in sorted(CANDIDATES.items())
             if not wanted or c in wanted]

    rows = []
    for code, name, url in items:
        row = probe_state(code, name, url)
        rows.append(row)
        flag = "OK  " if row["verdict"] == "OK" else "    "
        print(f"{flag}{code}  {row['verdict']:14} {row['jobs'] or '':>6}  "
              f"{name} -- {row['note'][:60]}", flush=True)

    good = [r for r in rows if r["verdict"] == "OK"]
    print(f"\n{len(good)} of {len(rows)} states are readable.\n")
    for verdict in sorted({r["verdict"] for r in rows if r["verdict"] != "OK"}):
        codes = [r["code"] for r in rows if r["verdict"] == verdict]
        print(f"  {verdict:14} {' '.join(codes)}")

    print("\n--- paste into jobdesk/radar/data/state_boards.toml ---\n")
    for r in good:
        print(f'[[board]]\nstate = "{r["code"]}"\n'
              f'name = "State of {r["name"]}"\n'
              f'ats = "sitemap"\nsitemap = "{r["sitemap"]}"\n'
              f'job_path = "{_job_path(r["sample"], r["sitemap"])}"\n'
              f'spacing = 5.0\n')


if __name__ == "__main__":
    main()
