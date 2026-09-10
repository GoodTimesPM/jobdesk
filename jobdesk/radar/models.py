"""The normalized Job model.

Every source module converts whatever shape its API returns into a `Job`.
Nothing downstream (dedupe, scoring, rendering, Notion) knows or cares which
board a posting came from -- same engine-agnostic split the news bot uses
between `sources/*` and `report.py`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_TAGS = re.compile(r"<[^>]+>")


def clean_text(raw: str | None) -> str:
    """Strip HTML tags and collapse whitespace. JD bodies arrive as HTML."""
    if not raw:
        return ""
    txt = _TAGS.sub(" ", raw)
    txt = (
        txt.replace("&amp;", "&")
        .replace("&nbsp;", " ")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&rsquo;", "'")
    )
    return _WS.sub(" ", txt).strip()


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
