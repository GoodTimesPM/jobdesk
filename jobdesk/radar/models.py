"""The normalized Job model.

Every source module converts whatever shape its API returns into a `Job`.
Nothing downstream (dedupe, scoring, rendering, Notion) knows or cares which
board a posting came from -- same engine-agnostic split the news bot uses
between `sources/*` and `report.py`.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_TAGS = re.compile(r"<[^>]+>")


def clean_text(raw: str | None) -> str:
    """Strip HTML tags and collapse whitespace. JD bodies arrive as HTML.

    Tags first, then entities: decoding first would turn a written `&lt;`
    into a `<` that the tag stripper then eats along with everything up to
    the next `>`.

    `html.unescape` rather than the seven hand-written replacements this used
    to do, because the eighth one mattered. A live Comcast posting reads
    "5&#43; years of experience" -- a numeric entity for a plus sign -- and
    with the plus still spelled out as five characters the years regex saw no
    years at all. The req asked for five and was scored as asking for none,
    which is worth +6 and a clean bill of health.
    """
    if not raw:
        return ""
    return _WS.sub(" ", html.unescape(_TAGS.sub(" ", raw))).strip()


def parse_date(value: Any) -> datetime | None:
    """Best-effort date parsing across the many formats these APIs return."""
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        # Workday and a few others use epoch milliseconds.
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# The label a shared job board prints above the employer that is actually
# hiring. Every Commonwealth of Virginia posting carries "Agency: Dept of Prof
# & Occup Reg" or similar in its header block, and that is the only place the
# real employer appears: the board's `company` is the Commonwealth and the URL
# is jobs.virginia.gov for all hundred-odd agencies.
# The labels that end an agency name, because the sitemap fetcher flattens a
# posting to a single line and there is no newline to stop at:
#
#   Title: Fair Housing Investigator State Role Title: Compliance/Safety
#   Officer III Hiring Range: $57,000 - $72,000 Pay Band: 4 Agency: Dept of
#   Prof & Occup Reg Location: DPOR Main Office Agency Website: ...
#
# Stopping at "the next capitalised word followed by a colon" reads that as
# "Dept of Prof & Occup" and drops the Reg, because "Reg Location:" fits the
# pattern too. So the terminators are named. An unfamiliar board whose labels
# are not in this list yields "" rather than half a name, which is the right
# way round: a wrong agency is worse than no agency.
_FIELDS = ("state role title", "role title", "hiring range", "pay band",
           "agency website", "recruitment type", "position number",
           "work location", "closing date", "opening date", "job type",
           "salary", "location", "title", "agency", "department", "division")
_SECTIONS = ("job duties", "minimum qualifications", "preferred qualifications",
             "additional considerations", "special instructions",
             "contact information", "about the agency")


def _alt(words):
    return "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))


_DIVISION_LABEL = re.compile(
    r"(?:^|[\s.;])(?:Hiring\s+)?(?:Agency|Department|Bureau|Division|Office)"
    r"\s*:\s*(?P<name>.{3,80}?)"
    r"(?=\s*(?:" + _alt(_FIELDS) + r")\s*:"      # the next labelled field
    r"|\s*(?:" + _alt(_SECTIONS) + r")\b"        # or the first prose heading
    r"|\s*[\r\n]|\s*$)",
    re.I)

# Labels that answer with a category rather than an employer. "Department:
# Engineering" is a team inside one company, and splitting the application cap
# by team would defeat the point of having one.
_NOT_A_DIVISION = re.compile(
    r"^(engineering|sales|marketing|finance|operations|hr|human resources|"
    r"it|information technology|legal|product|design|support|corporate|"
    r"various|n/?a|see below|multiple|other)$", re.I)


# An employer's name starts with a capital and is a handful of words. Prose
# does neither, and prose is what turns up when a body happens to contain the
# word "agency" before a colon -- "...the agency: irrelevant here. We are
# hiring." parses, on the flattened line, into a perfectly well-formed match.
_LOOKS_LIKE_A_NAME = re.compile(
    r"^[A-Z][A-Za-z0-9&.,'()/-]*(?: [A-Za-z0-9&.,'()/-]+){0,7}$")


def division_in(text: str) -> str:
    """The employer named inside a shared board's posting, or "".

    Only the header block is read. These boards print it at the top -- Title,
    State Role Title, Hiring Range, Pay Band, Agency, Location -- and further
    down the same body an "Agency:" turns up inside a paragraph about some
    other agency's programme, which would name the wrong employer.
    """
    head = (text or "")[:1500]
    for match in _DIVISION_LABEL.finditer(head):
        name = _WS.sub(" ", match.group("name")).strip(" .,;:-")
        if len(name) < 3 or _NOT_A_DIVISION.match(name):
            continue
        if not _LOOKS_LIKE_A_NAME.match(name):
            continue
        return name
    return ""


@dataclass
class Job:
    """One posting, normalized."""

    title: str
    company: str
    url: str
    source: str                      # which source module produced it
    location: str = ""
    description: str = ""
    posted_at: datetime | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    remote: bool = False             # source explicitly says remote
    department: str = ""
    external_id: str = ""            # the board's own id, when it has one

    # Who actually hires, when `company` is the board and not the employer.
    # "Commonwealth of Virginia" is one sitemap and about a hundred agencies:
    # the Department of Accounts and the Dept of Prof & Occup Reg share a job
    # board, a domain, and nothing else -- not an HR office, not a hiring
    # manager, not a building. USAJOBS and any university system are the same
    # shape. Left empty when the company really is the employer, and every
    # reader falls back to `company` then.
    #
    # This is the staffing-agency mistake wearing a different hat. There the
    # risk was crediting eight agencies with one requisition; here it is
    # charging one agency for another agency's application.
    division: str = ""

    # The description is a snippet the source cut short, not the posting.
    # Adzuna returns exactly 500 characters and an ellipsis, every time. The
    # two filters that do the most work -- the years gate and the stack match
    # -- read the body, so a posting whose body was never fully sent cannot
    # be scored the same way as one that was. Scoring reads this; see
    # `score.PARTIAL_CEILING`.
    partial: bool = False

    # --- filled in by dedupe.collapse() ------------------------------------
    # The OTHER sources carrying this same req. Set during collapse, read by
    # scoring as a competition proxy: a posting only the company's own ATS
    # carries is one you are early to, and one syndicated across five
    # aggregators is one you are late to. See `_syndication_points`.
    also_on: list[str] = field(default_factory=list)

    # --- filled in by the scoring pass -------------------------------------
    score: int = 0
    tier: str = ""                   # A / B / C / D / F
    job_family: str = ""             # coarse function category, see score.job_family
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    first_seen: datetime | None = None

    def __post_init__(self) -> None:
        self.title = _WS.sub(" ", (self.title or "").strip())
        self.company = _WS.sub(" ", (self.company or "").strip())
        self.division = _WS.sub(" ", (self.division or "").strip())
        self.location = _WS.sub(" ", (self.location or "").strip())
        self.description = clean_text(self.description)

    # -- identity ----------------------------------------------------------
    @property
    def dedupe_key(self) -> str:
        """Company + title, normalized to alphanumerics.

        Deliberately NOT url-based: the same req shows up on a company's own
        ATS, on an aggregator, and via three staffing agencies, each with a
        different URL. Collapsing on company+title is what makes "apply
        direct instead of through the agency repost" possible.
        """
        base = f"{self.company}|{self.title}"
        return _NON_ALNUM.sub("", base.lower())

    @property
    def uid(self) -> str:
        return hashlib.sha1(self.dedupe_key.encode("utf-8")).hexdigest()[:16]

    @property
    def age_days(self) -> float | None:
        if not self.posted_at:
            return None
        return (datetime.now(timezone.utc) - self.posted_at).total_seconds() / 86400

    @property
    def haystack(self) -> str:
        """Everything a matcher should look at, lowercased once."""
        return f"{self.title} {self.location} {self.department} {self.description}".lower()

    @property
    def salary_text(self) -> str:
        if self.salary_min and self.salary_max:
            return f"${self.salary_min:,.0f}-${self.salary_max:,.0f}"
        if self.salary_min:
            return f"${self.salary_min:,.0f}+"
        if self.salary_max:
            return f"up to ${self.salary_max:,.0f}"
        return ""

    def to_dict(self) -> dict:
        d = asdict(self)
        for key in ("posted_at", "first_seen"):
            d[key] = d[key].isoformat() if d[key] else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        d = dict(d)
        d["posted_at"] = parse_date(d.get("posted_at"))
        d["first_seen"] = parse_date(d.get("first_seen"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})
