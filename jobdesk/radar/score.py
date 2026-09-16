"""Fit scoring and triage (plan item 2).

Every posting gets 0-100 and a letter tier before you read a word of it.
The point is not precision -- it's that ~200 raw listings become ~10 worth
opening, with the reason for each verdict written down so a wrong call is
visible and fixable in `profile.py` rather than mysterious.

Deliberately rules-only, no LLM. Clear A's and clear F's sort themselves for
free; an LLM pass over the ambiguous middle is a later addition (plan item 2
calls for exactly that) and would slot in after `score_job` here.
"""

from __future__ import annotations

import re

from . import profile
from .models import Job

# "5+ years", "5-7 years", "minimum of 5 years", "at least five years"
#
# The leading (?<!\d) is load-bearing. Without it a four-digit calendar year
# followed by the word "year" is read as a requirement: a live Owens & Minor
# posting containing "2014 year over year" scored as a 14-YEAR requirement and
# took a -25 penalty for it. Any recent year does this -- 2019 -> 19, 2013 ->
# 13 -- and all of them clear the `<= 20` sanity guard below.
# The separator in the range half is REQUIRED, not optional, for the same
# reason. With it optional, "2014 year" still parses -- \d{1,2} takes "20" and
# the optional second number swallows "14" with nothing in between. Demanding
# a real "-" / "to" between the two halves means a bare four-digit run cannot
# masquerade as a range.
_YEARS_PATTERNS = [
    re.compile(r"(?<!\d)(\d{1,2})\s*\+?\s*(?:(?:-|to|–|—)\s*\d{1,2}\s*)?\+?\s*years?\b", re.I),
    re.compile(r"minimum(?:\s+of)?\s+(?<!\d)(\d{1,2})\s*years?\b", re.I),
    re.compile(r"at least\s+(?<!\d)(\d{1,2})\s*years?\b", re.I),
]

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_WORD_YEARS = re.compile(
    r"\b(" + "|".join(_WORD_NUMBERS) + r")\s*(?:\+)?\s*years", re.I)

# Salary written in the JD body.
#
# Most money in a job ad is not pay. Live bodies offered "$200B in annualized
# spend", "$250M+ earned through our platform", "educational assistance up to
# $2500" and "AD&D coverage valued at $10,000 each" -- none of them a wage,
# all of them one naive regex away from becoming one. So an amount has to
# either sit near a compensation cue or clear a plausibility band on its own.
#
# The separator is the other half of the problem. Greenhouse renders its band
# as markup -- <span>$72,000</span><span class="divider">&mdash;</span>
# <span>$115,000 USD</span> -- and Workday writes "$111,160/yr to $138,950/yr".
# Both are ordinary ranges wearing something between the numbers, so the two
# amounts are joined by a required dash-or-"to" with markup, entities and unit
# suffixes allowed on either side of it.
# A letter glued to the dollar sign usually names a different currency. A
# live Dart posting reads "SALARY: CI$60,000 - CI$80,000 pa" and those are
# Cayman Islands dollars, which is a Cayman Islands job -- a figure worth
# roughly $72,000 USD attached to a role nobody here can take. US$ is the one
# prefix that means what it says, so it is the one exception.
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


def _binding_text(job: Job) -> str:
    """The part of the JD that states real requirements."""
    text = job.description or job.title
    if not text:
        return ""
    m = _PREFERRED_MARKER.search(text)
    return text[:m.start()] if m and m.start() > 200 else text


