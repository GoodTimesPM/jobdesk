"""The Criteria tab's "About you" half: everything JobDesk knows about you.

The scoring half of the tab is `targeting.toml`. This half is the other two
files the program is built on: `master.toml`, which holds every true thing a
resume can say (contact details, schooling and the transcript, skills, jobs,
projects and their bullets), and `letter.toml`, which holds every paragraph a
cover letter can be built from. Both used to be visible only as TOML.

`view` turns them into sections of cards with plain labels. `edit` changes
one card's fields through `tomlpatch.patch_entry`, so the comments in the
files survive, and then runs the same checks the resume engine runs before it
builds anything. An edit that breaks one of those rules (a tag the vocabulary
does not know, a variant that adds a number its bullet does not have, a
letter slot the builder cannot fill) is put back and the reason is shown.

Only existing entries are edited here. Adding a job or a bullet means picking
an id, tags and a parent, which is a job for the file and the comment above
each table, and the page says so.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

from .. import profile
from . import tomlpatch


class Invalid(ValueError):
    """An edit the user can fix. The message says how."""


# How each editable key is shown and checked.
#   line  one line of text      long  a sentence or paragraph
#   tags  vocabulary term ids   int   a whole number
_LABELS = {
    "name": "Name", "location": "Location", "email": "Email", "phone": "Phone",
    "linkedin": "LinkedIn", "github": "GitHub",
    "degree": "Degree", "school": "School",
    "title": "Job title", "company": "Employer", "dates": "Dates",
    "category": "Group on the resume", "detail": "Shown after it",
    "label": "Written as",
    "text": "Wording", "template": "Paragraph", "fallback": "When no tools match",
    "tags": "What it shows",
    "priority": "Priority",
    "greeting": "Greeting", "sign_off": "Sign-off",
}

_HELP = {
    "tags": "Skill ids from vocabulary.toml, separated by commas. A posting "
            "that asks for one of these makes this line more likely to be "
            "picked.",
    "priority": "1 to 10. When two lines fit a posting equally, the higher "
                "number wins.",
    "template": "{role}, {company}, {tools} and {methods} are filled in when "
                "the letter is built. Every number in it has to be on the resume that "
                "goes with it, or the paragraph is left out.",
    "detail": "Optional, like \"Pandas, NumPy\" after Python.",
    "label": "Optional. How the skill is spelled on the resume, if not the "
             "usual way.",
}

# (file, header) -> (id key or None for a plain [section], {key: kind})
_EDITABLE = {
    ("master", "identity"): (None, {k: "line" for k in (
        "name", "location", "email", "phone", "linkedin", "github")}),
    ("master", "education"): ("id", {"degree": "line", "school": "line"}),
    ("master", "coursework"): ("id", {"name": "line", "tags": "tags",
                                      "priority": "int"}),
    ("master", "skill"): ("term", {"category": "line", "detail": "line",
                                   "label": "line", "priority": "int"}),
    ("master", "experience"): ("id", {"title": "line", "company": "line",
                                      "location": "line", "dates": "line"}),
    ("master", "project"): ("id", {"name": "line"}),
    ("master", "bullet"): ("id", {"text": "long", "tags": "tags",
                                  "priority": "int"}),
    ("master", "bullet.variant"): ("id", {"text": "long", "tags": "tags"}),
    ("master", "summary.opening"): ("id", {"text": "long"}),
    ("master", "summary.middle"): ("id", {"template": "long", "fallback": "long"}),
    ("master", "summary.closing"): ("id", {"text": "long"}),
    ("letter", "meta"): (None, {"greeting": "line", "sign_off": "line"}),
    ("letter", "opening"): ("id", {"template": "long"}),
    ("letter", "evidence"): ("id", {"template": "long"}),
    ("letter", "bridge"): ("id", {"template": "long"}),
    ("letter", "close"): ("id", {"template": "long"}),
}

# Keys that may be left blank. Everything else is a claim or a name, and a
# blank one prints as a gap on the resume.
_OPTIONAL = {"detail", "label"}

_FILES = {"master": "master.toml", "letter": "letter.toml"}
_SLOTS = {"role", "company", "tools", "methods"}

_FAMILY_WORDS = {
    "*": "every kind of job", "data": "data", "analysis": "analysis",
    "it-support": "IT support", "systems": "systems",
    "operations": "operations", "governance": "governance", "audit": "audit",
}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _read(name: str) -> dict:
    return tomllib.loads(profile.path(name).read_text(encoding="utf-8-sig"))


def _terms() -> dict[str, str]:
    try:
        vocab = profile.load("vocabulary.toml")
    except profile.ProfileError:
        return {}
    return {t["id"]: t.get("label", t["id"]) for t in vocab.get("term", [])}


def _families(entry: dict) -> str:
    fams = entry.get("families") or []
    if not fams:
        return "Used for every kind of job"
    words = [_FAMILY_WORDS.get(f, f.strip("*")) for f in fams]
    return "Used for " + ", ".join(words) + " jobs"


def _card(file: str, header: str, entry: dict, title: str, sub: str = "",
          badges: list[str] | None = None, terms: dict | None = None) -> dict:
    id_key, kinds = _EDITABLE[(file, header)]
    fields = []
    for key, kind in kinds.items():
        if key not in entry and key not in _OPTIONAL:
            continue
        value = entry.get(key, [] if kind == "tags" else "")
        field = {"key": key, "kind": kind, "label": _LABELS.get(key, key),
                 "help": _HELP.get(key, ""), "value": value}
        if kind == "tags" and terms is not None:
            field["names"] = [terms.get(t, t) for t in value]
        fields.append(field)
    return {"file": file, "header": header, "id_key": id_key,
            "id": entry.get(id_key) if id_key else None,
            "title": title, "sub": sub, "badges": badges or [],
            "fields": fields, "children": []}


def _bullets(raw: dict, parent: str, terms: dict) -> list[dict]:
    cards = []
    for b in raw.get("bullet", []):
        if b.get("parent") != parent:
            continue
        badges = ["Draft: left off every resume"] if b.get("draft") else []
        card = _card("master", "bullet", b, "Bullet", b["id"], badges, terms)
        for v in b.get("variant", []):
            card["children"].append(_card(
                "master", "bullet.variant", v, "Another way to say it",
                v["id"], [], terms))
        cards.append(card)
    return cards


def view() -> dict:
    """Every section of the two files, as cards the page can draw."""
    master = _read("master.toml")
    letter = _read("letter.toml")
    terms = _terms()
    sections = []

    sections.append({
        "id": "contact", "title": "Contact details",
        "intro": "The top of every resume and the letterhead of every cover "
                 "letter.",
        "cards": [_card("master", "identity", master.get("identity", {}),
                        "You")],
    })

    edu = [_card("master", "education", e, e.get("degree", ""), e.get("school", ""))
           for e in sorted(master.get("education", []), key=lambda e: e.get("order", 0))]
    courses = [_card("master", "coursework", c, c.get("name", ""),
                     "", [], terms)
               for c in sorted(master.get("coursework", []),
                               key=lambda c: -c.get("priority", 0))]
    sections.append({
        "id": "education", "title": "Education and transcript",
        "intro": "Degrees print as written. The courses are your transcript: "
                 "each resume lists the few that match the posting best, "
                 "under your first degree.",
        "cards": edu,
        "groups": [{"title": "Courses", "cards": courses}],
    })

    order = master.get("skills", {}).get("category_order", [])
    skills = master.get("skill", [])
    cats = order + sorted({s.get("category", "") for s in skills} - set(order))
    groups = []
    for cat in cats:
        cards = [_card("master", "skill", s,
                       s.get("label") or terms.get(s["term"], s["term"]),
                       f"Priority {s.get('priority', 5)}")
                 for s in skills if s.get("category", "") == cat]
        if cards:
            groups.append({"title": cat or "No group", "cards": cards})
    sections.append({
        "id": "skills", "title": "Skills",
        "intro": "The skills line on your resume. Postings that ask for a "
                 "skill bring it forward; the rest fill in by priority.",
        "cards": [], "groups": groups,
    })

    jobs = []
    for e in sorted(master.get("experience", []), key=lambda e: e.get("order", 0)):
        card = _card("master", "experience", e, e.get("title", ""),
                     ", ".join(x for x in (e.get("company"), e.get("dates")) if x))
        card["children"] = _bullets(master, e["id"], terms)
        jobs.append(card)
    sections.append({
        "id": "experience", "title": "Work experience",
        "intro": "Each job and every bullet it can show. A resume picks the "
                 "bullets that fit the posting, and may use one of the other "
                 "wordings instead. Another wording can change the words but "
                 "not the numbers.",
        "cards": jobs,
    })

    projects = []
    for p in sorted(master.get("project", []), key=lambda p: p.get("order", 0)):
        card = _card("master", "project", p, p.get("name", ""))
        card["children"] = _bullets(master, p["id"], terms)
        projects.append(card)
    sections.append({
        "id": "projects", "title": "Projects",
        "intro": "Coursework and competition projects, picked the same way "
                 "as job bullets.",
        "cards": projects,
    })

    summary = master.get("summary", {})
    shown = master.get("render", {}).get("summary", "full")
    groups = []
    for key, title in (("opening", "First sentence"), ("middle", "Tools sentence"),
                       ("closing", "Last sentence")):
        cards = [_card("master", f"summary.{key}", part, _families(part), part["id"])
                 for part in summary.get(key, [])]
        if cards:
            groups.append({"title": title, "cards": cards})
    sections.append({
        "id": "summary", "title": "Resume summary",
        "intro": ("Three sentences picked by the kind of job. "
                  + ("Your resumes leave the summary off right now "
                     "(summary = \"none\" in master.toml), so these are not "
                     "printed." if shown == "none" else "")),
        "cards": [], "groups": groups,
    })

    groups = []
    for key, title in (("opening", "Opening paragraph"),
                       ("evidence", "Middle paragraphs, about your work"),
                       ("bridge", "Linking paragraph"), ("close", "Closing paragraph")):
        cards = []
        for entry in letter.get(key, []):
            sub = _families(entry)
            if entry.get("requires_tools"):
                sub += ", only when the posting names a tool you list"
            if entry.get("requires_methods"):
                sub += ", only when the posting uses your Core Competencies words"
            cards.append(_card("letter", key, entry, entry["id"], sub))
        if cards:
            groups.append({"title": title, "cards": cards})
    sections.append({
        "id": "letter", "title": "Cover letter",
        "intro": "A cover letter is built from these paragraphs and nothing "
                 "else. For each posting JobDesk picks one opening, the "
                 "middle paragraphs that fit, and a close.",
        "cards": [_card("letter", "meta", letter.get("meta", {}), "Greeting and sign-off")],
        "groups": groups,
    })

    return {
        "files": {k: str(profile.path(v)) for k, v in _FILES.items()},
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _clean(key: str, kind: str, value, terms: dict) -> object:
    label = _LABELS.get(key, key)
    if kind == "int":
        try:
            number = int(str(value).strip())
        except ValueError:
            raise Invalid(f"{label} has to be a whole number") from None
        if not 0 <= number <= 100:
            raise Invalid(f"{label} has to be between 0 and 100")
        return number
    if kind == "tags":
        items = value if isinstance(value, list) else str(value).split(",")
        tags = []
        for item in items:
            tag = str(item).strip().lower()
            if tag and tag not in tags:
                tags.append(tag)
        unknown = [t for t in tags if terms and t not in terms]
        if unknown:
            raise Invalid(
                f"{', '.join(unknown)} {'is' if len(unknown) == 1 else 'are'} "
                "not in vocabulary.toml. Use an id from that file, or add the "
                "term there first.")
        return tags
    if not isinstance(value, str):
        raise Invalid(f"{label} has to be text")
    text = " ".join(value.split())
    if not text and key not in _OPTIONAL:
        raise Invalid(f"{label} cannot be blank")
    return text


def _master_problems(path: Path) -> set[str]:
    from ..engine import master as master_mod
    from ..engine import vocab as vocab_mod
    try:
        content = master_mod.load(path)
    except (KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        return {f"master.toml no longer loads: {exc}"}
    return set(master_mod.check(content, vocab_mod.load(profile.path("vocabulary.toml"))))


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)


def edit(file: str, header: str, ident, changes: dict) -> list[str]:
    """Apply one card's changes and return the keys that changed."""
    if (file, header) not in _EDITABLE:
        raise Invalid(f"{header} in {file} is not editable here")
    id_key, kinds = _EDITABLE[(file, header)]
    if not isinstance(changes, dict) or not changes:
        raise Invalid("no changes were sent")
    terms = _terms()
    clean = {}
    for key, value in changes.items():
        if key not in kinds:
            raise Invalid(f"{key} is not editable here; open the file for that")
        clean[key] = _clean(key, kinds[key], value, terms)

    if file == "letter":
        for key, text in clean.items():
            if kinds[key] == "long":
                bad = set(re.findall(r"\{(\w*)\}", text)) - _SLOTS
                if bad or text.count("{") != text.count("}"):
                    raise Invalid(
                        "A paragraph can only use {role}, {company}, "
                        "{tools} and {methods}. Anything else in curly brackets would print "
                        "as written.")

    path = profile.path(_FILES[file])
    original = path.read_bytes().decode("utf-8")
    try:
        if id_key is None:
            patched = tomlpatch.patch(original, clean, section=header)
        else:
            patched = tomlpatch.patch_entry(original, header, ident, clean,
                                            id_key=id_key)
    except tomlpatch.PatchError as exc:
        raise Invalid(f"{_FILES[file]} could not be edited: {exc}") from None
    if patched == original:
        return []

    before = _master_problems(path) if file == "master" else set()
    _write(path, patched)
    profile.forget()
    if file == "master":
        new = _master_problems(path) - before
        if new:
            _write(path, original)
            profile.forget()
            raise Invalid("Not saved. " + " ".join(sorted(new)))
    return list(clean)
