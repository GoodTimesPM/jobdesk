"""The shared vocabulary: canonical terms, aliases, and the matcher.

One matcher serves both directions -- reading a JD and validating master
content tags -- so the two can never drift apart. Everything is compiled once
per process into a single alternation per term.

Two optional fields per term widen what tailoring can do with a true claim:

- `implies`: other term ids this one is evidence of. A Power BI bullet is a
  dashboards bullet whether or not its text says "dashboard", so tailoring
  credits it for both. Applied transitively by `expand`.
- `echo`: aliases that mean exactly what the term means, so the resume may
  print the posting's own wording of a skill it can already back up. An ATS
  mostly matches literal text; "data analytics" in the JD scores nothing
  against "data analysis" on the page. Only phrases listed here are ever
  printed, and each must already be one of the term's aliases or its label.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import config


@dataclass(frozen=True)
class Term:
    id: str
    label: str
    kind: str
    aliases: tuple[str, ...]
    implies: tuple[str, ...] = ()
    echo: tuple[str, ...] = ()


def _pattern(phrases) -> re.Pattern:
    # Sort longest-first so "power bi" wins over a hypothetical "bi", and use
    # alnum lookarounds rather than \b: aliases like "node.js" end in a
    # non-word character, where \b silently fails to match.
    alts = "|".join(re.escape(a) for a in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alts})(?![a-z0-9])", re.IGNORECASE)


@dataclass
class Vocabulary:
    terms: dict[str, Term] = field(default_factory=dict)
    _patterns: dict[str, re.Pattern] = field(default_factory=dict)
    _echo: dict[str, re.Pattern] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for tid, term in self.terms.items():
            self._patterns[tid] = _pattern(term.aliases)
            if term.echo:
                self._echo[tid] = _pattern(term.echo)

    def find(self, text: str) -> dict[str, int]:
        """Term id -> number of occurrences in `text`."""
        lowered = text.lower()
        hits: dict[str, int] = {}
        for tid, pattern in self._patterns.items():
            n = len(pattern.findall(lowered))
            if n:
                hits[tid] = n
        return hits

    def expand(self, tags) -> set[str]:
        """`tags` plus everything they imply, followed all the way down."""
        out: set[str] = set()
        todo = list(tags)
        while todo:
            tid = todo.pop()
            if tid in out:
                continue
            out.add(tid)
            term = self.terms.get(tid)
            if term:
                todo.extend(term.implies)
        return out

    def surfaces(self, tid: str, text: str) -> list[str]:
        """The echo phrases of `tid` as `text` spells them, first seen first.

        Casing comes from `text`, so "Data Analytics" in a posting comes back
        capitalised and "data analytics" comes back lower-case. One entry per
        phrase however many times it occurs.
        """
        pattern = self._echo.get(tid)
        if pattern is None:
            return []
        seen: dict[str, str] = {}
        for m in pattern.finditer(text):
            seen.setdefault(m.group(0).lower(), m.group(0))
        return list(seen.values())

    def label(self, tid: str) -> str:
        term = self.terms.get(tid)
        return term.label if term else tid

    def kind(self, tid: str) -> str:
        term = self.terms.get(tid)
        return term.kind if term else ""

    def of_kind(self, kinds: tuple[str, ...]) -> set[str]:
        return {t.id for t in self.terms.values() if t.kind in kinds}


def load(path: Path | None = None) -> Vocabulary:
    raw = tomllib.loads((path or config.VOCAB_FILE).read_text(encoding="utf-8-sig"))
    terms: dict[str, Term] = {}
    for entry in raw.get("term", []):
        tid = entry["id"]
        if tid in terms:
            raise ValueError(f"vocabulary.toml: duplicate term id {tid!r}")
        aliases = tuple(entry.get("aliases") or [entry["label"]])
        terms[tid] = Term(
            id=tid, label=entry["label"], kind=entry.get("kind", "method"),
            aliases=aliases, implies=tuple(entry.get("implies", ())),
            echo=tuple(entry.get("echo", ())),
        )
    if not terms:
        raise ValueError("vocabulary.toml has no terms")

    for term in terms.values():
        for other in term.implies:
            if other not in terms:
                raise ValueError(f"vocabulary.toml: {term.id} implies {other!r}, "
                                 f"which is not a term id")
        # An echo phrase gets printed on the resume, so it has to be a word
        # the term already answers to. Anything else would be the vocabulary
        # writing claims of its own.
        known = {a.lower() for a in term.aliases} | {term.label.lower()}
        for phrase in term.echo:
            if phrase.lower() not in known:
                raise ValueError(f"vocabulary.toml: {term.id} echoes {phrase!r}, "
                                 f"which is not one of its aliases")
        if term.echo and term.kind not in ("tool", "method"):
            raise ValueError(f"vocabulary.toml: {term.id} is a {term.kind} "
                             f"term; only tools and methods can echo")
    return Vocabulary(terms=terms)
