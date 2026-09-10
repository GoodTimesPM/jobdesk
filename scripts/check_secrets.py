"""Refuse to let a real credential sit in a tracked file.

Written after one got through. `job-radar/.env.example` -- a committed file,
the one whose whole job is to show the SHAPE of a secret -- carried a live
Notion integration token and a live Discord webhook for four commits. Nothing
was exposed, because this repo has never been pushed, but "not pushed yet" is
a fact about a Tuesday, not a property of the repo, and `git subtree split`
carries history into whatever it splits out.

So the shapes get checked instead of remembered. Only git-TRACKED files are
scanned: a real `.env` is git-ignored and is supposed to hold real values.

It also checks for the one thing a credential scanner normally misses: YOUR
OWN contact details. A phone number is not a secret, but it is the thing you
least want to discover in a repo you just made public, and it got into a test
fixture here for months without anything complaining. The values are read out
of `profile/master.toml`, which is git-ignored, so this file never has to name
them. No `profile/` means nothing to check.

    py scripts/check_secrets.py          # scan jobdesk/
    py scripts/check_secrets.py --all    # scan the whole repo

Runs as part of tests/run_all.py, and again inside publish-jobdesk.ps1
before anything leaves the machine.

Not installed as a git hook. This package lives inside the PROJECTS repo,
where `core.hooksPath` is repo-wide, and a hook that fires on every commit in
every project is how you end up with a hook everyone passes `--no-verify` to.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Each rule is (name, compiled pattern). Patterns match the LIVE shape only --
# placeholders like `ntn_your_integration_secret_here` or `webhooks/xxx/yyy`
# have to keep passing, or the example files become unwritable.
RULES = [
    ("Notion integration token",
     re.compile(r"\b(?:ntn_|secret_)[A-Za-z0-9]{40,}")),
    ("Discord webhook URL",
     re.compile(r"discord(?:app)?\.com/api/webhooks/\d{17,20}/[\w-]{60,}")),
    ("Slack token",
     re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("OpenAI/Anthropic-style API key",
     re.compile(r"\b(?:sk|sk-ant)-[A-Za-z0-9_-]{24,}")),
    ("AWS access key id",
     re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("Google API key",
     re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("GitHub token",
     re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}")),
    ("private key block",
     re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    # Not a secret, but it says which drive the author keeps their job search
    # on, and it breaks on every machine that is not this one. The application
    # log carried 151 of these before anything looked. A path relative to the
    # project root works everywhere and reveals nothing.
    ("a machine-absolute path",
     re.compile(r"(?<![\w.])[A-Za-z]:[\/]{1,2}(?:Users|home)[\/]|"
                r"(?<![\w.])/(?:home|Users)/[A-Za-z0-9._-]+/|"
                r"(?<![\w.])[A-Za-z]:\\?ALL STUFF")),
]

# A password assignment with something that is not obviously a placeholder on
# the right-hand side. Kept separate because it is the noisy one.
ASSIGNMENT = re.compile(
    r"^\s*(?:export\s+)?([A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY))"
    r"[ 	]*[:=][ 	]*[\"']?([^\s\"'#]+)",
    re.M)  # [ 	] not \s: \s eats the newline and reads the NEXT key as a value

PLACEHOLDER = re.compile(
    r"^$|your|example|placeholder|changeme|xxx|yyy|dummy|redacted|<.*>|"
    r"\.\.\.|test|fake|here$|^\$\{|^%.*%$",
    re.I)

# `REQUIRE_TOKEN = access.check(address)` is not a leaked token, and neither is
# `TOKEN_ENV = "JOBDESK_ACCESS_TOKEN"`. A secret pasted into source is a literal
# string of gibberish; a call or an attribute lookup is the code that goes and
# fetches one. Matching the shape of the expression is narrow enough to be safe,
# because a real credential holding a `(` and a `)` in that order, or reading as
# a dotted name, does not happen.
CODE = re.compile(r"^[A-Za-z_][\w.]*\(.*\)$|^[A-Za-z_]\w*(?:\.\w+)+$")

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".docx",
                 ".zip", ".woff", ".woff2", ".ttf"}


def identity_rules(strict: bool = False) -> list[tuple[str, re.Pattern[str]]]:
    """Your own contact details, read from the git-ignored real profile.

    Nothing here is hardcoded, so this scanner is safe to publish and stays
    correct for whoever runs it. The example profile is deliberately NOT
    consulted: its placeholder contact details are supposed to be in tracked
    files.

    `JOBDESK_IDENTITY_PROFILE` overrides where the identity is read from. That
    is for `publish-jobdesk.ps1`, which scans a staging folder built from a
    fresh copy: the staging folder has no `profile/` in it, by design, and a
    scanner that finds no identity to check quietly passes everything.

    `strict` adds the bare first name. Off by default and on for a publish,
    because most first names are also ordinary words, and a rule that fires
    on "a role for a Grant analyst" is a rule people learn to skip past. A
    publish is scanning a small tree that is about to be public, where a
    false alarm costs a glance and a miss costs a name on the internet.
    """
    override = os.environ.get("JOBDESK_IDENTITY_PROFILE", "").strip()
    master = Path(override) if override else ROOT / "profile" / "master.toml"
    if not master.exists():
        return []
    try:
        identity = tomllib.loads(master.read_text(encoding="utf-8")).get("identity", {})
    except (OSError, tomllib.TOMLDecodeError):
        return []

    rules: list[tuple[str, re.Pattern[str]]] = []
    for field in ("email", "linkedin", "github"):
        value = str(identity.get(field, "")).strip()
        if len(value) > 6:
            rules.append((f"your own {field}", re.compile(re.escape(value), re.I)))

    # The phone number, digit by digit, so a different separator still trips
    # it: 5551234567, 555-123-4567 and (555) 123 4567 are one number.
    digits = re.sub(r"\D", "", str(identity.get("phone", "")))
    if len(digits) >= 10:
        rules.append(("your own phone number",
                      re.compile(r"\D*".join(digits[-10:]))))

    # The full name, but not a bare first name: a first name is also a word.
    #
    # The separator is `[^A-Za-z]` rather than `\W`, and the edges are letter
    # lookarounds rather than `\b`, because an underscore is a word character
    # to both of those. That is not hypothetical. A resume variant is named
    # `First_Last_Company_Role`, the submission log holds 159 of them, and
    # this scanner called that file clean for as long as it has existed. A
    # name gets joined with whatever separator the surrounding format prefers,
    # so every non-letter has to count as a separator.
    name = str(identity.get("name", "")).strip()
    parts = [p for p in re.split(r"\s+", name) if len(p) > 2]
    if len(parts) >= 2:
        rules.append(("your own name",
                      re.compile(r"(?<![A-Za-z])" + r"[^A-Za-z]+".join(
                          re.escape(p) for p in (parts[0], parts[-1]))
                          + r"(?![A-Za-z])", re.I)))
        rules.append(("your own surname",
                      re.compile(r"(?<![A-Za-z])" + re.escape(parts[-1])
                                 + r"(?![A-Za-z])", re.I)))
        if strict:
            rules.append(("your own first name",
                          re.compile(r"(?<![A-Za-z])" + re.escape(parts[0])
                                     + r"(?![A-Za-z])", re.I)))
    return rules


def tracked_files(scope: Path) -> list[Path]:
    # Listed from INSIDE the scope, with a bare "." pathspec: passing an
    # absolute Windows path as a pathspec quietly matches the whole repo, and
    # a scanner that silently widens its scope is a scanner nobody reads.
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "."],
        cwd=str(scope), capture_output=True, text=True, check=True).stdout
    return [scope / name for name in out.split("\0") if name]


def scan(paths: list[Path], strict: bool = False) -> list[str]:
    rules = RULES + identity_rules(strict)
    hits: list[str] = []
    for path in paths:
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if path.name == "check_secrets.py":
            continue  # the rules themselves are not a leak
        for line_no, line in enumerate(body.splitlines(), 1):
            # The one place a real name belongs in a public repo. An MIT
            # license with no copyright holder is not a license, so this line
            # is the deliberate exception and every other line is a finding.
            if path.name == "LICENSE" and line.startswith("Copyright"):
                continue
            for name, pattern in rules:
                if pattern.search(line):
                    hits.append(f"{path}:{line_no}  {name}")
        for match in ASSIGNMENT.finditer(body):
            key, value = match.group(1), match.group(2)
            if (PLACEHOLDER.search(value) or CODE.match(value)
                    or len(value) < 12):
                continue
            line_no = body[:match.start()].count("\n") + 1
            hits.append(f"{path}:{line_no}  {key} looks like a real value")
    return hits


def main() -> int:
    args = sys.argv[1:]
    scope = ROOT.parent if "--all" in args else ROOT
    strict = "--strict" in args
    files = tracked_files(scope)
    hits = scan(files, strict)
    if hits:
        print(f"SECRETS FOUND in {len(hits)} place(s):")
        for hit in sorted(set(hits)):
            print("  " + hit)
        print("\nA credential goes into a git-ignored .env file, with a "
              "placeholder in its place, and then gets rotated -- it is in "
              "the history either way. A contact detail belongs in profile/, "
              "which is git-ignored; tests and examples use profile.example/.")
        return 1
    print(f"clean: {len(files)} tracked file(s) scanned, no live credentials")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
