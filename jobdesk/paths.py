"""Where everything lives, for all three packages.

The one module every package may import from its parent. It exists because
`radar`, `engine` and `apply` used to be three repos with three roots, and
each one worked out its own paths from `__file__`. Now there is one root and
one answer, and a package that wants a directory asks here for it.

Nothing else is shared. `radar`, `engine` and `apply` still never import each
other -- see tests/test_apply.py, which asserts it.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DATA = ROOT / "data"
LOGS = ROOT / "logs"


def load_env(*names: str) -> None:
    """Read `.env` files from ROOT, in order, into the environment.

    Minimal, to avoid a python-dotenv dependency. Existing environment
    variables always win, so a value exported in the shell overrides the file,
    and an earlier file in `names` beats a later one.

    Read as utf-8-sig because Notepad and `Set-Content -Encoding utf8` both
    prepend a BOM, which turns the first key into a name nothing will match.

    A blank `KEY=` is a placeholder, not a setting, and is skipped -- setting
    it to "" would stop a later file in the chain from ever filling it in.
    """
    for name in names or (".env",):
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            if not value:
                continue
            os.environ.setdefault(key.strip(), value)


def read_switch(name: str) -> bool:
    """Read a kill switch file. Fails OPEN -- missing or unreadable means ON.

    Same contract as the news bot's switch. Each package keeps its own file:
    pausing discovery is a different decision from pausing packet builds.
    """
    try:
        text = (ROOT / name).read_text(encoding="utf-8-sig")
    except OSError:
        return True
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        return line.lower() not in ("off", "0", "false", "no", "disabled", "paused")
    return True
