"""Bridge between the two Notion trackers and the packet-build trigger.

Plan item 6's automatic trigger used to be "this posting scored >= 80"
(`cmd_auto`, 2026-08-11). You changed that on 2026-08-20: a score is Job
Radar's opinion, and grinding out 80+ drafts nobody asked to apply to yet
decided nothing. The new trigger is your own hand on the go signal -- the
'Date Applied' field getting filled on a row in the Job Radar Tracker --
read straight out of Notion.

One event, two things happen off it, because they are the same decision seen
from two tables:

  1. The matching fields on the corresponding row in the OG Job & Internship
     Tracker get filled in -- only the fields that database's *live* schema
     actually has right now, so a renamed or removed column degrades to
     "skipped", not a wrong guess.
  2. A packet gets built locally for it, same shape as any other `auto` run,
     so the folder is already in APPLICATIONS by the time you go to
     actually apply.

No shared code with Job Radar's own `radar/notion.py` -- sub-projects stay
isolated (see ../PROJECT.md), so this hits the REST API directly with its own
small copy of the same handful of calls, rather than importing that module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .. import paths, profile
from . import config

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# Job Radar Tracker property names. `radar/notion.py` writes most of these;
# Date Applied and Agency Submitted By are the two you fill by hand --
# see docs/radar-PROJECT.md, "the application half is yours to update".
JR_ROLE = "Role"
JR_COMPANY = "Company"
JR_URL = "URL"
JR_LOCATION = "Location"
JR_SALARY = "Salary"
JR_APPLIED = "Date Applied"
JR_AGENCY = "Agency Submitted By"
JR_SCORE = "Fit Score"
JR_TIER = "Tier"

# OG Job & Internship Tracker column -> which value out of a fetched row
# fills it. Restricted to columns that plainly correspond to something Job
# Radar knows; "Industry", "Interview Date", and "Attached Documents/Cover
# Letter" have no source here and are left alone.
_OG_FIELD_SOURCE = {
    "Position": "role",
    "Company": "company",
    "Location": "location",
    "Salary": "salary",
    "Application Link": "url",
    "Applied": "applied",
}
_OG_STATUS_FIELD = "Application Status"
_OG_STATUS_VALUE = "Applied"

STATE_FILE = config.DATA / "notion_sync_state.json"


def _load_env() -> None:
    """Pull Notion credentials from .env.apply, then .env.radar, then .env.

    .env.radar stays in the chain because these credentials were configured
    there first, when radar was its own repo. Reading another package's .env
    is data, not code -- the same category as reading its candidates.json.
    """
    paths.load_env(".env.apply", ".env.radar", ".env")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('NOTION_API_KEY')}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _configured() -> bool:
    _load_env()
    return bool(os.getenv("NOTION_API_KEY") and os.getenv("NOTION_JOBS_DB"))


def _plain_text(prop: dict) -> str:
    """Read whatever text a Notion property holds, regardless of its type."""
    kind = prop.get("type")
    if kind == "title":
        return "".join(t.get("plain_text", "") for t in prop.get("title", []))
    if kind == "rich_text":
        return "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
    if kind == "url":
        return prop.get("url") or ""
    if kind == "select":
        return (prop.get("select") or {}).get("name", "")
    if kind == "date":
        return (prop.get("date") or {}).get("start") or ""
    if kind == "number":
        return str(prop.get("number")) if prop.get("number") is not None else ""
    return ""


# -- local state: which Job Radar Tracker rows have already been handled ----

def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"synced": []}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"synced": []}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def unmark_synced(page_ids: list[str]) -> int:
    """Put rows back in the queue for the next run.

    The dedupe cache exists so a row is not applied to twice, not so a row is
    consumed by a run that failed to do anything with it. Two rows dated
    2026-08-26 were marked synced by runs whose OG sync had already failed,
    and nothing in the tool could ever pick them up again.
    """
    state = _load_state()
    before = set(state.get("synced", []))
    state["synced"] = sorted(before - set(page_ids))
    _save_state(state)
    return len(before) - len(state["synced"])


def mark_synced(page_ids: list[str]) -> None:
    if not page_ids:
        return
    state = _load_state()
    state["synced"] = sorted(set(state.get("synced", [])) | set(page_ids))
    _save_state(state)


# -- reading the Job Radar Tracker -------------------------------------------

def fetch_newly_applied() -> tuple[list[dict], str]:
    """Rows in the Job Radar Tracker with 'Date Applied' set, not seen before.

    Returns (rows, note). A row is a plain dict carrying everything the
    packet builder and the OG tracker sync both need, read once: page_id,
    role, company, url, location, salary, applied, agency, score, tier.
    """
    if not _configured():
        return [], ("NOTION_API_KEY / NOTION_JOBS_DB not set (checked this "
                    ".env.apply and .env.radar) -- nothing to poll. "
                    "Fill the OG tracker and build packets by hand until "
                    "then.")
    try:
        import requests
    except ImportError:
        return [], "requests isn't installed, so nothing was polled"

    seen = set(_load_state().get("synced", []))
    db = os.getenv("NOTION_JOBS_DB")
    rows: list[dict] = []
    total_applied = 0
    cursor = None
    while True:
        payload = {
            "filter": {"property": JR_APPLIED, "date": {"is_not_empty": True}},
            "page_size": 100,
        }
        if cursor:
            payload["start_cursor"] = cursor
        try:
            resp = requests.post(f"{API}/databases/{db}/query",
                                 headers=_headers(), json=payload, timeout=25)
        except Exception as exc:                   # requests raises its own tree
            return [], f"Notion query failed ({type(exc).__name__})"
        if resp.status_code >= 300:
            return [], f"Notion query failed: HTTP {resp.status_code}"
        data = resp.json()
        for page in data.get("results", []):
            total_applied += 1
            if page["id"] in seen:
                continue
            props = page.get("properties", {})
            rows.append({
                "page_id": page["id"],
                "role": _plain_text(props.get(JR_ROLE, {})),
                "company": _plain_text(props.get(JR_COMPANY, {})),
                "url": _plain_text(props.get(JR_URL, {})),
                "location": _plain_text(props.get(JR_LOCATION, {})),
                "salary": _plain_text(props.get(JR_SALARY, {})),
                "applied": _plain_text(props.get(JR_APPLIED, {})),
                "agency": _plain_text(props.get(JR_AGENCY, {})),
                "score": int(_plain_text(props.get(JR_SCORE, {})) or 0),
                "tier": _plain_text(props.get(JR_TIER, {})),
            })
        cursor = data.get("next_cursor")
        if not data.get("has_more") or not cursor:
            break
    # A bare "0 newly-applied" reads the same whether Notion has zero rows
    # with Date Applied filled or every one of them is already in
    # notion_sync_state.json's `synced` list -- and those are different
    # problems. 2026-08-26: two applications you marked Date Applied on
    # still came back "0 newly-applied" three runs in a row with no way to
    # tell which case it was. Naming the already-synced count here means the
    # next zero either points at Notion (nothing has Date Applied set -- check
    # it was filled on the Job Radar Tracker row, not the OG tracker) or at
    # the state file (it's stale -- see `unmark_synced`).
    if not rows and total_applied:
        return rows, (f"0 newly-applied row(s) in the Job Radar Tracker "
                      f"({total_applied} have Date Applied filled, all "
                      f"already in notion_sync_state.json's synced list)")
    return rows, f"{len(rows)} newly-applied row(s) in the Job Radar Tracker"


# -- the OG Job & Internship Tracker ------------------------------------------

# The OG tracker's title, used only to recover from a wrong ID in .env.
OG_TITLE = "Job & Internship Tracker"

# Resolved once per process by `_og_db`, so the recovery search below runs at
# most one time per run rather than once per row.
_OG_DB_CACHE: list = []


def _og_db() -> tuple[str, str]:
    """The OG tracker's database ID, and why one could not be found.

    `NOTION_OG_TRACKER_DB` held `278e98f0-1273-81c2-a749-000ba88a6d6c` for six
    days, which is the ID of the *page* the tracker lives on and not the ID of
    the database. Every read of it returned 404, and 404 from Notion reads
    identically whether the object was never shared or simply does not exist,
    so the message pointed at a sharing problem that had already been solved.

    On a 404 this asks Notion what databases the integration can actually see
    and takes the one whose title matches. A wrong ID is then a warning rather
    than a wall, and the correct one is printed so .env can be corrected.
    """
    if _OG_DB_CACHE:
        return _OG_DB_CACHE[0]
    # Its own `_load_env`, because this is now called from `_find_og_page` and
    # `_og_schema` as well as from `sync_to_og_tracker`, and only the last of
    # those loaded the environment first. Without it the ID reads as unset in
    # any process that has not already synced something.
    _load_env()
    og_db = os.getenv("NOTION_OG_TRACKER_DB")
    if not og_db:
        # Failures are deliberately not cached. A cached "not set" would
        # outlive the thing that fixed it for the rest of the process.
        return "", "NOTION_OG_TRACKER_DB is not set"
    import requests
    try:
        resp = requests.get(f"{API}/databases/{og_db}", headers=_headers(),
                            timeout=25)
    except Exception as exc:
        return "", f"reading the OG database failed ({type(exc).__name__})"
    if resp.status_code < 300:
        _OG_DB_CACHE.append((og_db, ""))
        return _OG_DB_CACHE[0]
    if resp.status_code != 404:
        return "", (f"reading the OG database failed: HTTP {resp.status_code} "
                    f"-- {resp.text[:200]}")

    found, why = _search_db_by_title(OG_TITLE)
    if found:
        print(f"  NOTION_OG_TRACKER_DB={og_db} is not a database Notion can "
              f"see. Using {found}, the database titled {OG_TITLE!r}. "
              f"Correct .env to stop this lookup happening every run.")
        _OG_DB_CACHE.append((found, ""))
        return _OG_DB_CACHE[0]
    return "", (
        f"Notion cannot see database {og_db}, and no database titled "
        f"{OG_TITLE!r} is shared with this integration either{why}. Open the "
        f"tracker in Notion, the ... menu, Connections, and add the connection "
        f"NOTION_API_KEY belongs to -- an integration sees nothing until a page "
        f"is explicitly shared with it.")


def _search_db_by_title(title: str) -> tuple[str, str]:
    """The ID of the one shared database with this title, if there is one."""
    import requests
    try:
        resp = requests.post(
            f"{API}/search", headers=_headers(), timeout=25,
            json={"filter": {"value": "database", "property": "object"},
                  "page_size": 100})
    except Exception as exc:
        return "", f" (the search for it failed: {type(exc).__name__})"
    if resp.status_code >= 300:
        return "", f" (the search for it failed: HTTP {resp.status_code})"
    want = title.strip().casefold()
    for db in resp.json().get("results", []):
        name = "".join(t.get("plain_text", "") for t in db.get("title", []))
        if name.strip().casefold() == want:
            return db["id"], ""
    return "", ""


def _og_schema() -> tuple[dict[str, str], str]:
    """The OG tracker's live property names and types, and why not.

    Read fresh on every call rather than cached, so a column you rename
    or deletes in Notion is skipped on the very next sync, not guessed at
    from a stale copy.

    It used to return a bare set, so every distinct failure — no database ID,
    a network error, a 404 — arrived at the caller as an empty set and was
    reported as "Could not read the OG tracker's schema". That sentence went
    into logs/auto.log on 2026-08-24 for the Home Depot row and said nothing
    about the actual cause, which was a 404: the OG database had never been
    shared with the "Job Radar" integration. A Notion integration sees
    nothing until a page is explicitly shared with it, and that is the one
    failure here that no amount of retrying fixes, so it has to be named.
    """
    og_db, why = _og_db()
    if not og_db:
        return {}, why
    import requests
    try:
        resp = requests.get(f"{API}/databases/{og_db}", headers=_headers(),
                            timeout=25)
    except Exception as exc:
        return {}, f"reading the schema failed ({type(exc).__name__})"
    if resp.status_code >= 300:
        return {}, (f"reading the schema failed: HTTP {resp.status_code} "
                    f"-- {resp.text[:200]}")
    # Types, not just names. Reading only the names meant the writer below had
    # to guess a type for each column, and it guessed `rich_text` for anything
    # it had no special case for. The OG tracker's Location is multi_select,
    # its Salary is number and its Application Status is a status property, so
    # every write of those three came back
    # `400 validation_error: Location is expected to be multi_select`.
    return {name: prop.get("type", "") for name, prop
            in resp.json().get("properties", {}).items()}, ""


def home_label() -> str:
    """The home city, upper-cased, as it appears on the tracker.

    This used to be the literal "RICHMOND". It is the city out of
    `home_metro` now, so a user in Denver gets DENVER and nobody has to edit
    Python to move house.
    """
    metro = profile.load("targeting.toml").get("home_metro", "")
    city = metro.split(",")[0].strip()
    return city.upper() if city else "LOCAL"


def location_values() -> tuple[str, ...]:
    return ("REMOTE", home_label(), "HYBRID", "ON-SITE")


def normalize_location(text: str) -> str:
    """Whatever a job posting scraped for location, collapsed to one of four
    values on the OG tracker (2026-08-26) -- REMOTE, the home city, HYBRID,
    ON-SITE -- replacing raw text like "United States", "Remote-USA", "VA",
    "San Francisco". Checked in that order because a posting can say both,
    e.g. "Hybrid - Denver, CO": hybrid still means some days in an office, so
    it wins over the city name.
    """
    t = (text or "").strip().lower()
    if "hybrid" in t:
        return "HYBRID"
    local = home_label()
    if local != "LOCAL" and local.lower() in t:
        return local
    if "remote" in t:
        return "REMOTE"
    return "ON-SITE"


def _as_number(text: str) -> float | None:
    """The first number in a salary string, or None if there isn't one.

    "$95,000 - $115,000 a year" is what Job Radar stores and a Notion number
    property will not take it. The bottom of a range is the honest single
    figure to record; a string with no digits at all is left alone rather than
    guessed at.
    """
    import re
    match = re.search(r"\d[\d,]*(?:\.\d+)?", text.replace(" ", ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _property_value(kind: str, value: str):
    """A Notion property payload of `kind` holding `value`, or None.

    None means this column cannot hold this value -- an unrecognised property
    type, or a number column given text with no digits in it. The caller skips
    those rather than sending something Notion will reject, because one bad
    field used to fail the whole page write and lose the other nine.
    """
    value = (value or "").strip()
    if not value:
        return None
    if kind == "title":
        return {"title": [{"text": {"content": value[:2000]}}]}
    if kind == "rich_text":
        return {"rich_text": [{"text": {"content": value[:2000]}}]}
    if kind == "url":
        return {"url": value[:2000]}
    if kind == "email":
        return {"email": value[:200]}
    if kind == "phone_number":
        return {"phone_number": value[:200]}
    if kind == "date":
        return {"date": {"start": value}}
    if kind == "number":
        number = _as_number(value)
        return {"number": number} if number is not None else None
    if kind == "select":
        return {"select": {"name": value[:100]}}
    if kind == "status":
        return {"status": {"name": value[:100]}}
    if kind == "multi_select":
        names = [part.strip()[:100] for part in value.split(",") if part.strip()]
        return {"multi_select": [{"name": n} for n in names]} if names else None
    if kind == "checkbox":
        return {"checkbox": value.lower() in ("true", "yes", "1", "y")}
    return None


def _find_og_page(company: str, role: str) -> str | None:
    """The OG row for this company + role, if one already exists there."""
    og_db, _ = _og_db()
    if not og_db:
        return None
    import requests
    # Filters carry the property's type too, so this reads the schema rather
    # than assuming Company is rich_text -- the same assumption that made
    # every write fail.
    schema, _ = _og_schema()
    clauses = []
    for name, wanted in (("Company", company), ("Position", role)):
        kind = schema.get(name)
        if kind in ("rich_text", "title", "select", "status", "url"):
            clauses.append({"property": name, kind: {"equals": wanted}})
    if not clauses:
        return None
    payload = {"filter": {"and": clauses} if len(clauses) > 1 else clauses[0]}
    try:
        resp = requests.post(f"{API}/databases/{og_db}/query",
                             headers=_headers(), json=payload, timeout=25)
    except Exception:
        return None
    if resp.status_code >= 300:
        return None
    results = resp.json().get("results", [])
    return results[0]["id"] if results else None


def sync_to_og_tracker(row: dict, echo=print) -> bool:
    """Populate the matching fields on the OG tracker's row for this posting.

    Only fields the OG schema has right now are written to -- that is what
    "only fields present in the OG schema are transferred" means in practice:
    a live schema read, not a hardcoded guess at column names.
    """
    _load_env()
    og_db, why = _og_db()
    if not og_db:
        echo(f"  OG tracker sync SKIPPED -- {why}")
        return False
    schema, why = _og_schema()
    if not schema:
        echo(f"  OG tracker sync SKIPPED -- {why}")
        return False

    props: dict = {}
    unwritable: list[str] = []
    for og_field, source_key in _OG_FIELD_SOURCE.items():
        if og_field not in schema:
            continue
        value = row.get(source_key, "")
        if og_field == "Location":
            value = normalize_location(value)
        payload = _property_value(schema[og_field], value)
        if payload is None:
            if (row.get(source_key) or "").strip():
                unwritable.append(f"{og_field} ({schema[og_field]})")
            continue
        props[og_field] = payload
    if _OG_STATUS_FIELD in schema:
        payload = _property_value(schema[_OG_STATUS_FIELD], _OG_STATUS_VALUE)
        if payload is not None:
            props[_OG_STATUS_FIELD] = payload
    if unwritable:
        echo(f"  columns skipped, the value does not fit the column's type: "
             f"{', '.join(unwritable)}")
    if not props:
        echo("  Nothing in the OG schema matched this row -- skipping")
        return False

    import requests
    page_id = _find_og_page(row["company"], row["role"])
    body = {"properties": props}
    try:
        if page_id:
            resp = requests.patch(f"{API}/pages/{page_id}", headers=_headers(),
                                  json=body, timeout=25)
        else:
            body["parent"] = {"database_id": og_db}
            resp = requests.post(f"{API}/pages", headers=_headers(), json=body,
                                 timeout=25)
    except Exception as exc:
        echo(f"  OG tracker sync failed ({type(exc).__name__})")
        return False
    if resp.status_code >= 300:
        echo(f"  OG tracker sync failed: HTTP {resp.status_code} -- "
            f"{resp.text[:200]}")
        return False
    echo(f"  OG tracker: {'updated' if page_id else 'created'} the row for "
        f"{row['company']} -- {row['role']}")
    return True


def backfill_og_locations(echo=print) -> tuple[int, int]:
    """One-time pass over every existing OG tracker row: re-write Location to
    one of REMOTE / <home city> / HYBRID / ON-SITE from whatever raw text is
    there now (2026-08-26). New syncs already normalize on write; this is for
    the rows written before that existed.

    Returns (changed, total). A row whose Location already normalizes to its
    current value is left alone -- no-op patches, no noise in Notion's edit
    history.
    """
    _load_env()
    og_db, why = _og_db()
    if not og_db:
        echo(f"  backfill SKIPPED -- {why}")
        return 0, 0
    schema, why = _og_schema()
    if not schema or "Location" not in schema:
        echo(f"  backfill SKIPPED -- {why or 'no Location column in the OG schema'}")
        return 0, 0
    if schema["Location"] != "multi_select":
        echo(f"  backfill SKIPPED -- Location is {schema['Location']!r}, not "
             f"multi_select")
        return 0, 0

    import requests
    changed = total = 0
    cursor = None
    while True:
        payload: dict = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        try:
            resp = requests.post(f"{API}/databases/{og_db}/query",
                                 headers=_headers(), json=payload, timeout=25)
        except Exception as exc:
            echo(f"  backfill query failed ({type(exc).__name__})")
            return changed, total
        if resp.status_code >= 300:
            echo(f"  backfill query failed: HTTP {resp.status_code}")
            return changed, total
        data = resp.json()
        for page in data.get("results", []):
            total += 1
            current = [opt.get("name", "") for opt in
                      page.get("properties", {}).get("Location", {})
                      .get("multi_select", [])]
            raw = ", ".join(current)
            wanted = normalize_location(raw) if current else ""
            if not wanted or current == [wanted]:
                continue
            patch = {"properties": {"Location": {"multi_select": [{"name": wanted}]}}}
            try:
                presp = requests.patch(f"{API}/pages/{page['id']}",
                                       headers=_headers(), json=patch, timeout=25)
            except Exception as exc:
                echo(f"  page {page['id']}: patch failed ({type(exc).__name__})")
                continue
            if presp.status_code >= 300:
                echo(f"  page {page['id']}: patch failed HTTP {presp.status_code}")
                continue
            changed += 1
            echo(f"  {raw!r} -> {wanted}")
        cursor = data.get("next_cursor")
        if not data.get("has_more") or not cursor:
            break
    echo(f"{changed} of {total} row(s) updated.")
    return changed, total


# -- turning a tracker row into something `prep()` can build -----------------

def enrich_with_cache(row: dict):
    """A Candidate for this row, with Job Radar's cached JD text if it has it.

    The Notion row alone doesn't carry the JD body, so this matches it back
    against the local candidate cache by company + role -- the same identity
    `applog.same_role` uses for the cooldown check -- and falls back to a
    bare candidate (JD obtained by fetch or paste, same as a manual `prep`)
    if the cache has already rolled past it.
    """
    from . import candidates as candidates_mod
    from .applog import company_key, role_key

    cached, _ = candidates_mod.load(limit=5000)
    ckey, rkey = company_key(row["company"]), role_key(row["role"])
    for candidate in cached:
        if company_key(candidate.company) == ckey and role_key(candidate.title) == rkey:
            return candidate
    return candidates_mod.Candidate(
        title=row["role"], company=row["company"], url=row["url"],
        location=row["location"], score=row["score"], tier=row["tier"],
        source="notion-applied", origin="manual")
