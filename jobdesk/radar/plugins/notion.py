"""Notion tracker rows, over the REST API.

Set `NOTION_API_KEY` and `NOTION_JOBS_DB` to turn this on. Create the token at
notion.so/my-integrations, then share the tracker database with it -- a Notion
integration sees nothing until a page is shared with it. Unset, the plugin is
skipped and the run is unaffected.

There used to be a second mode: with no token, rows were written to
`data/notion_queue.json` for an assistant to push through the Notion MCP
connection later. That made the fallback path depend on someone opening a chat
session. JobDesk's own window shows the run now, so the queue is gone.

The schema is the one specified in the plan: role, company, source, URL, date
found, date applied, status, fit score, resume variant, salary, location,
recruiter, follow-up due, stage, notes. The pipeline fills the discovery
half; the application half is yours to update, by hand.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from .. import config
from ..models import Job

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# Property names in the tracker database. Change here if the DB is renamed.
#
# Note for whoever pushes rows through the Notion MCP tools instead of this
# module: the MCP layer exposes the URL column as `userDefined:URL`, because
# a bare "URL" collides with the built-in row url. The REST API used here
# wants the real property name, "URL". Same column, two spellings.
P_ROLE = "Role"
P_COMPANY = "Company"
P_STATUS = "Status"
P_SCORE = "Fit Score"
P_TIER = "Tier"
P_SOURCE = "Source"
P_URL = "URL"
P_LOCATION = "Location"
P_SALARY = "Salary"
P_FOUND = "Date Found"
P_POSTED = "Date Posted"
P_FLAGS = "Flags"
P_WHY = "Why"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('NOTION_API_KEY')}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _properties(job: Job) -> dict:
    props = {
        P_ROLE: {"title": [{"text": {"content": job.title[:2000] or "Untitled"}}]},
        P_COMPANY: {"rich_text": [{"text": {"content": job.company[:2000]}}]},
        P_STATUS: {"select": {"name": "Found"}},
        P_SCORE: {"number": job.score},
        P_TIER: {"select": {"name": job.tier or "F"}},
        P_SOURCE: {"select": {"name": job.source[:100]}},
        P_LOCATION: {"rich_text": [{"text": {"content": job.location[:2000]}}]},
        P_FOUND: {"date": {"start": datetime.now(timezone.utc).date().isoformat()}},
        P_WHY: {"rich_text": [{"text": {"content": "; ".join(job.reasons)[:2000]}}]},
    }
    if job.url:
        props[P_URL] = {"url": job.url[:2000]}
    if job.salary_text:
        props[P_SALARY] = {"rich_text": [{"text": {"content": job.salary_text}}]}
    if job.posted_at:
        props[P_POSTED] = {"date": {"start": job.posted_at.date().isoformat()}}
    if job.flags:
        props[P_FLAGS] = {"rich_text": [{"text": {"content": ", ".join(job.flags)[:2000]}}]}
    return props


NAME = "Notion"


def configured() -> bool:
    config.load_env()
    return bool(os.getenv("NOTION_API_KEY") and os.getenv("NOTION_JOBS_DB"))


def deliver(jobs: list[Job], stats: dict, new_only: list[Job], log=print) -> None:
    """Tracker rows are for postings you have not seen before.

    `stats` and the full `jobs` list are part of the interface every plugin
    gets; Notion only wants the new ones, because a row per run per posting
    would bury the tracker in duplicates.
    """
    push(new_only, log=log)


def push(jobs: list[Job], log=print) -> tuple[int, int]:
    """Write jobs to Notion. Returns (written, failed)."""
    if not jobs:
        return 0, 0

    key = os.getenv("NOTION_API_KEY")
    db = os.getenv("NOTION_JOBS_DB")
    if not (key and db):
        log("  Notion: no API key set, skipping")
        return 0, 0

    from .. import http as _http   # local import: this module stays importable
    written = 0                    # on a machine with no requests installed
    for job in jobs:
        payload = {"parent": {"database_id": db}, "properties": _properties(job)}
        resp = _http.post(f"{API}/pages", headers=_headers(), json=payload,
                          spacing=0.4, timeout=25)
        if resp is not None and resp.status_code < 300:
            written += 1
        else:
            detail = resp.text[:200] if resp is not None else "no response"
            log(f"  Notion: failed on '{job.title[:40]}' -- {detail}")
    log(f"  Notion: wrote {written}/{len(jobs)} row(s)")
    return written, len(jobs) - written
