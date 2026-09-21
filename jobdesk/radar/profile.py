"""The user's targeting rules, read from `profile/targeting.toml`.

This module used to BE the profile: 500 lines of your tier lists, your
Richmond commute ring, your salary floor, your stack. Every one of those is now
a line in a TOML file the user owns, and this is the adapter that hands them
to `score.py` under the names it already uses.

`score.py` is untouched by the move, which was the point. It reads
`profile.TIER_1_TITLES` exactly as before; where that list comes from is not
its business.

Attribute access is lazy on purpose. The profile directory is chosen at call
time, not import time, so a test can point `JOBDESK_PROFILE` at a fixture and
the next lookup follows it. The underlying read is cached, so the cost after
the first hit is a dict lookup.
"""

from __future__ import annotations

from typing import Any

from .. import profile as _profile

FILE = "targeting.toml"

# Module attribute -> key in targeting.toml. Everything here is a plain
# passthrough except the two shaped below.
_KEYS = (
    "TIER_1_TITLES", "TIER_2_TITLES", "TIER_3_TITLES", "ADJACENT_TITLES",
    "DOMAIN_MODIFIERS", "CLERICAL_MARKERS", "OFF_FIELD_MARKERS",
    "VOLUNTEER_MARKERS", "ENTRY_LEVEL_MARKERS", "TITLE_DISQUALIFIERS",
    "HARD_DISQUALIFIERS", "STAFFING_AGENCIES", "AGENCY_PHRASES",
    "COMP_STRUCTURE_BLOCKS", "HOME_METRO", "LOCAL_TERMS", "STATE_TERMS",
    "REMOTE_TERMS", "HYBRID_TERMS", "REMOTE_EXCLUSIONS", "NON_US_MARKERS",
    "CORE_SKILLS", "SUPPORTING_SKILLS", "FOREIGN_SKILLS",
    "YEARS_COMFORTABLE", "MAX_YEARS_STRETCH", "EQUIVALENCY_CEILING",
    "EQUIVALENCY_PHRASES", "NO_EXPERIENCE_PHRASES", "DEGREE_FIELDS",
    "BACHELORS_TERMS", "ADVANCED_DEGREE_TERMS", "SALARY_FLOOR",
    "SALARY_TARGET", "FRESH_DAYS", "STALE_DAYS",
)

# The two that arrive as arrays of tables and leave as the tuples score.py
# unpacks. Shape stays where it always was rather than leaking into the
# scoring loop.
_SHAPED = {
    "FUNCTION_FAMILIES": ("function_families", ("points", "label", "terms")),
    "DEALBREAKER_SIGNALS": ("dealbreaker_signals", ("label", "points",
                                                    "phrases")),
}


# Optional keys. A profile that omits one gets the default, and the source
# module derives something workable from the tier lists instead. They exist
# because the derived guess is only ever a guess: what a keyword board should
# be asked for is a judgment about a market, and a user who knows their market
# should be able to say so without editing Python.
_OPTIONAL = {
    # Optional and defaulting to nothing on purpose. A profile written before
    # this key existed keeps scoring exactly as it did except for the bare
    # family noun, and no bonus at all beats a bonus aimed at somebody else's
    # profession.
    "FAMILY_TITLES": ("family_titles", []),
    "SEARCH_QUERIES": ("search_queries", []),
    "WORKDAY_SEARCH_TERMS": ("workday_search_terms", []),
    "SYNONYMS_ENABLED": ("synonyms_enabled", True),
    "SYNONYM_DISCOUNT": ("synonym_discount", 6),
    "SEARCH_SYNONYM_LIMIT": ("search_synonym_limit", 6),
}


def _data() -> dict[str, Any]:
    return _profile.load(FILE)


def synonyms() -> dict[str, list[str]]:
    """Canonical term -> the other words a posting might use for it.

    Flat dict rather than the array of tables it is written as, because every
    reader wants the same lookup: "does this text say something that means
    <term>". Returned empty when `synonyms_enabled` is false, so switching the
    experiment off needs one line in the profile and no code path of its own.
    """
    data = _data()
    if not data.get("synonyms_enabled", True):
        return {}
    out: dict[str, list[str]] = {}
    for row in data.get("synonym", []):
        term = str(row.get("for", "")).strip().lower()
        if not term:
            continue
        also = [str(a).strip().lower() for a in row.get("also", [])]
        out.setdefault(term, []).extend(a for a in also if a and a != term)
    return out


def __getattr__(name: str) -> Any:          # PEP 562
    if name in _SHAPED:
        key, fields = _SHAPED[name]
        return [tuple(row[f] for f in fields) for row in _data().get(key, [])]
    if name in _OPTIONAL:
        key, default = _OPTIONAL[name]
        return _data().get(key, default)
    if name in _KEYS:
        key = name.lower()
        data = _data()
        if key not in data:
            raise AttributeError(
                f"{_profile.path(FILE)} has no '{key}'. Copy the missing key "
                f"out of profile.example/{FILE}.")
        return data[key]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted([*_KEYS, *_SHAPED, *_OPTIONAL, "FILE", "synonyms"])
