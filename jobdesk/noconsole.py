"""Start child processes without a console window flashing on screen.

JobDesk runs as a windowed app under `pythonw.exe`, which has no console of
its own. Windows gives every console child process a console, so a GUI parent
starting `powershell` gets a black window that opens, does its work and closes
in the time it takes to blink -- once per call. The Setup tab alone asks
PowerShell about the firewall rule and about the logon task every time it is
drawn, so the panel that reports "everything is fine" was the loudest thing in
the app.

`CREATE_NO_WINDOW` is the fix and it is one flag. It suppresses the console
allocation without hiding anything else: stdout and stderr are still captured
by the pipes the caller sets up, exit codes still come back, and a program that
wanted to be interactive would still be waiting on input it never gets, which
is a bug this does not create and does not paper over.

Harmless on a GUI program, which allocates no console to suppress, and a no-op
off Windows, where the flag does not exist.
"""

from __future__ import annotations

import sys

CREATE_NO_WINDOW = 0x08000000


def flags() -> dict:
    """Keyword arguments for `subprocess`, spread with `**`."""
    return {"creationflags": CREATE_NO_WINDOW} if sys.platform == "win32" else {}
