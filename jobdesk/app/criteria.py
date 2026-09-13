"""What the Criteria tab shows, in the words it shows it in.

The tab used to be sixteen boxes labelled with their TOML key names --
`max_years_stretch`, `hybrid_terms` -- in the order the dict happened to be
in. Accurate, and useless to anyone who had not read `radar/score.py`. So
each setting now carries a plain label, the number of points it is actually
worth, and a group it belongs to, and the page draws from this list.

The point values in the help text are the ones `score.py` uses. If a weight
changes there, the sentence here that quotes it has to change with it;
`tests/test_app.py` checks the tier numbers so the two cannot drift silently.

Validation lives here too, because a panel that saves `fresh_days = 40` and
`stale_days = 30` has saved a contradiction the scorer will not complain
about. It will just score strangely, which is worse.
"""

from __future__ import annotations

# kind: list = one phrase per line; weights = "skill: points" per line;
#       money / years / days / int = a whole number; text = one line.
SECTIONS = [
    {
        "id": "titles",
        "title": "The jobs you want",
        "intro": (
            "The job title is the biggest part of a score. JobDesk looks for "
            "each line below inside the title, so keep them short: \"data "
            "analyst\" also matches \"Senior Data Analyst II\"."),
        "outro": (
            "A title that matches none of these, and that JobDesk does not "
            "otherwise recognise as your line of work, cannot score above 40."),
        "fields": [
            ("tier_1_titles", "list", "Jobs you would take today",
             "Worth 35 points."),
            ("tier_2_titles", "list", "Jobs you could land with the right pitch",
             "Worth 24 points."),
            ("tier_3_titles", "list", "Stretch jobs",
             "Worth 14 points."),
        ],
    },
    {
        "id": "place",
        "title": "Where you can work",
        "intro": "",
        "outro": "",
        "fields": [
            ("home_metro", "text", "Your area",
             "The name shown on local jobs, like \"Richmond, VA\". The town "
             "list below is what actually decides what counts as local."),
            ("local_terms", "list", "Towns within commuting distance",
             "Worth 25 points when the job's location names one. On-site jobs "
             "anywhere else are hidden, because JobDesk assumes you are not "
             "moving."),
            ("remote_terms", "list", "Words that mean remote",
             "Worth 22 points when the job uses one."),
            ("hybrid_terms", "list", "Words that mean partly in the office",
             "A remote job that also uses one of these loses 8 points."),
        ],
    },
    {
        "id": "skills",
        "title": "Your skills",
        "intro": (
            "One skill per line, with its points after a colon, like "
            "\"sql: 6\". A skill earns its points when the job description "
            "mentions it."),
        "outro": "Skills add up to 25 points at most, however many a job mentions.",
        "fields": [
            ("core_skills", "weights", "Your main skills",
             "Leave the number off and it is worth 4."),
            ("supporting_skills", "weights", "Skills you also have",
             "Leave the number off and it is worth 2."),
        ],
    },
    {
        "id": "pay",
        "title": "Pay",
        "intro": (
            "Most postings do not list pay at all, and those are not marked "
            "down for it. A posting that does list pay starts with 3 points, "
            "because a company that shows its pay up front usually has the "
            "job funded."),
        "outro": "",
        "fields": [
            ("salary_floor", "money", "The least you would accept, per year",
             "A job paying less than this loses 10 points."),
            ("salary_target", "money", "What you are aiming for, per year",
             "A job paying this much or more gets 8 more. In between gets 3 more."),
        ],
    },
    {
        "id": "experience",
        "title": "Experience",
        "intro": "",
        "outro": "",
        "fields": [
            ("years_comfortable", "years", "Years of experience you have",
             "A job asking for this many years or fewer gets 15 points."),
            ("max_years_stretch", "years", "The most years you would still apply for",
             "Up to this counts as a stretch and gets 4 points. A job asking "
             "for more is hidden."),
        ],
    },
    {
        "id": "freshness",
        "title": "How new the posting is",
        "intro": (
            "Recruiters read applications in the order they arrive, so an "
            "early one is worth more than a perfect one sent late."),
        "outro": "",
        "fields": [
            ("fresh_days", "days", "Counts as brand new for",
             "Posted within this many days: 10 points. Within two weeks: 5."),
            ("stale_days", "days", "Counts as old after",
             "Older than this loses 10 points. Older than 90 days loses 18, "
             "because a posting that old is often never filled."),
        ],
    },
    {
        "id": "no",
        "title": "Automatic no",
        "intro": "",
        "outro": "",
        "fields": [
            ("hard_disqualifiers", "list", "Phrases that rule a job out",
             "If the description contains any of these, the job scores 0 and "
             "is hidden. Use it for things you cannot get past, like "
             "\"security clearance\" or \"cdl required\"."),
        ],
    },
]

