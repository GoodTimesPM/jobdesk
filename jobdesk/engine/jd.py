"""Reading a job description.

Input is whatever you can copy: a .txt file, a paste on stdin, or HTML
saved from a posting. Output is a `JobDescription` -- sectioned, weighted, and
reduced to canonical term ids.

The single most important thing this module does is split *required* from
*preferred*. A term under "Basic Qualifications" is a gate the resume has to
answer; the same term under "Nice to have" is worth one line at most. Treating
the JD as one undifferentiated bag of words is what produces a resume that
matches the fluff and misses the requirement.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .. import profile
from . import config
from .vocab import Vocabulary

_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")

# Heading tests run in this order. "Preferred Qualifications" contains
# "qualifications", so preferred must win before required is considered.
_PREFERRED = (
    "preferred", "nice to have", "nice-to-have", "bonus", "a plus", "pluses",
    "desired", "additional qualifications", "would be great", "icing on the cake",
)
_REQUIRED = (
    "what you'll need", "what you will need", "what you need", "requirements",
    "qualifications", "required", "who you are", "what we're looking for",
    "what we are looking for", "must have", "minimum", "basic qualifications",
    "skills and experience", "education and experience", "you have",
)
_RESPONSIBILITIES = (
    "what you'll do", "what you will do", "what you'll be doing",
    "responsibilities", "duties", "about the role", "the role", "the position",
    "day to day", "day-to-day", "essential functions", "job summary",
    "position summary", "in this role", "your impact", "what you'll own",
)
_IGNORE = (
    "about us", "about the company", "who we are", "benefits", "perks",
    "compensation", "pay range", "equal opportunity", "eeo", "why join",
    "our values", "diversity", "accommodation", "disclaimer", "how to apply",
    "our mission", "life at",
)

_SECTION_WEIGHT = {
    "required": config.WEIGHT_REQUIRED,
    "responsibilities": config.WEIGHT_RESPONSIBILITIES,
    "preferred": config.WEIGHT_PREFERRED,
    "other": config.WEIGHT_OTHER,
    "ignore": 0,
}

# Years-of-experience, read only from the binding half of the JD.
# `(?<!\d)` and the mandatory range separator both exist because Job Radar hit
# a live posting reading "2014 year over year" and scored it as a 14-year
# requirement. Same trap, so the same guard.
_YEARS = re.compile(
    r"(?<!\d)(\d{1,2})(?:\s*(?:-|to|–)\s*(\d{1,2}))?\s*\+?\s*"
    r"(?:years?|yrs?)\b(?![^.]{0,30}\bold\b)",
    re.IGNORECASE,
)

# Candidate technology names the vocabulary doesn't know yet. Capitalised or
# all-caps tokens are how product names look in a JD; the stoplist removes the
# English that happens to be capitalised too.
_CANDIDATE = re.compile(r"\b([A-Z][A-Za-z0-9+#\.]{2,}|[A-Z]{2,6})\b")
_STOP = {
    "The", "This", "That", "These", "Those", "You", "Your", "We", "Our", "Us",
    "They", "Their", "It", "Its", "He", "She", "As", "At", "By", "For", "From",
    "In", "Of", "On", "To", "With", "And", "But", "Or", "If", "Not", "All",
    "Any", "Are", "Have", "Has", "Will", "Can", "May", "Must", "Should",
    "Work", "Working", "Team", "Teams", "Role", "Job", "Position", "Company",
    "Experience", "Skills", "Ability", "Able", "Strong", "Excellent", "Good",
    "Knowledge", "Understanding", "Support", "Manage", "Managing", "Develop",
    "Build", "Ensure", "Provide", "Perform", "Assist", "Help", "Other",
    "Preferred", "Required", "Requirements", "Qualifications", "Education",
    "Bachelor", "Bachelors", "Degree", "Years", "Year", "Minimum", "Plus",
    "Responsibilities", "Duties", "Benefits", "Equal", "Opportunity",
    "Employer", "Candidates", "Candidate", "Applicants", "Please", "Note",
    "About", "What", "Who", "Why", "How", "When", "Where", "Full", "Time",
    "Remote", "Hybrid", "Onsite", "Office", "Monday", "Friday", "EEO", "US",
    "USA", "United", "States", "New", "One", "Two", "Three", "Both", "Each",
    "Every", "Some", "Most", "More", "Less", "Best", "Great", "High", "Level",
    "Business", "Data", "Information", "Systems", "System", "Technology",
    "Technical", "Analyst", "Analysis", "Management", "Project", "Projects",
    "Process", "Processes", "Customer", "Client", "Clients", "Service",
    "Services", "Quality", "Health", "Care", "Group", "Inc", "LLC",
}


@dataclass
class JobDescription:
    company: str
    title: str
    raw: str
    sections: dict[str, str] = field(default_factory=dict)
    # term id -> weight (the highest-weight section it appeared in)
    weights: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    required_terms: set[str] = field(default_factory=set)
    unmapped: list[tuple[str, int]] = field(default_factory=list)
    years_required: int | None = None
    fetched: str = ""

    @property
    def slug(self) -> str:
        base = f"{self.company}-{self.title}".lower()
        base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
        return base[:70] or "untitled"

    @property
    def digest(self) -> str:
        return hashlib.sha1(self.raw.encode("utf-8")).hexdigest()[:12]

    def weight_of(self, term_id: str) -> int:
        return self.weights.get(term_id, 0)

    def family(self) -> str:
        """Which of the user's target families this posting belongs to.

        Drives only the summary's opening and middle clause -- every opening
        in master.toml is true of the person, so the choice is about emphasis,
        not accuracy.

        The keyword map used to be six `if` statements here, which meant an
        accountant's profile still got classified against "help desk". It is
        `[[summary_family]]` in targeting.toml now; first match wins, and the
        last entry is the fallback.
        """
        families = profile.load("targeting.toml").get("summary_family", [])
        if not families:
            return "analysis"
        title = self.title.lower()
        for entry in families[:-1]:
            if any(k in title for k in entry.get("title_keywords", [])):
                return entry["name"]
        return families[-1]["name"]

    def domain(self, vocab: Vocabulary) -> str:
        """The strongest domain term, if the JD has an obvious industry."""
        best, best_n = "", 0
        for tid, n in self.counts.items():
            if vocab.kind(tid) == "domain" and n > best_n:
                best, best_n = tid, n
        # One passing mention is a coincidence; a real industry is repeated.
        return best if best_n >= 3 else ""

    def to_dict(self) -> dict:
        return {
            "company": self.company, "title": self.title, "fetched": self.fetched,
            "digest": self.digest, "weights": self.weights, "counts": self.counts,
            "required_terms": sorted(self.required_terms),
            "unmapped": self.unmapped, "years_required": self.years_required,
            "family": self.family(), "raw": self.raw,
        }


def clean(text: str) -> str:
    if "<" in text and ">" in text:
        text = _TAGS.sub("\n", text)
    text = (
        text.replace("&amp;", "&").replace("&nbsp;", " ").replace("&lt;", "<")
        .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
        .replace("&rsquo;", "'").replace("•", "\n- ").replace("\r", "")
    )
    text = _WS.sub(" ", text)
    return _BLANKS.sub("\n\n", text).strip()


def _classify(line: str) -> str | None:
    """Is this line a section heading, and which section does it open?"""
    stripped = line.strip().rstrip(":").strip()
    if not stripped or len(stripped) > 70:
        return None
    low = stripped.lower()
    # A heading is short and label-like -- a sentence with a period is prose
    # that happens to contain the word "requirements".
    if stripped.endswith(".") and len(stripped.split()) > 6:
        return None
    for name, needles in (
        ("preferred", _PREFERRED), ("required", _REQUIRED),
        ("responsibilities", _RESPONSIBILITIES), ("ignore", _IGNORE),
    ):
        if any(n in low for n in needles):
            return name
    return None


def split_sections(text: str) -> dict[str, str]:
    current = "other"
    buckets: dict[str, list[str]] = {}
    for line in text.splitlines():
        found = _classify(line)
        if found:
            current = found
            continue
        buckets.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in buckets.items() if "".join(v).strip()}


def required_years(sections: dict[str, str]) -> int | None:
    """The binding years requirement, or None.

    Only the required section counts, and the maximum within it -- a JD asking
    for 5 years overall and 1 year of Python is a 5-year job.
    """
    text = sections.get("required", "")
    if not text:
        return None
    found: list[int] = []
    for m in _YEARS.finditer(text):
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        if 0 < hi <= 20:
            found.append(hi)
    return max(found) if found else None


def unmapped_terms(sections: dict[str, str], vocab: Vocabulary,
                   limit: int = 12) -> list[tuple[str, int]]:
    """Capitalised tokens the vocabulary has no term for.

    This is how the vocabulary grows: the gap report surfaces what Richmond
    employers keep asking for that nobody has taught the engine to see yet.
    """
    text = "\n".join(sections.get(k, "") for k in ("required", "preferred",
                                                   "responsibilities"))
    if not text:
        return []
    known = set()
    for term in vocab.terms.values():
        known.update(a.lower() for a in term.aliases)
        known.add(term.label.lower())
    counts: dict[str, int] = {}
    for m in _CANDIDATE.finditer(text):
        token = m.group(1)
        if token in _STOP or token.lower() in known or len(token) < 3:
            continue
        if token.rstrip("s") in _STOP:
            continue
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(t, n) for t, n in ranked if n >= 2][:limit]


def parse(text: str, company: str, title: str, vocab: Vocabulary) -> JobDescription:
    body = clean(text)
    sections = split_sections(body)

    weights: dict[str, int] = {}
    counts: dict[str, int] = {}
    required_terms: set[str] = set()
    for name, chunk in sections.items():
        weight = _SECTION_WEIGHT.get(name, config.WEIGHT_OTHER)
        for tid, n in vocab.find(chunk).items():
            counts[tid] = counts.get(tid, 0) + n
            if weight > weights.get(tid, 0):
                weights[tid] = weight
            if name == "required":
                required_terms.add(tid)

    return JobDescription(
        company=company.strip(), title=title.strip(), raw=body, sections=sections,
        weights=weights, counts=counts, required_terms=required_terms,
        unmapped=unmapped_terms(sections, vocab),
        years_required=required_years(sections),
        fetched=date.today().isoformat(),
    )


def read_source(source: str) -> str:
    """A path, or '-' to read a paste from stdin."""
    if source == "-":
        import sys
        return sys.stdin.read()
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"no such JD file: {source}")
    return path.read_text(encoding="utf-8-sig", errors="replace")


def store(jd: JobDescription) -> Path:
    """Persist the JD to the corpus behind the gap report.

    The raw text is kept on purpose. Term weights are only as good as today's
    vocabulary, and when a term is added later every stored JD can be re-read
    -- which is impossible if only the extracted terms were saved and the
    posting has since come down.
    """
    path = config.JD_STORE / f"{jd.fetched}_{jd.slug}.json"
    path.write_text(json.dumps(jd.to_dict(), indent=2), encoding="utf-8")
    return path
