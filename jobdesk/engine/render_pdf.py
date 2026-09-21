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

One page is not negotiable, so the page budget is spent in a fixed order:
set the page tighter first, and only cut content when there is no tightness
left. `LADDER` is that order, and `render_fitted` walks it.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    "–": "-", "—": "-", "…": "...", " ": " ",
    "•": "-", "·": "-", "−": "-",
}


def ascii_safe(text: str) -> str:
    for bad, good in _ASCII.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


@dataclass(frozen=True)
class Layout:
    """How tightly the page is set. The default is the document as designed.

    Three dials rather than one, because a millimetre bought from each of them
    costs a different amount. `gap` is the air between sections and `lead` is
    the air between lines; a reader does not notice either one tightening, and
    between them they are most of the blank space on the page. `type` is the
    one that shows, so it moves last and has a floor -- a resume set small
    enough to squint at is a resume that gets skipped, which is the thing the
    page budget exists to prevent in the first place.

    `side` and `pad` are the margins. They stop at about 0.4 inch, which is
    where a home printer starts clipping.
    """
    type: float = 1.0
    lead: float = 1.0
    gap: float = 1.0
    side: float = MARGIN
    pad: float = BOT_MARGIN

    def pt(self, size: float) -> float:
        return size * self.type

    def lh(self, height: float) -> float:
        return height * self.lead

    def sp(self, space: float) -> float:
        return space * self.gap

    @property
    def squeezed(self) -> bool:
        return self != Layout()

    def describe(self) -> str:
        return (f"type {self.type:.0%}, leading {self.lead:.0%}, "
                f"spacing {self.gap:.0%}, margins {self.side:.0f}mm")


# Tried in order. Air goes first and in big steps; type size only starts
# moving once the page has none left to give, and stops at 90%, which puts
# the 9pt body text at 8.1pt.
LADDER: tuple[Layout, ...] = (
    Layout(),
    Layout(lead=0.97, gap=0.80, side=13.0, pad=11.0),
    Layout(lead=0.94, gap=0.62, side=12.0, pad=10.5),
    Layout(type=0.97, lead=0.92, gap=0.50, side=11.0, pad=10.0),
    Layout(type=0.94, lead=0.90, gap=0.42, side=10.5, pad=10.0),
    Layout(type=0.90, lead=0.88, gap=0.35, side=10.0, pad=10.0),
)


@dataclass
class Fit:
    """What `render_fitted` had to do to get the document onto one page."""
    pages: int
    slack: float
    dropped: list[str]
    layout: Layout
    broke_floors: bool = False

    @property
    def overflowed(self) -> bool:
        return self.pages > 1


class ResumePDF(FPDF):
    def __init__(self, layout: Layout) -> None:
        super().__init__("P", "mm", "Letter")
        self.layout = layout
        # Auto page break ON, unlike the original script: overflow has to
        # create a second page so the fitting loop can SEE that it overflowed.
        # With it off, extra content just fell off the bottom silently.
        self.set_auto_page_break(auto=True, margin=layout.pad)
        self.set_title("")
        self.set_author("")

    def section_header(self, title: str, sp: float = 2.5) -> None:
        lay = self.layout
        self.set_font("Helvetica", "B", lay.pt(10.5))
        self.set_text_color(30, 30, 30)
        self.cell(0, lay.lh(5.5), title.upper(), new_x="LMARGIN", new_y="NEXT")
        y = self.get_y()
        self.set_draw_color(50, 50, 50)
        self.set_line_width(0.35)
        self.line(self.l_margin, y, self.w - self.r_margin, y)
        self.ln(lay.sp(sp))

    def bullet(self, text: str, indent: float = 8, lh: float = 4.0) -> None:
        lay = self.layout
        self.set_x(self.get_x() + indent * lay.type)
        self.set_font("Helvetica", "", lay.pt(9))
        self.set_text_color(40, 40, 40)
        height = lay.lh(lh)
        self.cell(self.get_string_width("-  "), height, "-", new_x="END")
        self.multi_cell(self.w - self.r_margin - self.get_x() - 1, height,
                        " " + text)
        self.ln(lay.sp(0.6))


