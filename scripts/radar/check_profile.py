"""Check a targeting profile for the mistakes TOML cannot catch.

TOML tells you the file parses. It does not tell you that `for = "data analsyt"`
is a typo, and the scorer will not tell you either: an anchor that matches no
tier list and no skill key is silently worth nothing, so a bad synonym looks
exactly like a synonym that never fires.

Run it against the live profile:

    python scripts/radar/check_profile.py

or any other one:

    python scripts/radar/check_profile.py profile.example

Exit code is 1 if anything is wrong, so this works in a pre-commit hook.
"""

from __future__ import annotations

import pathlib
import sys
import tomllib

# The scorer matches alternates on word boundaries, so a short one is safe
# from colliding inside a longer word. Two characters is still not a term.
MIN_SYNONYM_LEN = 3


def check(path: pathlib.Path) -> list[str]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    problems: list[str] = []

    titles = set()
    for key in ("tier_1_titles", "tier_2_titles", "tier_3_titles",
                "adjacent_titles"):
        titles.update(data.get(key, []))
    skills = set(data.get("core_skills", {})) | set(data.get("supporting_skills", {}))

    seen: set[str] = set()
    for row in data.get("synonym", []):
        anchor = str(row.get("for", "")).strip().lower()
        if not anchor:
            problems.append("a [[synonym]] block has no 'for'")
            continue
        if anchor in seen:
            problems.append(f"{anchor!r}: two [[synonym]] blocks, merge them")
        seen.add(anchor)
        if anchor not in titles and anchor not in skills:
            problems.append(
                f"{anchor!r}: matches no tier list and no skill key, so it "
                f"scores nothing")
        for alt in row.get("also", []):
            alt = str(alt).strip().lower()
            if not alt:
                problems.append(f"{anchor!r}: an empty alternate")
            elif len(alt) < MIN_SYNONYM_LEN:
                problems.append(
                    f"{anchor!r}: {alt!r} is too short to match on, it will "
                    f"fire inside unrelated words")
            elif alt == anchor:
                problems.append(f"{anchor!r}: lists itself as an alternate")
            elif anchor in skills and alt.find(anchor) >= 0:
                problems.append(
                    f"{anchor!r}: {alt!r} already contains it, the plain "
                    f"substring match covers this one")
    return problems


def main(argv: list[str]) -> int:
    root = pathlib.Path(argv[1]) if len(argv) > 1 else pathlib.Path("profile")
    path = root / "targeting.toml" if root.is_dir() else root
    if not path.exists():
        print(f"no such profile: {path}")
        return 1

    problems = check(path)
    for p in problems:
        print(f"  {p}")
    print(f"{path}: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
