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
#
# 120 -> 250 on 2026-09-19. Counted rather than guessed: the 2026-09-19 pull
# collected 2,686 postings with no body, and 321 of them cleared
# DETAIL_FETCH_MIN_SCORE. The budget bought 120 of those, and because it was
# being spent in collection order it bought the wrong 120 -- it ran out before
# reaching the Commonwealth sitemap, which is 304 of the 321. 250 covers the
# whole 40+ band several times over; what it still cuts is the bottom of the
# 30s, which is below MIN_SCORE_TO_REPORT and only reaches the board at all if
# reading the body rescues it. Each call is about a second, so this is roughly
# two minutes on a run that takes thirteen.
MAX_DETAIL_FETCHES = 250

# ...and no more than this many on any one host. Sorting the budget by score
# was the right fix and it created this problem: the Commonwealth of Virginia
# sitemap is 304 of the 321 postings that want a body, so best-first hands it
# essentially the whole budget. That host answers one request per five seconds
# without complaint and starts refusing above that, which would be twenty
# minutes on a run that takes thirteen, with every other board waiting behind
# it. 60 is five minutes of Commonwealth, taken best-first, and the backlog
# comes down over successive runs.
MAX_DETAIL_FETCHES_PER_HOST = 60

# Snippet postings whose body is fetched from the real posting per run, for
# `sources.fill_partials`. Separate from the budget above so that a pull heavy
# in aggregator results cannot starve the ATS boards, which are the channel
# worth being early on. Each one is a single GET of a page a person could have
# opened themselves by clicking the link in the digest.
MAX_PARTIAL_FETCHES = 60

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


# Seeding is the same discovery, run against a metro instead of against a run.
# `learn` waits for a company to post something good; `seed` goes looking for
# every employer the area has, before any of them posts. It is a one-off sweep
# a user runs by hand, so its numbers are an order of magnitude larger than
# learn's -- and it costs nothing on the days it is not run. See radar/seed.py.

# Names per sweep. A metro's worth of employers, not a day's worth.
SEED_MAX_PER_SWEEP = 300

# Calls for the whole sweep. Roughly 8 per company on average across the six
# simple ATSes; a Workday guess can want 37 on its own. No API key limits this
# -- these are public board endpoints -- so the cap exists to bound the time.
SEED_PROBE_BUDGET = 4000

# A seeded company has posted nothing we have read, so it has earned no
# priority. Tier 3 sits below both the curated list and learn's finds.
SEED_TIER = 3

# Calls a single company's website may cost before resolution gives up. The
# walk is a homepage plus up to three careers links plus two hops from each,
# and every measured hit landed within three. Ten leaves room for redirects
# without letting one sprawling corporate site eat a sweep. See radar/resolve.py.
RESOLVE_PROBE_CALLS = 10

# How many times a company must have turned up locally before its name is
# worth a probe. One sighting is often a typo, an aggregator's mangling of a
# real name, or a one-off contract listing.
SEED_MIN_SIGHTINGS = 2