def required_years(job: Job) -> int | None:
    """The years-of-experience gate the posting actually imposes.

    Two rules, both learned from real postings:

      * Only look at the binding half of the JD. A Capital One req whose
        Basic Qualifications say "at least 5 years" and whose Preferred
        section says "at least 1 year of Python" is a 5-year job, and taking
        the minimum across the whole document read it as a 1-year job.

      * Within that half, take the MAXIMUM. If a posting demands 5 years
        anywhere in its requirements, 5 years is the gate -- an earlier
        "1+ years of SQL" line doesn't lower it.

    Ranges ("3-5 years") are read at their lower bound by the pattern itself,
    which is the one place being generous is correct.
    """
    text = _binding_text(job)
    if not text:
        return None
    found: list[int] = []
    for pat in _YEARS_PATTERNS:
        for m in pat.finditer(text):
            try:
                n = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if 0 < n <= 20:
                found.append(n)
    for m in _WORD_YEARS.finditer(text):
        found.append(_WORD_NUMBERS[m.group(1).lower()])
    return max(found) if found else None


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
    """The years figure that should HARD BLOCK, as opposed to merely penalise.

    Split out from `required_years` because the hard block is a much heavier
    consequence than a penalty and needs a correspondingly higher bar. Two
    things narrow it:

      * **Proximity.** The figure only counts if a requirement cue sits within
        `_GATE_WINDOW` characters. `required_years` takes the maximum anywhere
        in the binding half, which is right for scoring but too blunt to erase
        a posting on -- a "celebrating 15 years in Richmond" line in an About
        Us paragraph should not delete the req underneath it.

      * **Equivalency.** "5 years of experience OR an equivalent combination of
        education and experience" is not a 5-year gate; it is the standard HR
        escape hatch, and it is precisely the wording your degree opens.
        Same for an explicit "we will train". Either one returns None here, so
        the posting falls through to the ordinary penalty in
        `_experience_points` and is demoted rather than erased.
    """
    text = _binding_text(job)
    if not text:
        return None

    gated: list[int] = []
    for pat in _YEARS_PATTERNS:
        for m in pat.finditer(text):
            try:
                n = int(m.group(1))
            except (TypeError, ValueError):
                continue
            if not 0 < n <= 20:
                continue
            window = text[max(0, m.start() - _GATE_WINDOW): m.end() + _GATE_WINDOW]
            if _REQUIREMENT_CUE.search(window):
                gated.append(n)
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
    """Pull a salary range out of the JD body when the API didn't give one.

    Four passes, widening as they go. A range inside a compensation cue's
    window is trusted on sight. A range anywhere else has to clear the bands
    alone. Then a lone hourly rate, because "$32/hour" says what it is in a
    way a bare annual figure does not. Then one figure on a cue, as a floor.

    Returns (low, high); `high` is None when the posting gave a number but no
    ceiling. The table already renders that as "$40k+", so a floor is worth
    keeping rather than rounding down to nothing.
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


# Numeric early-career levels: "Analyst 1", "Data Analyst I". Roman I and
# arabic 1 only -- II/2 and up are not entry.
_ENTRY_LEVEL_NUM = re.compile(r"(?<![a-z])(?:i|1)(?![a-z0-9])", re.I)


def entry_level_marker(title: str) -> str | None:
    """The early-career signal in the title, if any.

    Associate / Junior / Entry-Level / Graduate / a trailing level-1. This is
    the strongest positive signal in your applied set, and it also disarms
    the seniority block on combined-level postings (see below).
    """
    t = f" {flatten_title(title)} "
    for mark in profile.ENTRY_LEVEL_MARKERS:
        if re.search(r"(?<![a-z])" + re.escape(mark) + r"(?![a-z])", t):
            return mark
    if _ENTRY_LEVEL_NUM.search(title):
        return "level 1"
    return None


def seniority_block(title: str) -> str | None:
    """The disqualifying word in the title, if there is one.

    Matched on word boundaries rather than substrings: "Manager" must not
    fire on "Management", and "lead" must not fire on "leadership".

    This is a hard block, not a penalty. Live data made the case: a "Lead
    Software Engineer" at $179k scored 75/100 on geo + freshness + salary
    alone, because merely withholding the title points left everything else
    intact. You are not a candidate for a lead role, and a triage engine
    that surfaces one has failed at its only job.

    Exception: a title that *also* carries an entry-level marker is a
    combined-level posting -- "Associate Data Engineer / Data Engineer II /
    Senior Data Engineer" lists one req spanning Associate through Senior, and
    its floor (Associate) is squarely in range. Blocking it on the word
    "senior" threw away a role you actually applied to, so co-occurrence of
    an entry marker disarms the block.
    """
    t = f" {title.lower()} "
    for bad in profile.TITLE_DISQUALIFIERS:
        if re.search(r"(?<![a-z])" + re.escape(bad.strip()) + r"(?![a-z])", t):
            if entry_level_marker(title):
                return None
            return bad.strip()
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
    """(points, label) from the FUNCTION axis -- the open-vocabulary fallback.

    Tried when the closed tier lists miss. See the long note in profile.py:
    the lists are ~60 exact strings, and calibration showed real applied roles
    buried purely because their wording wasn't on one ("analysis" rather than
    "analyst", a slash in the middle of "Desktop/End User Support Tech").

    Clerical and off-field markers are checked first. Loosening the vocabulary
    widens the risk that a stray token carries a genuinely wrong title
    on-target, so the guards get to speak before the families do.
    """
    t = f" {flatten_title(title)} "

    for bad in profile.CLERICAL_MARKERS:
        if bad in t:
            return 0, f"clerical title ({bad})"
    for bad in profile.OFF_FIELD_MARKERS:
        if re.search(r"(?<![a-z])" + re.escape(bad) + r"(?![a-z])", t):
            return 0, f"off-field title ({bad})"

    for points, label, tokens in profile.FUNCTION_FAMILIES:
        for token in tokens:
            if re.search(r"(?<![a-z])" + re.escape(token) + r"(?![a-z])", t):
                # A domain word beside the function word sharpens the read:
                # "Data Analyst" beats a bare "Analyst".
                # `d not in token` rather than `d != token`: "data engineer"
                # already carries its domain word, and pairing it with the
                # "data" modifier reads as "data data engineer".
                domain = next(
                    (d for d in profile.DOMAIN_MODIFIERS
                     if d not in token and token not in d and
                     re.search(r"(?<![a-z])" + re.escape(d) + r"(?![a-z])", t)),
                    None)
                if domain:
                    return points + 4, f"{label} ({domain} {token})"
                return points, f"{label} ({token})"
    return 0, ""


def _title_tier(title: str) -> tuple[int, str]:
    """(points, label) for the posting title.

    A non-zero return also means "on target": the off-target cap in `score_job`
    keys off `title_pts == 0`, so anything that scores here is exempt from it.

    Two vocabularies, and the better of the two wins. The tier lists are a
    closed set of hand-ranked strings encoding your stated priority; the
    function families are an open fallback that reads an unenumerated title.
    Taking the max means the hand-tuned weights can only help -- nothing that
    scores today regresses -- while a title the lists never anticipated still
    gets a real reading instead of the off-target cap.
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
                # A bare "Analyst" with no qualifier still counts. Raised
                # 12 -> 15 after calibration: at 12, a remote generic-analyst
                # role landed at exactly 43, one point under the C-tier line,
                # so the whole family was buried.
                if "analyst" in t:
                    listed, listed_why = 15, "generic analyst title"
                else:
                    for good in profile.ADJACENT_TITLES:
                        if good in t:
                            listed, listed_why = 12, f"analyst-adjacent ({good.strip()})"
                            break

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
    """Coarse function category for the posting -- hiring.cafe's `jobCategory`.

    Not used in scoring. It exists so the accumulating snapshots carry a
    groupable dimension: the Richmond Job Market Dashboard (plan item 11)
    wants "how many analytics reqs opened in Q3 vs. support reqs", and raw
    titles do not aggregate. Cheap to derive now and impossible to backfill
    later, since the snapshots are the only record of postings that have
    since come down.
    """
    t = f" {flatten_title(title)} "
    if _has_any(t, profile.CLERICAL_MARKERS):
        return "clerical"
    for bad in profile.OFF_FIELD_MARKERS:
        if re.search(r"(?<![a-z])" + re.escape(bad) + r"(?![a-z])", t):
            return "other-field"
    for _points, label, tokens in profile.FUNCTION_FAMILIES:
        for token in tokens:
            if re.search(r"(?<![a-z])" + re.escape(token) + r"(?![a-z])", t):
                return label.replace(" function", "")
    return "unclassified"


