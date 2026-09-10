"""The bridge to the engine.

Called as a subprocess through its own CLI, never imported, even though both
now live in one package. That is the isolation rule made concrete: this
package depends on the engine's *interface* (four subcommands and the files
they write), not its internals. Its selection algorithm, its master content
format, and its dependencies can all change without touching this folder.
`app` is the one place allowed to import all three.

It also means the engine keeps working exactly as documented when run by
hand -- this is a convenience layer over it, not a replacement for it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .. import noconsole, profile
from . import config


@dataclass
class TailorResult:
    ok: bool
    out_dir: Path | None
    stdout: str
    stderr: str
    stem: str = ""
    numbers: dict[str, str] = field(default_factory=dict)

    @property
    def report(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()

    def file(self, suffix: str) -> Path | None:
        if not self.out_dir or not self.stem:
            return None
        path = self.out_dir / f"{self.stem}{suffix}"
        return path if path.exists() else None


_WRITTEN = re.compile(r"^\s*Written to\s*:\s*(.+?)\s*$", re.M)
_FIELD = re.compile(r"^\s{2}([A-Za-z][A-Za-z /]+?)\s*:\s*(.+?)\s*$", re.M)


def available() -> bool:
    return (config.ROOT / "jobdesk" / "engine" / "main.py").exists()


def _run(args: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    """Run the engine CLI from the repo root.

    `sys.executable` rather than "py": whichever interpreter is running this
    is the one whose environment was set up, and the engine's dependencies
    (fpdf2, python-docx, PyMuPDF) live there.
    """
    return subprocess.run(
        [sys.executable, "-m", "jobdesk.engine.main", *args],
        cwd=str(config.ROOT),
        capture_output=capture, text=True, encoding="utf-8", errors="replace",
        **noconsole.flags(),
    )


def check() -> tuple[int, str]:
    proc = _run(["check"])
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def gap() -> tuple[int, str]:
    proc = _run(["gap"])
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def ats(pdf: Path, jd_file: Path | None = None) -> tuple[int, str]:
    args = ["ats", str(pdf)]
    if jd_file:
        args += ["--jd", str(jd_file)]
    proc = _run(args)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def tailor(jd_file: Path, company: str, role: str,
           include_draft: bool = False) -> TailorResult:
    """Build the tailored packet for one posting.

    A non-zero exit is a real outcome, not an exception: the engine returns 1
    when its truthfulness gate refuses to deliver a resume, and that refusal
    has to reach you intact rather than as a stack trace.
    """
    if not available():
        return TailorResult(False, None, "",
                            "Engine not found -- expected jobdesk/engine/main.py")

    args = ["tailor", "--jd", str(jd_file), "--company", company, "--role", role]
    if include_draft:
        args.append("--include-draft")
    proc = _run(args)
    stdout, stderr = proc.stdout or "", proc.stderr or ""

    out_dir = None
    match = _WRITTEN.search(stdout)
    if match:
        candidate = Path(match.group(1))
        if not candidate.is_absolute():
            candidate = config.ROOT / candidate
        out_dir = candidate if candidate.exists() else None

    # The engine names its output <person>_<company>_<role>.pdf, and the only
    # part this side can predict is the person -- so it asks the profile for
    # the same stem the engine built the name from, rather than either package
    # hardcoding a name or importing the other.
    stem = ""
    if out_dir:
        pdfs = sorted(out_dir.glob(f"{profile.file_stem()}_*.pdf"))
        if pdfs:
            stem = pdfs[0].stem

    numbers = {k.strip(): v.strip() for k, v in _FIELD.findall(stdout)}
    ok = proc.returncode == 0 and out_dir is not None and bool(stem)
    return TailorResult(ok, out_dir, stdout, stderr, stem, numbers)
