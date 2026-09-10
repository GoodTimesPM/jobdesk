"""Every JSON endpoint the page calls, and nothing else.

One dict, `ROUTES`, mapping (method, path) to a function of (query, body).
Return a JSON-able object and the server sends 200; raise `BadRequest` and it
sends 400 with the message, which is why every message in here is written for
a person to read rather than a log to swallow.

The rule that keeps this file small: **no logic lives here that the CLI does
not already have**. `setup.write` builds a profile, `score.score_all` scores
postings, `tomlpatch.patch` edits a file. This module unpacks a request, calls
one of them, and packs the answer. If a behaviour exists only in here, the
page and the terminal have drifted, and the terminal is the one people fall
back on when something breaks.
"""

from __future__ import annotations

import base64
import binascii
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .. import paths, profile
from ..radar import config as radar_config
from . import actions, archive, jdstruct, resume_import, runner, setup, tomlpatch


class BadRequest(RuntimeError):
    """The request cannot be honoured and the user can fix it. Say how."""


class Raw:
    """A response that is bytes rather than JSON.

    One route needs this: handing over a file from a packet. Every other
    endpoint answers with an object the page turns into DOM, and adding a
    general "return whatever you like" contract for the sake of one of them
    would cost more than the small special case in `server._dispatch`.
    """

    __slots__ = ("body", "content_type", "filename")

    def __init__(self, body: bytes, content_type: str, filename: str = ""):
        self.body = body
        self.content_type = content_type
        self.filename = filename


# ---------------------------------------------------------------------------
# Where the app is
# ---------------------------------------------------------------------------

def status(query, body) -> dict:
    """What the page shows before it decides which screen to open on.

    A profile that is the shipped example is reported as no profile, because
    scores computed against a fictional candidate are worse than none: they
    look real.
    """
    problems = profile.check()
    configured = profile.REAL.is_dir() and not problems
    rows, stamp = _candidates()
    return {
        "configured": configured,
        "using_example": profile.is_example(),
        "profile_dir": str(profile.directory()) if not problems else "",
        "problems": problems,
        "name": profile.identity().get("name", "") if not problems else "",
        "job_count": len(rows),
        "last_run": stamp,
    }


def _candidates() -> tuple[list[dict], str]:
    """The candidate cache and when the radar last wrote it."""
    path = radar_config.CANDIDATES
    if not path.exists():
        return [], ""
    try:
        rows = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise BadRequest(
            f"{path.name} could not be read ({exc}). Run the radar again to "
            f"rebuild it: py -m jobdesk.radar.main")
    stamp = datetime.fromtimestamp(path.stat().st_mtime,
                                   timezone.utc).isoformat()
    return [r for r in rows if isinstance(r, dict)], stamp


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------

def parse_resume(query, body) -> dict:
    """A resume file in, the fields the parser could find out.

    The file arrives base64-encoded in the JSON body rather than as a
    multipart upload, because a resume is a couple hundred KB and parsing
    multipart by hand in `http.server` is a security surface nobody needs for
    one local user.

    Every guessed field is listed in `filled` so the page can mark it as a
    guess. The parser is confident about the email and the phone; it is
    genuinely unsure which lines are job titles, so it returns several and the
    user picks. Presenting six candidates and asking beats picking one and
    being wrong half the time.
    """
    name = str(body.get("filename") or "").strip()
    raw = body.get("content") or ""
    if not name:
        raise BadRequest("no filename came with the upload")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise BadRequest("the file did not arrive intact. Try the upload again.")
    if not data:
        raise BadRequest(f"{name} is empty")

    # Written to a temp file because the readers (fitz, python-docx) both want
    # a path, and deleted immediately after. A resume is the most personal
    # file JobDesk touches and it has no business lingering in %TEMP%.
    suffix = Path(name).suffix or ".txt"
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    temp = Path(handle.name)
    try:
        handle.write(data)
        handle.close()
        parsed = resume_import.parse(temp)
    except resume_import.UnreadableResume as exc:
        raise BadRequest(str(exc))
    finally:
        temp.unlink(missing_ok=True)

    result = parsed.as_dict()
    result["filename"] = name
    # The raw text goes back to the page and returns with the save, where it
    # is written beside the profile as a reference copy. Round-tripping it
    # rather than caching it server-side keeps the server stateless: two
    # browser tabs half way through setup cannot overwrite each other.
    result["text"] = parsed.text
    return result


