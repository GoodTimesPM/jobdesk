"""Raw-postings ingestion for the Richmond Job Market Dashboard (plan item 11).

Writes every posting the radar pulls, before dedupe/scoring collapses or filters
anything, into MySQL's `raw_postings` table -- one row per (posting, source,
run). This is a pure byproduct of the normal scheduled run: it never affects
what the radar itself scores, dedupes, or sends to Discord/Notion.

No-ops cleanly (logs and returns) if MYSQL_PASSWORD is unset or the server
is unreachable -- a dashboard-side outage must never take down the radar run
itself.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Callable

from .models import Job

# MySQL runs in strict mode, so an over-long value is error 1406 -- and because
# the insert is one executemany, a single bad row discards the entire run's
# worth of postings. A "remote" listing that names every eligible country
# reached 1,772 characters in `location`, which is what kept this table empty.
#
# These widths used to be written out here as constants "from
# schema/001_raw_postings.sql". That file is not in this repo, the constants
# drifted from the live table, and on 2026-09-08 a run of 7,508 postings was
# thrown away because the table said location was VARCHAR(255) while this file
# said 1000. Nothing reported the disagreement, because a value that fits the
# constant is never checked against anything else.
#
# So the widths are read from the table itself now. There is one source of
# truth and it is the schema, which is the only copy that can actually reject a
# row. The map below is a fallback for the case where information_schema cannot
# be read; it is deliberately conservative.
#
# 2026-09-12: `location` is TEXT in the live table (schema/002_raw_lossless.sql),
# so information_schema reports no width for it and _fit stops trimming it. That
# is the intended end state. A landing table should never reject or silently
# shorten what a source sent -- length rules belong in the cleaned layer, where
# a violation can be flagged and looked at instead of disappearing.
_FALLBACK_WIDTHS = {
    "run_stamp": 20, "dedupe_key": 255, "uid": 16, "source": 50,
    "title": 500, "company": 255, "location": 255,
    "department": 255, "external_id": 255,
}


def _column_widths(conn, database: str,
                   log: Callable[[str], None]) -> dict[str, int]:
    """The table's own char limits, keyed by column name.

    Only character columns come back with a limit; TEXT and LONGTEXT are left
    out on purpose, since `url` and `description` are wide enough that trimming
    them would lose more than it saves.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COLUMN_NAME, CHARACTER_MAXIMUM_LENGTH "
                "FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'raw_postings' "
                "AND DATA_TYPE IN ('varchar', 'char')",
                (database,),
            )
            widths = {name: int(limit) for name, limit in cur.fetchall() if limit}
    except Exception as exc:
        log(f"mysql: could not read column widths, using fallbacks ({exc})")
        return dict(_FALLBACK_WIDTHS)
    return widths or dict(_FALLBACK_WIDTHS)


def _fit(value: str | None, column: str,
         widths: dict[str, int]) -> str | None:
    """Trim a value to its column's width. None and short values pass through.

    A column the table does not report is left alone rather than guessed at:
    the point of reading the schema is to stop this file having opinions.
    """
    if value is None:
        return None
    limit = widths.get(column)
    if limit is None or len(value) <= limit:
        return value
    return value[:limit]


def write_raw_postings(jobs: list[Job], run_stamp: str,
                        log: Callable[[str], None] = print) -> None:
    password = os.environ.get("MYSQL_PASSWORD")
    if not password:
        log("mysql: MYSQL_PASSWORD unset - skipping raw_postings write")
        return
    if not jobs:
        return

    try:
        import pymysql
    except ImportError:
        log("mysql: pymysql not installed - skipping raw_postings write")
        return

    try:
        conn = pymysql.connect(
            host=os.environ.get("MYSQL_HOST", "localhost"),
            port=int(os.environ.get("MYSQL_PORT", "3306")),
            user=os.environ.get("MYSQL_USER", ""),
            password=password,
            database=os.environ.get("MYSQL_DB", "job_radar"),
            charset="utf8mb4",
            connect_timeout=5,
        )
    except Exception as exc:  # pymysql raises its own OperationalError etc.
        log(f"mysql: connection failed - skipping raw_postings write ({exc})")
        return

    widths = _column_widths(conn, os.environ.get("MYSQL_DB", "job_radar"), log)
    collected_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows = [
        (
            _fit(run_stamp, "run_stamp", widths),
            collected_at,
            _fit(job.dedupe_key, "dedupe_key", widths),
            _fit(job.uid, "uid", widths),
            _fit(job.source, "source", widths),
            _fit(job.title, "title", widths),
            _fit(job.company, "company", widths),
            job.url,                      # TEXT column, no limit to enforce
            _fit(job.location, "location", widths),
            job.description or None,      # LONGTEXT
            job.posted_at.strftime("%Y-%m-%d %H:%M:%S") if job.posted_at else None,
            job.salary_min,
            job.salary_max,
            job.remote,
            _fit(job.department, "department", widths),
            _fit(job.external_id, "external_id", widths),
        )
        for job in jobs
    ]

    try:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO raw_postings
                    (run_stamp, collected_at, dedupe_key, uid, source, title,
                     company, url, location, description, posted_at,
                     salary_min, salary_max, remote, department, external_id)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )
        conn.commit()
        log(f"mysql: wrote {len(rows)} raw posting(s) to raw_postings")
    except Exception as exc:
        log(f"mysql: write failed, run continues ({exc})")
    finally:
        conn.close()
