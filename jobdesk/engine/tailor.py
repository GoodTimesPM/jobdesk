"""Tailoring: choose, order, and phrase -- never write.

Every line the tailorer emits comes out of master.toml verbatim. The only
assembled string in the whole module is the summary, and it is assembled from
approved fragments with two interpolations: the tools the JD actually asked for
(pulled from the skills list, so they are tools you have) and, optionally, an
industry word. Nothing else is generated, which is why `verify.py` can prove
mechanically that a rendered PDF says nothing the master file doesn't.

Selection is maximal-marginal-coverage, not top-N-by-score. Ranking bullets by
raw relevance and taking the best four gets you four bullets about Active
Directory for a JD that mentions it once; greedy coverage spends each slot on
whatever the resume hasn't answered yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .jd import JobDescription
from .master import Bullet, Course, Experience, Master, Project, Variant
from .vocab import Vocabulary


@dataclass
class ChosenBullet:
    bullet: Bullet
    variant: Variant
    value: float          # total JD weight this bullet's tags carry
    gain: float           # JD weight it added that nothing else covered
    reason: str

    @property
    def text(self) -> str:
        return self.variant.text

    @property
    def rephrased(self) -> bool:
        return self.variant.id != self.bullet.id


@dataclass
class Section:
    entry: Experience | Project
    chosen: list[ChosenBullet] = field(default_factory=list)


@dataclass
class Plan:
    master: Master
    jd: JobDescription
    summary: str
    skills: list[tuple[str, list[str]]]        # (category, rendered items)
    coursework: list[Course]
    experience: list[Section]
    projects: list[Section]
    notes: list[str] = field(default_factory=list)

    # -- content access ----------------------------------------------------
    def all_chosen(self) -> list[ChosenBullet]:
        out: list[ChosenBullet] = []
        for section in (*self.experience, *self.projects):
            out.extend(section.chosen)
        return out

    def plain_text(self) -> str:
        """Everything the document will say, for coverage and verification."""
        parts = [self.summary] if self.summary else []
        parts += [f"{cat}: {', '.join(items)}" for cat, items in self.skills]
        parts += [c.name for c in self.coursework]
        for section in (*self.experience, *self.projects):
            entry = section.entry
            parts.append(getattr(entry, "name", "") or
                         f"{entry.title} {entry.company}")
            parts += [c.text for c in section.chosen]
        return "\n".join(p for p in parts if p)

    def bullet_count(self) -> int:
        return len(self.all_chosen())

    def drop_weakest(self) -> ChosenBullet | None:
        """Remove the least valuable optional bullet, for page fitting.

        Returns what was dropped, or None when every remaining bullet is
        protected by its section's `min_bullets`.
        """
        worst: tuple[Section, ChosenBullet] | None = None
        for section in (*self.experience, *self.projects):
            if len(section.chosen) <= section.entry.min_bullets:
                continue
            local = min(section.chosen, key=lambda c: (c.gain, c.value,
                                                       c.bullet.priority))
            if worst is None or (local.gain, local.value) < (worst[1].gain,
                                                            worst[1].value):
                worst = (section, local)
        if worst is None:
            return None
        section, chosen = worst
        section.chosen.remove(chosen)
        # A section that just lost its last bullet leaves the plan entirely.
        # Projects can empty out (their floor is 0), and a heading with nothing
        # under it printed on the resume, counted toward coverage in
        # `plain_text`, and told the reader about a project the document does
        # not actually describe.
        if not section.chosen:
            for group in (self.experience, self.projects):
                if section in group:
                    group.remove(section)
        return chosen


# --------------------------------------------------------------------------
# the pieces
# --------------------------------------------------------------------------

def _selectable(vocab: Vocabulary) -> set[str]:
    return vocab.of_kind(config.SELECTION_KINDS)


def _value(tags, jd: JobDescription, selectable: set[str]) -> float:
    return sum(jd.weight_of(t) for t in tags if t in selectable)


def _pick_variant(bullet: Bullet, jd: JobDescription,
                  selectable: set[str]) -> tuple[Variant, bool]:
    """Choose the phrasing whose vocabulary best matches this JD.

    Base text wins ties by half a point, so a tailored resume only diverges
    from the live one where the JD gave it a reason to.
    """
    best, best_score = None, float("-inf")
    for i, phrasing in enumerate(bullet.phrasings()):
        score = _value(phrasing.tags, jd, selectable) + (0.5 if i == 0 else 0.0)
        if score > best_score:
            best, best_score = phrasing, score
    assert best is not None
    return best, best.id != bullet.id


def _select_bullets(master: Master, jd: JobDescription, vocab: Vocabulary,
                    include_draft: bool) -> tuple[list[Section], list[Section]]:
    selectable = _selectable(vocab)
    ceiling = int(master.render.get("total_bullet_ceiling", 12))

    entries: list[Experience | Project] = [*master.experience, *master.projects]
    sections = {e.id: Section(entry=e) for e in entries}
    pool: dict[str, list[Bullet]] = {
        e.id: master.bullets_for(e.id, include_draft) for e in entries
    }
    covered: dict[str, int] = {}

    def marginal(bullet: Bullet) -> float:
        return sum(
            jd.weight_of(t) for t in bullet.tags
            if t in selectable and t not in covered
        )

    def take(entry_id: str, bullet: Bullet, reason: str) -> None:
        variant, _ = _pick_variant(bullet, jd, selectable)
        gain = marginal(bullet)
        sections[entry_id].chosen.append(ChosenBullet(
            bullet=bullet, variant=variant,
            value=_value(bullet.tags, jd, selectable), gain=gain, reason=reason,
        ))
        for t in bullet.tags:
            covered[t] = covered.get(t, 0) + 1
        pool[entry_id].remove(bullet)

    # Pass 1 -- every role gets its floor, best-first. A role with no bullets
    # is a hole in the timeline, which reads worse than a weak bullet.
    for entry in entries:
        floor = min(entry.min_bullets, len(pool[entry.id]))
        for _ in range(floor):
            candidates = pool[entry.id]
            if not candidates:
                break
            best = max(candidates, key=lambda b: (marginal(b),
                                                  _value(b.tags, jd, selectable),
                                                  b.priority))
            take(entry.id, best, "required to keep the role represented")

    # Pass 2 -- spend what's left on whatever the resume still doesn't answer.
    while sum(len(s.chosen) for s in sections.values()) < ceiling:
        best: tuple[float, float, int, str, Bullet] | None = None
        for entry in entries:
            section = sections[entry.id]
            if len(section.chosen) >= entry.max_bullets:
                continue
            for bullet in pool[entry.id]:
                key = (marginal(bullet), _value(bullet.tags, jd, selectable),
                       bullet.priority, entry.id, bullet)
                if best is None or key[:3] > best[:3]:
                    best = key
        if best is None:
            break
        gain, _value_, _priority, entry_id, bullet = best
        take(entry_id, bullet,
             "covers terms nothing else did" if gain > 0
             else "strongest remaining bullet for this role")

    # Read order inside a role: most relevant to this JD first. Recruiters
    # skim the first line of each block and stop.
    for section in sections.values():
        section.chosen.sort(key=lambda c: (-c.value, -c.bullet.priority))

    exp = [sections[e.id] for e in master.experience if sections[e.id].chosen]
    proj = [sections[p.id] for p in master.projects if sections[p.id].chosen]
    return exp, proj


def _order_skills(master: Master, jd: JobDescription,
                  vocab: Vocabulary) -> list[tuple[str, list[str]]]:
    """Put the JD's stack first -- and add nothing that isn't already here.

    A skill you don't have never enters this list no matter how loudly the
    JD asks for it. It leaves as a line in the gap report instead, which is the
    honest response: go learn it.
    """
    by_category: dict[str, list] = {}
    for skill in master.skills:
        by_category.setdefault(skill.category, []).append(skill)

    ranked_rows: list[tuple[int, str, list[str]]] = []
    for category in master.skill_category_order:
        skills = by_category.get(category, [])
        if not skills:
            continue
        skills.sort(key=lambda s: (-jd.weight_of(s.term), -s.priority,
                                   vocab.label(s.term)))
        items = []
        for s in skills:
            label = s.label or vocab.label(s.term)
            items.append(f"{label} ({s.detail})" if s.detail else label)
        rank = max(jd.weight_of(s.term) for s in skills)
        ranked_rows.append((rank, category, items))

    ranked_rows.sort(key=lambda row: (-row[0],
                                      master.skill_category_order.index(row[1])))
    return [(category, items) for _, category, items in ranked_rows]


def _pick_coursework(master: Master, jd: JobDescription,
                     vocab: Vocabulary) -> list[Course]:
    selectable = _selectable(vocab)
    limit = int(master.render.get("coursework_shown", 7))
    ranked = sorted(
        master.coursework,
        key=lambda c: (-_value(c.tags, jd, selectable), -c.priority, c.name),
    )
    return ranked[:limit]


def _build_summary(master: Master, jd: JobDescription, vocab: Vocabulary,
                   mode: str = "full") -> str:
    """Assemble the summary. `mode` decides how much of it survives.

    "none" returns an empty string and the renderers omit the section. That
    buys about four lines of page budget, and it costs an ATS parse point --
    SUMMARY is one of the four sections the simulator expects to find -- so
    "short" (opening + tool clause, minus the boilerplate closing) is usually
    the better trade: it frees two lines and still names the tools.
    """
    if mode == "none":
        return ""
    family = jd.family()
    opening = next((o for o in master.openings if family in o.families),
                   master.openings[0])

    # The middle clause names tools the JD asked for AND you have -- the
    # intersection, computed from the skills list, so it can't overclaim.
    have = {s.term: s for s in master.skills if s.summary_eligible}
    wanted = sorted(
        (
            (jd.weight_of(term), skill.priority, term)
            for term, skill in have.items()
            if jd.weight_of(term) > 0 and vocab.kind(term) == "tool"
        ),
        reverse=True,
    )
    labels = [vocab.label(t) for _, _, t in wanted[:4]]
    middle_clause = next((m for m in master.middles if family in m.families),
                        master.middles[0])
    if len(labels) >= master.middle_min_tools:
        # Two items take a plain "A and B". The serial-comma form was printing
        # "Tableau, and Excel" on every JD that matched exactly two tools.
        if len(labels) == 1:
            tools = labels[0]
        elif len(labels) == 2:
            tools = f"{labels[0]} and {labels[1]}"
        else:
            tools = ", ".join(labels[:-1]) + f", and {labels[-1]}"
        middle = middle_clause.template.format(tools=tools)
    else:
        middle = middle_clause.fallback

    if mode == "short":
        return " ".join(p for p in (opening.text, middle) if p)

    domain_id = jd.domain(vocab)
    closing = next((c for c in master.closings if "*domain*" in c.families), None)
    if domain_id and closing:
        closing_text = closing.text.format(domain=vocab.label(domain_id))
    else:
        default = next((c for c in master.closings if not c.families),
                       master.closings[-1])
        closing_text = default.text

    return " ".join(p for p in (opening.text, middle, closing_text) if p)


# --------------------------------------------------------------------------

def build(master: Master, jd: JobDescription, vocab: Vocabulary,
          include_draft: bool = False, summary_mode: str = "full") -> Plan:
    experience, projects = _select_bullets(master, jd, vocab, include_draft)
    skills = _order_skills(master, jd, vocab)
    coursework = _pick_coursework(master, jd, vocab)
    summary = _build_summary(master, jd, vocab, summary_mode)

    plan = Plan(
        master=master, jd=jd, summary=summary, skills=skills,
        coursework=coursework, experience=experience, projects=projects,
    )
    if jd.years_required and jd.years_required > 3:
        plan.notes.append(
            f"This JD's binding requirement is {jd.years_required} years. "
            f"Tailoring can't close that -- decide whether it's worth applying."
        )
    return plan


# --------------------------------------------------------------------------
# coverage report
# --------------------------------------------------------------------------

@dataclass
class Coverage:
    required_hit: list[str]
    required_missed: list[str]
    wanted_hit: list[str]
    wanted_missed: list[str]
    draft_would_help: list[tuple[str, list[str]]]
    unmapped: list[tuple[str, int]]

    @property
    def required_rate(self) -> float:
        total = len(self.required_hit) + len(self.required_missed)
        return len(self.required_hit) / total if total else 1.0


def coverage(plan: Plan, vocab: Vocabulary) -> Coverage:
    """What the finished document actually says, measured against the JD.

    Read off the rendered text rather than off the selection, so a term that
    only ever appeared in a bullet that got dropped for page fit counts as
    missed -- which is the truth.
    """
    said = set(vocab.find(plan.plain_text()))
    jd = plan.jd
    selectable = _selectable(vocab)

    required = sorted(t for t in jd.required_terms if t in selectable)
    wanted = sorted(
        t for t in jd.weights
        if t in selectable and t not in jd.required_terms
    )

    missed = [t for t in required if t not in said] + \
             [t for t in wanted if t not in said]
    draft_help: list[tuple[str, list[str]]] = []
    for b in plan.master.bullets:
        if not b.draft:
            continue
        helps = [t for t in b.tags if t in missed]
        if helps:
            draft_help.append((b.id, [vocab.label(t) for t in helps]))

    return Coverage(
        required_hit=[vocab.label(t) for t in required if t in said],
        required_missed=[vocab.label(t) for t in required if t not in said],
        wanted_hit=[vocab.label(t) for t in wanted if t in said],
        wanted_missed=[vocab.label(t) for t in wanted if t not in said],
        draft_would_help=draft_help,
        unmapped=jd.unmapped,
    )
