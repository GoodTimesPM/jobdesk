"""The JobRadarApply scheduled task, run under `pythonw.exe` so no window opens.

The task used to start `powershell.exe -WindowStyle Hidden` on `run_auto.ps1`.
PowerShell draws its console before it reads that flag, so a black window
jumped up three times a day. `pythonw` never gets a console at all.

`pythonw` leaves `sys.stdout` as None, which is why the PowerShell runner used
`py.exe`: every echo() in a run would vanish. This points stdout and stderr at
`logs/apply/auto.log` first, so the log reads the same as it did.

    pythonw -m jobdesk.apply.auto_task --max 10

`run_auto.ps1` is still there for running it by hand in a console.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback

from . import config

LOG = config.LOGS / "auto.log"
KEEP_LINES = 2000


def _trim() -> None:
    """Keep the last ~2,000 lines once the log passes 1 MB, as run_auto.ps1 does."""
    try:
        if LOG.stat().st_size <= 1_000_000:
            return
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        LOG.write_text("\n".join(lines[-KEEP_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--max", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    _trim()
    with open(LOG, "a", encoding="utf-8", errors="replace") as log:
        sys.stdout = sys.stderr = log
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n==== {stamp}  auto_task (max {args.max}) ====", flush=True)
        forwarded = ["auto", "--max", str(args.max)]
        if args.dry_run:
            forwarded.append("--dry-run")
        try:
            from . import main as apply_main
            code = apply_main.main(forwarded)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        except Exception:
            traceback.print_exc()
            code = 1
        print(f"---- exit {code} ----", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
