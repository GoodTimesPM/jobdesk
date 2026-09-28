"""Fit scoring and triage.

Every posting gets 0-100 and a letter tier, with the reasons written down so
a wrong call can be traced to a rule in `profile.py`. Rules only, no LLM.
"""

from __future__ import annotations

import functools
import re

from . import profile
from .models import Job, division_in

# "5+ years", "5-7 years", "minimum of 5 years", "at least five years".
# The (?<!\d) and the required range separator stop a calendar year from
# reading as a requirement: "2014 year over year" once scored as 14 years.
# The top of a range is kept, because "3-5 years" is a band and both ends
# say something.
_YEARS_PATTERNS = [
    re.compile(r"(?<!\d)(\d{1,2})\s*\+?\s*(?:(?:-|to|–|—)\s*(\d{1,2})\s*)?\+?\s*years?\b", re.I),
    re.compile(r"minimum(?:\s+of)?\s+(?<!\d)(\d{1,2})\s*years?\b", re.I),
    re.compile(r"at least\s+(?<!\d)(\d{1,2})\s*years?\b", re.I),
]

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_WORD_YEARS = re.compile(
    r"\b(" + "|".join(_WORD_NUMBERS) + r")\s*(?:\+)?\s*years", re.I)

# Salary written in the JD body. Most dollar amounts in a posting are not pay
# ("$200B in spend", "tuition up to $2500"), so an amount has to sit near a
# pay cue or clear a plausibility band on its own. The two ends of a range
# may have markup, entities or "/yr" between them (Greenhouse, Workday). A
# letter glued to the $ (CI$60,000) is another currency; US$ is the one
# exception.
_USD = r"(?<![A-Za-z])(?:US)?\$"
_AMOUNT = _USD + r"\s?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\s*(k\b)?"
_DASHY = r"(?:-|–|—|&[mn]dash;|\bto\b)"
_FILLER = (r"(?:\s|<[^>]{0,120}>|&nbsp;|&amp;|USD|/\s*(?:yr|year|hr|hour)"
           r"|per\s+(?:year|hour|annum)|annually|hourly)*")
_SALARY_RANGE = re.compile(_AMOUNT + _FILLER + _DASHY + _FILLER + _AMOUNT, re.I)
_SINGLE = re.compile(_AMOUNT, re.I)
_HOURLY = re.compile(
    _USD + r"\s?(\d{1,3}(?:\.\d{1,2})?)\s*(?:/|\s+per\s+|\s+an\s+)\s*(?:hr|hour)",
    re.I)

# Where a real band is usually written. A hit opens a 300-character window
# that the range regex gets first refusal on, which is how a band buried in
# benefits copy is read as pay while "$200B in annualized spend" three
# paragraphs up is not.
_PAY_CUE = re.compile(
    r"(?i)\b(?:salary|compensation|pay\s*[-\s]?range|pay\s+band|pay\s+rate"
    r"|base\s+pay|hourly\s+rate|wage|remuneration|hiring\s+range"
    r"|class=\"pay-range\")")

# An hourly rate and an annual figure cannot be told apart by size alone once
# "$95k" is allowed, so the bands do it. Nothing below the federal minimum
# wage is a real rate, which is what rejects Workday's "$1.00 - $1.00"
# placeholder -- a string that appears verbatim in live postings.
_HOUR_BAND = (7.0, 300.0)
_YEAR_BAND = (15_000.0, 900_000.0)
_HOURS_A_YEAR = 2080

# "$200B in annualized spend" and "$250M+ earned through our platform" both
# hand a bare 200 or 250 to anything reading digits, and both land inside the
# hourly band as a $416,000 and a $520,000 job. The suffix is the only thing
# that says otherwise, so an amount wearing one is thrown out.
_MAGNITUDE = re.compile(r"\s*(?:[bm]\b|bn\b|billion|million|trillion)", re.I)


# Most large employers split the JD into "Basic Qualifications" (the real
# gate) and "Preferred Qualifications" (the wish list). Only the first half
# is binding, so years mentioned after this marker are ignored.
_PREFERRED_MARKER = re.compile(
    r"(preferred qualification|preferred experience|nice to have|"
    r"bonus points|desired qualification|plus(?:es)?:)", re.I)


# Requirement words strong enough to count below the "preferred" marker. A
# structured footer can put the only years line there ("Relevant Work
# Experience 5-7 Years"). Tighter than _REQUIREMENT_CUE, because the
# preferred half talks about experience by nature.
_TAIL_REQUIREMENT = re.compile(
    r"(?i)(required|requirement|minimum|must have|at least|"
    r"relevant work experience|years of experience)")

# How far either side of a years figure the tail cue may sit.
_TAIL_WINDOW = 60


def _binding_text(job: Job) -> str:
    """The part of the JD that states real requirements."""
    text = job.description or job.title
    if not text:
        return ""
    m = _PREFERRED_MARKER.search(text)
    return text[:m.start()] if m and m.start() > 200 else text


