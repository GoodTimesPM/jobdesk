"""The ATS simulator: read the finished PDF back the way a machine would.

The premise is that a resume is two documents. One is the page a human sees.
The other is whatever text an applicant tracking system manages to pull out of
it, and that second document is the one that decides whether a human ever sees
the first. They are usually not the same, and nothing on your screen tells
you when they diverge.

So this module throws away the layout and re-reads the generated PDF with
PyMuPDF -- extraction, reading order, blocks, fonts, images -- and reports what
survived. Two separate numbers, because they are two separate failures:

  * PARSE score  -- can a machine read it at all? Missing contact details,
                    two-column layout, images, page overflow.
  * KEYWORD match -- given that it parsed, does it answer the JD's stated
                    requirements?

A resume can score 100 on one and fail the other, and the fix is different in
each case, so averaging them into a single number would hide the useful part.

Note: PyMuPDF, not pdfplumber (which the plan named). pdfplumber wraps
pdfminer.six for text extraction; PyMuPDF was already installed for other work
on this machine, does block-level extraction natively, and additionally exposes
the font and image tables this module needs for the structural checks. Same
job, one fewer dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

from . import config
from .jd import JobDescription
from .vocab import Vocabulary

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")
_LINKEDIN = re.compile(r"linkedin\.com/in/[A-Za-z0-9\-_%]+", re.IGNORECASE)
_LOCATION = re.compile(r"\b[A-Z][a-zA-Z.'\- ]{2,20},\s*(?:[A-Z]{2}\b|Virginia\b)")

EXPECTED_SECTIONS = ("SUMMARY", "EDUCATION", "SKILLS", "EXPERIENCE")
OPTIONAL_SECTIONS = ("PROJECTS", "CERTIFICATIONS")

# A right-hand block has to carry real prose to count as a second column. The
# date on an experience line sits right of centre and is about 26 characters;
# a sidebar is paragraphs. Without this threshold every correctly-built resume
# with right-aligned dates reports itself as two-column.
_COLUMN_MIN_CHARS = 60
_COLUMN_MIN_BANDS = 3


@dataclass
class AtsReport:
    path: Path
    pages: int
    text: str
    parse_score: int
    warnings: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    contact: dict[str, str] = field(default_factory=dict)
    sections_found: list[str] = field(default_factory=list)
    sections_missing: list[str] = field(default_factory=list)
    fonts: list[str] = field(default_factory=list)
    images: int = 0
    # keyword half -- only populated when a JD was supplied
    keyword_rate: float | None = None
    keyword_hit: list[str] = field(default_factory=list)
    keyword_missing: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.parse_score >= 90:
            return "parses cleanly"
        if self.parse_score >= 70:
            return "parses, with issues"
        return "likely to parse badly"


def _multi_column(page) -> bool:
    """Is there a sustained second column of real content?"""
    mid = page.rect.width / 2
    blocks = [b for b in page.get_text("blocks") if b[6] == 0]
    left = [b for b in blocks if b[2] < mid]
    right = [b for b in blocks if b[0] > mid and len(b[4].strip()) >= _COLUMN_MIN_CHARS]
    bands = 0
    for r in right:
        if any(not (l[3] < r[1] or l[1] > r[3]) for l in left):
            bands += 1
    return bands >= _COLUMN_MIN_BANDS


def simulate(pdf_path: Path, jd: JobDescription | None = None,
             vocab: Vocabulary | None = None) -> AtsReport:
    doc = fitz.open(str(pdf_path))
    try:
        text = "\n".join(page.get_text("text") for page in doc)
        pages = doc.page_count
        images = sum(len(page.get_images(full=True)) for page in doc)
        fonts, type3, multi = set(), False, False
        for page in doc:
            for f in page.get_fonts(full=True):
                fonts.add(f[3])
                if (f[2] or "").lower() == "type3":
                    type3 = True
            if _multi_column(page):
                multi = True
    finally:
        doc.close()

    warnings: list[str] = []
    passed: list[str] = []
    penalty = 0

    def fail(key: str, message: str) -> None:
        nonlocal penalty
        penalty += config.ATS_PENALTIES.get(key, 5)
        warnings.append(message)

    upper = text.upper()
    contact = {}
    for label, pattern, key, note in (
        ("email", _EMAIL, "no_email", "No email address found in the parsed text. "
         "This is the field most ATSes key the whole record on."),
        ("phone", _PHONE, "no_phone", "No phone number found in the parsed text."),
        ("linkedin", _LINKEDIN, "no_linkedin", "No LinkedIn URL found."),
        ("location", _LOCATION, "no_location", "No 'City, ST' location found -- "
         "many ATSes filter on parsed location."),
    ):
        match = pattern.search(text)
        if match:
            contact[label] = match.group(0)
            passed.append(f"{label} parsed as {match.group(0)}")
        else:
            fail(key, note)

    found = [s for s in EXPECTED_SECTIONS if s in upper]
    missing = [s for s in EXPECTED_SECTIONS if s not in upper]
    found += [s for s in OPTIONAL_SECTIONS if s in upper]
    for section in missing:
        fail("missing_section",
             f"Standard heading '{section}' not found. Parsers segment a resume "
             f"by these words; a section they can't find often lands in 'other'.")
    if not missing:
        passed.append("all standard section headings found")

    if multi:
        fail("multi_column", "Layout reads as multi-column. This is the single "
                             "most common cause of scrambled ATS output.")
    else:
        passed.append("single-column layout")

    if images:
        fail("images", f"{images} image(s) embedded. Anything inside an image is "
                       f"invisible to every parser.")
    else:
        passed.append("no images or graphics")

    if pages > 1:
        fail("over_pages", f"{pages} pages. Some parsers and most recruiters "
                           f"only take the first.")
    else:
        passed.append("one page")

    if type3:
        fail("type3_font", "A Type3 font is embedded; text extraction from Type3 "
                           "is unreliable.")
    if "�" in text or "(cid:" in text:
        fail("bad_glyphs", "Extracted text contains unmapped glyphs -- some "
                           "characters have no reliable Unicode mapping.")
    else:
        passed.append("all glyphs map to real characters")

    report = AtsReport(
        path=pdf_path, pages=pages, text=text,
        parse_score=max(0, 100 - penalty), warnings=warnings, passed=passed,
        contact=contact, sections_found=found, sections_missing=missing,
        fonts=sorted(fonts), images=images,
    )

    if jd is not None and vocab is not None:
        selectable = vocab.of_kind(config.SELECTION_KINDS)
        wanted = sorted(t for t in jd.required_terms if t in selectable)
        said = set(vocab.find(text))
        report.keyword_hit = [vocab.label(t) for t in wanted if t in said]
        report.keyword_missing = [vocab.label(t) for t in wanted if t not in said]
        report.keyword_rate = (
            len(report.keyword_hit) / len(wanted) if wanted else 1.0
        )

    return report


def to_markdown(report: AtsReport) -> str:
    lines = [
        f"# ATS simulation -- {report.path.name}",
        "",
        f"**Parse score: {report.parse_score}/100** ({report.verdict})",
    ]
    if report.keyword_rate is not None:
        hit, total = len(report.keyword_hit), len(report.keyword_hit) + len(report.keyword_missing)
        lines.append(
            f"**Keyword match: {report.keyword_rate:.0%}** "
            f"({hit}/{total} of the JD's required terms appear in the parsed text)"
        )
    lines += ["", f"Pages: {report.pages} | Images: {report.images} | "
                  f"Fonts: {', '.join(report.fonts) or 'none'}", ""]

    if report.warnings:
        lines += ["## Problems", ""]
        lines += [f"- {w}" for w in report.warnings] + [""]
    if report.keyword_missing:
        lines += ["## JD requirements the document never says", "",
                  "These are required-section terms with no match in the parsed "
                  "text. Each one is either a real skill gap or a bullet worth "
                  "adding to master.toml -- it is never a reason to write "
                  "something untrue.", ""]
        lines += [f"- {k}" for k in report.keyword_missing] + [""]
    lines += ["## Passed", ""] + [f"- {p}" for p in report.passed] + [""]
    lines += ["## What the machine actually reads", "", "```", report.text.strip(), "```", ""]
    return "\n".join(lines)