def save_setup(query, body) -> dict:
    """Write `profile/` from the finished form."""
    answers = setup.Answers(
        name=_text(body, "name"),
        email=_text(body, "email"),
        phone=_text(body, "phone"),
        linkedin=_text(body, "linkedin"),
        github=_text(body, "github"),
        city=_text(body, "city"),
        state=_text(body, "state"),
        titles_1=_list(body, "titles_1"),
        titles_2=_list(body, "titles_2"),
        titles_3=_list(body, "titles_3"),
        work_mode=_text(body, "work_mode") or "any",
        radius_miles=_int(body, "radius_miles", 30),
        salary_floor=_int(body, "salary_floor", 0),
        salary_target=_int(body, "salary_target", 0),
        dealbreakers=_list(body, "dealbreakers"),
        employers=[e for e in body.get("employers") or [] if isinstance(e, dict)],
        resume_path=_text(body, "resume_path"),
        resume_text=str(body.get("resume_text") or ""),
        delivery_resumes=_text(body, "delivery_resumes"),
        delivery_packets=_text(body, "delivery_packets"),
    )
    problems = setup.validate(answers)
    if problems:
        raise BadRequest(" ".join(problems))
    try:
        written = setup.write(answers, overwrite=bool(body.get("overwrite")))
    except setup.SetupError as exc:
        raise BadRequest(str(exc))
    written["next"] = (
        "Run the radar to fill the job list: py -m jobdesk.radar.main. "
        "Then open master.toml and write your own experience into it -- "
        "setup filled in your contact details and left the rest alone, "
        "because nothing should put words in your mouth.")
    return written


def check_setup(query, body) -> dict:
    """Validate a form without writing anything, so Next can be greyed out."""
    problems = setup.validate(setup.Answers(
        name=_text(body, "name"), email=_text(body, "email"),
        city=_text(body, "city"), state=_text(body, "state"),
        titles_1=_list(body, "titles_1"),
        work_mode=_text(body, "work_mode") or "any",
        salary_floor=_int(body, "salary_floor", 0),
        salary_target=_int(body, "salary_target", 0),
        delivery_resumes=_text(body, "delivery_resumes"),
        delivery_packets=_text(body, "delivery_packets"),
    ))
    return {"ok": not problems, "problems": problems}


# ---------------------------------------------------------------------------
# The command table
# ---------------------------------------------------------------------------

# The JD body is the biggest field by an order of magnitude and the table
# never shows it, so the list endpoint drops it and `/api/job` fetches one on
# demand. 300 postings with bodies is several MB of JSON for a table that
# renders 300 rows of text.
_LIST_DROP = ("description",)


def jobs(query, body) -> dict:
    """Today's scored postings, for the table.

    No filtering or sorting here. The page has all the rows and does both in
    the browser, which is instant and means a filter never costs a round trip.
    """
    rows, stamp = _candidates()
    listed = [{k: v for k, v in row.items() if k not in _LIST_DROP}
              for row in rows]
    for row in listed:
        row["age_days"] = _age_days(row.get("posted_at"))
    _mark_rows(listed)
    return {
        "jobs": listed,
        "last_run": stamp,
        "using_example": profile.is_example(),
        "tiers": {t: sum(1 for r in listed if r.get("tier") == t)
                  for t in ("A", "B", "C", "D", "F")},
        "floor": _effective_floor(listed),
    }


def _effective_floor(rows: list[dict]) -> dict:
    """The score the cache actually starts at, next to the one that was set.

    These are the same number until the size cap bites, and then they are not,
    and the gap is invisible from the table: a Jobs tab holding nothing under
    68 looks exactly like a market with no C-tier postings in it. It is worth
    one line above the table to say which of those two things is happening.
    """
    from ..radar import candidates, config as radar_config
    configured = radar_config.MIN_SCORE_TO_REPORT
    lowest = min((r.get("score") or 0 for r in rows), default=configured)
    return {
        "configured": configured,
        "lowest": lowest,
        "cap": candidates.MAX_ENTRIES,
        "capped": len(rows) >= candidates.MAX_ENTRIES and lowest > configured,
    }


