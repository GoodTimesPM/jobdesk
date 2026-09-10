"""The rules that stop an application before it costs something.

Plan item 6 is explicit that the danger in an automated job search is not the
automation, it is the pattern it creates: fourteen applications to one company
in an hour, a reapplication to a req that already said no, two agencies
submitting the same candidate to the same employer. Those are what produce an
actual company-level blacklist.

So every check is run before a packet is built, and each returns a `Check`
rather than raising: the caller decides whether to stop. A BLOCK can still be
overridden with an explicit acknowledgement, because a rule with no override
gets bypassed outside the tool, where nothing is recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from . import config
from .applog import Application, Log

BLOCK = "BLOCK"
WARN = "WARN"
NOTE = "NOTE"


@dataclass
class Check:
    level: str
    rule: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.rule}: {self.message}"


def _days_since(iso: str) -> int | None:
    if not iso:
        return None
    try:
        return (date.today() - date.fromisoformat(iso)).days
    except ValueError:
        return None


def run(log: Log, *, company: str, role: str, url: str = "", uid: str = "",
        dedupe_key: str = "", flags: list[str] | None = None) -> list[Check]:
    """Every guard, in severity order."""
    checks: list[Check] = []
    flags = flags or []

    # 1. The same requisition, twice. The one true never.
    for prior in log.same_req(uid=uid, url=url, dedupe_key=dedupe_key):
        if prior.status == "prepared":
            checks.append(Check(
                WARN, "same-req",
                f"a packet for this exact req was already prepared "
                f"({prior.id}) but never submitted -- reuse it instead of "
                f"building a second one"))
            continue
        days = _days_since(prior.applied_on or prior.prepared_on)
        checks.append(Check(
            BLOCK, "same-req",
            f"already applied to this exact posting on "
            f"{prior.applied_on or prior.prepared_on} "
            f"({days} days ago, status: {prior.status})"))

    # 2. The same role at the same company, inside the cooldown.
    for prior in log.same_role(company, role):
        if prior.status == "prepared" or prior in log.same_req(
                uid=uid, url=url, dedupe_key=dedupe_key):
            continue
        days = _days_since(prior.applied_on or prior.prepared_on)
        if days is not None and days < config.SAME_ROLE_COOLDOWN_DAYS:
            checks.append(Check(
                BLOCK if prior.status == "rejected" else WARN, "role-cooldown",
                f"applied to '{prior.role}' at {prior.company} {days} days ago "
                f"(status: {prior.status}); the cooldown on the same role is "
                f"{config.SAME_ROLE_COOLDOWN_DAYS} days"))

    # 3. Concurrency. Two live applications at one employer reads as interest;
    #    six reads as a spray.
    open_now = [a for a in log.open_at(company)
                if a not in log.same_req(uid=uid, url=url, dedupe_key=dedupe_key)]
    if len(open_now) >= config.MAX_OPEN_PER_COMPANY:
        listed = ", ".join(f"{a.role} ({a.status})" for a in open_now[:4])
        checks.append(Check(
            BLOCK, "concurrency",
            f"{len(open_now)} application(s) already open at {company}: "
            f"{listed}. The cap is {config.MAX_OPEN_PER_COMPANY}"))
    elif open_now:
        checks.append(Check(
            NOTE, "concurrency",
            f"{len(open_now)} application already open at {company} "
            f"({open_now[0].role}) -- this would be number {len(open_now) + 1} "
            f"of {config.MAX_OPEN_PER_COMPANY}"))

    # 4. Agency double-submission (plan item 13). Job Radar's scoring already
    #    flags agency reposts; this is where that flag has to be acted on.
    if any(f.startswith("agency") or f == "staffing-agency" for f in flags):
        prior_direct = [a for a in log.for_company(company) if not a.agency]
        detail = ""
        if prior_direct:
            detail = (f" -- and there is already a direct application at "
                      f"{company} ({prior_direct[0].role}), which is exactly "
                      f"the duplicate-submission case that gets a candidate "
                      f"rejected outright")
        checks.append(Check(
            WARN if not prior_direct else BLOCK, "agency",
            f"Job Radar flagged this as an agency posting{detail}. Find the "
            f"direct req if one exists, and record the agency name on the "
            f"application so the next one can see it"))

    # 5. A previously-agency-submitted employer, applying direct now.
    agencies = {a.agency for a in log.for_company(company) if a.agency}
    if agencies and not any(c.rule == "agency" for c in checks):
        checks.append(Check(
            NOTE, "agency-history",
            f"{company} has been submitted to before via "
            f"{', '.join(sorted(agencies))} -- mention it if a recruiter asks, "
            f"and don't let a second agency submit you there"))

    order = {BLOCK: 0, WARN: 1, NOTE: 2}
    checks.sort(key=lambda c: order[c.level])
    return checks


def blocking(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.level == BLOCK]


def summarize(checks: list[Check]) -> str:
    if not checks:
        return "No guard rules fired -- clear to apply."
    return "\n".join(str(c) for c in checks)
