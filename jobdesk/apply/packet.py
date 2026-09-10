"""Building one application packet -- the actual product of this sub-project.

Plan item 6 draws the line exactly here: automation produces everything up to
the submit button and stops. What comes out of this module is a folder you
opens, reads, and submits from by hand, in about three to five minutes instead
of twenty-five.

    {date}_{Company}_{Role}/
        APPLY.md                     the checklist -- open this one first
        <Your_Name>_...pdf        tailored resume (Resume Engine)
        <Your_Name>_...docx       for Workday and Taleo
        <Your_Name>_...txt        for "paste your resume" boxes
        COVER_LETTER.txt / .md / .pdf / .docx
        ANSWERS.md                   the ATS free-text bank
        TAILORING.md                 every resume choice and why
        ats_report.md                the parse simulation
        jd.txt                       the posting, as it was read

Nothing in here submits anything, and nothing in here talks to a job board
except the single optional JD fetch in `jdtext`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import answers as answers_mod
from . import config, engine, guard, letter as letter_mod
from .applog import Application, Log
from .candidates import Candidate

_SAFE = re.compile(r"[^A-Za-z0-9]+")
_DATE_PREFIX = re.compile(r"\d{4}-\d{2}-\d{2}$")

_ABOUT_HEADS = ("about us", "about the company", "about ", "who we are",
                "our mission", "company overview")


def safe(text: str) -> str:
    return _SAFE.sub("_", text).strip("_") or "Unknown"


@dataclass
class Result:
    ok: bool
    folder: Path | None = None
    delivered: Path | None = None
    application: Application | None = None
    problems: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    tailor: engine.TailorResult | None = None
    letter_dropped: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------

def _family_from_report(tailoring_md: str) -> str:
    match = re.search(r"Target family read from the title: `([a-z-]+)`", tailoring_md)
    return match.group(1) if match else "analysis"


def _missing_required(tailoring_md: str) -> list[str]:
    """The 'required terms the resume does not say' list, lifted verbatim.

    This is the most valuable paragraph the Resume Engine produces and the
    easiest to never read, so the packet's checklist repeats it.
    """
    block = re.search(r"## Required terms the resume does not say\n(.*?)(?=\n## |\Z)",
                      tailoring_md, re.S)
    if not block:
        return []
    return [ln[2:].strip() for ln in block.group(1).splitlines()
            if ln.startswith("- ")]


def about_section(jd_text: str, limit: int = 900) -> str:
    """The employer's own description of itself, for the 'why us' answer."""
    lines = jd_text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip().rstrip(":").lower()
        if not stripped or len(stripped) > 60:
            continue
        if any(stripped.startswith(head) for head in _ABOUT_HEADS):
            body = "\n".join(lines[index + 1:index + 14]).strip()
            if len(body) > 120:
                return body[:limit]
    paragraphs = [p.strip() for p in jd_text.split("\n\n") if len(p.strip()) > 150]
    return paragraphs[0][:limit] if paragraphs else ""


def open_folder(path: Path) -> None:
    try:
        subprocess.run(["explorer.exe", str(path)], check=False)
    except OSError:
        pass


# ---------------------------------------------------------------------------