def job(query, body) -> dict:
    """One posting in full, for the expanded row.

    The JD comes back as blocks rather than as the stored string. Half these
    postings arrive from Workday with every newline stripped out, and the row
    used to render that as one unbroken grey paragraph -- which is a wall
    nobody reads, on the single screen where reading carefully is the point.
    """
    uid = (query.get("uid") or [""])[0]
    if not uid:
        raise BadRequest("which posting? pass ?uid=")
    rows, _ = _candidates()
    for row in rows:
        if row.get("uid") == uid:
            full = dict(row)
            body_text = full.pop("description", "") or ""
            full["blocks"] = jdstruct.structure(body_text)
            full["jd_chars"] = len(body_text)
            full["age_days"] = _age_days(full.get("posted_at"))
            _mark_rows([full])
            full["guards"] = actions.check_guards(actions.candidate(uid))
            return full
    raise BadRequest(
        f"no posting with id {uid} in the current list. It may have aged out "
        f"of the cache -- the radar keeps 30 days.")


def _age_days(posted: str | None) -> int | None:
    """How many days ago a posting went up, or None if it never said."""
    from ..radar.models import parse_date
    when = parse_date(posted)
    if when is None:
        return None
    return max(0, (datetime.now(timezone.utc) - when).days)


def _log_state() -> dict[str, dict]:
    """uid -> what the application log says about that posting.

    A packet on disk and a submitted application are two different facts and
    the table used to show them as one: every posting with a packet was greyed
    out as "applied", which hid 127 postings nobody had actually applied to.
    So this carries the status, and the page can say `packet built` and
    `applied` separately.

    Read defensively. The log is the apply package's file, and a missing or
    half-written one should dim nothing rather than break the table.
    """
    path = paths.DATA / "apply" / "applications.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(rows, list):
        return {}
    state: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("uid"):
            continue
        state[str(row["uid"])] = {
            "id": row.get("id") or "",
            "status": row.get("status") or "prepared",
            "applied_on": row.get("applied_on") or "",
            "packet": row.get("packet") or "",
        }
    return state


# Statuses that mean a human actually sent it. `prepared` is a folder on disk.
_SENT = ("applied", "followed-up", "screening", "interview", "offer",
         "rejected", "ghosted", "withdrawn")


def _mark_rows(rows: list[dict]) -> None:
    """Stamp each posting with what the application log knows about it."""
    state = _log_state()
    for row in rows:
        known = state.get(row.get("uid") or "")
        row["applied"] = bool(known and known["status"] in _SENT)
        row["prepared"] = bool(known and known["status"] == "prepared")
        row["application"] = known["id"] if known else ""
        row["app_status"] = known["status"] if known else ""


# ---------------------------------------------------------------------------
# The criteria panel
# ---------------------------------------------------------------------------

# What the panel is allowed to edit. A deliberately short list: these are the
# settings whose effect you can see in the table the moment they change.
# Everything else in targeting.toml stays a file you open, because a checkbox
# for `equivalency_ceiling` without the paragraph above it explaining what it
# means is a worse interface than the paragraph.
EDITABLE = {
    "tier_1_titles": list, "tier_2_titles": list, "tier_3_titles": list,
    "hard_disqualifiers": list, "local_terms": list, "remote_terms": list,
    "hybrid_terms": list, "core_skills": list, "supporting_skills": list,
    "salary_floor": int, "salary_target": int,
    "years_comfortable": int, "max_years_stretch": int,
    "fresh_days": int, "stale_days": int,
    "home_metro": str,
}


def targeting(query, body) -> dict:
    """The editable settings and their current values."""
    if profile.is_example():
        raise BadRequest(
            "You are running on the example profile, which is shared code -- "
            "editing it would change what everyone else's fresh clone starts "
            "from. Finish setup first and this panel edits your own file.")
    data = profile.load("targeting.toml")
    return {
        "file": str(profile.path("targeting.toml")),
        "values": {key: data.get(key) for key in EDITABLE},
        "types": {key: kind.__name__ for key, kind in EDITABLE.items()},
    }