def _preferred_tail(job: Job) -> str:
    """Whatever `_binding_text` cut off, or an empty string."""
    text = job.description or job.title
    if not text:
        return ""
    m = _PREFERRED_MARKER.search(text)
    return text[m.start():] if m and m.start() > 200 else ""


def _bands_in(text: str) -> list[tuple[int, int]]:
    """Every years requirement in a stretch of text, as (floor, ceiling).

    "3-5 years" is (3, 5). A bare "5+ years" is (5, 5): the plus is open-ended
    and inventing a ceiling for it would be worse than having none.
    """
    bands: list[tuple[int, int]] = []
    for pat in _YEARS_PATTERNS:
        for m in pat.finditer(text):
            try:
                low = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if not 0 < low <= 20:
                continue
            high = low
            if m.lastindex and m.lastindex >= 2 and m.group(2):
                try:
                    top = int(m.group(2))
                except (TypeError, ValueError):
                    top = low
                if low <= top <= 20:
                    high = top
            bands.append((low, high))
    for m in _WORD_YEARS.finditer(text):
        n = _WORD_NUMBERS[m.group(1).lower()]
        bands.append((n, n))
    return bands


def _tail_bands(job: Job) -> list[tuple[int, int]]:
    """Years requirements below the preferred marker that still bind."""
    tail = _preferred_tail(job)
    if not tail:
        return []
    kept: list[tuple[int, int]] = []
    for pat in _YEARS_PATTERNS:
        for m in pat.finditer(tail):
            window = tail[max(0, m.start() - _TAIL_WINDOW): m.end() + _TAIL_WINDOW]
            if not _TAIL_REQUIREMENT.search(window):
                continue
            kept.extend(_bands_in(m.group(0)) or [])
    return kept


def required_years(job: Job) -> int | None:
    """The years-of-experience gate the posting actually imposes.

    Reads only the binding half of the JD (not "Preferred") and takes the
    maximum there, so an earlier "1+ years of SQL" does not lower a 5-year gate.
    A range counts at its floor; `required_band` keeps the ceiling.
    """
    band = required_band(job)
    return band[0] if band else None


def required_band(job: Job) -> tuple[int, int] | None:
    """The years gate as (floor, ceiling), or None if the posting never said.

    The floor is the highest binding floor. The ceiling is the highest band top,
    which may come from a different band.
    """
    bands = _bands_in(_binding_text(job)) + _tail_bands(job)
    if not bands:
        return None
    return max(b[0] for b in bands), max(b[1] for b in bands)


# Words that turn a years figure into an actual gate. "5+ years of experience
# required" is a requirement; "25 years of serving Virginia families" is
# company boilerplate that happens to contain a number and the word "years".
_REQUIREMENT_CUE = re.compile(
    r"(experience|require|required|minimum|must have|at least|"
    r"qualification|background in|track record|proficien)", re.I)

_GATE_WINDOW = 90


def _has_any(text: str, phrases: list[str]) -> str | None:
    for phrase in phrases:
        if phrase in text:
            return phrase
    return None


def equivalency_clause(job: Job) -> str | None:
    """The 'or equivalent combination of education and experience' escape, if present."""
    return _has_any(_binding_text(job).lower(), profile.EQUIVALENCY_PHRASES)


def no_experience_signal(job: Job) -> str | None:
    """An explicit 'no prior experience needed' / 'we will train' signal."""
    return _has_any(job.haystack, profile.NO_EXPERIENCE_PHRASES)


def blocking_years(job: Job) -> int | None:
    """The years figure that hard-blocks a posting, as opposed to penalising it.

    Stricter than `required_years`. The figure needs a requirement cue within
    `_GATE_WINDOW` characters ("celebrating 15 years in Richmond" is not a
    gate), and "or equivalent experience" or "we will train" returns None, so
    the posting is demoted by `_experience_points` instead of removed.
    """
    text = _binding_text(job)
    if not text:
        return None

    gated: list[int] = []
    for pat in _YEARS_PATTERNS:
        for m in pat.finditer(text):
            window = text[max(0, m.start() - _GATE_WINDOW): m.end() + _GATE_WINDOW]
            if not _REQUIREMENT_CUE.search(window):
                continue
            # The FLOOR of the band, not its top. "5-8 years" blocks at five,
            # because five is what the posting says gets you considered.
            gated.extend(b[0] for b in _bands_in(m.group(0)))
    for low, _high in _tail_bands(job):
        gated.append(low)
    if not gated:
        return None

    years = max(gated)
    # The escape clauses only disarm a requirement that is plausibly
    # negotiable. Past EQUIVALENCY_CEILING the phrase is boilerplate rather
    # than an opening, and letting it through would undo the filter.
    if years <= profile.EQUIVALENCY_CEILING and \
            (equivalency_clause(job) or no_experience_signal(job)):
        return None
    return years