def non_us_location(location: str) -> str | None:
    """The foreign country/city named in the location, if any."""
    loc = f" {location.lower()} "
    for marker in profile.NON_US_MARKERS:
        if re.search(r"(?<![a-z])" + re.escape(marker) + r"(?![a-z])", loc):
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
        in_va = any(term in hay for term in profile.STATE_TERMS)
        detail = "elsewhere in Virginia" if in_va else "outside commute range"
        flags.append("out-of-area")
        return -100, [f"onsite, {detail}, not remote (no relocation planned)"], flags
    return points, reasons, flags


def _stack_points(job: Job) -> tuple[int, list[str], list[str]]:
    hay = job.haystack
    hits: list[str] = []
    raw = 0

    for skill, weight in profile.CORE_SKILLS.items():
        if skill in hay:
            raw += weight
            hits.append(skill)
    for skill, weight in profile.SUPPORTING_SKILLS.items():
        if skill in hay:
            raw += weight
            hits.append(skill)

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
    # Roles past MAX_YEARS_STRETCH are already hard-blocked in score_job before
    # this runs, so in practice `years` is None or <= MAX_YEARS_STRETCH here.
    # The final branch stays as a defensive fallback.
    years = required_years(job)
    if years is None:
        return 6, ["no explicit years requirement"], []
    if years <= profile.YEARS_COMFORTABLE:
        return 15, [f"{years}+ years required - in range"], []
    if years <= profile.MAX_YEARS_STRETCH:
        return 4, [f"{years}+ years required - a stretch"], ["stretch-experience"]
    return -25, [f"{years}+ years required - out of range"], ["over-experienced-req"]


