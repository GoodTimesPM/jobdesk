"""What the buttons do. The app's side of radar, engine and apply.

`api.py` unpacks requests; this module is where the app actually drives the
three packages. It is the one place in the project allowed to touch all three,
and it does it the same way the CLI does: `radar.main.run()` for a sweep,
`apply.packet.build()` for a packet, `apply.applog.Log` for the record.

**No behaviour is invented here.** Every function below is the body of a
command that already existed, with the `input()` calls taken out and the guard
answers handed in instead. If a build behaves differently in the window than it
does in the terminal, that is a bug in this file.

**And nothing here submits an application.** `build_packet` writes a folder and
returns its path; `mark_applied` records that a human went and did it. There is
no third function, and there is not going to be one.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from .. import paths, profile
from ..apply import candidates as apply_candidates
from ..apply import config as apply_config
from ..apply import guard, jdtext, packet
from ..apply.applog import STATUSES, Application, Log
from . import jdstruct


class ActionError(RuntimeError):
    """Something the user asked for cannot be done, with the reason."""


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def sweep(emit, *, dry: bool = False, only: str = "") -> dict:
    """One radar run: fetch, dedupe, score, rewrite the candidate cache.

    The same call `py -m jobdesk.radar.main` makes. It prints as it goes and
    the runner is capturing stdout, so the console shows the sweep live.
    """
    from ..radar import main as radar_main

    emit(f"radar sweep starting{' (dry run)' if dry else ''}\n")
    code = radar_main.run(dry_run=dry, only=only or None)
    profile._read.cache_clear()
    rows, stamp = candidate_rows()
    emit(f"\nsweep finished with code {code}; {len(rows)} postings in the cache\n")
    return {"code": code, "jobs": len(rows), "last_run": stamp, "dry": dry}


def candidate_rows() -> tuple[list[dict], str]:
    """The candidate cache as raw rows, plus when the radar last wrote it."""
    path = apply_config.RADAR_CANDIDATES
    if not path.exists():
        return [], ""
    try:
        rows = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ActionError(
            f"{path.name} could not be read ({exc}). Run a radar sweep from "
            f"the Console tab to rebuild it.")
    stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    return [r for r in rows if isinstance(r, dict)], stamp


def candidate(uid: str) -> apply_candidates.Candidate:
    """One posting out of the cache, as the thing `packet.build` wants."""
    rows, _ = candidate_rows()
    for row in rows:
        if row.get("uid") == uid:
            known = set(apply_candidates.Candidate.__dataclass_fields__)
            return apply_candidates.Candidate(
                **{k: v for k, v in row.items() if k in known})
    raise ActionError(
        f"no posting with id {uid} is in the current list. The cache keeps 30 "
        f"days, so it may have aged out -- run a sweep, or build from the URL.")


# ---------------------------------------------------------------------------
# the packet
# ---------------------------------------------------------------------------

def check_guards(cand: apply_candidates.Candidate) -> list[dict]:
    """What the anti-blacklist rules say about applying to this posting.

    Called before the build so the page can show the warnings, and again
    inside the build so the packet records them. Cheap either way: it reads
    the application log and nothing else.
    """
    log = Log()
    checks = guard.run(log, company=cand.company, role=cand.title,
                       url=cand.url, uid=cand.uid,
                       dedupe_key=cand.dedupe_key, flags=cand.flags,
                       division=cand.division)
    return [{"level": c.level, "rule": c.rule, "message": c.message}
            for c in checks]


def build_packet(emit, *, uid: str = "", url: str = "", company: str = "",
                 title: str = "", note: str = "", jd: str = "",
                 force: bool = False, allow_fetch: bool = True) -> dict:
    """Build one application packet. The centre of the whole tool.

    Ends at a folder: a tailored resume in three formats, a cover letter, the
    ATS answer bank, the tailoring report, and APPLY.md, which is the checklist
    a person works through by hand. Nothing is sent.

    `jd` is a description pasted into the page. It wins over everything else:
    if you went to the posting and copied the text, that text is the posting,
    and whatever the radar cached weeks ago is not.

    `force` overrides a blocking guard. It exists because a rule you cannot
    override gets worked around outside the tool, where nothing is logged --
    but the override is written into the packet's own record either way.
    """
    if uid:
        cand = candidate(uid)
    else:
        if not company or not title:
            raise ActionError(
                "a posting needs a company and a role title -- they name the "
                "packet folder and drive the duplicate-application rules")
        cand = apply_candidates.Candidate(title=title.strip(),
                                          company=company.strip(),
                                          url=url.strip(), source="manual",
                                          origin="manual")

    log = Log()
    checks = guard.run(log, company=cand.company, role=cand.title,
                       url=cand.url, uid=cand.uid,
                       dedupe_key=cand.dedupe_key, flags=cand.flags,
                       division=cand.division)
    listed = [{"level": c.level, "rule": c.rule, "message": c.message}
              for c in checks]
    for check in checks:
        emit(f"  {check}\n")

    blocking = guard.blocking(checks)
    if blocking and not force:
        emit("\nBLOCKED. These are the rules that get a candidate remembered "
             "badly, so they stop the build.\n")
        return {"ok": False, "blocked": True, "checks": listed,
                "problems": [c.message for c in blocking]}
    overrides: list[str] = []
    if blocking and force:
        overrides = [c.rule for c in blocking]
        emit(f"\nOverridden by hand: {', '.join(overrides)}. The packet "
             f"records it and so does the application log.\n")

    emit("\nfinding the job description...\n")
    pasted = (jd or "").strip()
    if pasted and len(pasted) < apply_config.MIN_JD_CHARS:
        raise ActionError(
            f"that description is {len(pasted)} characters, and anything under "
            f"{apply_config.MIN_JD_CHARS} tailors badly. Copy the whole "
            f"posting, requirements and all.")
    if pasted:
        jd_text, how = pasted, f"pasted into the app ({len(pasted)} chars)"
    else:
        jd_text, how = jdtext.obtain(cached=cand.description, url=cand.url,
                                     allow_fetch=allow_fetch, allow_paste=False,
                                     cached_partial=cand.partial_description,
                                     echo=lambda m: emit(str(m) + "\n"))
    if not jd_text:
        raise ActionError(
            "no job description text, so there is nothing to tailor against. "
            "Open the posting, copy the description, and paste it into the box "
            "on this row.")
    emit(f"JD source: {how}\n\n")

    result = packet.build(cand, jd_text, log=log, note=note, checks=checks,
                          overrides=overrides,
                          echo=lambda m="": emit(str(m) + "\n"))

    for step in result.steps:
        emit(f"  + {step}\n")
    for problem in result.problems:
        emit(f"  ! {problem}\n")

    if not result.ok:
        return {"ok": False, "checks": listed,
                "problems": result.problems or ["the packet did not build"]}

    app = result.application
    emit(f"\nPACKET: {result.folder}\n")
    if result.delivered:
        emit(f"COPIED: {result.delivered}\n")
    emit("Open APPLY.md first. Mark it applied on the Applied tab once you "
         "have actually submitted it.\n")
    return {"ok": True, "checks": listed,
            "id": app.id if app else "",
            "folder": str(result.folder),
            "delivered": str(result.delivered) if result.delivered else "",
            "problems": result.problems}


# ---------------------------------------------------------------------------
# the packet, read back
# ---------------------------------------------------------------------------

# What a packet folder holds, in the order a person works through it. APPLY.md
# is first because it is the checklist and everything else is an attachment.
_READABLE = (".md", ".txt")


def packet_files(app_id: str) -> dict:
    """One packet's folder listing, with the text files read in.

    The page renders APPLY.md, the cover letter and the answers inline, and
    links the PDF and DOCX out to the file manager -- a browser cannot attach
    a file to someone else's careers site, and this tool would not do it if it
    could.
    """
    folder = apply_config.packet_dir(app_id)
    if not folder.is_dir():
        raise ActionError(
            f"no packet folder for {app_id}. It may have been deleted, or "
            f"built on another machine.")
    files, texts = [], {}
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        files.append({"name": path.name, "size": path.stat().st_size,
                      "readable": path.suffix.lower() in _READABLE})
        if path.suffix.lower() in _READABLE and path.stat().st_size < 400_000:
            texts[path.name] = path.read_text(encoding="utf-8", errors="replace")
    # APPLY.md leads. It is the only file in there that tells you what to do.
    files.sort(key=lambda f: (f["name"] != "APPLY.md", f["name"]))
    return {"id": app_id, "folder": str(folder), "files": files, "texts": texts}


def open_path(target: str) -> dict:
    """Show a folder or file in Explorer.

    Only paths inside the project or inside the user's own delivery folders are
    opened. The server is on loopback and this is a single-user tool, but a
    route that runs `explorer.exe <anything the body says>` is still a route
    worth keeping honest.
    """
    path = Path(target).expanduser()
    allowed = [paths.ROOT, apply_config.PACKETS]
    for key in ("packets", "resumes"):
        raw = profile.load_optional("delivery.toml").get(key)
        if raw:
            allowed.append(Path(raw).expanduser())
    resolved = path.resolve()
    if not any(resolved == root.resolve() or root.resolve() in resolved.parents
               for root in allowed):
        raise ActionError(f"{path} is outside the folders JobDesk manages")
    if not resolved.exists():
        raise ActionError(f"{path} is not there any more")
    if sys.platform == "win32":
        # `explorer.exe` returns 1 even when it worked, so its exit code is
        # not checked. Selecting the file when it is a file, opening it when
        # it is a folder.
        args = ["explorer.exe"]
        args += ["/select,", str(resolved)] if resolved.is_file() else [str(resolved)]
        subprocess.run(args, check=False)
    else:
        subprocess.run(["xdg-open", str(resolved)], check=False)
    return {"opened": str(resolved)}


# ---------------------------------------------------------------------------
# the application record
# ---------------------------------------------------------------------------

def applications() -> dict:
    """Every application, the funnel, and what is overdue for a follow-up."""
    log = Log()
    rows = []
    for row in sorted(log.rows, key=lambda r: (r.applied_on or r.prepared_on),
                      reverse=True):
        rows.append({
            "id": row.id, "company": row.company, "role": row.role,
            "url": row.url, "source": row.source, "uid": row.uid,
            "score": row.score, "tier": row.tier, "status": row.status,
            "prepared_on": row.prepared_on, "applied_on": row.applied_on,
            "follow_up_due": row.follow_up_due,
            "follow_up_overdue": row.follow_up_overdue,
            "agency": row.agency, "notes": row.notes,
            "rejected_on": row.rejected_on, "rejected_stage": row.rejected_stage,
            "rejected_reason": row.rejected_reason,
            "packet": row.packet, "history": row.history,
            "days_since": _days_since(row.applied_on or row.prepared_on),
        })
    by_source = {k: {"sent": s, "responses": r}
                 for k, (s, r) in log.by_source().items()}
    return {
        "applications": rows,
        "statuses": list(STATUSES),
        "funnel": log.funnel(),
        "rejections": log.rejection_report(),
        "by_source": by_source,
        "due": [r.id for r in log.follow_ups_due()],
    }


def _days_since(iso: str) -> int | None:
    if not iso:
        return None
    try:
        return (date.today() - date.fromisoformat(iso[:10])).days
    except ValueError:
        return None


def _find(log: Log, app_id: str) -> Application:
    app = log.get(app_id)
    if app is None:
        raise ActionError(f"no application with id {app_id}")
    return app


def mark_applied(app_id: str, when: str = "") -> dict:
    """Record that a human went to the posting and submitted it.

    The one place the word "applied" is written, and it is written because
    someone clicked a button that says they did it, never because this program
    did anything.
    """
    log = Log()
    app = _find(log, app_id)
    if when:
        try:
            date.fromisoformat(when)
        except ValueError:
            raise ActionError(f"{when!r} is not a date -- use YYYY-MM-DD")
    log.mark_applied(app, when)
    return {"id": app.id, "status": app.status, "applied_on": app.applied_on,
            "follow_up_due": app.follow_up_due}


def set_status(app_id: str, status: str, note: str = "") -> dict:
    log = Log()
    app = _find(log, app_id)
    if status == "applied":
        return mark_applied(app_id)
    if status not in STATUSES:
        raise ActionError(f"{status!r} is not a status. Pick one of: "
                          f"{', '.join(STATUSES)}")
    log.set_status(app, status, note)
    return {"id": app.id, "status": app.status}


def forget(app_id: str) -> dict:
    """Take a row off the log, for a packet that never became an application.

    Deliberately not called "delete": the packet folder stays on disk. What
    goes is the claim that this was applied to, which is the claim that was
    wrong.
    """
    log = Log()
    app = _find(log, app_id)
    log.remove(app)
    return {"id": app.id, "company": app.company, "role": app.role,
            "removed": True}


def mark_rejected(app_id: str, *, stage: str = "", reason: str = "",
                  when: str = "") -> dict:
    log = Log()
    app = _find(log, app_id)
    log.mark_rejected(app, stage=stage, reason=reason, when=when)
    return {"id": app.id, "status": app.status,
            "rejected_stage": app.rejected_stage,
            "rejected_on": app.rejected_on}


def log_manual(*, company: str, role: str, url: str = "", when: str = "",
               source: str = "manual", notes: str = "") -> dict:
    """Record an application made somewhere else entirely.

    A job found on a company's own site and applied to in a browser is still
    an application, and leaving it out of the log quietly corrupts every number
    on the funnel panel and every duplicate check the guard makes.
    """
    company, role = company.strip(), role.strip()
    if not company or not role:
        raise ActionError("a logged application needs a company and a role")
    when = when or date.today().isoformat()
    try:
        date.fromisoformat(when)
    except ValueError:
        raise ActionError(f"{when!r} is not a date -- use YYYY-MM-DD")
    log = Log()
    app_id = f"{when}_{packet.safe(company)}_{packet.safe(role)}"
    if log.get(app_id):
        raise ActionError(f"{app_id} is already in the log")
    app = Application(id=app_id, company=company, role=role, url=url.strip(),
                      source=source or "manual", prepared_on=when, notes=notes)
    log.add(app)
    log.mark_applied(app, when)
    return {"id": app.id, "status": app.status, "applied_on": app.applied_on}


# ---------------------------------------------------------------------------
# the resume engine
# ---------------------------------------------------------------------------

def engine_report(emit, *, which: str) -> dict:
    """`check` (does the master resume still verify) or `gap` (what is missing).

    Both are engine subcommands run as a subprocess, so their output comes back
    as a string rather than through the captured stdout -- printed here so it
    lands in the console like everything else.
    """
    from ..apply import engine as engine_bridge

    if which not in ("check", "gap"):
        raise ActionError(f"{which!r} is not an engine report")
    if not engine_bridge.available():
        raise ActionError("the resume engine is not in this checkout")
    code, text = (engine_bridge.check() if which == "check"
                  else engine_bridge.gap())
    emit(text if text.endswith("\n") else text + "\n")
    return {"code": code, "which": which}


# ---------------------------------------------------------------------------
# the job description, structured
# ---------------------------------------------------------------------------

def described(row: dict) -> dict:
    """One posting with its JD parsed into blocks the page can render."""
    body = row.get("description") or ""
    return {**row, "blocks": jdstruct.structure(body),
            "jd_chars": len(body)}