def _annualise(raw: str, kilo: str | None) -> float | None:
    """One written amount as an annual figure, or None if it is not pay.

    "$95k" is 95,000. "$18.50" is a rate, times 2080 hours. "$72,000" is
    already annual. Anything landing between the two bands -- a $2,500 tuition
    benefit, a $1.00 placeholder -- is not a wage and comes back None.
    """
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    if kilo:
        value *= 1000
    if _YEAR_BAND[0] <= value <= _YEAR_BAND[1]:
        return value
    if _HOUR_BAND[0] <= value <= _HOUR_BAND[1] and not kilo:
        return value * _HOURS_A_YEAR
    return None


def _range_in(text: str) -> tuple[float, float] | None:
    """The first plausible pay range in a stretch of text."""
    for m in _SALARY_RANGE.finditer(text):
        if _MAGNITUDE.match(text, m.end()):
            continue
        lo = _annualise(m.group(1), m.group(2))
        hi = _annualise(m.group(3), m.group(4))
        if lo and hi and lo <= hi <= lo * 6:
            # The ceiling matters: "$18/hr to $95,000" is two different things
            # quoted side by side, and a real band never spans six times.
            return lo, hi
    return None


def parse_salary(job: Job) -> tuple[float | None, float | None]:
    """A salary range from the JD body when the API gave none, as (low, high).

    Passes, widening: a range near a pay cue, any range that clears the bands,
    a lone hourly rate, then one figure near a cue as a floor (high is None).
    """
    if job.salary_min or job.salary_max:
        return job.salary_min, job.salary_max
    text = job.description
    if not text:
        return None, None

    for cue in _PAY_CUE.finditer(text):
        found = _range_in(text[cue.start():cue.start() + 300])
        if found:
            return found

    found = _range_in(text)
    if found:
        return found

    m = _HOURLY.search(text)
    if m:
        rate = float(m.group(1))
        if _HOUR_BAND[0] <= rate <= _HOUR_BAND[1]:
            annual = rate * _HOURS_A_YEAR
            return annual, annual

    # Last: one figure sitting on a cue, reported as a floor and not a band.
    # "Compensation & Benefits $40,000, with an increase to $42,000 after the
    # probationary period" is a starting salary and a raise schedule, and
    # reading it as a $40k-$42k range would be inventing a ceiling the
    # employer never wrote. A floor is the most the text supports.
    for cue in _PAY_CUE.finditer(text):
        window = text[cue.start():cue.start() + 200]
        for hit in _SINGLE.finditer(window):
            if _MAGNITUDE.match(window, hit.end()):
                continue
            value = _annualise(hit.group(1), hit.group(2))
            if value:
                return value, None
    return None, None


# Numeric early-career levels: "Analyst 1", "Data Analyst I". II/2 and up
# are not entry. Digits are guarded on both sides so a req number ending in
# 1 ("Lead Budget Analyst 00151") is not read as level 1.
_ENTRY_LEVEL_NUM = re.compile(r"(?<![a-z0-9])(?:i|1)(?![a-z0-9])", re.I)

# Senior ranks that contain a junior word. "Associate Director" and "Senior
# Associate" are not early-career; reading them that way once scored an
# Associate Director at 100.
_JUNIOR_WORD = r"associate|assistant|asst\.?|deputy|junior|jr\.?"
_SENIOR_NOUN = (r"director|manager|vice\s+president|vp|president|principal|"
                r"partner|chief|head|dean|counsel|controller|supervisor|"
                r"architect|officer|administrator")
_SENIOR_WORD = (r"senior|sr\.?|lead|principal|staff|executive|managing|"
                r"global|head")
_COMPOUND_RANK = re.compile(
    r"(?<![a-z])(?:(?:" + _JUNIOR_WORD + r")\s+(?:" + _SENIOR_NOUN + r")"
    r"|(?:" + _SENIOR_WORD + r")\s+(?:" + _JUNIOR_WORD + r"))(?![a-z])", re.I)


def entry_level_marker(title: str) -> str | None:
    """The early-career signal in the title, if any.

    Associate / Junior / Entry-Level / Graduate / a trailing level 1. It also
    disarms the seniority block on combined-level postings. A junior word on a
    senior rank ("Associate Director") does not count.
    """
    t = f" {flatten_title(title)} "
    for mark in profile.ENTRY_LEVEL_MARKERS:
        for m in _word(mark).finditer(t):
            # Only this occurrence has to be clean. A title can name a rank
            # and a rung ("Associate Director / Associate Analyst") and the
            # second one still counts.
            if not _compound_at(t, m.start(), m.end()):
                return mark
    if _ENTRY_LEVEL_NUM.search(title):
        return "level 1"
    return None


def _compound_at(text: str, start: int, end: int) -> bool:
    """Is the level word at [start:end] half of a seniority rank?"""
    return any(m.start() <= start and m.end() >= end
               for m in _COMPOUND_RANK.finditer(text))


