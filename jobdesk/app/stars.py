"""The yellow star: postings worth coming back to.

Kept in `data/stars.json` on the server rather than in the browser, for the
same reason the settings are: one app, opened from a desktop window and a
phone. A star put on a row at lunch should be on the row that evening.

The file holds more than a list of ids. The radar keeps thirty days and then
a posting falls out of the candidate cache, and a star that pointed at it
would otherwise become a dangling id nobody could read. So each star also
carries the title, the company and the link as they were when it was set,
which is enough for the Jobs tab to say "three starred postings have aged
out" and name them instead of losing them quietly.

Read and write defensively. This is one person's bookmarks; a half-written
file should cost the stars, not the Jobs tab.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .. import paths

FILE = paths.DATA / "stars.json"

# What is worth remembering about a posting after it ages out of the cache.
_KEEP = ("title", "company", "url", "source")


def load() -> dict[str, dict]:
    """uid -> what was starred, as far as the file can be trusted."""
    try:
        data = json.loads(FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for uid, entry in data.items():
        if not isinstance(uid, str) or not uid:
            continue
        entry = entry if isinstance(entry, dict) else {}
        kept = {k: str(entry.get(k) or "") for k in _KEEP}
        kept["at"] = str(entry.get("at") or "")
        out[uid] = kept
    return out


def marked() -> set[str]:
    """Just the ids, for stamping a list of rows."""
    return set(load())


def _write(data: dict[str, dict]) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(FILE)


def set_star(uid: str, on: bool, about: dict | None = None) -> dict:
    """Star or unstar one posting. Returns the state it ended up in.

    Setting a star that is already set rewrites the label and leaves the date
    alone, so a second click from a stale tab is harmless rather than a way
    to make a posting look freshly starred.
    """
    uid = str(uid or "").strip()
    if not uid:
        raise ValueError("which posting? a star needs a uid")
    data = load()
    if not on:
        data.pop(uid, None)
        _write(data)
        return {"uid": uid, "starred": False, "count": len(data)}
    about = about or {}
    entry = data.get(uid) or {}
    entry.update({k: str(about.get(k) or entry.get(k) or "") for k in _KEEP})
    entry["at"] = entry.get("at") or datetime.now(timezone.utc).isoformat()
    data[uid] = entry
    _write(data)
    return {"uid": uid, "starred": True, "count": len(data)}


def gone(known: set[str]) -> list[dict]:
    """Starred postings that are no longer in the list `known` describes.

    Newest star first, because the one you set yesterday is the one you are
    still looking for.
    """
    missing = [dict(entry, uid=uid) for uid, entry in load().items()
               if uid not in known]
    missing.sort(key=lambda e: e.get("at") or "", reverse=True)
    return missing