def build(candidate: Candidate, jd_text: str, *, log: Log,
          note: str = "", include_draft: bool = False,
          checks: list[guard.Check] | None = None,
          auto: bool = False, echo=print) -> Result:
    """Run the whole pipeline for one posting."""
    result = Result(ok=False)
    company, role = candidate.company.strip(), candidate.title.strip()
    folder_name = f"{date.today().isoformat()}_{safe(company)}_{safe(role)}"
    folder = config.packet_dir(folder_name)
    folder.mkdir(parents=True, exist_ok=True)

    jd_path = folder / "jd.txt"
    jd_path.write_text(jd_text, encoding="utf-8")
    result.steps.append(f"JD saved ({len(jd_text)} chars)")

    # -- 1. the resume, via the Resume Engine's own CLI ---------------------
    echo("  tailoring the resume (Resume Engine)...")
    tailored = engine.tailor(jd_path, company, role, include_draft=include_draft)
    result.tailor = tailored
    if not tailored.ok:
        result.problems.append(
            "the Resume Engine did not deliver a resume -- its output follows. "
            "A verification failure here is the engine refusing to send "
            "something it cannot stand behind, not a crash.")
        result.problems.append(tailored.report or "(no output)")
        result.folder = folder
        return result
    result.steps.append(
        f"resume tailored: {tailored.numbers.get('Bullets selected', '?')} bullets, "
        f"ATS {tailored.numbers.get('ATS parse score', '?')}, "
        f"required coverage {tailored.numbers.get('Required terms', '?')}")

    stem = tailored.stem
    for suffix in (".pdf", ".docx", ".txt"):
        source = tailored.file(suffix)
        if source:
            shutil.copy2(source, folder / source.name)
    for name in ("TAILORING.md", "ats_report.md"):
        source = tailored.out_dir / name if tailored.out_dir else None
        if source and source.exists():
            shutil.copy2(source, folder / name)

    resume_txt_path = folder / f"{stem}.txt"
    resume_text = resume_txt_path.read_text(encoding="utf-8") if resume_txt_path.exists() else ""
    tailoring_md = (folder / "TAILORING.md").read_text(encoding="utf-8") \
        if (folder / "TAILORING.md").exists() else ""
    family = _family_from_report(tailoring_md)
    missing = _missing_required(tailoring_md)

    # -- 2. the cover letter, through the same kind of gate ----------------
    letter = letter_mod.build(company=company, role=role, family=family,
                              resume_text=resume_text, jd_text=jd_text, note=note)
    problems = letter_mod.verify(letter, resume_text, jd_text)
    if problems:
        result.problems.append("the cover letter failed verification and was "
                               "NOT written:")
        result.problems += [f"  - {p}" for p in problems]
    else:
        (folder / "COVER_LETTER.txt").write_text(letter.to_text(), encoding="utf-8")
        (folder / "COVER_LETTER.md").write_text(letter.to_markdown(), encoding="utf-8")
        letter_mod.write_pdf(letter, folder / "COVER_LETTER.pdf")
        letter_mod.write_docx(letter, folder / "COVER_LETTER.docx")
        result.letter_dropped = letter.dropped
        result.steps.append(
            f"cover letter: {len(letter.paragraphs)} paragraph(s), family "
            f"'{family}', tools {', '.join(letter.tools) or 'none named'}"
            + (f", {len(letter.dropped)} dropped by the gate" if letter.dropped else ""))

    # -- 3. the answer bank -------------------------------------------------
    bank = answers_mod.load()
    (folder / "ANSWERS.md").write_text(
        answers_mod.to_markdown(bank, company=company, role=role,
                                about=about_section(jd_text)),
        encoding="utf-8")
    pending = [a for a in bank if a.needs_confirmation or a.is_placeholder]
    result.steps.append(f"answer bank: {len(bank)} answers "
                        f"({len(pending)} need your review)")

    # -- 4. the checklist ---------------------------------------------------
    checks = checks or []
    (folder / "APPLY.md").write_text(
        _apply_md(candidate, tailored, letter if not problems else None,
                  checks, missing, pending, jd_text, folder),
        encoding="utf-8")

    # -- 5. deliver + log ---------------------------------------------------
    result.folder = folder
    result.delivered = _deliver(folder, folder_name)
    application = log.add(Application(
        id=folder_name, company=company, role=role, url=candidate.url,
        source=candidate.source or "manual", uid=candidate.uid,
        dedupe_key=candidate.dedupe_key, score=candidate.score,
        tier=candidate.tier, status="prepared", resume_variant=stem,
        # The NAME, not the path. Nothing reads this field, and the full
        # path put the author's drive layout into a tracked file 151 times.
        packet=(result.delivered or folder).name, auto=auto,
    ))
    # Rebuilding an auto packet by hand promotes it: `log.add` returns the
    # existing row, and if it stayed flagged `auto` it would keep its exemption
    # from the queue filter and show up as "already built" forever.
    if application.auto and not auto:
        application.auto = False
        application.touch("rebuilt by hand")
        log.save()
    result.application = application
    result.ok = True
    return result


def _deliver(folder: Path, name: str) -> Path | None:
    # The delivery folder is grouped by application date, so a packet named
    # `2026-09-03_Company_Role` goes under a `2026-09-03` folder. A name that
    # somehow lacks the date prefix stays at the top level rather than
    # inventing a bucket for it.
    destination = config.delivery_dir()
    if destination is None:
        return None
    try:
        day = name[:10]
        parent = destination / day if _DATE_PREFIX.match(day) else destination
        target = parent / name
        target.mkdir(parents=True, exist_ok=True)
        for item in folder.iterdir():
            if item.is_file():
                shutil.copy2(item, target / item.name)
        return target
    except OSError:
        return None