def seniority_block(title: str) -> str | None:
    """The disqualifying word in the title, if there is one.

    Word-bounded, so "Manager" does not fire on "Management". A hit is a hard
    block: a "Lead Software Engineer" once reached 75 on location, freshness
    and pay alone when the title only lost its points.

    The exception is a combined-level slash list like "Associate Data Engineer
    / Data Engineer II / Senior Data Engineer", where one slash segment is an
    entry-level role with no senior word of its own.
    """
    t = f" {title.lower()} "
    for bad in profile.TITLE_DISQUALIFIERS:
        if _word(bad.strip()).search(t):
            if _junior_rung(title):
                return None
            return bad.strip()
    return None


def _junior_rung(title: str) -> str | None:
    """A slash-separated segment of the title that is an entry-level role.

    The test for a combined-level posting. One segment has to stand on its
    own as something you could be hired as: an early-career marker, and no
    disqualifying rank anywhere in the same segment.
    """
    if "/" not in title:
        return None
    for segment in title.split("/"):
        segment = segment.strip()
        if not segment or not entry_level_marker(segment):
            continue
        seg = f" {segment.lower()} "
        if any(_word(bad.strip()).search(seg)
               for bad in profile.TITLE_DISQUALIFIERS):
            continue
        return segment
    return None


# Title separators. "Desktop/End User Support Tech" contains "desktop support"
# to a human and to nobody else -- flattening these to spaces is what lets a
# token match survive the punctuation employers actually use in titles.
_TITLE_SEPARATORS = re.compile(r"[/&,\-–—()|:]+")
_WS_RUN = re.compile(r"\s+")


def flatten_title(title: str) -> str:
    """Lowercased title with separators flattened to single spaces."""
    return _WS_RUN.sub(" ", _TITLE_SEPARATORS.sub(" ", title.lower())).strip()


def _function_match(title: str) -> tuple[int, str]:
    """(points, label) from the function families, when the tier lists miss.

    Catches wording the lists never had ("analysis" for "analyst"). Clerical
    and off-field markers are checked first, so a stray token cannot carry a
    wrong title on target.
    """
    t = f" {flatten_title(title)} "

    for bad in profile.CLERICAL_MARKERS:
        if bad in t:
            return 0, f"clerical title ({bad})"
    for bad in profile.OFF_FIELD_MARKERS:
        if _word(bad).search(t):
            return 0, f"off-field title ({bad})"

    for points, label, tokens in profile.FUNCTION_FAMILIES:
        for token in tokens:
            if _word(token).search(t):
                # A domain word beside the function word sharpens the read:
                # "Data Analyst" beats a bare "Analyst".
                # `d not in token` rather than `d != token`: "data engineer"
                # already carries its domain word, and pairing it with the
                # "data" modifier reads as "data data engineer".
                domain = next(
                    (d for d in profile.DOMAIN_MODIFIERS
                     if d not in token and token not in d and
                     _word(d).search(t)),
                    None)
                if domain:
                    return points + 4, f"{label} ({domain} {token})"
                return points, f"{label} ({token})"
    return 0, ""


# --------------------------------------------------------------------------
# Synonyms: the same work under a different word ("Help Desk", "Service
# Desk"). Declared once in the profile and read here and by the search-query
# builders. A title synonym scores one notch below the term it stands for, so
# an exact hit wins. A skill synonym earns full weight: "Power BI" and
# "PowerBI" are one skill.
# --------------------------------------------------------------------------

def _tier_points(term: str) -> int:
    """What an exact hit on this term would have been worth."""
    if term in profile.TIER_1_TITLES:
        return 35
    if term in profile.TIER_2_TITLES:
        return 24
    if term in profile.TIER_3_TITLES:
        return 14
    if term in profile.ADJACENT_TITLES:
        return 12
    return 0


@functools.lru_cache(maxsize=4096)
def _word(term: str) -> re.Pattern:
    """`term` between two non-letters, compiled once per term."""
    return re.compile(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])")


@functools.lru_cache(maxsize=4096)
def _any_said(phrases: tuple[str, ...]) -> re.Pattern:
    """Any of `phrases` as a whole word, in one pass over the text."""
    alts = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
    return re.compile(r"(?<![a-z0-9])(?:" + alts + r")(?![a-z0-9])")


def _said(text: str, phrase: str) -> bool:
    """Does `text` use `phrase` as a whole word?

    Word-bounded because a long synonym list will collide with substrings:
    "elt" is inside "delta". This is what makes DAX, GCP and ELT safe to list.
    """
    # The substring test is a C loop and rules out nearly every phrase.
    return phrase in text and _any_said((phrase,)).search(text) is not None


def _synonym_title(flat: str) -> tuple[int, str]:
    """(points, label) for a title that names a target role by another word."""
    best, why = 0, ""
    for term, alternates in profile.synonyms().items():
        worth = _tier_points(term)
        if worth <= 0:
            continue                    # a skill synonym, not a title one
        points = max(0, worth - profile.SYNONYM_DISCOUNT)
        if points <= best:
            continue
        for alt in alternates:
            if _said(flat, alt):
                best = points
                why = f"reads as {term} ({alt})"
                break
    return best, why


