"""Where the user's own answers live, and how the packages read them.

Everything JobDesk knows about a particular person -- which titles to chase,
which city, which employers, the resume itself, the letter voice, the salary
floor -- is data in a `profile/` directory, not constants in a module. Before
this existed, `radar/profile.py` was 500 lines of you, `companies.py` was
65 Richmond employers, and two delivery paths were absolute strings pointing
at one laptop's D: drive. None of that is code, and all of it was.

Two directories, and the difference between them is the whole design:

    profile/          your real answers. Git-ignored, never committed.
    profile.example/  a fictional candidate. Committed, and the fixture the
                      test suite runs against.

`profile/` wins when it exists. Otherwise the example loads, which means a
fresh clone runs end to end before the user has typed anything -- and it
means the example cannot quietly rot, because the tests are using it.

Set JOBDESK_PROFILE to point somewhere else entirely (an absolute path) to
run against a third profile without touching either.

Nothing here is Claude-shaped. These are TOML files a person edits in any
text editor, and step 3's setup wizard just writes the same files from a
form.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import paths

REAL = paths.ROOT / "profile"
EXAMPLE = paths.ROOT / "profile.example"

# The files a complete profile has. `delivery.toml` is optional: without it
# nothing is copied off the repo, which is the right default for a stranger.
REQUIRED = ("targeting.toml", "employers.toml", "master.toml",
            "vocabulary.toml", "letter.toml", "answers.toml")


class ProfileError(RuntimeError):
    """A profile is missing or unreadable. Raised with what to do about it."""


def directory() -> Path:
    """Which profile directory is in force, in precedence order."""
    override = os.environ.get("JOBDESK_PROFILE")
    if override:
        path = Path(override).expanduser()
        if not path.is_dir():
            raise ProfileError(
                f"JOBDESK_PROFILE points at {path}, which is not a directory")
        return path
    if REAL.is_dir():
        return REAL
    if EXAMPLE.is_dir():
        return EXAMPLE
    raise ProfileError(
        f"No profile found. Copy {EXAMPLE.name}/ to {REAL.name}/ and edit it.")


def is_example() -> bool:
    """True when the running profile is the shipped fictional one.

    Worth surfacing in the UI. Someone who has not filled anything in should
    be told the scores they are looking at belong to a made-up person.
    """
    return directory().resolve() == EXAMPLE.resolve()


def path(name: str) -> Path:
    return directory() / name


@lru_cache(maxsize=None)
def _read(name: str, directory_key: str) -> dict[str, Any]:
    # directory_key is in the signature only so the cache invalidates when
    # JOBDESK_PROFILE changes mid-process, which is exactly what the tests do.
    file = Path(directory_key) / name
    if not file.exists():
        raise ProfileError(
            f"{file} is missing. A profile needs: {', '.join(REQUIRED)}")
    try:
        with file.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{file} is not valid TOML: {exc}") from exc


def load(name: str) -> dict[str, Any]:
    """Read one file out of the running profile."""
    return _read(name, str(directory()))


def load_optional(name: str) -> dict[str, Any]:
    """Same, but a missing file is an empty profile section rather than an
    error. Used for delivery.toml, which most users will never write."""
    try:
        return load(name)
    except ProfileError:
        return {}


def check() -> list[str]:
    """Everything wrong with the running profile, as a list of sentences."""
    problems = []
    try:
        where = directory()
    except ProfileError as exc:
        return [str(exc)]
    for name in REQUIRED:
        if not (where / name).exists():
            problems.append(f"{where / name} is missing")
            continue
        try:
            load(name)
        except ProfileError as exc:
            problems.append(str(exc))
    return problems


def identity() -> dict[str, Any]:
    """The `[identity]` block out of master.toml: name, email, phone, links."""
    return load("master.toml").get("identity", {})


def file_stem() -> str:
    """The leading part of every generated filename.

    Generated filenames look like `<stem>_CarMax_Business_Analyst.pdf`, and
    the stem used to be the author's own first and last name, compiled into
    engine/main.py as a literal that `apply` then globbed for to find the file
    the engine had just written. Two packages agreeing on one person's name is
    not a coupling either of them should have had.

    Drops a middle initial, so a three-part name still gives a two-part stem.
    Falls back to "Resume" so an unnamed profile still produces a file rather
    than one beginning with an underscore.
    """
    full = str(identity().get("name", "")).strip()
    parts = [_safe(part) for part in full.split() if _safe(part)]
    parts = [p for p in parts if len(p) > 1] or parts
    return "_".join(parts) or "Resume"


def _safe(text: str) -> str:
    return "".join(c for c in text if c.isalnum())
