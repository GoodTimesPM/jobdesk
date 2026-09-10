"""The desktop shortcut. `py -m jobdesk.app --shortcut`, or the button in Setup.

A `.lnk` is a COM object rather than a file format anyone can reasonably
hand-write, so this drives `WScript.Shell` through PowerShell, which is what
every Windows installer does.

Two details matter more than they look:

  * The target is **pythonw.exe**, not python.exe. python.exe would leave a
    console window parked behind the app for as long as it is open, and the
    point of the desktop window is that JobDesk is an application and not a
    script somebody is running.
  * The working directory is the project root. `profile/`, `data/`, `packets/`
    and the `.env` files are all resolved from it, so a shortcut launched from
    the desktop has to arrive where a terminal launch would.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .. import noconsole, paths

NAME = "JobDesk"
DESCRIPTION = "Job radar, assisted apply and the resume engine, in one window."


class ShortcutError(RuntimeError):
    """The shell would not write the link, and the message says why."""


def pythonw() -> Path:
    """The windowed interpreter beside whichever python is running us."""
    here = Path(sys.executable)
    candidate = here.with_name("pythonw.exe")
    return candidate if candidate.is_file() else here


def desktop_dir() -> Path:
    """Where the shortcut goes.

    OneDrive redirects the Desktop on most Windows installs and leaves the
    original folder in place but empty, so a link written to
    `%USERPROFILE%\\Desktop` lands somewhere the user never looks. Prefer the
    redirected folder when there is one.
    """
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    if onedrive and (Path(onedrive) / "Desktop").is_dir():
        return Path(onedrive) / "Desktop"
    return Path(os.path.expanduser("~")) / "Desktop"


def icon() -> Path | None:
    ico = paths.ROOT / "jobdesk.ico"
    return ico if ico.is_file() else None


def _quote(value) -> str:
    """A PowerShell single-quoted string. A literal quote is doubled.

    These paths come off the filesystem rather than out of a text box, but a
    home directory with an apostrophe in it would otherwise end the string
    halfway through a word.
    """
    return "'" + str(value).replace("'", "''") + "'"


def create(directory: Path | None = None, *, port: int = 0) -> Path:
    """Write (or overwrite) the shortcut and return its path."""
    target = directory or desktop_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ShortcutError(f"{target} could not be opened: {exc}")
    link = target / f"{NAME}.lnk"

    args = "-m jobdesk.app" + (f" --port {port}" if port else "")
    ico = icon()
    icon_line = f"$sc.IconLocation = {_quote(str(ico) + ',0')}" if ico else ""
    script = f"""
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut({_quote(link)})
$sc.TargetPath = {_quote(pythonw())}
$sc.Arguments = {_quote(args)}
$sc.WorkingDirectory = {_quote(paths.ROOT)}
$sc.Description = {_quote(DESCRIPTION)}
{icon_line}
$sc.WindowStyle = 1
$sc.Save()
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=60, **noconsole.flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ShortcutError(f"powershell could not be run: {exc}")
    if result.returncode != 0 or not link.is_file():
        detail = (result.stderr or result.stdout or "the shell refused").strip()
        raise ShortcutError(detail.splitlines()[0] if detail else "unknown error")
    return link
