"""Read a resume file and pull out what the wizard can fill in for you.

This is the first screen of the setup wizard, and it is deliberately dumb.
There is no model here and there will not be one: a regex that finds an email
address is right about the email address, and when it is wrong the user is
looking at the field and can see that it is wrong. A model that is confidently
wrong about a phone number in a form nobody re-reads is worse than a blank.

So the contract with the user is: this fills in what it is sure about, shows
you every field it filled, and asks you to confirm before anything is written.
Nothing here decides; it proposes.

Three formats, because those are the three a resume is ever in: .pdf via
PyMuPDF (already a dependency, the ATS simulator reads PDFs back), .docx via
python-docx, and .txt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Section headings, as people actually type them. Matched on a line of its own,
# case-insensitively, with optional punctuation, because "EXPERIENCE" and
# "Experience:" and "Work Experience" are the same heading.
_SECTIONS = {
    "summary": ("summary", "profile", "objective", "about"),
    "education": ("education", "academic background"),
    "skills": ("skills", "technical skills", "core competencies",
               "skills & tools", "technologies"),
    "experience": ("experience", "work experience", "professional experience",
                   "employment", "employment history", "work history"),
    "projects": ("projects", "personal projects", "portfolio",
                 "selected projects"),
    "certifications": ("certifications", "certificates", "licenses",
                       "certifications & licenses"),
}

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_LINKEDIN = re.compile(r"(?:linkedin\.com/in/)([\w-]+)", re.I)
_GITHUB = re.compile(r"(?:github\.com/)([\w-]+)", re.I)
# Ten digits with any of the usual separators, optionally +1 in front. Not
# anchored to a line, because a contact line runs everything together.
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
# "Richmond, VA" or "Richmond, Virginia". A city name can be two words.
_CITY_STATE = re.compile(
    r"\b([A-Z][a-z]+(?:[ -][A-Z][a-z]+)?),\s*([A-Z]{2}|[A-Z][a-z]+)\b")

_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}

_BULLET_CHARS = "•-*–—◦"

# A date range, in the forms a resume writes one: "February 2022 - April 2022",
# "Apr 2023 - Present", "04/2023 - 06/2024", "2019-2020". This is the only
# reliable marker of an experience entry's header line -- bullets never carry
# one, and every job does.
_MONTHS = ("jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec")
_DATE_RANGE = re.compile(
    r"(?:(?:" + _MONTHS + r")[a-z]*\.?\s+\d{4}|\d{1,2}/\d{4}|\b\d{4})"
    r"\s*(?:-|–|—|to)\s*"
    r"(?:(?:" + _MONTHS + r")[a-z]*\.?\s+\d{4}|\d{1,2}/\d{4}|\b\d{4}|present|current)",
    re.I)


class UnreadableResume(RuntimeError):
    """The file could not be turned into text, with the reason."""


@dataclass
class Parsed:
    """What the parser believes, and how sure it is.

    `filled` names every field this found, so the UI can mark those inputs as
    "we guessed this" rather than presenting a guess as a fact. Anything not
    in `filled` was left blank on purpose.
    """
    name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    state: str = ""
    linkedin: str = ""
    github: str = ""
    titles: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    sections: dict[str, str] = field(default_factory=dict)
    text: str = ""
    filled: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        data = {k: v for k, v in self.__dict__.items() if k != "text"}
        data["characters"] = len(self.text)
        return data


# ---------------------------------------------------------------------------
# Getting to text
# ---------------------------------------------------------------------------

def read_text(path: Path) -> str:
    """A resume file's plain text, whatever it was written in."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix == ".docx":
        return _read_docx(path)
    if suffix in (".txt", ".md", ".text"):
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".doc":
        raise UnreadableResume(
            ".doc is the old Word format and nothing can read it without Word. "
            "Open it and Save As .docx or .pdf, then drop that in.")
    raise UnreadableResume(
        f"{suffix or 'that file'} is not a resume format I can read. "
        "Use .pdf, .docx or .txt.")


def _read_pdf(path: Path) -> str:
    try:
        import fitz  # PyMuPDF, already required by the ATS simulator
    except ImportError as exc:
        raise UnreadableResume(
            "reading a PDF needs PyMuPDF: pip install -r requirements.txt"
        ) from exc
    try:
        with fitz.open(str(path)) as doc:
            text = "\n".join(page.get_text("text") for page in doc)
    except Exception as exc:
        raise UnreadableResume(f"that PDF would not open ({exc})") from exc
    if len(text.strip()) < 100:
        raise UnreadableResume(
            "that PDF has almost no text in it, which usually means it is a "
            "scan or an image. An ATS cannot read it either, which is worth "
            "knowing. Export a text PDF from the original document.")
    return text


def _read_docx(path: Path) -> str:
    try:
        import docx
    except ImportError as exc:
        raise UnreadableResume(
            "reading a .docx needs python-docx: pip install -r requirements.txt"
        ) from exc
    try:
        document = docx.Document(str(path))
    except Exception as exc:
        raise UnreadableResume(f"that .docx would not open ({exc})") from exc
    lines = [p.text for p in document.paragraphs]
    # Plenty of resumes lay the contact line out in a table, and python-docx
    # does not return table text among the paragraphs.
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                lines.append("  ".join(cells))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reading the text
# ---------------------------------------------------------------------------

def _heading_of(line: str) -> str | None:
    """The section a line names, if it is a heading and not prose."""
    bare = line.strip().strip(":_ ").strip(_BULLET_CHARS).strip().lower()
    if not bare or len(bare) > 40:
        return None
    for key, names in _SECTIONS.items():
        if bare in names:
            return key
    return None


