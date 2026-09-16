"""How a posting compares with similar roles in the same area.

The Jobs table shows a score, which says how well a posting matches the
profile. It said nothing about the market the posting sits in, and those are
different questions: a 90-scoring analyst job in a city with sixty other
analyst openings is a different proposition from the same job where there are
four.

This module answers the second question from data already on disk. The radar
writes one snapshot per run to `snapshots_dir()` -- no JD bodies, just the
structured record of what the market looked like that morning -- and there are
months of them. Nothing here calls out to a salary API or a third-party
dataset; the comparison is against postings this machine collected itself.

The peer group
--------------
(job family, region). Both come from data the radar already assigns:
`job_family` from the scoring pass, region from the location string. A posting
is compared only against other postings of its own family in its own region,
because "is this a good offer" has no meaning across those boundaries.

Remote is its own region rather than being spread across fifty states. A
remote posting competes nationally and its salary band reflects that.

What the percentage means
-------------------------
A percentile against that peer group, re-expressed as a distance from the
median. +18 means the posting sits at the 68th percentile: better than 68% of
comparable postings. -12 means the 38th. Zero means typical.

It is a relative measure and it is only as good as the peer group, so a group
under MIN_PEERS postings returns nothing at all rather than a number with no
support behind it. A confident-looking arrow computed from four postings would
be worse than an empty cell.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone

from ..radar import config as radar_config

# How many snapshot files to read. They run three times a day, so 90 files is
# roughly the last month -- recent enough that a salary band is still current,
# long enough that a peer group fills up.
MAX_FILES = 90

# Below this the peer group is not a sample, it is an anecdote.
MIN_PEERS = 8
# Below this the number is shown with a warning attached.
CONFIDENT_PEERS = 25

# 40 hours x 52 weeks. Only applied when a figure is small enough that it
# cannot be an annual salary -- see _annual.
HOURS_PER_YEAR = 2080

_lock = threading.Lock()
_index: dict | None = None
_building = False

_WS = re.compile(r"\s+")
_SENIORITY = re.compile(
    r"\b(senior|sr|junior|jr|lead|principal|staff|entry.level|mid.level|"
    r"associate|i{1,3}|iv|v|1|2|3)\b")

# Enough to resolve the states these sources actually write out. A location
# that names no state lands in the country or "Elsewhere" bucket, which is
# honest -- guessing a region would poison the comparison it feeds.
_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "district of columbia": "DC", "florida": "FL",
    "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY",
    "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "puerto rico": "PR", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
_CODES = set(_STATES.values())

# Cities that name themselves and never their state in these feeds.
_CITIES = {
    "new york city": "NY", "new york": "NY", "nyc": "NY",
    "san francisco": "CA", "los angeles": "CA", "san diego": "CA",
    "boston": "MA", "chicago": "IL", "seattle": "WA", "austin": "TX",
    "dallas": "TX", "houston": "TX", "atlanta": "GA", "denver": "CO",
    "philadelphia": "PA", "phoenix": "AZ", "miami": "FL", "richmond": "VA",
    "washington": "DC", "washington dc": "DC", "arlington": "VA",
    "charlotte": "NC", "nashville": "TN", "portland": "OR",
    "minneapolis": "MN", "detroit": "MI", "pittsburgh": "PA",
    "baltimore": "MD", "st louis": "MO", "tampa": "FL", "orlando": "FL",
}

_REMOTE = re.compile(r"remote|anywhere|work from home|distributed|wfh")


# -- shaping one posting -------------------------------------------------

def _annual(value) -> float | None:
    """A salary figure as an annual number, or None if it is not usable.

    Sources put hourly contract rates in the same field as annual salaries.
    No US annual salary is under $1,000 and no hourly rate is over it, so the
    boundary is unambiguous for this data -- but it is a property of this
    data, not a law, so check it again before reusing the rule.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number * HOURS_PER_YEAR if number < 1000 else number


