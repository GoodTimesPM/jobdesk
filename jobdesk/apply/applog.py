"""The application log -- the submission record behind every guard rule.

This file is the point of the sub-project as much as the packets are. Plan
item 13 calls a submission log "real blacklist risk that automation actively
prevents", and item 8's funnel analytics has nothing to analyze without it.

One JSON list, one row per application, written atomically. It is committed
on purpose: it is the dataset behind the weekly review, and it contains
nothing secret.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config

# The lifecycle. `prepared` means a packet exists but nothing was sent -- that
# distinction is what keeps the concurrency cap honest, since a folder on disk
# is not an application.
STATUSES = ("prepared", "applied", "followed-up", "screening", "interview",
            "offer", "rejected", "ghosted", "withdrawn")

# Statuses that still occupy a slot against MAX_OPEN_PER_COMPANY.
OPEN_STATUSES = ("applied", "followed-up", "screening", "interview")

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_LEVEL = re.compile(r"\b(i{1,3}|iv|v|1|2|3|jr|sr|junior|senior|associate|lead)\b")


def role_key(title: str) -> str:
    """A title normalized enough that "Analyst II" and "Analyst 2" collide.

    Used only for the same-role cooldown, which is a warning. Being slightly
    over-eager here costs a confirmation prompt; being under-eager costs a
    duplicate application to the same team.
    """
    low = _LEVEL.sub(" ", (title or "").lower())
    return _NON_ALNUM.sub("", low)


def company_key(company: str) -> str:
    return _NON_ALNUM.sub("", (company or "").lower())


# One Commonwealth agency writes itself four ways across four postings: "Dept
# Conservation & Recreation", "Department of Conservation and Recreation",
# "Dept. of Conservation & Rec". Spelling them apart would put the cap back
# where it started, only quieter -- two open applications at what is really
# one HR office, counted as one each.
_DIVISION_WORDS = {
    "dept": "department", "depts": "department", "div": "division",
    "svcs": "services", "svc": "service", "admin": "administration",
    "assistance": "assistance", "med": "medical", "rec": "recreation",
    "univ": "university", "comm": "commission", "auth": "authority",
    "&": "and",
}
_DIVISION_DROP = {"of", "the", "for", "and", "a"}


def division_key(division: str) -> str:
    """A division name normalized enough that its abbreviations collide."""
    words = re.split(r"[^a-z0-9&]+", (division or "").lower())
    out = []
    for word in words:
        word = _DIVISION_WORDS.get(word, word)
        if word and word not in _DIVISION_DROP:
            out.append(word)
    return "".join(out)


@dataclass
class Application:
    id: str                          # {date}_{company}_{role}, the packet name
    company: str
    role: str
    division: str = ""               # the employer inside a shared board --
                                      # "Dept of Accounts" under "Commonwealth
                                      # of Virginia". Empty when `company` is
                                      # already the employer.
    url: str = ""
    source: str = ""                 # which radar source found it, or "manual"
    uid: str = ""                    # Job Radar's uid, when it came from there
    dedupe_key: str = ""             # Job Radar's company|title identity
    score: int = 0
    tier: str = ""
    status: str = "prepared"
    prepared_on: str = ""
    applied_on: str = ""
    follow_up_due: str = ""
    follow_up_pinged: str = ""       # the follow_up_due value already pushed
                                      # to Discord, so a due date pings once
    resume_variant: str = ""         # the packet folder the resume came from
    agency: str = ""                 # plan item 13: who submitted, if not direct
    packet: str = ""                 # the delivered folder's NAME, never a
                                     # path: this file is tracked in git
    auto: bool = False               # built by `auto`, unread by a human
    overrides: list[str] = field(default_factory=list)
                                     # rules a human waived to build this. The
                                     # point of an override you can reach is
                                     # that it is recorded; a rule with no
                                     # override gets worked around outside the
                                     # tool, where nothing is written down.
    notes: str = ""
    rejected_on: str = ""            # date the "no" arrived
    rejected_stage: str = ""         # furthest stage reached first -- the
                                      # win/loss signal: screening vs. interview
                                      # vs. offer are very different rejections
    rejected_reason: str = ""        # whatever you were actually told
    history: list[str] = field(default_factory=list)

    def touch(self, message: str) -> None:
        self.history.append(f"{datetime.now():%Y-%m-%d %H:%M}  {message}")

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def follow_up_overdue(self) -> bool:
        if self.status != "applied" or not self.follow_up_due:
            return False
        return self.follow_up_due <= date.today().isoformat()


class Log:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.APPLICATIONS_FILE
        self.rows: list[Application] = []
        self.load()

    # -- persistence --------------------------------------------------------
    def load(self) -> None:
        if not self.path.exists():
            self.rows = []
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            # Never lose the file to a parse error -- move it aside so the
            # next save doesn't overwrite whatever is in there.
            backup = self.path.with_suffix(".corrupt.json")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            self.rows = []
            return
        known = set(Application.__dataclass_fields__)
        self.rows = [Application(**{k: v for k, v in row.items() if k in known})
                     for row in raw if isinstance(row, dict)]

    def save(self) -> None:
        payload = json.dumps([asdict(r) for r in self.rows], indent=2)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.path)

    # -- queries ------------------------------------------------------------
    def get(self, app_id: str) -> Application | None:
        for row in self.rows:
            if row.id == app_id:
                return row
        return None

    def find(self, needle: str) -> list[Application]:
        low = needle.lower()
        return [r for r in self.rows
                if low in r.id.lower() or low in r.company.lower()
                or low in r.role.lower()]

    def for_company(self, company: str) -> list[Application]:
        key = company_key(company)
        return [r for r in self.rows if company_key(r.company) == key]

    def open_at(self, company: str) -> list[Application]:
        """Live applications at one company, agencies and all.

        Deliberately does not split by division. Whether two of these rows
        count as the same employer depends on what is known about the incoming
        posting as well, which is the guard's question, not the log's -- and a
        second answer to it living here would only get out of step with the
        first.
        """
        return [r for r in self.for_company(company) if r.is_open]

    def same_req(self, *, uid: str = "", url: str = "",
                 dedupe_key: str = "") -> list[Application]:
        """Applications for the identical requisition.

        Three identities because postings arrive three ways: Job Radar's uid,
        the company|title dedupe key (which survives a URL change), and the
        raw URL for anything typed in by hand.
        """
        hits = []
        for row in self.rows:
            if uid and row.uid and row.uid == uid:
                hits.append(row)
            elif dedupe_key and row.dedupe_key and row.dedupe_key == dedupe_key:
                hits.append(row)
            elif url and row.url and row.url == url:
                hits.append(row)
        return hits

    def same_role(self, company: str, role: str) -> list[Application]:
        ckey, rkey = company_key(company), role_key(role)
        return [r for r in self.rows
                if company_key(r.company) == ckey and role_key(r.role) == rkey]

    def queue_hidden_uids(self) -> set[str]:
        """UIDs the candidate queue should stop showing.

        Everything you have touched, *except* packets `auto` built and never
        opened. Those are the whole point of `auto`: it runs unattended right
        after the morning radar sweep, so hiding them would empty the queue
        before you ever sees it and the packets would sit undiscovered on disk.
        They stay listed, marked as already built.

        The moment one is submitted -- or hand-prepped, which clears `auto` --
        it drops out like any other.
        """
        return {r.uid for r in self.rows
                if r.uid and not (r.auto and r.status == "prepared")}

    def auto_prepared(self) -> dict[str, Application]:
        """uid -> the unopened auto-built packet waiting for it."""
        return {r.uid: r for r in self.rows
                if r.uid and r.auto and r.status == "prepared"}

    def follow_ups_due(self) -> list[Application]:
        return [r for r in self.rows if r.follow_up_overdue]

    def unpinged_follow_ups_due(self) -> list[Application]:
        """Overdue follow-ups whose current due date hasn't been pushed yet.

        `follow_up_pinged` records the due date a ping already went out for,
        not a bare flag -- so a resubmission that moves `follow_up_due`
        forward (mark_applied) is free to ping again, but the same due date
        never fires twice, including across separate script runs (`auto`
        runs three times a day).
        """
        return [r for r in self.follow_ups_due()
                if r.follow_up_pinged != r.follow_up_due]

    def mark_follow_up_pinged(self, app: Application) -> Application:
        app.follow_up_pinged = app.follow_up_due
        self.save()
        return app

    # -- mutation -----------------------------------------------------------
    def add(self, app: Application) -> Application:
        existing = self.get(app.id)
        if existing:
            return existing
        app.prepared_on = app.prepared_on or date.today().isoformat()
        app.touch(f"packet prepared ({app.status})")
        self.rows.append(app)
        self.save()
        return app

    def remove(self, app: Application) -> Application:
        """Drop a row entirely, for the packets that were never applications.

        A posting can close between the packet being built and you getting to
        the form. Nothing was submitted, so nothing belongs in a submission
        log: leaving the row in overstates the funnel, blocks the company
        against the concurrency cap, and hides the posting from the queue if
        it ever reopens.

        The packet folder on disk is left alone. This file is the record of
        what was sent, and deleting someone's tailored resume because they
        tidied a row is not a trade worth making.
        """
        self.rows = [r for r in self.rows if r.id != app.id]
        self.save()
        return app

    def mark_applied(self, app: Application, when: str = "") -> Application:
        app.applied_on = when or date.today().isoformat()
        app.status = "applied"
        due = date.fromisoformat(app.applied_on) + timedelta(days=config.FOLLOW_UP_DAYS)
        app.follow_up_due = due.isoformat()
        app.touch(f"submitted on {app.applied_on}, follow up {app.follow_up_due}")
        self.save()
        return app

    def set_status(self, app: Application, status: str, note: str = "") -> Application:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}; expected one of "
                             f"{', '.join(STATUSES)}")
        app.status = status
        if note:
            app.notes = (app.notes + "\n" + note).strip() if app.notes else note
        app.touch(f"status -> {status}" + (f" ({note})" if note else ""))
        self.save()
        return app

    def mark_rejected(self, app: Application, *, stage: str = "",
                      reason: str = "", when: str = "") -> Application:
        """Log a rejection with enough on it for a later win/loss read.

        `stage` defaults to whatever status the application was already at --
        the furthest point it reached before the no -- because a rejection at
        `screening` (never read past the resume) and one at `interview`
        (something said out loud) are different problems, and plan item 8
        has nothing to analyze if that distinction isn't recorded here.
        """
        app.rejected_stage = stage or app.status
        app.rejected_reason = reason
        app.rejected_on = when or date.today().isoformat()
        app.status = "rejected"
        note = f"rejected at {app.rejected_stage}" + (f": {reason}" if reason else "")
        app.touch(note)
        self.save()
        return app

    def rejection_report(self) -> dict[str, int]:
        """Rejections grouped by the stage reached -- item 8's win/loss read:
        are the no's landing at screening (a resume/ATS problem) or after an
        interview (something else entirely)."""
        counts: dict[str, int] = {}
        for row in self.rows:
            if row.status != "rejected":
                continue
            key = row.rejected_stage or "unknown"
            counts[key] = counts.get(key, 0) + 1
        return counts

    # -- funnel (plan item 8) ----------------------------------------------
    def funnel(self) -> dict[str, int]:
        counts = {s: 0 for s in STATUSES}
        for row in self.rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        return counts

    def by_source(self) -> dict[str, tuple[int, int]]:
        """source -> (applications sent, responses).

        A response is anything past `applied`/`followed-up`/`ghosted` -- the
        point of the number is "did a human reply", not "did I get the job".
        """
        out: dict[str, list[int]] = {}
        for row in self.rows:
            if row.status == "prepared":
                continue
            slot = out.setdefault(row.source or "unknown", [0, 0])
            slot[0] += 1
            if row.status in ("screening", "interview", "offer", "rejected"):
                slot[1] += 1
        return {k: (v[0], v[1]) for k, v in sorted(out.items())}