def _says(hay: str, skill: str, alternates: list[str]) -> str | None:
    """The wording this posting used for `skill`, if it used one at all."""
    if skill in hay:
        return skill
    # Substring first: a phrase the text never contains cannot be a word in
    # it. List order is kept, because the first alternate found is the label.
    for alt in alternates:
        if _said(hay, alt):
            return alt
    return None


def _title_tier(title: str) -> tuple[int, str]:
    """(points, label) for the posting title.

    The better of the tier lists (hand-ranked) and the function families (an
    open fallback) wins, so the fallback can only add. Non-zero means on
    target: `score_job` caps a posting whose title scores 0.
    """
    # Flattened here too, not just in the function fallback: "Help-Desk
    # Analyst" does not contain the TIER_1 string "help desk" until the
    # hyphen becomes a space.
    t = flatten_title(title)
    listed, listed_why = 0, ""

    for good in profile.TIER_1_TITLES:
        if good in t:
            listed, listed_why = 35, f"tier-1 title ({good})"
            break
    else:
        for good in profile.TIER_2_TITLES:
            if good in t:
                listed, listed_why = 24, f"tier-2 title ({good})"
                break
        else:
            for good in profile.TIER_3_TITLES:
                if good in t:
                    listed, listed_why = 14, f"tier-3 title ({good})"
                    break
            else:
                # A bare family noun with no qualifier still counts ("Accountant",
                # "Nurse"). 15 rather than 12: at 12 a remote generic role landed one point
                # under the C line. The nouns come from the profile, and an empty list
                # skips the bonus.
                family = next((f for f in profile.FAMILY_TITLES if f in t), "")
                if family:
                    listed, listed_why = 15, f"generic {family} title"
                else:
                    for good in profile.ADJACENT_TITLES:
                        if good in t:
                            listed, listed_why = 12, f"analyst-adjacent ({good.strip()})"
                            break

    if not listed:
        listed, listed_why = _synonym_title(t)

    fn_pts, fn_why = _function_match(title)

    # A clerical or off-field verdict is a suppression, not a zero score: it
    # must beat a tier-list hit, or "Real Estate Data Entry Operator" rides
    # in on the word "data" the same way it would have before.
    if fn_why.startswith(("clerical", "off-field")):
        return 0, fn_why

    if fn_pts > listed:
        return fn_pts, fn_why
    return listed, (listed_why or "title not on the target list")


def job_family(title: str) -> str:
    """Coarse function category, like hiring.cafe's `jobCategory`.

    Not used in scoring. It gives the saved snapshots something to group by,
    and it cannot be backfilled once a posting comes down.
    """
    t = f" {flatten_title(title)} "
    if _has_any(t, profile.CLERICAL_MARKERS):
        return "clerical"
    for bad in profile.OFF_FIELD_MARKERS:
        if _word(bad).search(t):
            return "other-field"
    for _points, label, tokens in profile.FUNCTION_FAMILIES:
        for token in tokens:
            if _word(token).search(t):
                return label.replace(" function", "")
    return "unclassified"


def non_us_location(location: str) -> str | None:
    """The foreign country/city named in the location, if any."""
    loc = f" {location.lower()} "
    for marker in profile.NON_US_MARKERS:
        if _word(marker).search(loc):
            return marker
    return None


def _geo_points(job: Job) -> tuple[int, list[str], list[str]]:
    points, reasons, flags = 0, [], []
    hay = f"{job.location} {job.title}".lower()
    body = job.haystack

    is_local = any(term in hay for term in profile.LOCAL_TERMS)
    is_remote = job.remote or any(term in hay for term in profile.REMOTE_TERMS)
    is_hybrid = any(term in hay for term in profile.HYBRID_TERMS) or \
        any(term in body[:1500] for term in profile.HYBRID_TERMS)

    # A foreign location beats every other geo signal, including a "remote"
    # tag -- "Remote, Philippines" is remote, just not for you.
    foreign = non_us_location(job.location)
    if foreign and not is_local:
        flags.append("remote-but-not-US")
        return -100, [f"located outside the US ({foreign})"], flags

    wrong_region = any(term in body for term in profile.REMOTE_EXCLUSIONS)

    if wrong_region:
        # Not a penalty -- a blocker. "Remote (Canada)" is not a job the user
        # can take, and letting it score into C-tier wastes the one thing the
        # triage engine exists to protect: reading time.
        flags.append("remote-but-not-US")
        return -100, ["remote, but restricted to a region you can't work in"], flags

    if is_local:
        points += 25
        reasons.append(f"{profile.HOME_METRO} metro")
        if is_hybrid:
            flags.append("hybrid")
    elif is_remote:
        points += 22
        reasons.append("remote")
        if is_hybrid:
            points -= 8
            flags.append("hybrid")
            reasons.append("listed remote but mentions hybrid/onsite")
    else:
        # Onsite (or hybrid) and outside the ~1-hour commute ring around 23225.
        # A hard block, not a penalty: you are not relocating, so a role you'd
        # have to move for is never worth surfacing no matter how well it scores
        # otherwise. Mirrors the foreign / wrong-region blocks above; anything
        # genuinely remote already matched the branch just above.
        in_state = any(term in hay for term in profile.STATE_TERMS)
        # "elsewhere in Virginia" was written here. The reason a job was
        # dropped is shown to the user, so it has to say the user's state.
        # HOME_METRO is already "Richmond, VA" or "Denver, CO", and the half
        # after the comma is the only new thing needed.
        where = str(profile.HOME_METRO).rpartition(",")[2].strip()
        detail = (f"elsewhere in {where}" if in_state and where
                  else "elsewhere in your state" if in_state
                  else "outside commute range")
        flags.append("out-of-area")
        return -100, [f"onsite, {detail}, not remote (no relocation planned)"], flags
    return points, reasons, flags


