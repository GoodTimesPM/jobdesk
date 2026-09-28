"""Find a metro's employers before any of them posts anything.

`learn.py` probes a company's ATS only after one of its postings scores on
an aggregator. A large local employer that posts only to its own Workday
never trips that trigger, so it stays invisible. This closes that gap.

Two sources of names, neither Richmond-specific:

  1. Companies the radar has already seen hiring in this metro, at any
     score. Most users never type anything and still get a few hundred.
  2. `profile/seed_companies.toml`, a plain list for the employers the
     aggregators miss (hospital, utility, university, banks). An entry may
     carry the company's website, and it is worth the typing.

Every name still goes through `discover.find`. A seed company has posted
nothing we have read, so its board must be hiring in this metro or be
linked from the company's own website (`discover.confirms_local`,
`discover.from_website`). A website in the profile skips both steps.

    py -m jobdesk.radar.seed --list      # see the names, probe nothing
    py -m jobdesk.radar.seed             # sweep

Not on the daily schedule. It is a one-off sweep of a few hundred names;
after that `learn.py`'s five a day keeps up.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from datetime import date, datetime, timedelta, timezone

from .. import profile as profile_files
from . import config, discover, learn, profile as targeting


def places() -> set[str]:
    """What "here" means for this profile, as whole place names read from it.

    Kept whole: split into words, "New Kent" and "Short Pump" matched half of
    New York and every "short term contract".
    """
    out = {str(t).strip().lower() for t in targeting.LOCAL_TERMS}
    out.add(str(targeting.HOME_METRO).split(",")[0].strip().lower())
    return {t for t in out if t}


def home_state() -> str:
    """The profile's state, as a two-letter code, or "".

    Read off HOME_METRO, which is already written the way a location
    string is ("Richmond, VA"), so there is nothing new to configure.
    """
    return discover.state_named(str(targeting.HOME_METRO))


def is_local(text: str, here: set[str]) -> bool:
    return discover.in_places(text, here, home_state()) is not None


# --------------------------------------------------------------------------
# Where the names come from
# --------------------------------------------------------------------------

def from_profile() -> list[tuple[str, str]]:
    """Names the user pasted in, as (name, website) pairs.

    An entry is either a bare name or a table with a website beside it:

        company = [
          "Markel",
          { name = "Federal Reserve Bank of Richmond",
            site = "https://www.richmondfed.org" },
        ]

    Guessing a website from the name fails on the employers a metro list is
    full of (richmondfed.org, vcu.edu). Supplying it took the hit rate from
    20 of 72 to 21 of 35.
    """
    path = profile_files.directory() / "seed_companies.toml"
    if not path.exists():
        return []
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    out: list[tuple[str, str]] = []
    for item in data.get("company", []):
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            site = str(item.get("site") or "").strip()
        else:
            name, site = str(item).strip(), ""
        if name:
            out.append((name, site))
    return out


def from_history(here: set[str]) -> list[tuple[str, int]]:
    """Companies the store has seen hiring here, most-sighted first.

    Every posting on file, not just the ones that scored. A row counts only
    when its recorded location is local; older rows with no location are
    skipped, since `seen.json` is mostly remote boards.
    """
    tally: dict[str, list] = {}
    for rec in _postings():
        name = str(rec.get("company") or "").strip()
        if not name or not is_local(str(rec.get("location") or ""), here):
            continue
        row = tally.setdefault(name.lower(), [name, 0])
        row[1] += 1
    rows = sorted(tally.values(), key=lambda r: (-r[1], r[0]))
    return [(name, n) for name, n in rows]


def _postings() -> list[dict]:
    """Every posting the radar has on file, from `seen.json` and
    `candidates.json`. The overlap is double-counted on purpose, so a company
    advertising now outranks one last seen weeks ago."""
    return _rows(config.SEEN_FILE) + _rows(config.CANDIDATES)


def _rows(path) -> list[dict]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    try:
        data = json.loads(raw) if raw.strip() else []
    except ValueError:
        return []
    if isinstance(data, dict):
        data = list(data.values())
    return [r for r in data if isinstance(r, dict)]


# --------------------------------------------------------------------------
# Which of them are worth a probe
# --------------------------------------------------------------------------

def candidates(limit: int, today: date,
               min_sightings: int | None = None,
               retry_missed: bool = False) -> list[tuple[str, str]]:
    """Seed names worth probing, best first.

    Profile names first, then history by how often the company turns up.
    Dropped: companies already watched, staffing firms, and companies probed
    recently and missed (`learn.py`'s bookkeeping, so a second sweep in a week
    does not pay for the same misses).

    `retry_missed` lifts that cooldown for the day the inputs change, like a
    tightened confirmation rule or a batch of newly pasted websites.
    """
    if min_sightings is None:
        min_sightings = config.SEED_MIN_SIGHTINGS
    known = learn._known_names()
    misses = learn._misses(learn._read(config.LEARNED_EMPLOYERS))
    cutoff = today - timedelta(days=config.LEARN_RETRY_DAYS)

    if retry_missed:
        # A name is skipped when its miss is NEWER than the cutoff, so the way
        # to let every one of them through is to put the cutoff in the future.
        cutoff = date.max
    named = [(n, site, min_sightings) for n, site in from_profile()]
    seen_here = [(n, "", count) for n, count in from_history(places())]
    out: list[tuple[str, str]] = []
    taken: set[str] = set()
    for name, site, sightings in named + seen_here:
        low = name.lower()
        if low in taken or low in known or learn._is_agency(name):
            continue
        if sightings < min_sightings:
            continue
        miss = misses.get(low)
        if miss and learn._tried_on(miss) > cutoff:
            continue
        taken.add(low)
        out.append((name, site))
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------

def run(limit: int, calls: int, *, today: date | None = None,
        retry_missed: bool = False, log=print) -> list[dict]:
    """Probe up to `limit` seed companies against a budget of `calls`."""
    today = today or datetime.now(timezone.utc).date()
    wanted = candidates(limit, today, retry_missed=retry_missed)
    if not wanted:
        log("seed: no names to probe. Every local company on file is already "
            "watched, is a staffing firm, or was probed recently and missed.")
        return []

    here = places()
    budget = discover.Budget(calls)
    data = learn._read(config.LEARNED_EMPLOYERS)
    misses = list(data.get("miss", []))
    at_miss = {str(m.get("name", "")).lower(): i for i, m in enumerate(misses)}
    found: list[dict] = []
    probed = 0

    log(f"seed: {len(wanted)} name(s), {calls} call(s) of budget")
    for name, site in wanted:
        if budget.left <= 0:
            log(f"seed: budget spent after {probed} name(s)")
            break
        probed += 1
        board, note = discover.find(name, [], budget, places=here, site=site,
                                    resolve_calls=config.RESOLVE_PROBE_CALLS,
                                    state=home_state())
        if board is None:
            row = {"name": name, "tried_on": today.isoformat(), "note": note}
            where = at_miss.get(name.lower())
            if where is None:
                misses.append(row)
            else:
                misses[where] = row
            continue
        entry = board.entry(name)
        entry.update({
            "tier": config.SEED_TIER,
            "learned_on": today.isoformat(),
            # Nothing scored this one. It is a seed, not a hit, and saying so
            # keeps it from reading like a company that earned its way on.
            "learned_score": 0,
            "confirmed_by": note,
        })
        found.append(entry)
        log(f"  + {name}: {note} ({board.count} open)")

    learn.write(list(data.get("employer", [])) + found, misses)
    log(f"seed: {len(found)} board(s) confirmed out of {probed} probed, "
        f"{budget.left} call(s) left")
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="jobdesk.radar.seed",
        description="Probe this metro's employers for their ATS, in bulk.")
    ap.add_argument("--limit", type=int, default=config.SEED_MAX_PER_SWEEP,
                    help="how many company names to probe")
    ap.add_argument("--calls", type=int, default=config.SEED_PROBE_BUDGET,
                    help="hard cap on HTTP calls for the whole sweep")
    ap.add_argument("--list", action="store_true",
                    help="print the names that would be probed, probe nothing")
    ap.add_argument("--retry-missed", action="store_true",
                    help="probe names that missed recently too, for when the "
                         "rules or the profile have changed since")
    args = ap.parse_args(argv)

    config.load_env()
    if args.list:
        names = candidates(args.limit, datetime.now(timezone.utc).date(),
                           retry_missed=args.retry_missed)
        for name, site in names:
            print(f"{name}	{site}" if site else name)
        print(f"\n{len(names)} name(s) would be probed.")
        return 0
    run(args.limit, args.calls, retry_missed=args.retry_missed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
