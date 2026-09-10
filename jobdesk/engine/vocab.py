"""The shared vocabulary: canonical terms, aliases, and the matcher.

One matcher serves both directions -- reading a JD and validating master
content tags -- so the two can never drift apart. Everything is compiled once
per process into a single alternation per term.
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


@dataclass
class Vocabulary:
    terms: dict[str, Term] = field(default_factory=dict)
    _patterns: dict[str, re.Pattern] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for tid, term in self.terms.items():
            # Sort longest-first so "power bi" wins over a hypothetical "bi",
            # and use alnum lookarounds rather than \b: aliases like "node.js"
            # end in a non-word character, where \b silently fails to match.
            alts = "|".join(
                re.escape(a) for a in sorted(term.aliases, key=len, reverse=True)
            )
            self._patterns[tid] = re.compile(
                rf"(?<![a-z0-9])(?:{alts})(?![a-z0-9])", re.IGNORECASE
            )

    def find(self, text: str) -> dict[str, int]:
        """Term id -> number of occurrences in `text`."""
        lowered = text.lower()
        hits: dict[str, int] = {}
        for tid, pattern in self._patterns.items():
            n = len(pattern.findall(lowered))
            if n:
                hits[tid] = n
        return hits

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
            aliases=aliases,
        )
    if not terms:
        raise ValueError("vocabulary.toml has no terms")
    return Vocabulary(terms=terms)