def _stack_points(job: Job) -> tuple[int, list[str], list[str]]:
    hay = job.haystack
    hits: list[str] = []
    raw = 0
    alt = profile.synonyms()

    # Full weight on a synonym hit, unlike the title side. A posting asking
    # for DAX is asking for Power BI, and there is no judgment call in that.
    for table in (profile.CORE_SKILLS, profile.SUPPORTING_SKILLS):
        for skill, weight in table.items():
            said = _says(hay, skill, alt.get(skill, []))
            if said:
                raw += weight
                hits.append(skill if said == skill else f"{skill} ({said})")

    foreign = [s for s in profile.FOREIGN_SKILLS if s in hay]
    raw -= 2 * len(foreign)

    points = max(-6, min(25, raw))
    reasons = []
    if hits:
        reasons.append("stack overlap: " + ", ".join(sorted(set(hits))[:8]))
    else:
        reasons.append("no recognizable stack overlap")
    if foreign:
        reasons.append("unfamiliar stack: " + ", ".join(foreign[:4]))
    return points, reasons, hits


def _experience_points(job: Job) -> tuple[int, list[str], list[str]]:
    """Score the years gate on the whole band, not just its floor.

    Floors past MAX_YEARS_STRETCH are already blocked in `score_job`. The floor
    decides whether a posting is reachable; the ceiling decides how much of the
    in-range bonus it earns, since "2-5 years" tops out above you.
    """
    band = required_band(job)
    if band is None:
        if job.partial:
            # "No years stated" only means something on a full posting. On a
            # 500-character snippet the requirement is likely below the cut, so it
            # earns nothing.
            return 0, ["years requirement not visible in the snippet"], \
                ["unverified-experience"]
        return 6, ["no explicit years requirement"], []
    low, high = band

    if low > profile.MAX_YEARS_STRETCH:
        return -25, [f"{low}+ years required - out of range"], ["over-experienced-req"]

    span = f"{low}-{high}" if high > low else f"{low}+"
    if high > profile.MAX_YEARS_STRETCH:
        # The floor is reachable, the band is not aimed at you. Worth less
        # than a posting with no stated requirement at all: this one has
        # said out loud who it is looking for.
        return 3, [f"{span} years required - you are at the floor of the band"], \
            ["band-tops-out-high"]
    if low <= profile.YEARS_COMFORTABLE:
        if high <= profile.YEARS_COMFORTABLE:
            return 15, [f"{span} years required - in range"], []
        return 11, [f"{span} years required - top of the band is a stretch"], \
            ["stretch-experience"]
    return 4, [f"{span} years required - a stretch"], ["stretch-experience"]


def _education_points(job: Job) -> tuple[int, list[str], list[str]]:
    """Score the posting's education requirement against your degree.

    A req asking for your degree earns points, and one demanding a Master's
    costs them.
    """
    text = _binding_text(job).lower()
    if not text:
        return 0, [], []

    points, reasons, flags = 0, [], []

    if _has_any(text, profile.BACHELORS_TERMS):
        field = _has_any(text, profile.DEGREE_FIELDS)
        if field:
            points += 6
            reasons.append(f"bachelor's in {field} - your degree")
        else:
            points += 3
            reasons.append("bachelor's degree required - held")

    advanced = _has_any(text, profile.ADVANCED_DEGREE_TERMS)
    if advanced:
        points -= 8
        reasons.append(f"asks for a {advanced} - not held")
        flags.append("advanced-degree-req")

    return points, reasons, flags


def _no_experience_points(job: Job) -> tuple[int, list[str], list[str]]:
    """Points for a body that says no prior experience is required.

    Separate from the title's early-career bonus because the two show up
    independently: plenty of plain "Analyst" titles say "we will train".
    """
    signal = no_experience_signal(job)
    if signal:
        return 8, [f"no prior experience needed ({signal})"], ["no-experience-required"]

    equiv = equivalency_clause(job)
    if equiv:
        return 4, [f"education accepted in place of experience ({equiv})"], \
            ["equivalency-accepted"]
    return 0, [], []


