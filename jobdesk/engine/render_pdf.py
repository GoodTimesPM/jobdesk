"""ATS-safe PDF rendering.

Ported from `WORK/JOB SEARCH 2026/CLAUDE/build_resume_pdf.py`, which produced
the live resume, and turned into a function of a `Plan`. The visual result is
deliberately the same document -- what changed is that the content now comes
from master.toml instead of being hard-coded.

Every layout decision here is an ATS constraint, not a taste call:
  * one column, one text flow -- two columns are the single most common reason
    a resume parses into scrambled nonsense
  * no tables, text boxes, headers, footers, graphics or icons
  * a core PostScript font (Helvetica), so the text layer is real text with
    standard encoding rather than an embedded subset with custom glyph ids
  * section headings spelled the way parsers expect: SUMMARY, EDUCATION,
    SKILLS, EXPERIENCE, PROJECTS
  * dates as plain text on the same line as the title, right-aligned by
    position rather than by a table cell
"""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

from .tailor import Plan

PAGE_H = 279.4          # Letter, mm
BOT_MARGIN = 12
MARGIN = 14

# Core fonts are Latin-1. Anything outside it would raise, and a resume full of
# typographic quotes is worse for parsers anyway -- ASCII is the safe surface.
_ASCII = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", " ": " ",
    "•": "-", "·": "-", "−": "-",
}


def ascii_safe(text: str) -> str:
    for bad, good in _ASCII.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


class ResumePDF(FPDF):
    def __init__(self) -> None:
        super().__init__("P", "mm", "Letter")
        # Auto page break ON, unlike the original script: overflow has to
        # create a second page so the fitting loop can SEE that it overflowed.
        # With it off, extra content just fell off the bottom silently.
        self.set_auto_page_break(auto=True, margin=BOT_MARGIN)
        self.set_title("")
        self.set_author("")

    def section_header(self, title: str, sp: float = 2.5) -> None:
        self.set_font("Helvetica", "B", 10.5)
        self.set_text_color(30, 30, 30)
        self.cell(0, 5.5, title.upper(), new_x="LMARGIN", new_y="NEXT")
        y = self.get_y()
        self.set_draw_color(50, 50, 50)
        self.set_line_width(0.35)
        self.line(self.l_margin, y, self.w - self.r_margin, y)
        self.ln(sp)

    def bullet(self, text: str, indent: float = 8, lh: float = 4.0) -> None:
        self.set_x(self.get_x() + indent)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(40, 40, 40)
        self.cell(self.get_string_width("-  "), lh, "-", new_x="END")
        self.multi_cell(self.w - self.r_margin - self.get_x() - 1, lh, " " + text)
        self.ln(0.6)


def render(plan: Plan, path: Path) -> tuple[int, float]:
    """Write the PDF. Returns (page count, mm of slack on the last page)."""
    ident = plan.master.identity
    pdf = ResumePDF()
    pdf.add_page()
    pdf.set_margins(MARGIN, 12, MARGIN)
    pdf.set_y(12)
    pdf.set_x(MARGIN)

    # -- name -------------------------------------------------------------
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, 9, ascii_safe(ident["name"]), align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(0.5)

    contact = "  |  ".join(
        v for v in (ident.get("location"), ident.get("email"),
                    ident.get("phone"), ident.get("linkedin"),
                    ident.get("github")) if v
    )
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 4.5, ascii_safe(contact), align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # -- summary ----------------------------------------------------------
    if plan.summary:
        pdf.section_header("Summary")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(40, 40, 40)
        pdf.multi_cell(0, 4.2, ascii_safe(plan.summary))
        pdf.ln(4.5)

    # -- education --------------------------------------------------------
    pdf.section_header("Education")
    coursework = ", ".join(c.name for c in plan.coursework)
    for i, edu in enumerate(plan.master.education):
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(0, 4.5, ascii_safe(edu.degree), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, 4, ascii_safe(edu.school), new_x="LMARGIN", new_y="NEXT")
        if i == 0 and coursework:
            pdf.ln(1)
            pdf.set_font("Helvetica", "", 8.5)
            pdf.set_text_color(60, 60, 60)
            pdf.set_x(MARGIN + 4)
            pdf.multi_cell(0, 3.8, ascii_safe(f"Relevant Coursework: {coursework}"))
        pdf.ln(2)
    pdf.ln(2)

    # -- skills -----------------------------------------------------------
    pdf.section_header("Skills")
    for category, items in plan.skills:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(35, 4, ascii_safe(category))
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(50, 50, 50)
        pdf.multi_cell(0, 4, ascii_safe(", ".join(items)))
        pdf.ln(0.8)
    pdf.ln(3.5)

    # -- experience -------------------------------------------------------
    pdf.section_header("Experience")
    for i, section in enumerate(plan.experience):
        entry = section.entry
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_text_color(30, 30, 30)
        title = ascii_safe(entry.title)
        pdf.cell(pdf.get_string_width(title) + 2, 4.5, title)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 4.5, ascii_safe(entry.dates), align="R",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(80, 80, 80)
        org = f"{entry.company} - {entry.location}" if entry.location else entry.company
        pdf.cell(0, 4, ascii_safe(org), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1.5)
        for chosen in section.chosen:
            pdf.bullet(ascii_safe(chosen.text))
        if i < len(plan.experience) - 1:
            pdf.ln(3)

    # -- projects ---------------------------------------------------------
    if plan.projects:
        pdf.ln(4)
        pdf.section_header("Projects")
        for i, section in enumerate(plan.projects):
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.set_text_color(30, 30, 30)
            pdf.cell(0, 4.5, ascii_safe(section.entry.name),
                     new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
            for chosen in section.chosen:
                pdf.bullet(ascii_safe(chosen.text))
            if i < len(plan.projects) - 1:
                pdf.ln(2)

    slack = (PAGE_H - BOT_MARGIN) - pdf.get_y()
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    return pdf.pages_count, slack


def render_fitted(plan: Plan, path: Path) -> tuple[int, float, list[str]]:
    """Render, and drop the weakest optional bullets until it fits one page.

    The page budget is a real constraint on a resume, so it has to feed back
    into selection -- otherwise the tailorer happily picks eleven bullets and
    silently produces a two-page document that recruiters skim half of.
    """
    max_pages = int(plan.master.render.get("max_pages", 1))
    dropped: list[str] = []
    while True:
        pages, slack = render(plan, path)
        if pages <= max_pages:
            return pages, slack, dropped
        victim = plan.drop_weakest()
        if victim is None:
            # Every remaining bullet is protected by a min_bullets floor.
            return pages, slack, dropped
        dropped.append(victim.bullet.id)