def region(location: str, remote) -> str:
    """The market a posting competes in.

    Remote wins over any city named alongside it. A posting that says
    "San Francisco (Remote)" is hiring nationally and pays accordingly, so
    comparing it against on-site Bay Area roles would flatter it.
    """
    text = _WS.sub(" ", (location or "").strip())
    if remote or _REMOTE.search(text.lower()):
        return "Remote"
    if not text:
        return "Elsewhere"

    pieces = [p.strip() for p in text.split(",") if p.strip()]
    for piece in reversed(pieces):
        bare = re.sub(r"\(.*?\)", "", piece).strip()
        if bare.upper() in _CODES:
            return bare.upper()
        if bare.lower() in _STATES:
            return _STATES[bare.lower()]
    for piece in pieces:
        bare = re.sub(r"[^a-z ]", "", piece.lower()).strip()
        if bare in _CITIES:
            return _CITIES[bare]
    return "Elsewhere"


def _role_key(title: str) -> str:
    """A title with the seniority words taken out.

    "Senior Data Analyst" and "Data Analyst II" are the same role competing
    for the same applicants, and counting them separately would tell you a
    crowded field is empty.
    """
    text = re.sub(r"[^a-z0-9 ]", " ", (title or "").lower())
    text = _SENIORITY.sub(" ", text)
    return _WS.sub(" ", text).strip()


def _age_days(posted, now) -> float | None:
    if not posted:
        return None
    try:
        when = datetime.fromisoformat(str(posted))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (now - when).total_seconds() / 86400)


# -- the index -----------------------------------------------------------

def _blank() -> dict:
    return {"groups": {}, "families": {}, "places": {},
            "everything": {"low": [], "high": []},
            "files": 0, "postings": 0, "built_at": None}


def _read_snapshots() -> dict:
    """Fold the snapshot series down into per-peer-group aggregates.

    Only aggregates are kept. The series is 18MB of JSON and holding it all
    would cost more memory than the rest of the app put together, for numbers
    that are only ever read as distributions.

    Deduped by uid across files, keeping the most recent sighting, so a
    posting that survived thirty runs counts once.
    """
    now = datetime.now(timezone.utc)
    # The series moves: JOBDESK_SNAPSHOTS in .env.radar points it at whichever
    # project is analysing it. Only the radar's own entry point reads that
    # file, so the window has to read it too or it looks at an empty folder.
    try:
        radar_config.load_env()
    except Exception:
        pass
    try:
        files = sorted(radar_config.snapshots_dir().glob("jobs_*.json"))
    except OSError:
        return _blank()
    files = files[-MAX_FILES:]
    if not files:
        return _blank()

    seen: dict[str, dict] = {}
    for path in files:
        try:
            rows = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            # One unreadable snapshot is not a reason to have no market data.
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = row.get("title") or ""
            company = row.get("company") or ""
            if not title or not company:
                continue
            key = f"{company}|{title}".lower()
            seen[key] = row

    groups: dict[str, dict] = {}
    # The same published bands, pooled three more ways. A family-by-place cell
    # is the sharpest comparison and also the emptiest: "governance/controls
    # in VA" held two of them on 2026-09-15. Keeping the wider pools costs
    # four more lists and is what lets the estimator widen instead of give up.
    families: dict[str, dict] = {}
    places: dict[str, dict] = {}
    everything: dict = {"low": [], "high": []}

    for row in seen.values():
        family = row.get("job_family") or "other"
        place = region(row.get("location") or "", row.get("remote"))
        group = groups.setdefault(
            f"{family}\u0000{place}",
            {"pay": [], "low": [], "high": [], "age": [], "reach": [],
             "employers": set(), "roles": {}},
        )
        low = _annual(row.get("salary_min"))
        high = _annual(row.get("salary_max"))
        if low or high:
            for pool in (families.setdefault(family, {"low": [], "high": []}),
                         places.setdefault(place, {"low": [], "high": []}),
                         everything):
                if low:
                    pool["low"].append(low)
                if high:
                    pool["high"].append(high)
            group["pay"].append((low or high) / 2 + (high or low) / 2)
            if low:
                group["low"].append(low)
            if high:
                group["high"].append(high)
        days = _age_days(row.get("posted_at"), now)
        if days is not None:
            group["age"].append(days)
        group["reach"].append(1 + len(row.get("also_on") or []))
        group["employers"].add((row.get("company") or "").lower())
        role = _role_key(row.get("title") or "")
        if role:
            group["roles"][role] = group["roles"].get(role, 0) + 1

    for group in groups.values():
        for key in ("pay", "low", "high", "age", "reach"):
            group[key].sort()
        group["employers"] = len(group["employers"])
        group["count"] = max(len(group["reach"]), 1)

    for pool in (*families.values(), *places.values(), everything):
        pool["low"].sort()
        pool["high"].sort()

    return {"groups": groups, "families": families, "places": places,
            "everything": everything, "files": len(files),
            "postings": len(seen), "built_at": now.isoformat()}


