"""A skills library much bigger than vocabulary.toml, for reading postings.

vocabulary.toml is hand-made and small on purpose: every term in it can be
tagged on a resume bullet, implies other terms, and may be echoed into a
tailored resume. A posting asks for far more than that, and a word the reader
has no term for is a gap nobody sees. This library is for seeing them. It
never touches tailoring.

Two files under engine/data/:

- skills_library.json, built by scripts/build_skills_library.py from O*NET's
  technology skills and GitHub's topic list (both CC BY 4.0, see SOURCES.md).
  Rebuild it; do not edit it.
- skills_extra.toml, written by hand: certifications, compliance frameworks,
  finance and healthcare terms, methods the two sources lack, and the words
  that are never skills (benefits, equal-opportunity text). It wins over the
  generated file.

Matching is by whole phrases of tokens, looked up in a dict, so thousands of
terms cost about what one regex did. Each alias is either case-blind or
exact-case. Exact case is for acronyms and for names that are also common
words: "SOC" is a security operations center, "soc" is nothing; "Spark" is
Apache Spark, "spark" is a verb.

A library term that shares an alias with a vocabulary.toml term is folded
into that term instead of standing beside it, so "Microsoft SQL Server" from
O*NET counts as the profile's own SQL.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .vocab import Term, Vocabulary

DATA = Path(__file__).resolve().parent / "data"
LIBRARY_FILE = DATA / "skills_library.json"
EXTRA_FILE = DATA / "skills_extra.toml"

KINDS = ("tool", "method", "domain", "cert", "soft")

# A token is a run of letters and digits, kept whole across the joiners a
# skill name uses: node.js, ci/cd, t-sql, c++, c#, a+, ts/sci, 401(k) aside.
# A leading dot starts a token too, for .net. A trailing dot is a full stop.
_WORD = re.compile(r"\.?[A-Za-z0-9+#]+(?:[.\-/&'][A-Za-z0-9+#]+)*")


def tokens(text: str) -> list[str]:
    return _WORD.findall(text)


def phrase(text: str) -> str:
    """An alias as the index stores it: its tokens, one space apart."""
    return " ".join(tokens(text))


@dataclass
class Library:
    terms: dict[str, Term] = field(default_factory=dict)
    blind: dict[str, str] = field(default_factory=dict)   # lowered phrase -> term id
    exact: dict[str, str] = field(default_factory=dict)   # phrase as written -> term id
    not_skills: set[str] = field(default_factory=set)     # lowered
    longest: int = 1

    def find(self, text: str) -> dict[str, int]:
        """Term id -> occurrences, longest phrase first, no token counted twice."""
        toks = tokens(text)
        low = [t.lower() for t in toks]
        hits: dict[str, int] = {}
        i = 0
        while i < len(toks):
            for n in range(min(self.longest, len(toks) - i), 0, -1):
                tid = (self.exact.get(" ".join(toks[i:i + n]))
                       or self.blind.get(" ".join(low[i:i + n])))
                if tid:
                    hits[tid] = hits.get(tid, 0) + 1
                    i += n
                    break
            else:
                i += 1
        return hits

    def phrases(self, tid: str) -> tuple[list[str], list[str]]:
        return ([p for p, t in self.blind.items() if t == tid],
                [p for p, t in self.exact.items() if t == tid])


def _add(lib: Library, tid: str, blind, exact) -> None:
    for alias in blind:
        key = phrase(alias).lower()
        if key:
            lib.blind.setdefault(key, tid)
            lib.longest = max(lib.longest, key.count(" ") + 1)
    for alias in exact:
        key = phrase(alias)
        if key:
            lib.exact.setdefault(key, tid)
            lib.longest = max(lib.longest, key.count(" ") + 1)


def _entries() -> tuple[list[dict], set[str]]:
    """Hand-written entries first, so they claim their aliases first."""
    entries: list[dict] = []
    not_skills: set[str] = set()
    try:
        extra = tomllib.loads(EXTRA_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        extra = {}
    for entry in extra.get("term", []):
        entries.append({"id": "x:" + entry["id"], "label": entry["label"],
                        "kind": entry.get("kind", "tool"),
                        "ci": entry.get("aliases", []), "cs": entry.get("exact", []),
                        "same_as": entry.get("same_as", [])})
    not_skills.update(w.lower() for w in extra.get("not_skills", []))
    drop = {d.lower() for d in extra.get("drop", [])}
    try:
        built = json.loads(LIBRARY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        built = {}
    for entry in built.get("terms", []):
        if entry["label"].lower() in drop or entry["id"] in drop:
            continue
        entries.append(dict(entry, ci=[a for a in entry.get("ci", []) if a.lower() not in drop],
                            cs=[a for a in entry.get("cs", []) if a.lower() not in drop]))
    return entries, not_skills


def build(vocab: Vocabulary) -> Library:
    """The library, bound to one vocabulary: its terms win every alias."""
    lib = Library()
    entries, lib.not_skills = _entries()

    # Every phrase the vocabulary already answers to, pointed at its term.
    owned: dict[str, str] = {}
    for term in vocab.terms.values():
        for alias in (*term.aliases, term.label):
            owned.setdefault(phrase(alias).lower(), term.id)

    for entry in entries:
        ci, cs = entry.get("ci", []), entry.get("cs", [])
        if not ci and not cs:
            continue
        # Folded into a vocabulary term when the entry says so or when any of
        # its names is already that term's: two terms for one skill would
        # count it twice and could call it missing while the profile has it.
        target = next((t for t in entry.get("same_as", []) if t in vocab.terms), None)
        if target is None:
            target = next((owned[k] for k in (phrase(a).lower() for a in (*ci, *cs))
                           if k in owned), None)
        if target is None:
            tid = entry["id"]
            if tid in lib.terms or tid in vocab.terms:
                continue
            kind = entry.get("kind", "tool")
            lib.terms[tid] = Term(id=tid, label=entry["label"],
                                  kind=kind if kind in KINDS else "tool",
                                  aliases=tuple(dict.fromkeys(a.lower() for a in (*ci, *cs))))
            target = tid
        new_ci = [a for a in ci if phrase(a).lower() not in owned]
        new_cs = [a for a in cs if phrase(a).lower() not in owned]
        _add(lib, target, new_ci, new_cs)
        # Later entries fold into this one by the same rule, so O*NET's
        # Docker and GitHub's Docker are one term, not two.
        for a in (*new_ci, *new_cs):
            owned.setdefault(phrase(a).lower(), target)
    # A term every alias of which an earlier term took has nothing to match.
    used = set(lib.blind.values()) | set(lib.exact.values())
    lib.terms = {t: v for t, v in lib.terms.items() if t in used}
    return lib


class Reader:
    """vocabulary.toml and the library behind one interface, for reading text.

    `terms`, `label`, `kind`, `find` and `expand` answer like a Vocabulary's.
    `pattern(tid)` gives a regex for any term, for showing the line a match
    came from.
    """

    def __init__(self, vocab: Vocabulary, lib: Library | None = None):
        self.vocab = vocab
        self.library = lib if lib is not None else build(vocab)
        self.terms: dict[str, Term] = {**self.library.terms, **vocab.terms}
        self._extra: dict[str, re.Pattern] = {}

    def label(self, tid: str) -> str:
        term = self.terms.get(tid)
        return term.label if term else tid

    def kind(self, tid: str) -> str:
        term = self.terms.get(tid)
        return term.kind if term else ""

    def find(self, text: str) -> dict[str, int]:
        hits = self.vocab.find(text)
        for tid, n in self.library.find(text).items():
            hits[tid] = hits.get(tid, 0) + n
        return hits

    def expand(self, tags) -> set[str]:
        return self.vocab.expand(tags)

    def pattern(self, tid: str) -> re.Pattern | None:
        """A regex for every name of `tid`, case-blind or exact as stored."""
        if tid in self._extra:
            return self._extra[tid]
        blind, exact = self.library.phrases(tid)
        base = self.vocab._patterns.get(tid)
        if not blind and not exact:
            return base
        parts = [re.escape(p).replace(r"\ ", r"\W{1,3}") for p in blind]
        if base is not None:
            parts.append(base.pattern.removeprefix("(?<![a-z0-9])(?:").removesuffix(")(?![a-z0-9])"))
        alts = []
        if parts:
            alts.append("(?i:" + "|".join(parts) + ")")
        alts += [re.escape(p).replace(r"\ ", r"\W{1,3}") for p in exact]
        compiled = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(alts) + r")(?![A-Za-z0-9])")
        self._extra[tid] = compiled
        return compiled