def split_sections(text: str) -> dict[str, str]:
    """The resume's text, filed under the heading it appeared beneath.

    Anything before the first heading is the contact block, which is where
    every field below is found, so it is kept under "header".
    """
    out: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in text.splitlines():
        heading = _heading_of(line)
        if heading:
            current = heading
            out.setdefault(current, [])
            continue
        out.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in out.items() if "".join(v).strip()}


def _guess_name(header: str) -> str:
    """The first line that looks like a person and not a contact detail.

    A resume's name is almost always the first non-empty line. The exceptions
    are a header that opens with an address or a job title, so lines holding
    an @, a digit or a slash are skipped.
    """
    for line in header.splitlines():
        line = line.strip().strip("|•·").strip()
        if not line or len(line) > 60:
            continue
        if any(c in line for c in "@/\\") or any(c.isdigit() for c in line):
            continue
        words = line.split()
        if not 2 <= len(words) <= 4:
            continue
        if not all(w[0].isupper() for w in words if w and w[0].isalpha()):
            continue
        return line
    return ""


def _guess_titles(sections: dict[str, str]) -> list[str]:
    """Lines from the experience section that could be a job title.

    Candidates, not answers. The wizard shows these and the user clicks the
    ones that are real, which is the honest interface for a job that cannot be
    done reliably without guessing: a title line and the company line directly
    under it are structurally identical, and no amount of regex separates
    "Datacenter Technician" from "Lorien (Amazon Web Services)". Offering both
    and asking beats picking one and being wrong half the time.

    What narrows it to a short list is date adjacency. Every experience entry
    carries a date range, on the title line or within a line of it, and no
    bullet does. That one rule takes this from every line in the section down
    to two or three per job.

    The other half of the work is throwing out wrapped bullets. A bullet that
    runs onto a second line leaves a continuation with no bullet character on
    it, which is why "coordinating cross-team data and resource requirements."
    used to come back as a job title.
    """
    lines = [ln.strip() for ln in sections.get("experience", "").splitlines()]
    # Which lines are a date range, and which are the tail of a bullet.
    dated = [bool(_DATE_RANGE.search(ln)) for ln in lines]
    continuation = False
    is_bullet = []
    for line in lines:
        if not line:
            continuation = False
            is_bullet.append(False)
            continue
        if line[0] in _BULLET_CHARS or line.startswith(("* ", "•")):
            continuation = True
            is_bullet.append(True)
            continue
        # A line under a bullet that reads as prose is the rest of that bullet.
        is_bullet.append(continuation and (line.endswith((".", ",")) or line[0].islower()))
        if not is_bullet[-1]:
            continuation = False

    titles: list[str] = []
    seen: set[str] = set()
    for i, line in enumerate(lines):
        if not line or is_bullet[i] or len(line) > 90:
            continue
        near = dated[i] or dated[i - 1] if i else dated[i]
        near = near or (i + 1 < len(dated) and dated[i + 1])
        if not near:
            continue
        # "Datacenter Technician    February 2022 - April 2022, VA"
        head = _DATE_RANGE.split(line)[0]
        head = re.split(r"[,|@]|\s[-–—]\s", head)[0]
        head = re.sub(r"\(.*?\)", "", head).strip(" .*•")
        if not 1 <= len(head.split()) <= 6 or len(head) < 6:
            continue
        if any(c.isdigit() for c in head) or head.endswith("."):
            continue
        if head.lower() in seen:
            continue
        seen.add(head.lower())
        titles.append(head)
    return titles[:12]


def _guess_skills(sections: dict[str, str]) -> list[str]:
    """Comma- and pipe-separated items out of the skills section.

    Resumes write skills as "SQL, Python (Pandas, NumPy), Tableau", so the
    parenthetical has to come apart too or "Python (Pandas" ends up a skill.
    """
    raw = sections.get("skills", "")
    if not raw:
        return []
    raw = re.sub(r"^[^:]{0,40}:", "", raw, flags=re.M)   # drop "Data & Analytics:"
    raw = raw.replace("(", ", ").replace(")", ", ")
    items = re.split(r"[,;|•·\n]", raw)
    skills: list[str] = []
    seen: set[str] = set()
    for item in items:
        item = item.strip(" .*-–—")
        if not 2 <= len(item) <= 40 or item.lower() in seen:
            continue
        if item.endswith(":") or len(item.split()) > 4:
            continue
        seen.add(item.lower())
        skills.append(item)
    return skills[:60]


def parse(path: Path) -> Parsed:
    """Everything the wizard can pre-fill from one resume file."""
    text = read_text(path)
    sections = split_sections(text)
    header = sections.get("header", text[:1200])
    out = Parsed(text=text, sections=sections)

    if match := (_EMAIL.search(header) or _EMAIL.search(text)):
        out.email = match.group(0)
    if match := (_PHONE.search(header) or _PHONE.search(text)):
        out.phone = match.group(0).strip()
    if match := _LINKEDIN.search(text):
        out.linkedin = f"linkedin.com/in/{match.group(1)}"
    if match := _GITHUB.search(text):
        out.github = f"github.com/{match.group(1)}"
    if match := _CITY_STATE.search(header):
        city, state = match.group(1), match.group(2)
        out.city = city
        out.state = _STATES.get(state.lower(), state.upper()[:2])
    out.name = _guess_name(header)
    out.titles = _guess_titles(sections)
    out.skills = _guess_skills(sections)

    out.filled = [f for f in ("name", "email", "phone", "city", "state",
                              "linkedin", "github") if getattr(out, f)]
    if out.titles:
        out.filled.append("titles")
    if out.skills:
        out.filled.append("skills")
    return out
