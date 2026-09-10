"""One-time repair: postings recorded under the company "name".

Himalayas' API intermittently returns its own schema in the value slots, so a
whole page of postings arrives with companyName set to the literal string
"name". The collector learned to fall back to the company slug in the URL
(`radar/sources/boards.py`), but the rows collected before that fix are still
in seen.json, and the Archive tab lists "name" as the biggest employer in
Richmond.

The URL still carries the slug, so the rows are repairable rather than junk:

    py scripts/repair_seen_companies.py            # say what would change
    py scripts/repair_seen_companies.py --write    # change it

Writes seen.json directly and deliberately. `SeenStore.save()` prunes anything
past the retention window on its way out, which would take most of the archive
with it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jobdesk.radar import config
from jobdesk.radar.sources.boards import _PLACEHOLDER_COMPANY, _deslug

_SLUG = re.compile(r"himalayas\.app/companies/([^/]+)/")


def main() -> int:
    write = "--write" in sys.argv
    path = config.SEEN_FILE
    rows = json.loads(path.read_text(encoding="utf-8-sig"))

    fixed, stuck = {}, 0
    for uid, rec in rows.items():
        if str(rec.get("company", "")).strip().lower() not in _PLACEHOLDER_COMPANY:
            continue
        match = _SLUG.search(rec.get("url", ""))
        name = _deslug(match.group(1)) if match else ""
        if not name:
            stuck += 1
            continue
        fixed[uid] = name

    print(f"{len(rows)} rows, {len(fixed)} repairable, {stuck} with no slug to "
          f"read")
    for uid, name in list(fixed.items())[:10]:
        print(f"  {rows[uid]['company']!r} -> {name!r}  {rows[uid]['title'][:50]}")
    if len(fixed) > 10:
        print(f"  ... and {len(fixed) - 10} more")

    if not write:
        print("\nnothing written. Run again with --write.")
        return 0

    backup = path.with_suffix(".json.bak")
    backup.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    for uid, name in fixed.items():
        rows[uid]["company"] = name
    path.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"\nwrote {len(fixed)} row(s). The previous file is at {backup.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
