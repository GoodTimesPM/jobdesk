"""Which job titles a run searches for, and which it leaves for next time.

The Criteria tab shows three tier lists, around fifty titles, and the search
used to be a separate list of seven. A user reads that as "JobDesk only looks
for seven jobs", which was close enough to true for the keyword boards and
looked like a bug. The tier lists are now the search list.

Searching every title every run is not affordable, though. Adzuna allows 250
calls a day, and each title is one more search sent to each of the 27 Workday
employers on a live profile, so fifty titles there is 1,350 calls a run. So:

  * "Jobs you would take today" are searched every run.
  * The other two lists take turns, a few a run, and the turn advances once
    per real run. Every title gets searched within a handful of runs.
  * A title that contains another title on the list is covered by it. The
    boards match words, so "support analyst" already finds "IT Support
    Analyst", and sending both is a call spent on the same postings.

`search_queries` in targeting.toml still works, as extra phrases searched
every run. Company career pages and the remote boards are not keyword
searches at all: they are read whole and every posting is scored.
"""

from __future__ import annotations

import json
from typing import Any

from . import config, profile

TURN_FILE = config.DATA / "search_turn.json"

# Per-run budgets. Tier 1 is capped too, so a profile with eighty "take
# today" titles cannot spend the Adzuna day in two runs.
EVERY_RUN_CAP = 25
BOARD_TURNS = 10          # tier 2 and 3 titles per run, keyword boards
WORKDAY_TURNS = 6         # titles per run, on top of the broad Workday terms


def _clean(items) -> list[str]:
    out: list[str] = []
    for item in items:
        item = " ".join(str(item).lower().split())
        if item and item not in out:
            out.append(item)
    return out


def covers(short: str, long: str) -> bool:
    """True when searching `short` also finds `long`: whole words, in order."""
    return short != long and f" {short} " in f" {long} "


def collapse(titles: list[str], also: list[str] = ()) -> tuple[list[str], dict[str, str]]:
    """Drop titles another title (or a phrase in `also`) already covers.

    Returns the titles to search and {covered title: the one that covers it}.
    """
    pool = list(titles) + list(also)
    keep: list[str] = []
    covered: dict[str, str] = {}
    for title in titles:
        by = next((t for t in pool if covers(t, title)), None)
        if by:
            covered[title] = by
        else:
            keep.append(title)
    return keep, covered


def plan(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every title, sorted into searched-every-run, taking-turns and covered.

    `data` is a targeting.toml dict; omitted, the live profile is read.
    """
    if data is None:
        data = {
            "tier_1_titles": profile.TIER_1_TITLES,
            "tier_2_titles": profile.TIER_2_TITLES,
            "tier_3_titles": profile.TIER_3_TITLES,
            "search_queries": profile.SEARCH_QUERIES,
        }
    first = _clean(list(data.get("tier_1_titles", []))
                   + list(data.get("search_queries", [])))
    rest = [t for t in _clean(list(data.get("tier_2_titles", []))
                              + list(data.get("tier_3_titles", [])))
            if t not in first]
    first, covered = collapse(first)
    rest, more = collapse(rest, also=first)
    covered.update(more)
    every, overflow = first[:EVERY_RUN_CAP], first[EVERY_RUN_CAP:]
    return {"every_run": every, "in_turn": overflow + rest, "covered": covered}


def turn() -> int:
    try:
        return int(json.loads(TURN_FILE.read_text(encoding="utf-8"))["turn"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def advance() -> int:
    """Move to the next set of titles. Called once at the start of a real run."""
    n = turn() + 1
    try:
        TURN_FILE.write_text(json.dumps({"turn": n}), encoding="utf-8")
    except OSError:
        pass
    return n


def window(items: list[str], size: int, at: int) -> list[str]:
    """`size` items starting at turn `at`, wrapping round the end."""
    if not items or size <= 0:
        return []
    if size >= len(items):
        return list(items)
    start = (at * size) % len(items)
    return (items + items)[start:start + size]


def board_titles(at: int | None = None) -> list[str]:
    p = plan()
    return p["every_run"] + window(p["in_turn"], BOARD_TURNS,
                                   turn() if at is None else at)


def workday_titles(at: int | None = None) -> list[str]:
    p = plan()
    return window(p["every_run"] + p["in_turn"], WORKDAY_TURNS,
                  turn() if at is None else at)


def all_titles() -> list[str]:
    """Every title, covered ones included. For filters that cost nothing."""
    p = plan()
    return p["every_run"] + p["in_turn"] + list(p["covered"])


def _runs(n: int, size: int) -> int:
    return max(1, -(-n // size)) if n else 0


def summary(data: dict[str, Any], employers: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the Criteria tab says about searching, from the same plan."""
    p = plan(data)
    ats = [str(e.get("ats", "")) for e in (employers or {}).get("employer", [])]
    workday = ats.count("workday")
    pages = len(ats) - workday
    every, in_turn = p["every_run"], p["in_turn"]
    lines = [
        f"JobDesk searches the job boards for every title above. The "
        f"{len(every)} you would take today are searched on every run.",
    ]
    if in_turn:
        runs = _runs(len(in_turn), BOARD_TURNS)
        if runs == 1:
            lines.append(f"So are the other {len(in_turn)}.")
        else:
            lines.append(
                f"The other {len(in_turn)} take turns, {BOARD_TURNS} a run, "
                f"so each one is searched at least once every {runs} runs.")
    if p["covered"]:
        pairs = [f"\"{by}\" also finds \"{t}\"" for t, by in p["covered"].items()]
        lines.append("A title inside another title is covered by it: "
                     + "; ".join(pairs) + ".")
    if workday:
        total = len(every) + len(in_turn)
        lines.append(
            f"Each of your {workday} Workday employers is searched for its broad "
            f"terms every run, plus {min(WORKDAY_TURNS, total)} of these titles "
            f"in turns.")
    if pages:
        lines.append(
            f"The other {pages} company career pages, and the remote job "
            f"boards, are read in full, so every posting on them is scored "
            f"whatever its title.")
    return {"every_run": every, "in_turn": in_turn, "covered": p["covered"],
            "text": lines}
