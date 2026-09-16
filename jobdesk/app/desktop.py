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

from .. import paths
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


def launch(port: int = server.DEFAULT_PORT, *, window: bool = True) -> int:
    _set_app_id()
    url, _httpd = server.start_background(port)
    log(f"JobDesk is at {url}")

    if not window:
        print("Ctrl-C to stop.")
        return _idle()

    try:
        import webview
    except ImportError:
        log("pywebview is not installed, so there is no window. The address "
            "above works in any browser; `pip install pywebview` for the app.")
        return _idle()

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
        return _idle()
    return 0