def _education_points(job: Job) -> tuple[int, list[str], list[str]]:
    """Score the posting's education requirement against your B.S.

    Previously unscored entirely, which cut both ways: a req asking for exactly
    your degree earned nothing, and one demanding a Master's cost nothing.
    hiring.cafe carries education as a structured field; this is the rules-only
    read of the same thing.
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
    """hiring.cafe's 'No Prior Experience Required' bucket, read from prose.

    Their seniority ladder has a rung BELOW Entry Level, and it is the single
    most valuable rung for someone re-entering the field. Worth real points on
    its own, separate from the title-based early-career bonus, because the two
    signals appear independently -- plenty of "Analyst" titles carry no level
    marker but say "we will train" in the body.
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

    hiring.cafe shows views / submissions / saves per posting, which is the
    single most useful number on the site: it tells you whether you are
    applicant #15 or applicant #400, and plan item 3 says that difference is
    most of the value of applying early. We can't get their counts.

    We can compute something that predicts them, and until now we computed it
    and threw it away. `dedupe.collapse` already knows every source carrying a
    given req. A posting sitting on five aggregators is in front of tens of
    thousands of job-seekers; a posting that exists only on Capital One's own
    Workday has been seen by the few people who thought to look there. That
    asymmetry is exactly what the engagement numbers measure, and it is free.
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

    # Pay transparency is itself a signal, which is hiring.cafe's
    # `isCompensationTransparent` filter. Virginia does not mandate posting a
    # salary, so an employer who posts one anyway is running a real, funded,
    # compliance-minded req -- as against the reqs that omit it because the
    # number is embarrassing or the role is speculative. Small, and it applies
    # even to a below-floor posting, where knowing the number is the point.
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


def tier_for(score: int) -> str:
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    if score >= 45:
        return "C"
    if score >= 30:
        return "D"
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

    # Years-of-experience gate. Anything past MAX_YEARS_STRETCH (3) is a hard
    # block, deliberately: a role stating a minimum you're years short of is a
    # guaranteed rejection, so it should never reach your queue. Reads only the
    # binding half of the JD and takes the maximum there (see required_years).
    # `blocking_years`, not `required_years`: the block only fires on a figure
    # sitting next to a requirement cue, and an "or equivalent combination of
    # education and experience" clause (or an explicit "we will train")
    # disarms it entirely. Anything it lets through is still penalised by
    # `_experience_points` below.
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

    # Applying on the company's own ATS beats an aggregator repost: it is the
    # real req, it is fresher, and no agency is skimming the middle.
    #
    # Deliberately stacks with the exclusivity bonus in `_syndication_points`:
    # they measure different things (a better application path vs. less
    # competition) and a req that is both ATS-direct and unsyndicated is the
    # best case the radar can find. Combined ceiling is +14.
    if job.source.startswith(_ATS_SOURCES):
        total += 6
        reasons.append("direct to company ATS")

    # A title that isn't on the target list at all is capped below the
    # reporting threshold: recorded for the market dataset, never surfaced
    # as something to read.
    #
    # Set at 55 first, which wasn't enough -- "Licensed Mental Health
    # Therapist" and "Commercial Account Executive" both reached C-tier on
    # remote + fresh + salary, helped along by "excel" and "aws" matching
    # inside boilerplate. If the title isn't on the list, no amount of
    # everything-else should make it worth your attention.
    if title_pts == 0:
        total = min(total, 40)
        reasons.append("capped: title is off-target")

    job.score = max(0, min(100, total))
    job.tier = tier_for(job.score)
    job.reasons = reasons
    job.flags = flags
    return job


def score_all(jobs: list[Job]) -> list[Job]:
    return sorted((score_job(j) for j in jobs),
                  key=lambda j: (-j.score, j.company, j.title))