def staffing_agency(job: Job) -> str | None:
    """The staffing firm behind the posting, if this is an agency repost.

    Checks the company name first (the firms that flood every aggregator),
    then the body language that gives away the ones not on the list -- "our
    client is seeking" is an agency repost whoever filed it.
    """
    company = f" {job.company.lower()} "
    for agency in profile.STAFFING_AGENCIES:
        if agency.strip() in company:
            return job.company
    phrase = _has_any(job.haystack, profile.AGENCY_PHRASES)
    return f"agency repost ({phrase})" if phrase else None


def _agency_points(job: Job) -> tuple[int, list[str], list[str]]:
    agency = staffing_agency(job)
    if not agency:
        return 0, [], []
    return -6, [f"staffing agency: {agency}"], ["staffing-agency"]


def _dealbreaker_points(job: Job) -> tuple[int, list[str], list[str]]:
    """Score the attributes hiring.cafe exposes as structured filters."""
    hay = job.haystack
    points, reasons, flags = 0, [], []
    for label, penalty, phrases in profile.DEALBREAKER_SIGNALS:
        hit = _has_any(hay, phrases)
        if hit:
            points += penalty
            reasons.append(f"{label} ({hit})")
            flags.append(label.replace(" ", "-"))
    return points, reasons, flags


_ATS_SOURCES = ("greenhouse", "lever", "ashby", "workday",
                "smartrecruiters", "workable", "recruitee")


def _syndication_points(job: Job) -> tuple[int, list[str], list[str]]:
    """Competition proxy, from how widely the req is syndicated.

    A req on five aggregators is in front of far more applicants than one only
    on the company's own Workday. `dedupe.collapse` already knows every source
    carrying it, so this costs nothing.
    """
    breadth = len(job.also_on)
    on_ats = job.source.startswith(_ATS_SOURCES)

    if on_ats and breadth == 0:
        return 8, ["company ATS only - not yet syndicated, low competition"], \
            ["low-competition"]
    if breadth >= 4:
        return -5, [f"syndicated across {breadth + 1} boards - crowded"], \
            ["high-competition"]
    if breadth >= 2:
        return -2, [f"also on {breadth} other boards"], []
    return 0, [], []


def _freshness_points(job: Job) -> tuple[int, list[str], list[str]]:
    age = job.age_days
    if age is None:
        return 0, ["posting age unknown"], ["no-date"]
    if age <= profile.FRESH_DAYS:
        return 10, [f"posted {age:.0f}d ago - fresh"], ["fresh"]
    if age <= 14:
        return 5, [f"posted {age:.0f}d ago"], []
    if age <= profile.STALE_DAYS:
        return 0, [f"posted {age:.0f}d ago"], []
    if age <= 90:
        return -10, [f"posted {age:.0f}d ago - stale"], ["stale"]
    return -18, [f"posted {age:.0f}d ago - likely evergreen/ghost"], ["ghost-suspect"]


def _salary_points(job: Job) -> tuple[int, list[str], list[str]]:
    lo, hi = parse_salary(job)
    if lo:
        job.salary_min, job.salary_max = lo, hi
    top = hi or lo
    if not top:
        return 0, [], []

    # Posting a salary is a small sign of a real, funded req (Virginia does not
    # require it). Applies even below the floor.
    points, flags = 3, ["salary-posted"]
    if top < profile.SALARY_FLOOR:
        return -10 + points, [f"pays {job.salary_text} - below floor"], \
            ["below-salary-floor"] + flags
    if top >= profile.SALARY_TARGET:
        return 8 + points, [f"pays {job.salary_text} - posted up front"], flags
    return 3 + points, [f"pays {job.salary_text} - posted up front"], flags


def _disqualifiers(job: Job) -> list[str]:
    """Hard blockers: credentials you don't hold, plus pay structures
    that mean the posting isn't a salaried analyst role at all."""
    hay = job.haystack
    return [d for d in profile.HARD_DISQUALIFIERS + profile.COMP_STRUCTURE_BLOCKS
            + profile.VOLUNTEER_MARKERS if d in hay]


# --------------------------------------------------------------------------
# What 100 means.
#
# The axes add up to 159 at their best, and clamping at 100 tied every good
# posting at 100. Below SCORE_LINEAR_TO nothing changes, so the tier lines
# and weights keep their calibrated meaning. Above it the rest of the range
# is stretched, so 100 needs the best case on every axis.
#
# Each term is one scoring function's best case, in the order score_job calls
# them. Re-weight a rule and this needs the same edit; `tests/test_radar.py`
# holds it to the arithmetic.
# --------------------------------------------------------------------------
SCORE_CEILING = (
    35 + 4       # title: a tier-1 hit with a domain word beside it
    + 25         # geo: in the home metro
    + 15         # experience: a stated requirement inside your range
    + 6          # education: a bachelor's in one of your fields
    + 8          # no prior experience needed
    + 10         # freshness: posted inside FRESH_DAYS
    + 11         # salary: at or above target, and posted up front
    + 8          # syndication: the company's own ATS, not yet copied anywhere
    + 25         # stack: the cap in _stack_points
    + 6          # an early-career marker in the title
    + 6          # applying direct to the company ATS
)

