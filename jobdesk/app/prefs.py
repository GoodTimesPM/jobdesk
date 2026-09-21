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

import json
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
    # Where the app opens
    "start_tab": "jobs",
    # Appearance. `theme` is light or dark, `scheme` is which colours, and
    # they are separate because every scheme has both. Picking "Forest" should
    # not also decide whether the window is bright at 11pm.
    "theme": "system",          # system | light | dark
    "scheme": "slate",          # which palette
    "font": "system",           # which typeface
    "text_size": 100,           # percent
    "density": "normal",        # compact | normal | roomy
}

_CHOICES = {
    "jobs_sort": ("newest", "score"),
    "start_tab": ("jobs", "applied", "archive", "criteria", "console"),
    "theme": ("system", "light", "dark"),
    "scheme": ("slate", "ocean", "forest", "plum", "sand", "contrast"),
    "font": ("system", "humanist", "narrow", "serif", "mono"),
    "density": ("compact", "normal", "roomy"),
}

# A range rather than a list of sizes, so that the four the page offered
# before -- 90, 100, 115, 130 -- stay valid for anyone whose settings file
# already says one of them, and the page can offer a different set of steps
# tomorrow without invalidating today's.
_RANGES = {"score_min": (0, 100), "score_max": (0, 100),
           "text_size": (80, 160)}


def _valid(key: str, value) -> bool:
    default = DEFAULTS[key]
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
    out = dict(DEFAULTS)
    for key, value in saved.items():
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
    profile._read.cache_clear()
    return folders()
