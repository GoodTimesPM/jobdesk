"""The house style for anything that goes to an employer.

A resume line or a letter paragraph says what you did, how you did it, and what
it taught you. It does not say what you did not do. "Rather than clicking
through a designer" spends half a line on an alternative the reader never asked
about, and that contrast is the habit that makes writing read as machine-made.

`problems` is run by `engine.main check` over master.toml, letter.toml and
answers.toml, and by the letter gate over every paragraph it is about to send,
so the rule holds for every profile and not only the one it was written for.
"""

from __future__ import annotations

import re

# (pattern, what to do instead). Each pattern is a phrase that only ever
# introduces an alternative, an aside or a filler beat.
_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\brather than\b", re.I), "drop the alternative and state what you did"),
    (re.compile(r"\binstead of\b", re.I), "drop the alternative and state what you did"),
    (re.compile(r"\bnot (?:just|only|simply|merely)\b", re.I), "state the whole claim once"),
    (re.compile(r"\b(?:less|more) about\b.{0,80}?\bthan about\b", re.I | re.S),
     "say what the work was about"),
    (re.compile(r"\b(?:is|was|it's|isn't|wasn't)\s+not?\b[^.;]{0,60},\s*but\b", re.I),
     "drop the 'not X, but Y' turn and state Y"),
    (re.compile(r"\bI would rather\b", re.I), "state the fact without the preference"),
    (re.compile(r"\b(?:I will name|to be (?:honest|clear|direct)|let me be (?:honest|clear|direct)"
                r"|I want to be (?:honest|clear|direct))\b", re.I),
     "cut the throat-clearing and open with the fact"),
    (re.compile(r"\b(?:actually|really|exactly)\b", re.I), "cut the intensifier"),
    (re.compile(r"\s--\s|—"), "use a comma or a new sentence, not a dash aside"),
]


def problems(text: str, where: str) -> list[str]:
    """One message per rule the text breaks, naming the phrase it found."""
    found = []
    for pattern, fix in _RULES:
        hit = pattern.search(text or "")
        if hit:
            phrase = " ".join(hit.group(0).split())[:40].strip() or "--"
            found.append(f"{where}: '{phrase}': {fix}")
    return found


def check_entries(entries: list[dict], kind: str, field: str) -> list[str]:
    """Style problems across a list of TOML tables, by their ids."""
    out: list[str] = []
    for n, entry in enumerate(entries):
        out += problems(str(entry.get(field, "")), f"{kind} {entry.get('id', n + 1)}")
    return out


def profile_problems() -> list[str]:
    """Style problems in the running profile's letter.toml and answers.toml.

    master.toml is checked by `engine.master.check`, which knows its shape.
    A placeholder answer is skipped: it is a note to the user, not prose that
    goes out.
    """
    from . import profile

    letter = profile.load_optional("letter.toml")
    out: list[str] = []
    for section in ("opening", "evidence", "bridge", "close"):
        out += check_entries(letter.get(section, []), f"letter {section}", "template")
    answers = [a for a in profile.load_optional("answers.toml").get("answer", [])
               if not str(a.get("answer", "")).strip().startswith("PLACEHOLDER")]
    out += check_entries(answers, "answer", "answer")
    return out
