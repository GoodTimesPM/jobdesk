"""Run every check JobDesk has, in one command.

    py tests/run_all.py

Five suites plus the credential scan. They are separate files because the
three packages stay isolated from each other -- but "isolated" was never
supposed to mean "you have to remember four commands", and before the profile
split touches all three at once, one command that goes red is worth having.

The suites are plain scripts, not pytest. That was deliberate and it stays:
they run on a machine with nothing installed but the requirements.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHECKS = [
    ("credential scan", ["scripts/check_secrets.py"]),
    ("radar scoring", ["tests/test_radar.py", "--scoring"]),
    ("delivery plugins", ["tests/test_radar.py", "--plugins"]),
    ("resume engine", ["tests/test_engine.py"]),
    ("assisted apply", ["tests/test_apply.py"]),
    ("the window", ["tests/test_app.py"]),
]


def main() -> int:
    results = []
    for name, args in CHECKS:
        print(f"\n{'=' * 70}\n{name}\n{'=' * 70}", flush=True)
        code = subprocess.run([sys.executable, *args], cwd=str(ROOT)).returncode
        results.append((name, code))

    print(f"\n{'=' * 70}")
    for name, code in results:
        print(f"  {'ok  ' if code == 0 else 'FAIL'}  {name}")
    failed = [name for name, code in results if code != 0]
    print(f"{'=' * 70}")
    if failed:
        print(f"{len(failed)} of {len(results)} failed: {', '.join(failed)}")
        return 1
    print(f"all {len(results)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