KINDS: dict[str, str] = {key: kind for s in SECTIONS for key, kind, *_ in s["fields"]}

DEFAULT_WEIGHT = {"core_skills": 4, "supporting_skills": 2}
_NUMBER_KINDS = ("money", "years", "days", "int")


class Invalid(ValueError):
    """A value the scorer would accept and then misuse. Says which and why."""


def form(values: dict) -> list[dict]:
    """The sections, each field carrying its current value from the file."""
    out = []
    for s in SECTIONS:
        fields = []
        for key, kind, label, help_text in s["fields"]:
            value = values.get(key)
            if kind == "weights" and not isinstance(value, dict):
                value = {}
            elif kind == "list" and not isinstance(value, list):
                value = []
            fields.append({"key": key, "kind": kind, "label": label,
                           "help": help_text, "value": value})
        out.append({"id": s["id"], "title": s["title"], "intro": s["intro"],
                    "outro": s["outro"], "fields": fields})
    return out


def clean(changes: dict) -> tuple[dict[str, object], dict[str, dict]]:
    """Validate a save. Returns (top-level keys, whole tables).

    Top-level keys go through `tomlpatch.patch`; the two skill tables go
    through `tomlpatch.patch_table`, because their keys are the data.
    """
    top: dict[str, object] = {}
    tables: dict[str, dict] = {}
    for key, value in changes.items():
        kind = KINDS.get(key)
        if kind is None:
            raise Invalid(f"{key} is not editable from this panel. "
                          f"Open targeting.toml to change it.")
        if kind == "list":
            if not isinstance(value, list):
                raise Invalid(f"{key} has to be a list")
            top[key] = _dedupe(" ".join(str(v).lower().split()) for v in value)
        elif kind == "weights":
            tables[key] = _weights(key, value)
        elif kind in _NUMBER_KINDS:
            top[key] = _number(key, value)
        else:
            top[key] = " ".join(str(value).split())

    _consistent(top)
    return top, tables


def _dedupe(items) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


def _number(key: str, value) -> int:
    raw = str(value if value is not None else "").replace(",", "").replace("$", "").strip()
    try:
        n = int(float(raw)) if raw else 0
    except ValueError:
        raise Invalid(f"{_label(key)} has to be a whole number, not {value!r}")
    if n < 0:
        raise Invalid(f"{_label(key)} cannot be negative")
    return n


def _weights(key: str, value) -> dict[str, int]:
    """A skill table, from the object the page sends: {"sql": 6}."""
    if not isinstance(value, dict):
        raise Invalid(f"{key} has to be a set of skills with points")
    out: dict[str, int] = {}
    for name, points in value.items():
        name = " ".join(str(name).lower().split())
        if not name:
            continue
        if points in (None, ""):
            points = DEFAULT_WEIGHT[key]
        try:
            n = int(points)
        except (TypeError, ValueError):
            raise Invalid(f"\"{name}\" needs a whole number of points, not {points!r}")
        if not 0 <= n <= 25:
            raise Invalid(f"\"{name}\" is worth {n} points. Keep each skill "
                          f"between 0 and 25; the whole group tops out at 25.")
        out[name] = n
    return out


def _consistent(top: dict[str, object]) -> None:
    pairs = (
        ("fresh_days", "stale_days",
         "\"Counts as brand new\" has to be fewer days than \"counts as old\"."),
        ("years_comfortable", "max_years_stretch",
         "The most years you would apply for cannot be less than the years "
         "you have."),
        ("salary_floor", "salary_target",
         "The pay you are aiming for cannot be below the least you would accept."),
    )
    for low, high, message in pairs:
        a, b = top.get(low), top.get(high)
        if isinstance(a, int) and isinstance(b, int) and b and a > b:
            raise Invalid(message)
    if top.get("fresh_days") == top.get("stale_days") and "fresh_days" in top:
        raise Invalid(pairs[0][2])


def _label(key: str) -> str:
    for s in SECTIONS:
        for k, _kind, label, *_ in s["fields"]:
            if k == key:
                return label
    return key
