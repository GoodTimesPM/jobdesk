"""Loading master.toml into typed objects.

The loader is deliberately strict. A typo in a `tags` list would not crash
anything -- it would just quietly stop that bullet from ever being selected,
and the resume would come out subtly worse for reasons nobody could see. So
unknown tags are an error, not a warning.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .vocab import Vocabulary

# Any digit run, with optional separators. Used to enforce the one mechanical
# truthfulness rule: a variant may reword a claim, never renumber it.
_NUMBERS = re.compile(r"\d[\d,\.]*")


def numbers_in(text: str) -> set[str]:
    return {m.group(0).rstrip(".").replace(",", "") for m in _NUMBERS.finditer(text)}


@dataclass
class Variant:
    id: str
    text: str
    tags: tuple[str, ...]


@dataclass
class Bullet:
    id: str
    parent: str
    text: str
    tags: tuple[str, ...]
    priority: int = 5
    draft: bool = False
    needs: str = ""
    variants: tuple[Variant, ...] = ()

    def phrasings(self) -> list[Variant]:
        """The base text plus every approved variant, base first.

        Base first matters: on an equal vocabulary match the engine keeps the
        phrasing that is already on the live resume, so a tailored copy differs
        from the master only where the JD gave it a reason to.
        """
        return [Variant(id=self.id, text=self.text, tags=self.tags), *self.variants]


@dataclass
class Experience:
    id: str
    title: str
    company: str
    location: str
    dates: str
    order: int
    min_bullets: int = 1
    max_bullets: int = 4


@dataclass
class Project:
    id: str
    name: str
    order: int
    min_bullets: int = 1
    max_bullets: int = 2


@dataclass
class Skill:
    term: str
    category: str
    priority: int = 5
    label: str = ""
    detail: str = ""
    # Listed under SKILLS, but never named in the summary line.
    summary_eligible: bool = True


@dataclass
class Course:
    id: str
    name: str
    tags: tuple[str, ...]
    priority: int = 5


@dataclass
class Education:
    id: str
    degree: str
    school: str
    order: int


@dataclass
class SummaryPart:
    id: str
    text: str
    families: tuple[str, ...]


@dataclass
class MiddleClause:
    """The tools sentence. `template` takes {tools}; `fallback` takes none."""
    id: str
    families: tuple[str, ...]
    template: str
    fallback: str


@dataclass
class Master:
    identity: dict
    openings: list[SummaryPart]
    closings: list[SummaryPart]
    middles: list[MiddleClause]
    middle_min_tools: int
    education: list[Education]
    coursework: list[Course]
    skills: list[Skill]
    skill_category_order: list[str]
    experience: list[Experience]
    projects: list[Project]
    bullets: list[Bullet]
    render: dict

    def bullets_for(self, parent: str, include_draft: bool = False) -> list[Bullet]:
        return [
            b for b in self.bullets
            if b.parent == parent and (include_draft or not b.draft)
        ]

    def bullet(self, bid: str) -> Bullet | None:
        return next((b for b in self.bullets if b.id == bid), None)


def _parts(raw: dict, key: str) -> list[SummaryPart]:
    return [
        SummaryPart(id=p["id"], text=p["text"], families=tuple(p.get("families", [])))
        for p in raw.get("summary", {}).get(key, [])
    ]


def load(path: Path | None = None) -> Master:
    raw = tomllib.loads((path or config.MASTER_FILE).read_text(encoding="utf-8-sig"))
    summary = raw.get("summary", {})

    bullets: list[Bullet] = []
    for b in raw.get("bullet", []):
        variants = tuple(
            Variant(id=v["id"], text=v["text"], tags=tuple(v.get("tags", [])))
            for v in b.get("variant", [])
        )
        bullets.append(Bullet(
            id=b["id"], parent=b["parent"], text=b["text"],
            tags=tuple(b.get("tags", [])), priority=int(b.get("priority", 5)),
            draft=bool(b.get("draft", False)), needs=b.get("needs", ""),
            variants=variants,
        ))

    return Master(
        identity=raw["identity"],
        openings=_parts(raw, "opening"),
        closings=_parts(raw, "closing"),
        middles=[
            MiddleClause(id=m["id"], families=tuple(m.get("families", [])),
                         template=m["template"], fallback=m["fallback"])
            for m in summary.get("middle", [])
        ],
        middle_min_tools=int(summary.get("middle_min_tools", 2)),
        education=[Education(**e) for e in raw.get("education", [])],
        coursework=[
            Course(id=c["id"], name=c["name"], tags=tuple(c.get("tags", [])),
                   priority=int(c.get("priority", 5)))
            for c in raw.get("coursework", [])
        ],
        skills=[Skill(**s) for s in raw.get("skill", [])],
        skill_category_order=list(raw.get("skills", {}).get("category_order", [])),
        experience=sorted(
            (Experience(**e) for e in raw.get("experience", [])),
            key=lambda e: e.order,
        ),
        projects=sorted(
            (Project(**p) for p in raw.get("project", [])), key=lambda p: p.order
        ),
        bullets=bullets,
        render=raw.get("render", {}),
    )


# --------------------------------------------------------------------------
# Integrity checks -- run by `main.py check` and by test_local.py
# --------------------------------------------------------------------------

def check(master: Master, vocab: Vocabulary) -> list[str]:
    """Return a list of problems. Empty list means the content is sound."""
    problems: list[str] = []
    known_terms = set(vocab.terms)
    parents = {e.id for e in master.experience} | {p.id for p in master.projects}

    seen_ids: set[str] = set()
    for b in master.bullets:
        if b.id in seen_ids:
            problems.append(f"duplicate bullet id: {b.id}")
        seen_ids.add(b.id)

        if b.parent not in parents:
            problems.append(f"{b.id}: parent {b.parent!r} is not an experience or project")

        for tag in b.tags:
            if tag not in known_terms:
                problems.append(f"{b.id}: unknown tag {tag!r} (not in vocabulary.toml)")

        base_numbers = numbers_in(b.text)
        for v in b.variants:
            if v.id in seen_ids:
                problems.append(f"duplicate variant id: {v.id}")
            seen_ids.add(v.id)
            for tag in v.tags:
                if tag not in known_terms:
                    problems.append(f"{v.id}: unknown tag {tag!r} (not in vocabulary.toml)")
            # The rule that matters: a reworded claim keeps the same facts.
            invented = numbers_in(v.text) - base_numbers
            if invented:
                problems.append(
                    f"{v.id}: introduces number(s) {sorted(invented)} that are not in "
                    f"the base bullet {b.id} -- a variant may reword a claim, never "
                    f"renumber it"
                )

    for c in master.coursework:
        for tag in c.tags:
            if tag not in known_terms:
                problems.append(f"coursework {c.id}: unknown tag {tag!r}")

    for s in master.skills:
        if s.term not in known_terms:
            problems.append(f"skill {s.term!r}: not a vocabulary term id")
        if s.category not in master.skill_category_order:
            problems.append(
                f"skill {s.term!r}: category {s.category!r} missing from "
                f"[skills].category_order"
            )

    for parent_id in parents:
        live = master.bullets_for(parent_id)
        entry = next(
            (e for e in master.experience if e.id == parent_id),
            next((p for p in master.projects if p.id == parent_id), None),
        )
        if entry and live and len(live) < entry.min_bullets:
            problems.append(
                f"{parent_id}: min_bullets={entry.min_bullets} but only "
                f"{len(live)} non-draft bullets exist"
            )

    return problems


def drafts(master: Master) -> list[Bullet]:
    return [b for b in master.bullets if b.draft]
