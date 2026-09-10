"""Paths and run tuning.

No .env and no network anywhere in this sub-project -- the engine reads local
files, writes local files, and never talks to a job board. That is a deliberate
boundary: discovery is Job Radar's job, and keeping this side offline means a
tailoring run can't be rate-limited, blocked, or attributed to anyone.
"""

from __future__ import annotations

from pathlib import Path

from .. import paths, profile

ROOT = paths.ROOT

# The two files that are the user's own writing. They live in profile/, not
# in the package, and profile/ is git-ignored -- a resume is not source code.
MASTER_FILE = profile.path("master.toml")
VOCAB_FILE = profile.path("vocabulary.toml")

DATA = paths.DATA / "engine"
JD_STORE = DATA / "jds"          # the growing JD corpus behind the gap report
REPORTS = DATA / "reports"
OUT = ROOT / "out"               # generated packets (git-ignored)

for _d in (DATA, JD_STORE, REPORTS, OUT):
    _d.mkdir(parents=True, exist_ok=True)

# Where a finished resume gets copied for actual use, so the engine delivers
# into wherever the user already keeps their application material rather than
# asking them to remember a second location.
#
# Was an absolute path to one laptop's D: drive. Now it is a line in
# profile/delivery.toml, and None -- no file, no key, or a path that is not
# there -- means "leave it in out/", which is the right answer for a fresh
# clone.
def delivery_dir() -> Path | None:
    raw = profile.load_optional("delivery.toml").get("resumes")
    return Path(raw).expanduser() if raw else None

# -- Section weights -------------------------------------------------------
# A term in "Basic Qualifications" is a gate; the same term under "Preferred"
# is a wish. Job Radar learned this the hard way when a 5-year requirement hid
# next to "at least 1 year of Python" in Preferred and the posting scored as a
# 1-year job. Same lesson, independent implementation.
WEIGHT_REQUIRED = 3
WEIGHT_RESPONSIBILITIES = 2
WEIGHT_PREFERRED = 1
WEIGHT_OTHER = 1

# Soft skills are counted for the gap report but never drive bullet selection:
# every candidate claims "attention to detail", so matching on it would pick
# bullets at random.
SELECTION_KINDS = ("tool", "method")

# -- ATS simulator ---------------------------------------------------------
# Penalties are in points off 100. Ordered by how badly each one actually
# breaks a parse.
ATS_PENALTIES = {
    "multi_column": 30,
    "no_email": 25,
    "images": 20,
    "no_phone": 15,
    "over_pages": 15,
    "missing_section": 10,
    "no_linkedin": 5,
    "no_location": 5,
    "type3_font": 10,
    "bad_glyphs": 10,
}