SCORE_LINEAR_TO = 90

# The most a posting can score when its body was only sent in part. 78 clears
# the A floor, so a strong snippet still reaches you, but it cannot outrank a
# posting whose requirements were actually checked.
PARTIAL_CEILING = 78


def scale(total: int) -> int:
    """One raw total as a 0-100 score.

    Identity below SCORE_LINEAR_TO. Above it, the raw range that used to be
    flattened against the clamp is spread across the last ten points.
    """
    if total <= SCORE_LINEAR_TO:
        return max(0, total)
    room = SCORE_CEILING - SCORE_LINEAR_TO
    over = min(total, SCORE_CEILING) - SCORE_LINEAR_TO
    return SCORE_LINEAR_TO + round(over * (100 - SCORE_LINEAR_TO) / room)


# The lowest score for each letter, best first. The page reads these too.
TIER_FLOORS = (("A", 75), ("B", 60), ("C", 45), ("D", 30))

# The most each part of a score can add, as the Criteria tab lists them.
SCORE_PARTS = (("Title", 35), ("Location", 25), ("Skills", 25),
               ("Experience", 15), ("How new it is", 10), ("Pay", 11))


def tier_for(score: int) -> str:
    for letter, floor in TIER_FLOORS:
        if score >= floor:
            return letter
    return "F"


def score_job(job: Job) -> Job:
    """Score one posting in place and return it."""
    reasons: list[str] = []
    flags: list[str] = []
    total = 0

    # Set before any early return: hard-blocked postings are still written to
    # the snapshots, and the market dashboard wants them categorised too --
    # "how many senior reqs opened this quarter" is a real question about the
    # Richmond market even though none of them are jobs you can take.
    job.job_family = job_family(job.title)

    # Who actually hires, when the company is a shared board. Read here because
    # scoring is the one place every route for the body has been through. It
    # never overwrites a division a source already set.
    if not job.division:
        job.division = division_in(job.description)

    senior = seniority_block(job.title)
    if senior:
        job.score = 0
        job.tier = "F"
        job.reasons = [f"title is out of band ({senior})"]
        job.flags = ["seniority-mismatch"]
        return job

    blockers = _disqualifiers(job)
    if blockers:
        job.score = 0
        job.tier = "F"
        job.reasons = ["disqualified: " + ", ".join(blockers[:3])]
        job.flags = ["disqualified"]
        return job

    # Years gate. Past MAX_YEARS_STRETCH is a hard block, because a minimum you
    # are years short of is a guaranteed rejection. `blocking_years` needs a
    # requirement cue nearby and respects "or equivalent"; anything it lets
    # through is still penalised in `_experience_points`.
    years_req = blocking_years(job)
    if years_req is not None and years_req > profile.MAX_YEARS_STRETCH:
        job.score = 0
        job.tier = "F"
        job.reasons = [f"requires {years_req}+ years - beyond your "
                       f"~{profile.YEARS_COMFORTABLE}yr range"]
        job.flags = ["over-experienced-req"]
        return job

    title_pts, why = _title_tier(job.title)
    total += title_pts
    reasons.append(why)

    for fn in (_geo_points, _experience_points, _education_points,
               _no_experience_points, _freshness_points, _salary_points,
               _agency_points, _dealbreaker_points, _syndication_points):
        pts, why, fl = fn(job)
        total += pts
        reasons.extend(why)
        flags.extend(fl)

    pts, why, _hits = _stack_points(job)
    total += pts
    reasons.extend(why)

    # An explicit early-career signal in the title is the strongest fit signal
    # in your applied set. Small additive bonus, placed BEFORE the
    # off-target cap so it lifts real entry roles over the line without
    # rescuing a capped off-target title (a capped "Associate <off-target>"
    # is still held at 40).
    entry = entry_level_marker(job.title)
    if entry:
        total += 6
        reasons.append(f"early-career signal ({entry})")
        flags.append("entry-level")

    # The company's own ATS beats an aggregator repost: the real req, fresher,
    # no agency in the middle. Stacks with `_syndication_points` (a better path
    # vs. less competition); together they reach +14.
    if job.source.startswith(_ATS_SOURCES):
        total += 6
        reasons.append("direct to company ATS")

    # A title off the target list is capped below the reporting threshold: kept
    # for the market data, never surfaced. 55 was not low enough, since remote,
    # fresh and paid pushed off-field titles into C.
    if title_pts == 0:
        total = min(total, 40)
        reasons.append("capped: title is off-target")

    job.score = scale(total)
    if job.partial and job.score > PARTIAL_CEILING:
        job.score = PARTIAL_CEILING
        reasons.append("capped: only a snippet of this posting was published")
        flags.append("partial-description")
    job.tier = tier_for(job.score)
    job.reasons = reasons
    job.flags = flags
    return job


def score_all(jobs: list[Job]) -> list[Job]:
    return sorted((score_job(j) for j in jobs),
                  key=lambda j: (-j.score, j.company, j.title))