def render(plan: Plan, path: Path,
           layout: Layout | None = None) -> tuple[int, float]:
    """Write the PDF. Returns (page count, mm of slack on the last page)."""
    lay = layout or Layout()
    ident = plan.master.identity
    pdf = ResumePDF(lay)
    pdf.add_page()
    pdf.set_margins(lay.side, lay.pad, lay.side)
    pdf.set_y(lay.pad)
    pdf.set_x(lay.side)

    # -- name -------------------------------------------------------------
    pdf.set_font("Helvetica", "B", lay.pt(20))
    pdf.set_text_color(20, 20, 20)
    pdf.cell(0, lay.lh(9), ascii_safe(ident["name"]), align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(lay.sp(0.5))

    contact = "  |  ".join(
        v for v in (ident.get("location"), ident.get("email"),
                    ident.get("phone"), ident.get("linkedin"),
                    ident.get("github")) if v
    )
    pdf.set_font("Helvetica", "", lay.pt(9))
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, lay.lh(4.5), ascii_safe(contact), align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(lay.sp(5))

    # -- summary ----------------------------------------------------------
    # Empty unless the profile asks for one. See `_build_summary`: it is four
    # lines of page budget spent on sentences the reader has already decided
    # to skip, and those four lines buy two bullets.
    if plan.summary:
        pdf.section_header("Summary")
        pdf.set_font("Helvetica", "", lay.pt(9))
        pdf.set_text_color(40, 40, 40)
        pdf.multi_cell(0, lay.lh(4.2), ascii_safe(plan.summary))
        pdf.ln(lay.sp(4.5))

    # -- education --------------------------------------------------------
    pdf.section_header("Education")
    coursework = ", ".join(c.name for c in plan.coursework)
    for i, edu in enumerate(plan.master.education):
        pdf.set_font("Helvetica", "B", lay.pt(9.5))
        pdf.set_text_color(30, 30, 30)
        pdf.cell(0, lay.lh(4.5), ascii_safe(edu.degree),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "I", lay.pt(9))
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, lay.lh(4), ascii_safe(edu.school),
                 new_x="LMARGIN", new_y="NEXT")
        if i == 0 and coursework:
            pdf.ln(lay.sp(1))
            pdf.set_font("Helvetica", "", lay.pt(8.5))
            pdf.set_text_color(60, 60, 60)
            pdf.set_x(lay.side + 4 * lay.type)
            pdf.multi_cell(0, lay.lh(3.8),
                           ascii_safe(f"Relevant Coursework: {coursework}"))
        pdf.ln(lay.sp(2))
    pdf.ln(lay.sp(2))

    # -- skills -----------------------------------------------------------
    pdf.section_header("Skills")
    for category, items in plan.skills:
        pdf.set_font("Helvetica", "B", lay.pt(9))
        pdf.set_text_color(30, 30, 30)
        pdf.cell(35 * lay.type, lay.lh(4), ascii_safe(category))
        pdf.set_font("Helvetica", "", lay.pt(9))
        pdf.set_text_color(50, 50, 50)
        pdf.multi_cell(0, lay.lh(4), ascii_safe(", ".join(items)))
        pdf.ln(lay.sp(0.8))
    pdf.ln(lay.sp(3.5))

    # -- experience -------------------------------------------------------
    pdf.section_header("Experience")
    for i, section in enumerate(plan.experience):
        entry = section.entry
        pdf.set_font("Helvetica", "B", lay.pt(9.5))
        pdf.set_text_color(30, 30, 30)
        title = ascii_safe(entry.title)
        pdf.cell(pdf.get_string_width(title) + 2, lay.lh(4.5), title)
        pdf.set_font("Helvetica", "", lay.pt(8.5))
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, lay.lh(4.5), ascii_safe(entry.dates), align="R",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "I", lay.pt(9))
        pdf.set_text_color(80, 80, 80)
        org = f"{entry.company} - {entry.location}" if entry.location else entry.company
        pdf.cell(0, lay.lh(4), ascii_safe(org), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(lay.sp(1.5))
        for chosen in section.chosen:
            pdf.bullet(ascii_safe(chosen.text))
        if i < len(plan.experience) - 1:
            pdf.ln(lay.sp(3))

    # -- projects ---------------------------------------------------------
    if plan.projects:
        pdf.ln(lay.sp(4))
        pdf.section_header("Projects")
        for i, section in enumerate(plan.projects):
            pdf.set_font("Helvetica", "B", lay.pt(9.5))
            pdf.set_text_color(30, 30, 30)
            pdf.cell(0, lay.lh(4.5), ascii_safe(section.entry.name),
                     new_x="LMARGIN", new_y="NEXT")
            pdf.ln(lay.sp(1))
            for chosen in section.chosen:
                pdf.bullet(ascii_safe(chosen.text))
            if i < len(plan.projects) - 1:
                pdf.ln(lay.sp(2))

    slack = (PAGE_H - lay.pad) - pdf.get_y()
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    return pdf.pages_count, slack


def render_fitted(plan: Plan, path: Path) -> Fit:
    """Get the resume onto one page, and spend the cheapest thing first.

    A two-page resume is the one outcome this function may not return, so the
    order matters more than any single step in it:

    1. Set the page tighter, walking `LADDER`. Whitespace is free; a bullet is
       not. The previous version of this skipped straight to step 2 and threw
       away four bullets on a page that had 20mm of air left in it.
    2. Drop the weakest optional bullet, then start the ladder again from the
       top -- a bullet fewer should buy the roomy layout back rather than bank
       the tightness.
    3. Drop below the `min_bullets` floors, one bullet per section at a time.
       A floor is there so a job does not appear with nothing under it, and
       that is worth defending against a coverage score. It is not worth
       defending against a second page, so the floors give way last.

    Only if all three run out does a second page ship, and `Fit.overflowed`
    says so out loud rather than leaving it to be noticed in the PDF.
    """
    max_pages = int(plan.master.render.get("max_pages", 1))
    dropped: list[str] = []
    floors: list[int | None] = [None, 1, 0]
    broke = False

    while True:
        for layout in LADDER:
            pages, slack = render(plan, path, layout)
            if pages <= max_pages:
                return Fit(pages, slack, dropped, layout, broke)

        victim = None
        while victim is None and floors:
            victim = plan.drop_weakest(floor=floors[0])
            if victim is None:
                floors.pop(0)
            elif floors[0] is not None:
                broke = True
        if victim is None:
            # Nothing left to give: no air, no optional bullet, no bullet at
            # all. Ship it and say so.
            return Fit(pages, slack, dropped, LADDER[-1], broke)
        dropped.append(victim.bullet.id)
