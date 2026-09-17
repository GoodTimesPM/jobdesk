"""Paths, the kill switch, and .env loading."""

from __future__ import annotations

import os
from pathlib import Path

from .. import paths

ROOT = paths.ROOT
DATA = paths.DATA / "radar"
LOGS = paths.LOGS / "radar"
DIGESTS = DATA / "digests"
SEEN_FILE = DATA / "seen.json"
# The working set Assisted Apply reads: everything currently above the
# reporting threshold, WITH its JD body, so building an application packet
# doesn't re-fetch a posting this run already downloaded. Git-ignored and
# rewritten every run -- the kept series is `snapshots_dir()`.
CANDIDATES = DATA / "candidates.json"
SWITCH_FILE = ROOT / "SWITCH.radar.txt"


def snapshots_dir() -> Path:
    """Where the daily market snapshot is written.

    One file per run, no JD bodies, no scoring prose: the structured record
    of what the market looked like that morning. It is the only output here
    meant to be read by something other than JobDesk, which is why it is the
    only path that moves.

    `JOBDESK_SNAPSHOTS` sends it somewhere else. That is for the case where
    another project analyses the series: the dataset then sits next to the
    work that consumes it rather than inside the app that writes it.
    Resolved when it is called rather than when this module is imported, so
    a value in `.env.radar` counts.
    """
    override = os.environ.get("JOBDESK_SNAPSHOTS", "").strip()
    path = Path(override) if override else DATA / "snapshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


for _d in (DATA, LOGS, DIGESTS):
    _d.mkdir(parents=True, exist_ok=True)


def load_env() -> None:
    """Read .env.radar, then the shared .env."""
    paths.load_env(".env.radar", ".env")


def is_enabled() -> bool:
    return paths.read_switch("SWITCH.radar.txt")


# -- Run tuning ------------------------------------------------------------

# Postings below this score never reach Notion or the digest. C-tier and up.
MIN_SCORE_TO_REPORT = 45

# Fetching a Workday JD body costs one call each, so only the postings whose
# title already scored well get one. Below this, the title alone decides.
DETAIL_FETCH_MIN_SCORE = 30

# Cap on Workday detail calls per run -- a hard stop on runaway request counts.
# 60 -> 120 on 2026-07-30: the ATS scale-up took Workday boards from 5 to 16,
# so the old cap left ~4 detail fetches per company on average.
MAX_DETAIL_FETCHES = 120

# How many days a posting stays in seen.json before being forgotten.
SEEN_RETENTION_DAYS = 120

# -- Learning new employers ------------------------------------------------
#
# The watch list is hand-typed, and a run that surfaces 400 postings from 200
# companies throws most of them away. These knobs decide how much of that gets
# turned back into employers to check directly tomorrow. See radar/learn.py.

LEARN_ENABLED = True

# A company earns a probe on its BEST posting of the run. 75 is comfortably
# above the reporting floor of 45: a B+ posting, not merely one worth reading.
LEARN_MIN_SCORE = 75

# Per run, so the list grows by a handful a day rather than in one flood that
# nobody looks at. Probing is also the slowest thing the radar does.
LEARN_MAX_NEW_PER_RUN = 5

# Hard cap on HTTP calls spent discovering, across the whole run. A single
# Workday probe alone can want 37 (7 datacenters + 30 site guesses).
LEARN_PROBE_BUDGET = 120

# A company with no public board found is left alone this long. Companies do
# migrate ATS, so it is a wait, not a blacklist.
LEARN_RETRY_DAYS = 30

# Learned employers are checked, but after the curated ones. Tier 2 is the
# same rung the hand-added second-string companies sit on.
LEARN_TIER = 2

# Machine-owned, rewritten every run, git-ignored with the rest of data/.
LEARNED_EMPLOYERS = DATA / "employers.learned.toml"
