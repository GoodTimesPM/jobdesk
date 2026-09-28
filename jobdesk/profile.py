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

These are TOML files a person edits in any text editor. The setup wizard
writes the same files from a form.
"""

from __future__ import annotations

import os
import time
import tomllib
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


# Profile reads happen tens of thousands of times in one scoring pass, so the
# directory choice and each file's contents are cached. Both are rechecked
# against the disk at most once a second, which is how an edit made in a text
# editor is picked up without a restart. `forget()` drops everything at once,
# for code that has just written a profile file itself.
_RECHECK_S = 1.0
_dir_cache: tuple[str | None, float, Path] | None = None
_files: dict[tuple[str, str], tuple[float, int, dict[str, Any]]] = {}


def forget() -> None:
    global _dir_cache
    _dir_cache = None
    _files.clear()


def directory() -> Path:
    """Which profile directory is in force, in precedence order."""
    global _dir_cache
    override = os.environ.get("JOBDESK_PROFILE")
    now = time.monotonic()
    if (_dir_cache and _dir_cache[0] == override
            and now - _dir_cache[1] < _RECHECK_S):
        return _dir_cache[2]
    found = _directory(override)
    _dir_cache = (override, now, found)
    return found


def _directory(override: str | None) -> Path:
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


def _read(name: str, where: str) -> dict[str, Any]:
    key = (name, where)
    now = time.monotonic()
    hit = _files.get(key)
    if hit and now - hit[0] < _RECHECK_S:
        return hit[2]
    file = Path(where) / name
    try:
        stamp = file.stat().st_mtime_ns
    except OSError:
        raise ProfileError(
            f"{file} is missing. A profile needs: {', '.join(REQUIRED)}") from None
    if hit and hit[1] == stamp:
        _files[key] = (now, stamp, hit[2])
        return hit[2]
    try:
        with file.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{file} is not valid TOML: {exc}") from exc
    _files[key] = (now, stamp, data)
    return data


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


def leftovers() -> list[str]:
    """What a real profile still carries over from the fictional example.

    The setup wizard starts a profile from the example and rewrites only the
    identity block. Until the user replaces the rest, a resume built from it
    puts their name on Wren Adeyemi's jobs, which is the one thing this tool
    promises never to do. Empty when the profile is the example itself, since
    that is the test fixture and is supposed to be fictional.
    """
    try:
        if is_example():
            return []
        where = directory()
    except ProfileError:
        return []
    found: list[str] = []

    def both(name: str) -> tuple[dict, dict]:
        try:
            return _read(name, str(where)), _read(name, str(EXAMPLE))
        except ProfileError:
            return {}, {}

    mine, theirs = both("master.toml")
    jobs = {str(e.get("company", "")).strip().lower(): e.get("company", "")
            for e in theirs.get("experience", [])}
    kept = [jobs[c] for c in (str(e.get("company", "")).strip().lower()
                              for e in mine.get("experience", [])) if c in jobs]
    if kept:
        found.append("master.toml still lists the example's jobs ("
                     + ", ".join(kept) + ").")
    example_bullets = {b.get("text") for b in theirs.get("bullet", [])}
    same = sum(1 for b in mine.get("bullet", []) if b.get("text") in example_bullets)
    if same:
        found.append(f"master.toml still has {same} of the example's resume "
                     f"bullets, word for word.")

    mine, theirs = both("letter.toml")
    example_text = {row.get("template") for rows in theirs.values()
                    if isinstance(rows, list) for row in rows
                    if isinstance(row, dict)}
    same = sum(1 for rows in mine.values() if isinstance(rows, list)
               for row in rows
               if isinstance(row, dict) and row.get("template") in example_text)
    if same:
        found.append(f"letter.toml still has {same} of the example's "
                     f"paragraphs.")

    # A generic answer can match the example and still be true, so the
    # answers file counts as unreviewed only while it keeps the wizard's
    # header. The same goes for a letter file whose paragraphs were rewritten.
    for name in ("letter.toml", "answers.toml"):
        try:
            head = (where / name).read_text(encoding="utf-8")[:2000]
        except OSError:
            continue
        if UNREVIEWED in head and not any(name in f for f in found):
            found.append(f"{name} is still the example's, unreviewed. Edit it, "
                         f"then delete the NOT YOURS YET block at the top.")
    return found


# The marker the setup wizard writes at the top of a file it copied from the
# example without changing.
UNREVIEWED = "IT IS NOT YOURS YET"
