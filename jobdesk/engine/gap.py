"""The keyword gap report.

Every JD the engine reads is kept. Once there are enough of them, the corpus
answers a question no single posting can: what do the employers you are
actually applying to keep asking for, and which of those does your resume have
nothing to say about?

That turns the learning plan from a guess into a ranked list. "Power BI appears
in 71% of these postings and you have it; Snowflake appears in 34% and you have
nothing" is a study decision, and it is the honest way to close a gap -- go
learn the thing, rather than write the word.

Two lists come out of it:
  * KNOWN terms, ranked by how often they're required -- covered vs not
  * UNMAPPED tokens, the capitalised words the vocabulary has no term for yet.
    These are how vocabulary.toml grows; a token that keeps appearing is a
    technology the engine is currently blind to.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import config
from .master import Master
from .vocab import Vocabulary


@dataclass
class GapRow:
    term_id: str
    label: str
    kind: str
    required_in: int
    mentioned_in: int
    covered: bool

    def rate(self, total: int) -> float:
        return self.required_in / total if total else 0.0


def _master_terms(master: Master) -> set[str]:
    """Every term your content can currently demonstrate."""
    terms: set[str] = {s.term for s in master.skills}
    for b in master.bullets:
        if b.draft:
            continue
        terms.update(b.tags)
        for v in b.variants:
            terms.update(v.tags)
    for c in master.coursework:
        terms.update(c.tags)
    return terms


def analyze(master: Master, vocab: Vocabulary,
            store: Path | None = None) -> tuple[list[GapRow], Counter, int]:
    store = store or config.JD_STORE
    files = sorted(store.glob("*.json"))
    have = _master_terms(master)

    required = Counter()
    mentioned = Counter()
    unmapped = Counter()
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        for tid in data.get("required_terms", []):
            required[tid] += 1
        for tid in data.get("weights", {}):
            mentioned[tid] += 1
        for token, _n in data.get("unmapped", []):
            unmapped[token] += 1

    rows = [
        GapRow(
            term_id=tid, label=vocab.label(tid), kind=vocab.kind(tid),
            required_in=required.get(tid, 0), mentioned_in=n,
            covered=tid in have,
        )
        for tid, n in mentioned.items()
    ]
    rows.sort(key=lambda r: (-r.required_in, -r.mentioned_in, r.label))
    return rows, unmapped, len(files)


def to_markdown(rows: list[GapRow], unmapped: Counter, total: int) -> str:
    if not total:
        return ("# Keyword gap report\n\nNo job descriptions stored yet. Run "
                "`tailor` on a few postings first -- every one is kept, and the "
                "report gets useful somewhere around a dozen.\n")

    lines = [
        "# Keyword gap report",
        "",
        f"Generated {date.today().isoformat()} from **{total}** stored job "
        f"description(s).",
        "",
        "`Required in` counts postings that named the term in their "
        "requirements section, not just anywhere in the text.",
        "",
    ]

    gaps = [r for r in rows if not r.covered and r.required_in]
    if gaps:
        lines += [
            "## Gaps -- required by employers, absent from your content",
            "",
            "| Term | Required in | % of postings | Kind |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {r.label} | {r.required_in} | {r.rate(total):.0%} | {r.kind} |"
            for r in gaps[:25]
        ]
        lines += ["", "These are the study list. Learn the ones near the top; "
                      "they are what this market is actually asking for.", ""]

    covered = [r for r in rows if r.covered and r.required_in]
    if covered:
        lines += [
            "## Strengths -- required by employers, already in your content",
            "",
            "| Term | Required in | % of postings |",
            "|---|---|---|",
        ]
        lines += [
            f"| {r.label} | {r.required_in} | {r.rate(total):.0%} |"
            for r in covered[:25]
        ]
        lines += ["", "Lead with the top rows. If one is buried in a bullet the "
                      "tailorer rarely picks, that is worth a rewrite in "
                      "master.toml.", ""]

    unused = [r for r in rows if r.covered and not r.required_in
              and r.mentioned_in <= max(1, total // 10)]
    if unused:
        lines += [
            "## Carried but rarely asked for",
            "",
            ", ".join(r.label for r in unused[:20]),
            "",
            "Not wrong to keep -- but these are the lines to cut first when a "
            "resume runs long.",
            "",
        ]

    if unmapped:
        lines += [
            "## Unmapped -- words the vocabulary doesn't know yet",
            "",
            "Capitalised tokens appearing in requirements with no canonical "
            "term. Anything here that is a real technology should be added to "
            "`content/vocabulary.toml`; until it is, the engine cannot see it.",
            "",
        ]
        lines += [f"- **{token}** ({n} posting{'s' if n > 1 else ''})"
                  for token, n in unmapped.most_common(25)]
        lines += [""]

    return "\n".join(lines)


def write_report(master: Master, vocab: Vocabulary) -> Path:
    rows, unmapped, total = analyze(master, vocab)
    path = config.REPORTS / f"keyword_gap_{date.today().isoformat()}.md"
    path.write_text(to_markdown(rows, unmapped, total), encoding="utf-8")
    return path