def _apply_md(candidate: Candidate, tailored: engine.TailorResult,
              letter, checks: list[guard.Check], missing: list[str],
              pending: list, jd_text: str, folder: Path) -> str:
    numbers = tailored.numbers
    out = [f"# Apply -- {candidate.company}, {candidate.title}", ""]
    if candidate.url:
        out += [f"**[Open the posting]({candidate.url})**", ""]
    # A hand-entered job has no Job Radar score, and printing "Fit score: 0
    # (tier -)" on it reads as a verdict rather than an absence.
    if candidate.origin != "manual":
        out.append(f"- Fit score: **{candidate.score}** "
                   f"(tier {candidate.tier or '-'})")
        out.append(f"- {candidate.one_line()}")
    else:
        out.append("- Entered by hand -- no Job Radar score for this one.")
        details = candidate.one_line().replace(candidate.source, "").strip(" -")
        if details:
            out.append(f"- {details}")
    if candidate.required_years is not None:
        out.append(f"- Binding experience requirement: **{candidate.required_years} years**")
    if candidate.flags:
        out.append(f"- Job Radar flags: {', '.join(candidate.flags)}")
    if candidate.also_on:
        out.append(f"- Also posted on: {', '.join(candidate.also_on)} "
                   f"(more boards = more applicants)")
    out += [
        "",
        "## Before you submit",
        "",
        "- [ ] Read the resume PDF. It is a *subset* of your master content -- "
        "if a line surprises you, that is worth knowing before an interviewer "
        "reads it.",
        "- [ ] Read the cover letter and swap the company sentence for "
        "something specific (ANSWERS.md quotes the posting's own About text).",
        "- [ ] Apply **direct on the company's ATS** if this came from an "
        "aggregator -- the direct application is the one that gets read.",
        "- [ ] Upload the **.docx** on Workday/Taleo, the **.pdf** everywhere "
        "else, and paste from the **.txt** into any 'paste your resume' box. "
        "The cover letter ships in all three too -- default to its **.pdf**.",
        "- [ ] Same name, same email, same phone as every other application. "
        "One identity, always.",
        "- [ ] Come back to the Applied tab in JobDesk and mark it applied, "
        "so the follow-up date gets set and this job stops showing up as "
        "something to do.",
        "",
    ]

    if checks:
        out += ["## Guard rules", "",
                "From the application log, before this packet was built:", ""]
        out += [f"- {c}" for c in checks] + [""]

    out += ["## Numbers from the tailoring run", ""]
    for key in ("Bullets selected", "Page fit", "Required terms",
                "ATS parse score", "ATS keyword match"):
        if key in numbers:
            out.append(f"- {key}: {numbers[key]}")
    out.append("")

    if missing:
        out += ["## What this posting asks for that your resume does not say",
                "",
                "Not a bug and not a reason to skip the application -- these "
                "are the honest gaps. Expect them as interview questions, and "
                "treat a term that keeps appearing here as the next thing to "
                "learn -- the Console tab's gap report is the same "
                "list across every posting you have stored).", ""]
        out += [f"- {term}" for term in missing] + [""]

    if letter is not None and letter.dropped:
        out += ["## Cover letter paragraphs that were dropped", "",
                "Approved paragraphs held back because the tailored resume "
                "does not make the claim they rest on:", ""]
        out += [f"- `{pid}` -- {why}" for pid, why in letter.dropped] + [""]

    if pending:
        out += ["## Answers that need you", ""]
        out += [f"- **{a.id}** -- {a.note or 'unconfirmed draft'}" for a in pending]
        out += [""]

    out += ["## Files", "",
            "| file | use |", "| --- | --- |",
            "| `*.pdf` | the resume to upload |",
            "| `*.docx` | upload this one to Workday and Taleo |",
            "| `*.txt` | paste-your-resume boxes |",
            "| `COVER_LETTER.pdf` | the one to upload |",
            "| `COVER_LETTER.docx` | Workday and Taleo, same as the resume |",
            "| `COVER_LETTER.txt` | paste-into-a-box |",
            "| `ANSWERS.md` | the free-text questions |",
            "| `TAILORING.md` | why each resume line was chosen |",
            "| `ats_report.md` | what a parser actually reads |",
            "| `jd.txt` | the posting as it was read |",
            ""]
    return "\n".join(line for line in out if line is not None)


def load_json(path: Path) -> list | dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
