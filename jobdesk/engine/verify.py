"""Proving the output is true.

"Tailoring is selection of true statements, never invention" is easy to say and
easy to erode -- one convenient edit at a time, six months from now, under
deadline. So it is checked mechanically on every run instead of promised:

  1. every bullet in the plan is a phrasing that exists in master.toml
  2. no draft bullet is in the plan unless --include-draft was passed
  3. every one of those strings appears verbatim in the rendered PDF's own
     text layer -- read back out of the file, not trusted from memory

Step 3 matters because the PDF is what gets sent. If the renderer ever mangles,
truncates, or silently drops a line, this catches it before an employer does.
A failure here is a hard stop, not a warning.
"""

from __future__ import annotations

import re

from .master import Master
from .tailor import Plan

_NORM = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Collapse whitespace so PDF line-wrapping doesn't break comparison."""
    return _NORM.sub(" ", text).strip().lower()


def verify_plan(plan: Plan, include_draft: bool = False) -> list[str]:
    problems: list[str] = []
    master: Master = plan.master

    for chosen in plan.all_chosen():
        source = master.bullet(chosen.bullet.id)
        if source is None:
            problems.append(
                f"{chosen.bullet.id}: not in master.toml at all -- the plan "
                f"contains a bullet the content file does not"
            )
            continue
        approved = {normalize(p.text) for p in source.phrasings()}
        if normalize(chosen.text) not in approved:
            problems.append(
                f"{chosen.variant.id}: rendered text is not one of the approved "
                f"phrasings of {source.id}"
            )
        if source.draft and not include_draft:
            problems.append(
                f"{source.id}: draft content reached the resume. Drafts are "
                f"unconfirmed claims -- confirm it in master.toml first."
            )
    return problems


def verify_pdf(plan: Plan, pdf_text: str) -> list[str]:
    """Every line the plan promised is actually in the delivered file."""
    haystack = normalize(pdf_text)
    problems: list[str] = []

    if plan.summary and normalize(plan.summary) not in haystack:
        problems.append("the summary does not appear in the rendered PDF text")

    for chosen in plan.all_chosen():
        if normalize(chosen.text) not in haystack:
            problems.append(
                f"{chosen.variant.id}: selected but missing from the rendered "
                f"PDF (dropped, wrapped badly, or truncated)"
            )

    ident = plan.master.identity
    for field_ in ("name", "email", "phone"):
        value = ident.get(field_, "")
        if value and normalize(value) not in haystack:
            problems.append(f"identity.{field_} ({value}) missing from the PDF text")

    return problems
