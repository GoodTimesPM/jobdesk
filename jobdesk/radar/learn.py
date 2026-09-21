"""Turn a good posting into a company worth watching every day.

The aggregators are how you meet a company; the company's own ATS is how you
beat everyone else to its next req. Today those are separate worlds. 91
employers are on the watch list because someone sat down and typed them in,
while a run that surfaced 442 postings from 203 companies threw 155 of them
away the moment the digest went out. Several scored in the nineties. If a
company posted one job worth reading this week, its next one is worth reading
too, and nothing was learning that.

So: after scoring, take the companies whose best posting cleared a bar, probe
for their ATS, and keep the ones a probe can confirm. Three rules keep it from
turning into a crawler.

  * Confirmed or nothing. `discover.find` accepts a board only when it is
    advertising a title this company was already seen posting. A wrong board
    quietly pollutes every future run, which is worse than no board.
  * A miss is remembered. A company with no public feed is not re-probed
    tomorrow, or the same fifty names burn the budget every morning forever.
  * A hard call budget. Discovery is the one cost here that has nothing to do
    with how much data came in: 30 site guesses times 7 datacenters times
    however many companies turned up today.

What it writes is machine-owned and lives in `data/radar/`, never in the
user's profile. `profile/employers.toml` is a hand-curated file with comments
explaining why each company is on it, and a program has no business rewriting
that. Move a block across by hand to make it permanent and give it a tier.
"""

from __future__ import annotations

import json
import tomllib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .. import profile as profile_files
from . import config, discover, profile as targeting
from .models import Job


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        # A corrupt learned file is not worth failing a run over. It is
        # derived data: the next run rebuilds what it can.
        return {}


def learned() -> list[dict]:
    """Employers discovered by past runs, in employers.toml's own shape."""
    return _read(config.LEARNED_EMPLOYERS).get("employer", [])


def _misses(data: dict) -> dict[str, dict]:
    return {str(m.get("name", "")).lower(): m for m in data.get("miss", [])}


def _known_names() -> set[str]:
    curated = profile_files.load("employers.toml").get("employer", [])
    return {str(e.get("name", "")).lower() for e in curated + learned()}


def _is_agency(name: str) -> bool:
    """Staffing firms post constantly and are not employers you can watch."""
    low = name.lower()
    return any(a.lower() in low for a in targeting.STAFFING_AGENCIES)


def candidates(jobs: list[Job], data: dict, today: date) -> list[tuple[str, int, list[str]]]:
    """Companies worth probing, best first, as (name, best score, titles).

    A company qualifies on its BEST posting, not its average. One req in the
    nineties says the company hires people like you; four clerical reposts
    alongside it do not argue otherwise.
    """
    known = _known_names()
    misses = _misses(data)
    cutoff = today - timedelta(days=config.LEARN_RETRY_DAYS)

    best: dict[str, int] = {}
    titles: dict[str, list[str]] = {}
    for job in jobs:
        name = (job.company or "").strip()
        if not name or job.score < config.LEARN_MIN_SCORE:
            continue
        low = name.lower()
        if low in known or _is_agency(name):
            continue
        # The name list only knows the agencies somebody thought to type.
        # The flag knows the ones that gave themselves away in the body,
        # which is most of them: a single Richmond pull produced fourteen
        # contract shops advertising one state req between them, and every
        # one would otherwise have been probed and watched as an employer.
        if "staffing-agency" in (job.flags or []):
            continue
        miss = misses.get(low)
        if miss and _tried_on(miss) > cutoff:
            continue
        best[low] = max(best.get(low, 0), job.score)
        titles.setdefault(low, [])
        if job.title and job.title not in titles[low]:
            titles[low].append(job.title)

    # Re-key on the name as it was actually written, not the lowercased
    # comparison key, so what lands in the file is what the board said.
    display = {}
    for job in jobs:
        low = (job.company or "").strip().lower()
        if low in best and low not in display:
            display[low] = job.company.strip()

    rows = [(display[low], score, titles[low]) for low, score in best.items()]
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows[:config.LEARN_MAX_NEW_PER_RUN]


def _tried_on(miss: dict) -> date:
    try:
        return date.fromisoformat(str(miss.get("tried_on", "")))
    except ValueError:
        return date.min


