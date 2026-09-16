"""The window. A pywebview shell around the local server.

JobDesk opens as an application on the desktop rather than a tab in whatever
browser happened to be default. Same server, same page: `desktop.py` binds the
port on a background thread and points a window at it, so nothing about the
app changes based on how it was launched. `--serve` skips the window and prints
the URL, which is also the fallback on a machine with no WebView2 runtime.

Windowed launches have no console, so anything that would have been printed
before the window exists goes to `logs/desktop.log` instead. A GUI that dies
silently is a GUI you debug by guessing.
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from datetime import datetime

from .. import __version__, paths
from . import server, shortcut

TITLE = "JobDesk"
LOG_PATH = paths.LOGS / "desktop.log"
# WebView2 is handed its own profile folder so the page's localStorage -- which
# column you sorted by, which tab you were on -- survives closing the window.
PROFILE_DIR = paths.ROOT / "logs" / "webview"


def log(message: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}\n")
    except OSError:
        pass
    print(message)


# Windows groups taskbar buttons by AppUserModelID, and a process that never
# sets one is grouped by its executable. Every pywebview app on this machine
# runs under the same pythonw.exe, so without this JobDesk shares a taskbar
# button -- and an icon -- with whatever else is open. Must run before the
# first window exists, which is why launch() calls it first.
APP_ID = "GoodTimesPM.JobDesk"


def _set_app_id() -> None:
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def _app_window(title: str):
    """Our own visible top-level window with this title, or None.

    Not `FindWindowW`, which walks every window on the desktop in Z-order and
    returns the first title match. On Windows 11 that match is usually
    `Windows.Internal.Shell.TabProxyWindow` -- an invisible stand-in the shell
    creates for taskbar thumbnails, which copies our title. Setting an icon on
    it succeeds, reports success, and changes nothing you can see, which is
    exactly what JobDesk shipped doing.

    So the window is identified by what actually distinguishes it: it belongs
    to this process, it is visible, and it has no owner.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    me = os.getpid()
    found = []

    def visit(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != me or not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, 4):          # GW_OWNER: a dialog, not the frame
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value == title:
            found.append(hwnd)
            return False
        return True

    callback = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    user32.EnumWindows(callback, 0)
    return found[0] if found else None