def _build() -> None:
    global _index, _building
    try:
        built = _read_snapshots()
    except Exception:
        # Market context is a nice-to-have on a table that works without it.
        built = _blank()
    with _lock:
        _index = built
        _building = False


def start() -> None:
    """Begin building the index off the request thread.

    Called once when the server comes up. Reading and folding three months of
    snapshots takes a couple of seconds, which is fine in the background and
    is not fine in front of the first page load.
    """
    global _building
    with _lock:
        if _index is not None or _building:
            return
        _building = True
    threading.Thread(target=_build, name="market-index", daemon=True).start()


def ready() -> bool:
    with _lock:
        return _index is not None


def refresh() -> None:
    """Drop the index so the next `start()` rereads the snapshots."""
    global _index
    with _lock:
        _index = None
    start()


# -- scoring one posting against its peers -------------------------------

def _percentile(values: list[float], value: float) -> float:
    """Where `value` sits in a sorted list, 0-100.

    Ties count as half, so a posting whose salary equals every peer's lands
    at 50 rather than at 0 or 100.
    """
    if not values:
        return 50.0
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return 100.0 * (below + equal / 2) / len(values)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def _money(value) -> str:
    return "$" + format(round(value / 1000), ",") + "k"


def assess(job: dict) -> dict | None:
    """Market context for one posting, or None when there is no support for it.

    Four factors, each a percentile against the peer group, combined by
    weight. Every factor is oriented so that higher is better for the person
    applying, which is not the same as higher being a bigger number -- three
    of the four are inverted for exactly that reason.
    """
    with _lock:
        index = _index
    if not index:
        return None

    family = job.get("job_family") or "other"
    place = region(job.get("location") or "", job.get("remote"))
    group = index["groups"].get(f"{family}\u0000{place}")
    if not group or group["count"] < MIN_PEERS:
        return None

    now = datetime.now(timezone.utc)
    factors: list[dict] = []

    low = _annual(job.get("salary_min"))
    high = _annual(job.get("salary_max"))
    if (low or high) and len(group["pay"]) >= MIN_PEERS:
        mid = (low or high) / 2 + (high or low) / 2
        pct = _percentile(group["pay"], mid)
        typical = _median(group["pay"])
        factors.append({
            "key": "pay", "label": "Pay", "weight": 0.40, "percentile": pct,
            "detail": f"{_money(mid)} midpoint. The group's typical band "
                      f"centres on {_money(typical)}, across "
                      f"{len(group['pay'])} postings that published one.",
        })

    role = _role_key(job.get("title") or "")
    if role:
        same = group["roles"].get(role, 1)
        counts = sorted(group["roles"].values())
        # Inverted: more openings with this exact title means more people
        # applying to the same thing.
        pct = 100.0 - _percentile([float(c) for c in counts], float(same))
        factors.append({
            "key": "crowd", "label": "Competition", "weight": 0.25,
            "percentile": pct,
            "detail": f"{same} posting{' shares' if same == 1 else 's share'} this "
                      f"title, out of {group['count']} openings in the group "
                      f"across {group['employers']} employers.",
        })

    reach = 1 + len(job.get("also_on") or [])
    # Only scored when the peers actually differ. Most sources never report
    # syndication, and a factor where every posting lands on 50 says nothing
    # while diluting the three that do.
    if len(group["reach"]) >= MIN_PEERS and group["reach"][0] != group["reach"][-1]:
        # Inverted: a posting syndicated to five job boards is in front of
        # far more applicants than one that only exists on the company's ATS.
        pct = 100.0 - _percentile([float(r) for r in group["reach"]], float(reach))
        where = ("only on " + (job.get("source") or "one board")) if reach == 1 \
            else f"on {reach} job boards"
        factors.append({
            "key": "reach", "label": "Exposure", "weight": 0.15,
            "percentile": pct,
            "detail": f"Listed {where}; peers average "
                      f"{sum(group['reach']) / len(group['reach']):.1f} "
                      f"boards each.",
        })

    days = _age_days(job.get("posted_at"), now)
    if days is not None and len(group["age"]) >= MIN_PEERS:
        # Inverted: an older posting has had more applicants through it, and
        # often already has a shortlist.
        pct = 100.0 - _percentile(group["age"], days)
        typical = _median(group["age"])
        factors.append({
            "key": "fresh", "label": "Freshness", "weight": 0.20,
            "percentile": pct,
            "detail": f"Posted {_days(days)}, against a typical "
                      f"{_days(typical)} for openings in this group.",
        })

    if not factors:
        return None

    total = sum(f["weight"] for f in factors)
    composite = sum(f["percentile"] * f["weight"] for f in factors) / total
    delta = round(composite - 50)

    for factor in factors:
        factor["percentile"] = round(factor["percentile"])
        factor["share"] = round(100 * factor["weight"] / total)
        factor.pop("weight")

    return {
        "delta": delta,
        "direction": "up" if delta >= 3 else ("down" if delta <= -3 else "flat"),
        "percentile": round(composite),
        "peers": group["count"],
        "group": f"{family} in {_place_name(place)}",
        "confident": group["count"] >= CONFIDENT_PEERS,
        "factors": factors,
    }


