"""Which employers to watch, read from `profile/employers.toml`.

This module used to hold the list itself: sixty-five Richmond and remote
employers with their verified ATS coordinates, plus four lists of names that
had been checked and rejected. All of it was one person's search written as
Python, and a user in Denver had no way to change it without editing a module.

The list moved to the profile. What stayed here is the part that generalizes:
nothing at all, as it turns out, which is why this file is now eight lines of
code. How to READ a Workday feed is knowledge about Workday and lives in
`sources/ats.py`; which Workday to read is the user's answer and lives in
their profile.

The names that had been checked and rejected became a note in the author's
own docs. Nothing imported them, and a negative result is a note, not a
data structure.
"""

from __future__ import annotations

from .. import profile


def active(tiers: tuple[int, ...] = (1, 2)) -> list[dict]:
    """Every watched employer in the given tiers.

    Tier 1 is a top target, tier 2 is worth watching. An entry with no tier
    counts as 2, so a user who never fills the field in still gets their
    companies scanned.
    """
    entries = profile.load("employers.toml").get("employer", [])
    return [e for e in entries if e.get("tier", 2) in tiers]
