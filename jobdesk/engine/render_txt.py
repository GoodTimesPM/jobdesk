"""Plain-text rendering.

Two uses. Some application forms have a "paste your resume" box, and pasting
from a PDF drags in broken line breaks; this file pastes clean. And it is the
reference copy of what the document says, which makes it the thing to diff
against when a PDF parse looks wrong -- if the text file reads fine and the
PDF extract doesn't, the problem is the layout, not the content.
"""

from __future__ import annotations

from pathlib import Path

from .render_pdf import ascii_safe
from .tailor import Plan


def render(plan: Plan, path: Path) -> Path:
    ident = plan.master.identity
    lines: list[str] = [ident["name"]]
    contact = " | ".join(
        v for v in (ident.get("location"), ident.get("email"),
                    ident.get("phone"), ident.get("linkedin"),
                    ident.get("github")) if v
    )
    lines.append(contact)
    if plan.summary:
        lines += ["", "SUMMARY", plan.summary]
    lines += ["", "EDUCATION"]

    coursework = ", ".join(c.name for c in plan.coursework)
    for i, edu in enumerate(plan.master.education):
        lines.append(f"{edu.degree} - {edu.school}")
        if i == 0 and coursework:
            lines.append(f"  Relevant Coursework: {coursework}")

    lines += ["", "SKILLS"]
    for category, items in plan.skills:
        lines.append(f"{category}: {', '.join(items)}")

    lines += ["", "EXPERIENCE"]
    for section in plan.experience:
        entry = section.entry
        org = f"{entry.company} - {entry.location}" if entry.location else entry.company
        lines += ["", f"{entry.title} | {org} | {entry.dates}"]
        lines += [f"- {c.text}" for c in section.chosen]

    if plan.projects:
        lines += ["", "PROJECTS"]
        for section in plan.projects:
            lines += ["", section.entry.name]
            lines += [f"- {c.text}" for c in section.chosen]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ascii_safe("\n".join(lines)) + "\n", encoding="utf-8")
    return path