def estimate_salary(job: dict) -> dict | None:
    """A salary band for a posting that did not publish one.

    Medians of what comparable postings actually pay. It is a guess from local
    evidence and the UI must label it as one -- the `estimated` flag exists so
    no part of the app can show it as a figure the employer stated.

    Four rungs, narrowest first. The sharpest comparison is the same job
    family in the same place, and that cell is frequently too thin to median:
    48 of 435 live candidates fell through it, every one of them because the
    family-by-place cell held fewer than eight published bands. So the search
    widens -- the family anywhere, then the place across families, then the
    whole corpus -- and `basis` says which rung answered. A wide comparison is
    worth less than a narrow one and the user should be able to see that, but
    it is worth considerably more than an empty cell.
    """
    if _annual(job.get("salary_min")) or _annual(job.get("salary_max")):
        return None
    with _lock:
        index = _index
    if not index:
        return None

    family = job.get("job_family") or "other"
    place = region(job.get("location") or "", job.get("remote"))
    rungs = [
        (index.get("groups", {}).get(f"{family}\u0000{place}"),
         f"{family} postings in {_place_name(place)}"),
        (index.get("families", {}).get(family),
         f"{family} postings everywhere"),
        (index.get("places", {}).get(place),
         f"postings in {_place_name(place)}, across every job family"),
        (index.get("everything"),
         "postings in the whole series, across every family and place"),
    ]

    for pool, described in rungs:
        if not pool:
            continue
        lows, highs = pool["low"], pool["high"]
        if len(lows) < MIN_PEERS or len(highs) < MIN_PEERS:
            continue
        low, high = _median(lows), _median(highs)
        if not low or not high or high < low:
            continue
        return {
            "salary_min": round(low),
            "salary_max": round(high),
            "estimated": True,
            "basis": f"Median of {len(lows)} {described} that published a "
                     f"band. The employer did not state one.",
        }
    return None


def _place_name(place: str) -> str:
    if place == "Remote":
        return "remote roles"
    if place == "Elsewhere":
        return "unparsed locations"
    return place


def _days(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 1:
        return "today"
    if value < 2:
        return "yesterday"
    return f"{round(value)} days ago"


def summary() -> dict:
    """What the index is made of, for the note under the table."""
    with _lock:
        index, building = _index, _building
    if not index:
        return {"ready": False, "building": building}
    return {
        "ready": True,
        "building": False,
        "files": index["files"],
        "postings": index["postings"],
        "groups": len(index["groups"]),
        "built_at": index["built_at"],
    }