def save_targeting(query, body) -> dict:
    """Patch `targeting.toml`, then rescore the stored postings against it.

    The rescore is the point. Changing a tier list and immediately seeing
    which postings moved is the difference between tuning the criteria and
    guessing at them. It runs against the cache, so it costs no network calls
    and nothing is re-fetched.
    """
    if profile.is_example():
        raise BadRequest("finish setup before editing targeting")
    changes = body.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise BadRequest("no changes were sent")

    clean: dict[str, object] = {}
    for key, value in changes.items():
        kind = EDITABLE.get(key)
        if kind is None:
            raise BadRequest(f"{key} is not editable from this panel. "
                             f"Open targeting.toml to change it.")
        if kind is list:
            if not isinstance(value, list):
                raise BadRequest(f"{key} has to be a list")
            clean[key] = [" ".join(str(v).lower().split()) for v in value
                          if str(v).strip()]
        elif kind is int:
            try:
                clean[key] = int(value)
            except (TypeError, ValueError):
                raise BadRequest(f"{key} has to be a whole number, not {value!r}")
        else:
            clean[key] = str(value).strip()

    path = profile.path("targeting.toml")
    original = path.read_text(encoding="utf-8")
    try:
        patched = tomlpatch.patch(original, clean)
    except tomlpatch.PatchError as exc:
        raise BadRequest(f"targeting.toml could not be edited: {exc}")

    path.write_text(patched, encoding="utf-8")
    profile._read.cache_clear()          # the next lookup reads the new file
    try:
        result = rescore({}, {})
    except Exception:
        # A file that scores nothing is worse than an unsaved change, so put
        # the old one back before re-raising.
        path.write_text(original, encoding="utf-8")
        profile._read.cache_clear()
        raise
    result["saved"] = list(clean)
    return result


def rescore(query, body) -> dict:
    """Re-run scoring over the cached postings and report what moved.

    Writes nothing. The table redraws from what comes back; the cache is the
    radar's file and only the radar rewrites it.
    """
    from ..radar import score
    from ..radar.models import Job

    rows, stamp = _candidates()
    before = {r.get("uid"): (r.get("score") or 0) for r in rows}
    scored = []
    for row in rows:
        job_obj = score.score_job(Job.from_dict(row))
        out = {k: v for k, v in row.items() if k not in _LIST_DROP}
        out.update(score=job_obj.score, tier=job_obj.tier,
                   reasons=job_obj.reasons, flags=job_obj.flags,
                   job_family=job_obj.job_family)
        out["age_days"] = _age_days(row.get("posted_at"))
        out["was"] = before.get(row.get("uid"), 0)
        scored.append(out)
    _mark_rows(scored)
    scored.sort(key=lambda r: (-r["score"], r.get("company") or ""))

    moved = sum(1 for r in scored if r["score"] != r["was"])
    return {
        "jobs": scored,
        "last_run": stamp,
        "moved": moved,
        "tiers": {t: sum(1 for r in scored if r["tier"] == t)
                  for t in ("A", "B", "C", "D", "F")},
        "note": "Scores only. Nothing was written and no posting was fetched.",
    }


# ---------------------------------------------------------------------------

def _text(body: dict, key: str) -> str:
    return str(body.get(key) or "").strip()