def run(jobs: list[Job], *, today: date | None = None) -> list[dict]:
    """Probe today's best unwatched companies. Returns the new employer rows.

    Called once per run, after scoring. Returns an empty list and writes
    nothing when the feature is off, when nothing qualifies, or when every
    probe missed -- the misses are still recorded, which is the point of
    recording them.
    """
    if not config.LEARN_ENABLED:
        return []
    today = today or datetime.now(timezone.utc).date()
    data = _read(config.LEARNED_EMPLOYERS)
    wanted = candidates(jobs, data, today)
    if not wanted:
        return []

    budget = discover.Budget(config.LEARN_PROBE_BUDGET)
    found: list[dict] = []
    misses = list(data.get("miss", []))
    miss_index = {str(m.get("name", "")).lower(): i for i, m in enumerate(misses)}

    for name, score, seen_titles in wanted:
        if budget.left <= 0:
            break
        board, note = discover.find(name, seen_titles, budget)
        if board is None:
            row = {"name": name, "tried_on": today.isoformat(), "note": note}
            at = miss_index.get(name.lower())
            if at is None:
                misses.append(row)
            else:
                misses[at] = row
            continue
        entry = board.entry(name)
        entry.update({
            "tier": config.LEARN_TIER,
            "learned_on": today.isoformat(),
            "learned_score": score,
            "confirmed_by": discover.confirms(board, seen_titles) or "",
        })
        found.append(entry)

    write(list(data.get("employer", [])) + found, misses)
    return found


# --------------------------------------------------------------------------
# Writing it back
#
# Hand-rolled rather than a TOML writer, because the only thing being written
# is flat string/int tables and the dependency is not worth it. Values go
# through json.dumps, whose string escaping is what a TOML basic string wants.
# --------------------------------------------------------------------------

HEADER = """# Employers JobDesk found on its own.
#
# Written by the radar after each run, and rewritten wholesale -- edits here
# do not survive. A company lands here when one of its postings scored well
# and a probe found a public feed that was advertising that same job title.
#
# To keep one permanently, copy its block into profile/employers.toml and
# give it a tier. To stop watching one, delete its block and add the name to
# the [[miss]] list below; a name that is already a miss is not re-probed.
"""


def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return json.dumps(str(v))


# Bookkeeping about the row, not about the board it points at.
_ABOUT_THE_ROW = ("name", "tier", "learned_on", "learned_score", "confirmed_by")


def _board_of(row: dict) -> tuple:
    """What makes two entries the same board, ignoring who we filed it under."""
    return tuple(sorted((k, str(v)) for k, v in row.items()
                        if k not in _ABOUT_THE_ROW))


def _one_per_board(employers: list[dict]) -> list[dict]:
    """Drop entries that point at a board another entry already holds.

    One organisation can reach this list under two names. A seed list pasted
    from two public rosters carried both "Federal Reserve Bank Richmond" and
    "Richmond Federal Reserve", and both resolved to workday/rb; the same
    happened to the Virginia Retirement System. Nothing downstream noticed,
    which is the problem: every run then fetched that board twice and paid
    twice for one answer.

    The first entry wins, so the name that got there first is the name it
    keeps. Which of two names for one employer is the better one is not
    something this file can know, and either is right.
    """
    seen: set[tuple] = set()
    out = []
    for row in employers:
        board = _board_of(row)
        if board in seen:
            continue
        seen.add(board)
        out.append(row)
    return out


def write(employers: list[dict], misses: list[dict]) -> None:
    lines = [HEADER]
    for row in _one_per_board(employers):
        lines.append("[[employer]]")
        for key, value in row.items():
            lines.append(f"{key} = {_value(value)}")
        lines.append("")
    if misses:
        lines.append("# Probed and nothing public found. Retried after "
                     f"{config.LEARN_RETRY_DAYS} days.")
        lines.append("")
        for row in misses:
            lines.append("[[miss]]")
            for key, value in row.items():
                lines.append(f"{key} = {_value(value)}")
            lines.append("")
    config.LEARNED_EMPLOYERS.parent.mkdir(parents=True, exist_ok=True)
    config.LEARNED_EMPLOYERS.write_text("\n".join(lines), encoding="utf-8")