def _raise_other_window(title: str) -> bool:
    """Bring another process's JobDesk window to the front. Did it work?

    The companion to `_app_window`, which deliberately only finds our own.
    This one wants the opposite: a JobDesk belonging to some other process,
    which is what a second launch of the icon has found.

    Same two guards as the local search, and for the same reason -- visible,
    and no owner -- so this cannot fasten onto the invisible shell stand-in
    that copies our title for taskbar thumbnails. Raising that succeeds and
    shows you nothing.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return False

    user32 = ctypes.windll.user32
    me = os.getpid()
    found = []

    def visit(hwnd, _lparam):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == me or not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, 4):          # GW_OWNER: a dialog, not a frame
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value == title:
            found.append(hwnd)
            return False
        return True

    try:
        callback = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
        user32.EnumWindows(callback, 0)
        if not found:
            return False
        SW_RESTORE = 9
        user32.ShowWindow(found[0], SW_RESTORE)
        # Windows refuses SetForegroundWindow to a process that is not the
        # one the user is currently interacting with, and reports the refusal
        # as a plain zero. The restore above has already un-minimised it, so
        # a refusal still leaves the window on screen -- just not in front.
        user32.SetForegroundWindow(found[0])
        return True
    except Exception:
        return False


def _set_window_icon(title: str, tries: int = 40) -> None:
    """Hang the JobDesk icon on the window frame.

    pywebview accepts an `icon=` on its GTK and Qt backends only; on Windows
    the frame shows whatever icon the host process has, which for a shortcut
    launch is pythonw.exe -- the same generic snake as every other Python
    program on the machine. So it is set the Windows way, by finding the window
    once it exists and sending it WM_SETICON.

    Cosmetic, and it polls on its own thread because `webview.start()` blocks
    and the window does not exist until it does. Every failure is a silent
    return: an app that refuses to open because its icon would not load is a
    much worse bug than a plain icon.
    """
    path = shortcut.icon()
    if not path:
        return
    try:
        import ctypes
    except Exception:
        return

    # LR_DEFAULTSIZE is deliberately absent: it overrides the size asked for,
    # and the point of the two calls is one icon drawn for the title bar and a
    # larger one for the taskbar and alt-tab.
    IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
    WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
    try:
        user32 = ctypes.windll.user32
        for _ in range(tries):
            hwnd = _app_window(title)
            if hwnd:
                for which, size in ((ICON_SMALL, 16), (ICON_BIG, 32)):
                    handle = user32.LoadImageW(None, str(path), IMAGE_ICON,
                                               size, size, LR_LOADFROMFILE)
                    if handle:
                        user32.SendMessageW(hwnd, WM_SETICON, which, handle)
                return
            time.sleep(0.25)
    except Exception:
        pass


def _idle() -> int:
    """Hold the process open for the daemon server thread it owns."""
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


def _existing(port: int) -> str | None:
    """The address of a JobDesk already running here, if this window should use it.

    Opening the icon twice used to mean two JobDesks. The second one found its
    port busy, quietly took the next one, and served its own copy of the code
    and its own read of the data -- so a window left open for a week kept
    answering out of the week-old process while the file on disk moved on
    underneath it, and nothing on screen said which one you were looking at.

    A window is a view. If JobDesk is already running here, point at it.

    A different version answering is the one case worth a second server: that
    process is running code this one has since replaced, and silently
    attaching to it is how an update appears not to have happened. Say both
    numbers out loud and take the next port.
    """
    running = server.probe(port)
    if running == __version__:
        log(f"JobDesk {running} is already running on {port}.")
        return f"http://{server.HOST}:{port}/"
    if running:
        log(f"A JobDesk running {running} already holds port {port}, and this "
            f"one is {__version__}. Starting a second server rather than "
            f"showing you the old code. Close the other window.")
    return None


def _server_for(port: int) -> tuple[str, bool]:
    """Where this window points, and whether this process owns what is there."""
    attach = _existing(port)
    if attach:
        return attach, False
    url, _httpd = server.start_background(port)
    return url, True


def launch(port: int = server.DEFAULT_PORT, *, window: bool = True) -> int:
    """Open JobDesk. One of these per machine, not one per double-click.

    Opening the icon twice used to mean two JobDesks: the second found its
    port busy, quietly took the next one, and served its own copy of the code
    and its own read of the data. A window left open for a week then kept
    answering out of the week-old process while the files on disk moved on
    underneath it, and nothing on screen said which one you were looking at.

    So a second launch raises the window that is already open and stops. It is
    what a person double-clicking an icon a second time means by it, and it
    leaves exactly one process owning the server.
    """
    _set_app_id()
    if window and server.probe(port) == __version__ and _raise_other_window(TITLE):
        log("JobDesk is already open. Raised that window instead of "
            "starting a second one.")
        return 0

    url, ours = _server_for(port)
    log(f"JobDesk is at {url}")

    if not window:
        # Nothing to hold open if the server belongs to another process.
        if not ours:
            return 0
        print("Ctrl-C to stop.")
        return _idle()

    try:
        import webview
    except ImportError:
        log("pywebview is not installed, so there is no window. The address "
            "above works in any browser; `pip install pywebview` for the app.")
        return _idle() if ours else 0

    webview.create_window(
        TITLE, url,
        width=1480, height=940, min_size=(1020, 660),
        background_color="#12151c",
        # pywebview defaults `text_select` to False and enforces it by injecting
        # `user-select: none` across the document, which would make a job
        # description impossible to copy out of -- in an app whose whole job is
        # getting text from a posting into an application.
        text_select=True,
    )
    threading.Thread(target=_set_window_icon, args=(TITLE,), daemon=True).start()
    try:
        # `private_mode=True` is pywebview's default and hands WebView2 an
        # incognito profile, so every localStorage key the page wrote is thrown
        # away on close. The profile lives under logs/ with the rest of the
        # runtime state.
        webview.start(private_mode=False, storage_path=str(PROFILE_DIR))
    except Exception:  # no WebView2 runtime, no display, a driver that refused
        log("could not open a window, so JobDesk is running headless at the "
            "address above:\n" + traceback.format_exc())
        return _idle() if ours else 0
    return 0