def _int(body: dict, key: str, default: int) -> int:
    value = body.get(key)
    if value in (None, ""):
        return default
    try:
        return int(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        raise BadRequest(f"{key} has to be a number, not {value!r}")


def _list(body: dict, key: str) -> list[str]:
    value = body.get(key) or []
    if isinstance(value, str):
        value = [v for v in value.split(",")]
    if not isinstance(value, list):
        raise BadRequest(f"{key} has to be a list")
    return [str(v).strip() for v in value if str(v).strip()]


# ---------------------------------------------------------------------------
# The archive
# ---------------------------------------------------------------------------

def archive_search(query, body) -> dict:
    """The whole history of what the radar has seen, filtered server-side."""
    return archive.search(
        text=(query.get("q") or [""])[0],
        min_score=int((query.get("min_score") or ["0"])[0] or 0),
        since=(query.get("since") or [""])[0],
        limit=int((query.get("limit") or ["200"])[0] or 200),
        offset=int((query.get("offset") or ["0"])[0] or 0),
    ) | {"summary": archive.summary()}


def archive_companies(query, body) -> dict:
    """Who posts the most and whose postings actually score."""
    return {"companies": archive.companies(
        int((query.get("limit") or ["60"])[0] or 60))}


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

def applications(query, body) -> dict:
    """Everything prepared or sent, with the funnel and the follow-ups due."""
    return actions.applications()


def application_status(query, body) -> dict:
    """Move one application along: applied, screening, interview, rejected.

    `applied` is the only status in here that means a human did something
    outside this program, and it is set because a person clicked the button
    that says so.
    """
    app_id = _text(body, "id")
    if not app_id:
        raise BadRequest("which application? send an id")
    status_name = _text(body, "status")
    try:
        if status_name == "applied":
            return actions.mark_applied(app_id, _text(body, "when"))
        if status_name == "rejected":
            return actions.mark_rejected(app_id, stage=_text(body, "stage"),
                                         reason=_text(body, "reason"),
                                         when=_text(body, "when"))
        return actions.set_status(app_id, status_name, _text(body, "note"))
    except actions.ActionError as exc:
        raise BadRequest(str(exc))


def application_manual(query, body) -> dict:
    """Log an application made somewhere this tool never touched."""
    try:
        return actions.log_manual(
            company=_text(body, "company"), role=_text(body, "role"),
            url=_text(body, "url"), when=_text(body, "when"),
            source=_text(body, "source") or "manual",
            notes=_text(body, "notes"))
    except actions.ActionError as exc:
        raise BadRequest(str(exc))


def packet(query, body) -> dict:
    """One packet's folder, with APPLY.md and the letter read in."""
    app_id = (query.get("id") or [""])[0]
    if not app_id:
        raise BadRequest("which packet? pass ?id=")
    try:
        return actions.packet_files(app_id)
    except actions.ActionError as exc:
        raise BadRequest(str(exc))


def open_folder(query, body) -> dict:
    """Show a packet folder in Explorer."""
    try:
        return actions.open_path(_text(body, "path"))
    except actions.ActionError as exc:
        raise BadRequest(str(exc))


# ---------------------------------------------------------------------------
# Runs: the console
# ---------------------------------------------------------------------------

def _sweep_work(body):
    dry = bool(body.get("dry"))
    only = _text(body, "only")
    return lambda emit: actions.sweep(emit, dry=dry, only=only)


def _packet_work(body):
    kwargs = dict(uid=_text(body, "uid"), url=_text(body, "url"),
                  company=_text(body, "company"), title=_text(body, "title"),
                  note=str(body.get("note") or "").strip(),
                  jd=str(body.get("jd") or "").strip(),
                  force=bool(body.get("force")),
                  allow_fetch=body.get("fetch", True) is not False)
    return lambda emit: actions.build_packet(emit, **kwargs)


def _engine_work(body):
    which = _text(body, "which") or "check"
    return lambda emit: actions.engine_report(emit, which=which)


def start_run(query, body) -> dict:
    """Kick off a sweep, a packet build or an engine report.

    Returns immediately with a run id. The console polls `/api/run?id=` for
    output, so a build that takes ninety seconds does not hold a socket open
    for ninety seconds.
    """
    kind = _text(body, "kind")
    if kind == "sweep":
        label, work = "radar sweep", _sweep_work(body)
    elif kind == "packet":
        who = _text(body, "company") or _text(body, "uid") or "posting"
        if _text(body, "uid"):
            try:
                cand = actions.candidate(_text(body, "uid"))
                who = f"{cand.company} - {cand.title}"
            except actions.ActionError as exc:
                raise BadRequest(str(exc))
        label, work = f"packet: {who}", _packet_work(body)
    elif kind == "engine":
        which = _text(body, "which") or "check"
        label, work = f"resume engine: {which}", _engine_work(body)
    else:
        raise BadRequest(f"{kind!r} is not something the app runs. "
                         f"Expected sweep, packet or engine.")

    def guarded(emit):
        try:
            return work(emit)
        except actions.ActionError as exc:
            emit(f"\n{exc}\n")
            return {"ok": False, "problems": [str(exc)]}

    run = runner.start(label, kind, guarded)
    return {"id": run.id, "label": run.label, "kind": run.kind,
            "state": run.state}


def run_tail(query, body) -> dict:
    """Output since a line number, plus whether the run has finished."""
    run_id = (query.get("id") or [""])[0]
    if not run_id:
        raise BadRequest("which run? pass ?id=")
    after = int((query.get("after") or ["0"])[0] or 0)
    return runner.tail(run_id, after)


def run_list(query, body) -> dict:
    active = runner.active()
    return {"runs": [r.head() for r in runner.recent(20)],
            "active": active.head() if active else None}


# ---------------------------------------------------------------------------
# The desktop
# ---------------------------------------------------------------------------

def make_shortcut(query, body) -> dict:
    """Put JobDesk on the desktop, so opening it is a double-click."""
    from . import shortcut

    try:
        link = shortcut.create()
    except shortcut.ShortcutError as exc:
        raise BadRequest(str(exc))
    return {"path": str(link)}


def packet_file(query, body):
    """One file out of a packet, as bytes.

    This exists for the phone. On the desktop the answer to "let me see the
    PDF" is the Explorer button, and on a phone that button does nothing at
    all -- the file manager it opens is on a machine in another room. So the
    resume and the cover letter come down the same socket as everything else.

    `actions.packet_files` decides what is in the folder and this only serves
    what that listed, by name, with no path separators of any kind. A route
    that reads a filename out of a query string is a route that gets pointed
    at `../../.env` sooner or later.
    """
    app_id = (query.get("id") or [""])[0]
    name = (query.get("name") or [""])[0]
    if not app_id or not name:
        raise BadRequest("which file? pass ?id= and ?name=")
    listing = packet(query, body)
    if not any(f["name"] == name for f in listing["files"]):
        raise BadRequest(f"there is no {name} in that packet")
    target = Path(listing["folder"]) / name
    if target.name != name or not target.is_file():
        raise BadRequest(f"there is no {name} in that packet")
    import mimetypes
    kind, _ = mimetypes.guess_type(name)
    return Raw(target.read_bytes(), kind or "application/octet-stream", name)


# ---------------------------------------------------------------------------
# The phone
# ---------------------------------------------------------------------------

def phone_state(query, body) -> dict:
    """Everything the Phone panel draws, including the QR code as SVG.

    The token is inside that URL and inside that QR code, and this is the one
    route that hands it out. It is reachable from loopback, which is the
    desktop the .env file is already sitting on, or from a client that got past
    the gate and therefore already holds it. Neither learns anything it could
    not read directly.
    """
    from . import phone

    return phone.state(_int_query(query, "port", phone.DEFAULT_PORT))


def phone_switch(query, body) -> dict:
    """Turn phone access on or off.

    `net.NoAddress` is the honest failure and gets its own message: a laptop
    with the wifi off has nothing safe to bind, and that is a fact about the
    room rather than a bug.
    """
    from . import net, phone

    port = _int(body, "port", phone.DEFAULT_PORT)
    want_on = bool(body.get("on"))
    try:
        return phone.turn_on(port) if want_on else phone.turn_off(port)
    except net.NoAddress as exc:
        raise BadRequest(str(exc))
    except OSError as exc:
        raise BadRequest(f"could not set up phone access: {exc}")


def phone_rotate(query, body) -> dict:
    """Mint a new token, which logs every paired device out at once.

    Its own route rather than a flag on the switch, for the same reason it is
    its own function in `phone.py`: rotating is destructive to anything already
    paired, and destructive things do not happen on the way to somewhere else.
    """
    from . import phone

    try:
        return phone.rotate(_int(body, "port", phone.DEFAULT_PORT))
    except OSError as exc:
        raise BadRequest(f"could not write the new token: {exc}")


def _int_query(query, name: str, fallback: int) -> int:
    raw = (query.get(name) or [""])[0]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback


ROUTES = {
    ("GET", "/api/status"): status,
    ("GET", "/api/phone"): phone_state,
    ("POST", "/api/phone"): phone_switch,
    ("POST", "/api/phone/rotate"): phone_rotate,
    ("POST", "/api/setup/resume"): parse_resume,
    ("POST", "/api/setup/check"): check_setup,
    ("POST", "/api/setup/save"): save_setup,
    ("GET", "/api/jobs"): jobs,
    ("GET", "/api/job"): job,
    ("GET", "/api/targeting"): targeting,
    ("POST", "/api/targeting"): save_targeting,
    ("POST", "/api/rescore"): rescore,
    ("GET", "/api/archive"): archive_search,
    ("GET", "/api/archive/companies"): archive_companies,
    ("GET", "/api/applications"): applications,
    ("POST", "/api/application/status"): application_status,
    ("POST", "/api/application/manual"): application_manual,
    ("GET", "/api/packet"): packet,
    ("GET", "/api/packet/file"): packet_file,
    ("POST", "/api/open"): open_folder,
    ("POST", "/api/run/start"): start_run,
    ("GET", "/api/run"): run_tail,
    ("GET", "/api/runs"): run_list,
    ("POST", "/api/shortcut"): make_shortcut,
}
