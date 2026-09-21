"""Paths and the rules that decide what may be applied to.

Two cross-package couplings live here, and they are the only ones: a read of
radar's candidate cache, and a subprocess call to engine's CLI. Neither
imports the other's code. With neither present this package degrades to
"paste the JD in yourself" and still works.
"""

from __future__ import annotations

from pathlib import Path

from .. import paths, profile

ROOT = paths.ROOT

# -- sibling packages (data + CLI only, never imports) ----------------------
# This is the isolation rule made concrete: `apply` depends on what radar and
# engine WRITE and on their command lines, not on their internals. Either can
# be rewritten without touching this package, and with neither present this
# one degrades to "paste the JD in yourself" and still works.
RADAR_CANDIDATES = paths.DATA / "radar" / "candidates.json"
RADAR_DIGESTS = paths.DATA / "radar" / "digests"

# The user's own writing: letter voice, screening answers. In profile/,
# which is git-ignored -- nobody's cover letter belongs in a repo.
LETTER_FILE = profile.path("letter.toml")
ANSWERS_FILE = profile.path("answers.toml")
# Read as data, never imported: the letter gate checks its numbers
# against every claim the user has confirmed, not just the ones this
# posting's resume happened to select.
MASTER_FILE = profile.path("master.toml")

DATA = paths.DATA / "apply"
APPLICATIONS_FILE = DATA / "applications.json"
PACKETS = ROOT / "packets"          # git-ignored; regenerable
SCRATCH = DATA / "scratch"
LOGS = paths.LOGS / "apply"

for _d in (DATA, PACKETS, SCRATCH, LOGS):
    _d.mkdir(parents=True, exist_ok=True)


def packet_dir(packet_id: str) -> Path:
    """Where one packet lives on disk, from its id.

    Packets are grouped into a folder per build date, because a flat directory
    stops being browsable somewhere around a hundred of them and the thing you
    are looking for at 9pm is almost always "the ones I built today". The
    delivery copy has always been laid out this way; the local one was not.

    The id is unchanged, and it still begins with that date, so it stays the
    thing you type into `submitted` and the thing the application log stores.
    Nothing had to be renamed to group them.

    Packets built before 2026-09-08 sat directly under `packets/`. If one is
    still there, that is where this returns, so an old id keeps resolving.
    """
    flat = PACKETS / packet_id
    if flat.is_dir():
        return flat
    return PACKETS / packet_id[:10] / packet_id

# -- the unattended on/off switch ------------------------------------------
SWITCH_FILE = ROOT / "SWITCH.apply.txt"


def is_enabled() -> bool:
    """Its own switch, not radar's -- pausing discovery is a different
    decision from pausing packet builds."""
    return paths.read_switch("SWITCH.apply.txt")


def load_env() -> None:
    """Read .env.apply, then the shared .env.

    Deliberately NOT .env.radar. The two packages share a repo now but not a
    Discord channel or a schedule, and pulling radar's webhook in here would
    make `apply` start pinging a channel nobody pointed it at. The one value
    that does fall back to radar's file is the Notion credential pair, and
    notion_sync.py asks for that explicitly.
    """
    paths.load_env(".env.apply", ".env")


# Where the finished packet is copied for actual use, next to the rest of
# the user's application material. Same shape as the Resume Engine's
# delivery, read from the same file, and skipped silently when it is unset or
# the drive isn't there.
def delivery_dir() -> Path | None:
    raw = profile.load_optional("delivery.toml").get("packets")
    return Path(raw).expanduser() if raw else None

# -- the anti-blacklist rules (plan item 6) ---------------------------------
# These are the ones that actually get someone remembered badly, so they are
# enforced by the tool rather than left to memory. Each is overridable with an
# explicit acknowledgement, because a rule you can't override gets worked
# around outside the tool, where nothing is logged.

# "cap 2-3 concurrent applications per company". Two, because the point is to
# not look like a spray. Counted against the employer that would read the
# application: on a shared board like jobs.virginia.gov that is the agency,
# not the Commonwealth -- see `models.division_in`.
MAX_OPEN_PER_COMPANY = 2

# ...and a softer ceiling across a whole shared board. Splitting the cap by
# agency is right, and it would be naive on its own: the Commonwealth runs one
# applicant system, so any agency's HR can see every application you have open
# anywhere in it. Nine at once still reads as a spray even though no single
# agency sees more than two. This warns rather than blocks, because the person
# it looks odd to is not the person you are applying to.
MAX_OPEN_PER_BOARD = 6

# "never reapply to the same req, 3-6 month cooldown on the same role".
SAME_REQ_COOLDOWN_DAYS = 365        # effectively never, for the identical req
SAME_ROLE_COOLDOWN_DAYS = 120       # ~4 months, middle of the 3-6 month range

# Follow-up cadence (plan item 10). Day 7 after applying.
FOLLOW_UP_DAYS = 7

# A JD shorter than this didn't really come down -- almost always a JS shell
# or a cookie wall rather than the posting.
MIN_JD_CHARS = 400
