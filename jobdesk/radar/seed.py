"""Find a metro's employers before any of them posts anything.

`learn.py` waits. A company gets its ATS probed only after one of its
postings has turned up on an aggregator and scored 75 or better, which means
the pipeline finds an employer at the one moment it least needs to: the job
is already on the board, already read, already scored. What that buys is the
NEXT req, and that is worth having. But it leaves the obvious case untouched.
A large local employer that posts four analyst roles a year straight to its
own Workday and never to an aggregator is invisible to this system forever,
because the trigger never fires.

That is the gap, and it is the one the user noticed first: other job sites
keep showing openings this one does not.

Nothing here is Richmond-shaped. Two sources of names, both of which exist
for whoever runs it:

  1. What the radar has already seen hiring in this metro, at any score.
     `seen.json` is tens of thousands of postings and thousands of distinct
     companies, and the ones with a local `location` are, by definition, the
     employers in this user's area. Most people will never have to type
     anything for this to find a few hundred names.
  2. `profile/seed_companies.toml`, a plain list. For the employers a metro
     knows about and the aggregators do not: the hospital system, the
     utility, the university, the three banks. A chamber of commerce roster
     or a business journal's top-employers list, pasted in once. Each entry
     may carry the company's website, and it is worth the typing -- see
     `from_profile`.

Neither source is trusted. A name here is a guess at a company, so every hit
still goes through `discover.find` and its confirmation rule. What changes
for a seed company is which confirmation can apply: it has posted nothing we
have read, so there are no titles to match against, and the board has to be
hiring in this metro or be linked from the company's own website. A board
naming itself used to count too, and step 13 took that out: the slug was
guessed from the name, so the match confirms the spelling rather than the
company. See `discover.confirms_local` and `discover.from_website`.

Which is why the `site =` field in the profile is worth the typing. A website
skips the guessing and the confirming both, because the company published the
link itself.

Run it by hand:

    py -m jobdesk.radar.seed --list      # see the names, probe nothing
    py -m jobdesk.radar.seed             # sweep

Not on the daily schedule. It is a one-off sweep of a few hundred names
against a budget of a few thousand calls, and once a metro has been swept,
`learn.py`'s five a day is the right speed for keeping up with it.
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
    """What "here" means for this profile, as whole place names.

    Read, never hardcoded. The point of the exercise is that the next person
    to run it lives somewhere else.

    The terms stay whole. Splitting them into words was the first version and
    it was wrong: this profile knows about New Kent and Short Pump, and the
    loose words "new" and "short" matched half of New York and every "short
    term contract" in the file.
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

    The website is worth typing. Resolution works by reading the company's
    own careers page, so it has to find the website first, and guessing that
    from the name fails on exactly the employers a metro list is full of --
    the Federal Reserve Bank of Richmond is at richmondfed.org, VCU at
    vcu.edu. Supplying it took the measured hit rate from 20 of 72 to 21 of
    35, and a chamber of commerce directory prints the website in the same
    row as the name anyway.
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

    Every posting on file, not just the ones that scored. Whether a company's
    openings are a fit is the scorer's judgment and it gets to make it every
    morning once the board is on the list; what matters here is only that the
    company hires in this metro.

    A row is local when its recorded location says so. Rows written before
    the store kept a location have none and are skipped rather than guessed
    at -- `seen.json` is mostly remote boards, and a name taken from one
    would send the sweep probing companies three time zones away.
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
    """Every posting the radar has on file, from both places it keeps them.

    `seen.json` is the long memory, tens of thousands of rows over the
    retention window. `candidates.json` is the current working set, a few
    hundred rows that survived the score floor. They overlap heavily and that
    is fine: the overlap is double-counted into the sighting tally, which
    only means a company currently advertising outranks one last seen weeks
    ago. That is the right order to probe in anyway.
    """
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

    Profile names come first and unconditionally, sighting count or not:
    somebody typed them, which beats anything derivable. History follows,
    ordered by how often the company has turned up, because a company posting
    here every week is a better bet for a board of its own than one seen once
    in August.

    Dropped: companies already on a watch list, staffing firms, and companies
    probed recently and missed. Those last are `learn.py`'s bookkeeping and
    the seeder shares it, so a sweep run twice in a week does not pay twice
    for the same 200 misses.

    `retry_missed` suspends that last rule, and there is one situation that
    calls for it: the reason a name missed has changed. A miss records that
    the probe failed under the rules and the profile as they stood that day.
    Tighten a confirmation rule, or paste in 228 company websites, and
    yesterday's misses are answers to a question nobody is asking any more.
    The cooldown is right for a sweep run on a schedule and wrong the day
    after the inputs move, so it is a flag rather than a default.
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
