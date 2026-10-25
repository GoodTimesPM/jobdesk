"""The Settings screen: how the app looks and what the Jobs tab opens with.

Kept on the server in `data/settings.json` rather than in the browser's
localStorage, because JobDesk is one app opened from two places. A default
filter set on the desktop should be the default on the phone too, and a
phone's localStorage is a different store from the desktop window's.

Nothing in here is personal, but the file is git-ignored anyway: it is one
person's taste, and a fresh clone should start from the defaults below.

Unknown keys are dropped and bad values fall back to the default, on read and
on write. A settings file is the kind of thing people edit by hand, and a
typo in it should cost that one setting rather than stop the app opening.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from .. import paths, profile

FILE = paths.DATA / "settings.json"

DEFAULTS: dict[str, object] = {
    # Jobs tab
    "jobs_sort": "newest",      # newest | score
    "score_min": 45,
    "score_max": 100,
    "hide_applied": True,
    "hide_prepared": False,
    "remote_only": False,
    "starred_only": False,
    "has_pay": False,           # only postings that print a salary
    "market_up": False,         # only postings the market arrow points up on
    "tier": "",                 # "" for every tier, or one letter
    "posted_within": "any",     # any, or a number of days as a string
    "source": "",               # "" for every source, or one source's name
    # Where the app opens
    "start_tab": "jobs",
    # Appearance. `theme` is light or dark, `scheme` is which colors, and
    # they are separate because every scheme has both. Picking "Forest" should
    # not also decide whether the window is bright at 11pm.
    "theme": "system",          # system | light | dark
    "scheme": "mailroom",       # which palette
    "font": "system",           # which typeface
    "text_size": 100,           # percent
    "density": "normal",        # compact | normal | roomy
    "accent": "",               # "" for the scheme's own, or #rrggbb
    # Your own colors, built the way FitNext builds them: a background and an
    # accent, each as hue, saturation and lightness, with every other color
    # worked out from those two. `fade` swaps the background for two colors
    # the page fades between. `over` holds any color picked by hand in
    # Fine-tune, which wins over the worked-out one. While `on`, this
    # replaces the scheme. `id` says which saved theme it came from.
    "custom": {"on": False, "id": "", "bg": [216, 33, 97], "ac": [221, 83, 53],
               "fade": False, "a": [256, 45, 14], "b": [190, 60, 22],
               "fade_style": "diagonal", "over": {}},
    # Custom colors saved under a name, newest first.
    "themes": [],
    # The Jobs table after star, score and title, in order. A column left
    # out is hidden.
    "job_columns": ["company", "location", "age_days", "market", "salary",
                    "source"],
    # Named filter sets for the Jobs tab: [{"name": ..., "filters": {...}}].
    "views": [],
}

JOB_COLUMNS = ("company", "location", "age_days", "market", "salary", "source")
VIEW_FILTERS = ("search", "score_min", "score_max", "hide_applied",
                "hide_prepared", "remote_only", "starred_only", "jobs_sort",
                "has_pay", "market_up", "tier", "posted_within", "source")
MAX_VIEWS = 12
MAX_THEMES = 24
_HEX = re.compile(r"#[0-9a-fA-F]{6}")

# What a custom palette is made of. The colors in `over` are the page's own
# token names, so Fine-tune can name any of them and nothing else.
FADE_STYLES = ("diagonal", "vertical", "horizontal", "glow")
CUSTOM_TOKENS = ("ground", "card", "line", "line-soft", "ink", "ink-soft",
                 "ink-faint", "accent", "accent-ink", "good", "warn", "bad")
_PALETTE_KEYS = {"bg", "ac", "fade", "a", "b", "fade_style", "over"}

# One mode each: light or dark is part of the scheme. Kept in step with the
# `[data-scheme]` blocks in app.css and the Colors menu by test_settings.
FIXED_SCHEMES = (
    # Light
    "glacier", "bloom", "ember", "lagoon", "grape", "rose", "sandstone",
    "graphite", "citrus", "mint", "sky", "peach", "lavender", "paper",
    # Dark
    "polar-night", "sage-dusk", "aurora", "crimson", "carbon", "fjord",
    "evergreen", "espresso", "neon", "midnight",
    # The fades
    "fade-glass", "fade-aurora", "fade-tide", "fade-ember", "fade-dusk",
    "fade-moss", "fade-mist", "fade-peach",
)

# Values that were valid once and now mean something else. Slate was the
# default until Mailroom replaced it, and a settings file only says "slate"
# because every save writes the whole file, defaults included.
_RENAMED = {("scheme", "slate"): "mailroom"}

_CHOICES = {
    "jobs_sort": ("newest", "score"),
    "tier": ("", "A", "B", "C", "D"),
    "posted_within": ("any", "0", "3", "7", "14"),
    "start_tab": ("jobs", "applied", "archive", "criteria", "console"),
    "theme": ("system", "light", "dark"),
    # Light-and-dark schemes first, then the one-mode themes and the fades,
    # which Settings shows in groups of their own.
    "scheme": ("mailroom", "ocean", "forest", "plum", "sand", "contrast")
              + FIXED_SCHEMES,
    "font": ("system", "humanist", "narrow", "serif", "mono"),
    "density": ("compact", "normal", "roomy"),
}

# A range rather than a list of sizes, so that the four the page offered
# before -- 90, 100, 115, 130 -- stay valid for anyone whose settings file
# already says one of them, and the page can offer a different set of steps
# tomorrow without invalidating today's.
_RANGES = {"score_min": (0, 100), "score_max": (0, 100),
           "text_size": (80, 160)}


def _view(value) -> bool:
    if not isinstance(value, dict) or set(value) != {"name", "filters"}:
        return False
    name, filters = value["name"], value["filters"]
    if not isinstance(name, str) or not 0 < len(name.strip()) <= 40:
        return False
    if not isinstance(filters, dict) or not set(filters) <= set(VIEW_FILTERS):
        return False
    return all(isinstance(v, str) and len(v) <= 200 if k == "search"
               else _valid(k, v) for k, v in filters.items())


def _hsl(value) -> bool:
    return isinstance(value, list) and len(value) == 3 \
        and all(isinstance(n, int) and not isinstance(n, bool) for n in value) \
        and 0 <= value[0] <= 359 and 0 <= value[1] <= 100 and 0 <= value[2] <= 100


def _palette(value: dict) -> bool:
    over = value.get("over")
    return _hsl(value.get("bg")) and _hsl(value.get("ac")) \
        and _hsl(value.get("a")) and _hsl(value.get("b")) \
        and isinstance(value.get("fade"), bool) \
        and value.get("fade_style") in FADE_STYLES \
        and isinstance(over, dict) and set(over) <= set(CUSTOM_TOKENS) \
        and all(isinstance(v, str) and bool(_HEX.fullmatch(v)) for v in over.values())


def _custom(value) -> bool:
    return isinstance(value, dict) and set(value) == _PALETTE_KEYS | {"on", "id"} \
        and isinstance(value["on"], bool) and isinstance(value["id"], str) \
        and len(value["id"]) <= 40 and _palette(value)


def _theme(value) -> bool:
    if not isinstance(value, dict) or set(value) != _PALETTE_KEYS | {"id", "name"}:
        return False
    return isinstance(value["id"], str) and 0 < len(value["id"]) <= 40 \
        and isinstance(value["name"], str) and 0 < len(value["name"].strip()) <= 40 \
        and _palette(value)


def _valid(key: str, value) -> bool:
    default = DEFAULTS[key]
    if key == "accent":
        return value == "" or (isinstance(value, str) and bool(_HEX.fullmatch(value)))
    # A source is whatever the radar calls one, so any short name will do;
    # one that no posting has any more just matches nothing.
    if key == "source":
        return isinstance(value, str) and len(value) <= 60
    if key == "job_columns":
        return isinstance(value, list) and len(set(value)) == len(value) \
            and all(v in JOB_COLUMNS for v in value)
    if key == "custom":
        return _custom(value)
    if key == "themes":
        return isinstance(value, list) and len(value) <= MAX_THEMES \
            and all(_theme(v) for v in value) \
            and len({v["id"] for v in value}) == len(value)
    if key == "views":
        return isinstance(value, list) and len(value) <= MAX_VIEWS \
            and all(_view(v) for v in value) \
            and len({v["name"].strip().lower() for v in value}) == len(value)
    if key in _CHOICES:
        return value in _CHOICES[key]
    if isinstance(default, bool):
        return isinstance(value, bool)
    if key in _RANGES:
        lo, hi = _RANGES[key]
        return isinstance(value, int) and not isinstance(value, bool) \
            and lo <= value <= hi
    return False


def load() -> dict[str, object]:
    """The saved settings over the defaults. Never raises."""
    try:
        saved = json.loads(FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    out = copy.deepcopy(DEFAULTS)
    for key, value in saved.items():
        value = _RENAMED.get((key, value), value) if isinstance(value, str) else value
        if key in DEFAULTS and _valid(key, value):
            out[key] = value
    return out


class Invalid(ValueError):
    """A setting the page should not have been able to send. Says which."""


def save(changes: dict) -> dict[str, object]:
    """Merge `changes` into the saved settings and return the result."""
    if not isinstance(changes, dict):
        raise Invalid("settings have to arrive as an object")
    current = load()
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise Invalid(f"{key} is not a setting")
        if isinstance(value, str):
            value = _RENAMED.get((key, value), value)
        if key in _RANGES and isinstance(value, str) and value.strip().isdigit():
            value = int(value)
        if not _valid(key, value):
            raise Invalid(f"{value!r} is not a valid value for {key}")
        current[key] = value
    if current["score_min"] > current["score_max"]:
        current["score_min"], current["score_max"] = \
            current["score_max"], current["score_min"]
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    tmp.replace(FILE)
    return current


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

def folders() -> dict:
    """Where finished work lands, for the Files section of Settings.

    Two kinds of folder. JobDesk always builds into its own `packets/` and
    `out/`, which is what the app reads back. The delivery folders in
    `profile/delivery.toml` are an extra copy wherever the user keeps job
    search files, and they are the only part a user would want to change.
    """
    from ..apply import config as apply_config
    from ..engine import config as engine_config

    try:
        where = profile.directory()
        example = profile.is_example()
    except profile.ProfileError:
        where, example = None, True
    delivery = {} if example else profile.load_optional("delivery.toml")
    return {
        "packets_builtin": str(apply_config.PACKETS),
        "resumes_builtin": str(engine_config.OUT),
        "packets": str(delivery.get("packets") or ""),
        "resumes": str(delivery.get("resumes") or ""),
        "profile_dir": str(where) if where else "",
        "editable": not example,
    }


def check_folder(raw: str) -> str:
    """A delivery path the user typed, checked. Blank is allowed and means off.

    The folder itself is created if it is missing, since "copy them to
    D:\\Job Search\\Applications" is a reasonable thing to ask for before that
    folder exists. Its parent has to exist, though: a typo in the drive letter
    should be an error here rather than a quiet `mkdir` somewhere odd.
    """
    raw = raw.strip().strip('"').strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise Invalid(f"{raw} is not a full path. Start it with a drive, "
                      f"like D:\\Job Search\\Applications.")
    if not path.parent.exists():
        raise Invalid(f"{path.parent} does not exist, so {path.name} cannot "
                      f"be made inside it.")
    if path.exists() and not path.is_dir():
        raise Invalid(f"{path} is a file, not a folder.")
    try:
        path.mkdir(exist_ok=True)
    except OSError as exc:
        raise Invalid(f"{path} could not be created: {exc}")
    return str(path)


def set_delivery(changes: dict) -> dict:
    """Point the resume and packet copies somewhere else, or turn them off.

    Edits `profile/delivery.toml` line by line, the same way the wizard wrote
    it: a path uncomments (or replaces) its `key = ...` line, a blank comments
    it back out. The explanation above each line stays put.
    """
    import re

    from . import tomlpatch

    if profile.is_example():
        raise Invalid("Finish setup first. The example profile's folders "
                      "are not yours to change.")
    target = profile.REAL / "delivery.toml"
    if target.exists():
        text = target.read_text(encoding="utf-8")
    else:
        text = (profile.EXAMPLE / "delivery.toml").read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    text = text.replace("\r\n", "\n")

    for key in ("resumes", "packets"):
        if key not in changes:
            continue
        value = check_folder(str(changes[key] or ""))
        live = re.compile(r"^" + key + r"\s*=.*$", re.M)
        dormant = re.compile(r"^#\s*" + key + r"\s*=.*$", re.M)
        if value:
            line = f"{key} = {tomlpatch.dump_value(value)}"
            if live.search(text):
                text = live.sub(lambda _m: line, text, count=1)
            elif dormant.search(text):
                text = dormant.sub(lambda _m: line, text, count=1)
            else:
                text = text.rstrip("\n") + "\n" + line + "\n"
        else:
            text = live.sub(lambda m: "# " + m.group(0), text)

    target.write_text(text.replace("\n", newline), encoding="utf-8")
    profile.forget()
    return folders()
