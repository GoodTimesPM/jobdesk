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
  * The link carries the same AppUserModelID the window declares. Windows
    groups a taskbar button by that ID, and a link without one is grouped by
    its target, pythonw.exe. A pinned JobDesk then sat beside a second button
    for the open window, because the pin said "pythonw" and the window said
    "JobDesk". WScript.Shell cannot write the ID, so `_STAMP` does it through
    the link's property store.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .. import noconsole, paths

NAME = "JobDesk"
DESCRIPTION = "Job radar, assisted apply and the resume engine, in one window."

# What desktop.py declares for the process, and what every link has to carry
# for its pin and its window to share one taskbar button.
APP_ID = "GoodTimesPM.JobDesk"

# System.AppUserModel.ID is PKEY {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}, 5. It
# lives in the link's property store, which only IPropertyStore reaches, so
# this compiles the few lines of interop PowerShell has no cmdlet for.
_STAMP = r"""
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential, Pack = 4)]
public struct PropKey { public Guid fmtid; public uint pid; }
[StructLayout(LayoutKind.Explicit, Size = 24)]
public struct PropVar { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }
[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"),
 InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IPropStore {
    int GetCount(out uint n);
    int GetAt(uint i, out PropKey k);
    int GetValue(ref PropKey k, out PropVar v);
    int SetValue(ref PropKey k, ref PropVar v);
    int Commit();
}
public static class LinkAppId {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHGetPropertyStoreFromParsingName(string path, IntPtr bc,
        int flags, ref Guid iid, [MarshalAs(UnmanagedType.Interface)] out IPropStore store);
    public static void Set(string path, string id) {
        Guid iid = typeof(IPropStore).GUID;
        IPropStore store;
        SHGetPropertyStoreFromParsingName(path, IntPtr.Zero, 2, ref iid, out store);
        PropKey key = new PropKey();
        key.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
        key.pid = 5;
        PropVar value = new PropVar();
        value.vt = 31;
        value.p = Marshal.StringToCoTaskMemUni(id);
        try {
            Marshal.ThrowExceptionForHR(store.SetValue(ref key, ref value));
            Marshal.ThrowExceptionForHR(store.Commit());
        } finally {
            Marshal.FreeCoTaskMem(value.p);
            Marshal.ReleaseComObject(store);
        }
    }
}
'@
"""


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
{_STAMP}
[LinkAppId]::Set({_quote(link)}, {_quote(APP_ID)})
"""
    _powershell(script)
    if not link.is_file():
        raise ShortcutError("the shell reported success and wrote nothing")
    return link


def _powershell(script: str) -> None:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=60, **noconsole.flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ShortcutError(f"powershell could not be run: {exc}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "the shell refused").strip()
        raise ShortcutError(detail.splitlines()[0] if detail else "unknown error")


def pinned_dir() -> Path:
    """Where Explorer keeps its own copy of every link pinned to the taskbar."""
    return (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Internet Explorer"
            / "Quick Launch" / "User Pinned" / "TaskBar")


def has_app_id(link: Path) -> bool:
    """Whether the link already carries APP_ID.

    The property store is serialized into the .lnk as UTF-16, so the ID shows
    up as plain bytes. Reading them costs nothing; asking the shell costs a
    PowerShell start and a compile.
    """
    try:
        return APP_ID.encode("utf-16-le") in link.read_bytes()
    except OSError:
        return True  # nothing we could fix anyway


def _ours(link: Path) -> bool:
    """A JobDesk link, by what it runs rather than what it is called."""
    try:
        data = link.read_bytes()
    except OSError:
        return False
    return any("jobdesk.app".encode(enc) in data for enc in ("utf-16-le", "latin-1"))


def stale_links() -> list[Path]:
    """JobDesk links on the Desktop or the taskbar that lack APP_ID.

    Pinning copies the link, so a pin made before the links carried the ID
    keeps its old copy no matter how often the Desktop one is rewritten.
    """
    found = []
    for folder in (desktop_dir(), pinned_dir()):
        try:
            links = list(folder.glob("*.lnk"))
        except OSError:
            continue
        found += [ln for ln in links if _ours(ln) and not has_app_id(ln)]
    return found


def repair_links() -> list[Path]:
    """Stamp APP_ID onto every stale link and return the ones fixed.

    Explorer reads the ID when the taskbar loads the pin, so the fix shows
    after the next Explorer restart or sign-in, not in the session that ran it.
    """
    stale = stale_links()
    if not stale:
        return []
    calls = "\n".join(f"[LinkAppId]::Set({_quote(ln)}, {_quote(APP_ID)})" for ln in stale)
    _powershell(_STAMP + calls)
    return [ln for ln in stale if has_app_id(ln)]
