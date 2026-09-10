"""The ATS free-text answer bank (plan item 5).

Not clever, and deliberately so: the value is that the answers exist, are
consistent between applications, and are one keypress away at the moment the
portal asks. The only real logic is matching a question the portal asked to an
answer already written.

Answers carrying `confirm = true` are ones the example profile drafted that
you have never actually said. They still render -- an unusable answer bank
gets abandoned -- but they render marked, and the packet lists them at the top
so an unconfirmed legal or salary answer cannot go out unnoticed.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import config

_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class Answer:
    id: str
    question: str
    answer: str
    aliases: list[str] = field(default_factory=list)
    confirm: bool = False
    note: str = ""

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.confirm)

    @property
    def is_placeholder(self) -> bool:
        return self.answer.strip().startswith("PLACEHOLDER")


def load(path: Path | None = None) -> list[Answer]:
    data = tomllib.loads((path or config.ANSWERS_FILE).read_text(encoding="utf-8"))
    known = set(Answer.__dataclass_fields__)
    return [Answer(**{k: v for k, v in row.items() if k in known})
            for row in data.get("answer", [])]


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def match(question: str, bank: list[Answer] | None = None) -> list[tuple[int, Answer]]:
    """Rank the bank against a question, best first.

    An alias hit is worth far more than shared words: "salary" appearing in
    the asked question is decisive, while both strings containing "you" is
    noise.
    """
    bank = bank if bank is not None else load()
    low = question.lower()
    asked = _tokens(question)
    scored: list[tuple[int, Answer]] = []
    for entry in bank:
        score = 0
        for alias in entry.aliases:
            if alias.lower() in low:
                score += 10 + len(alias.split())
        overlap = asked & _tokens(entry.question)
        score += len(overlap - {"you", "the", "a", "to", "for", "in", "of",
                                "your", "is", "are", "do", "and", "this",
                                "what", "why", "how", "would", "like", "us"})
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda pair: -pair[0])
    return scored


def best(question: str, bank: list[Answer] | None = None) -> Answer | None:
    ranked = match(question, bank)
    return ranked[0][1] if ranked else None


def unconfirmed(bank: list[Answer] | None = None) -> list[Answer]:
    bank = bank if bank is not None else load()
    return [a for a in bank if a.needs_confirmation]


def to_markdown(bank: list[Answer] | None = None, *, company: str = "",
                role: str = "", about: str = "") -> str:
    """The ANSWERS.md that ships in every packet."""
    bank = bank if bank is not None else load()
    pending = [a for a in bank if a.needs_confirmation or a.is_placeholder]

    title = "# ATS answer bank"
    if company:
        title += f" -- {company}, {role}"
    out = [title, "",
           "Copy-paste answers for the free-text boxes. Same answers every "
           "time, which is the point: consistency across applications is what "
           "keeps a story straight when two of them reach the same recruiter.",
           ""]

    if pending:
        out += ["> **Read these first.** "
                f"{len(pending)} answer(s) below are unconfirmed drafts or "
                "placeholders. Do not paste them without reading:", ""]
        out += [f"> - **{a.id}** -- {a.note or 'unconfirmed draft'}" for a in pending]
        out += [""]

    if about:
        out += ["## What the posting says about the employer", "",
                "Raw material for the 'why this company' answer -- their own "
                "words, so a specific reaction is possible without inventing "
                "anything.", "",
                "> " + about.replace("\n", "\n> "), ""]

    out += ["---", ""]
    for entry in bank:
        marks = []
        if entry.needs_confirmation:
            marks.append("**CONFIRM**")
        if entry.is_placeholder:
            marks.append("**WRITE THIS ONE**")
        suffix = (" " + " ".join(marks)) if marks else ""
        out += [f"## {entry.question}{suffix}", ""]
        out += [entry.answer, ""]
        if entry.note:
            out += [f"*{entry.note}*", ""]
    return "\n".join(out)
