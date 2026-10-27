"""What the postings ask for that the profile has nothing to show for.

Two readers use this. The reader pane asks about one posting: which of its
requirement lines the profile answers and which it does not. The Growth tab
asks about all of them at once: across everything on the Jobs list and
everything applied to, what comes up most that the profile is missing.

Nothing here guesses. A skill is "asked for" when the shared vocabulary finds
it in a requirements line, the same matcher the resume engine tailors with.
A skill is "had" when master.toml can back it up (a skill, a bullet tag, a
course tag), when it is in the stack lists in targeting.toml, or when a
document dropped in on the Criteria tab named it (profile/evidence.toml). A
line the vocabulary cannot read is shown as unread, not as a gap, because
calling it missing would be a claim this module cannot make.

Years and degrees are read from the line too: a line asking for more years
than the criteria allow, or a degree above the highest one in master.toml, is
a gap of its own.
"""

from __future__ import annotations

import re
import threading
import tomllib
from collections import Counter

from .. import profile
from ..engine import gap, jd as jdmod, library, master as master_mod, vocab as vocab_mod
from ..radar import profile as radar_profile
from . import jdstruct

EVIDENCE = "evidence.toml"

# Inside a requirements section, a line that says one of these is a wish, not
# a gate. CarMax writes "Python, SQL, Excel or SAS preferred" under a heading
# called Qualifications and Requirements.
_SOFTENERS = re.compile(
    r"\b(preferred|a plus|nice to have|nice-to-have|bonus|desired|ideally|"
    r"is an asset|would be great)\b", re.I)

_REQUIRED_WORD = re.compile(r"\b(required|must have|must be|minimum)\b", re.I)

_DEGREES = (
    (4, re.compile(r"\b(ph\.?\s?d|doctorate|doctoral)\b", re.I), "Doctorate"),
    (3, re.compile(r"\b(master'?s|m\.s\.|mba|graduate degree|advanced degree)\b", re.I),
     "Master's degree"),
    (2, re.compile(r"\b(bachelor'?s?|b\.s\.|b\.a\.|four-year degree|4-year degree|"
                   r"undergraduate degree|college degree)\b", re.I), "Bachelor's degree"),
    (1, re.compile(r"\b(associate'?s? degree|associate of)\b", re.I), "Associate degree"),
)
_EQUIVALENT = re.compile(r"\bor (equivalent|comparable|related) (experience|work)\b|"
                         r"\bequivalent (combination|experience)\b", re.I)

# Product names the vocabulary does not know yet. Only shapes that look like
# a product are taken: all caps (SSMS) or a capital inside the word (GitHub).
# "Richmond" and "Monday" are capitalised too, and are not skills.
_PRODUCT = re.compile(r"\b([A-Z]{2,6}|[A-Z][a-z]+[A-Z][A-Za-z]+)\b")

# Capitals that turn up in requirement lines and are not products: states,
# money, benefits, degree letters, and the LinkedIn tags recruiters paste in.
_NOT_PRODUCTS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO",
    "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "USD", "PTO", "LI", "PC", "HR", "IT", "ID", "OR", "AND", "THE", "FT", "PT",
    "BA", "BS", "MBA", "GED", "EOE", "ADA", "FAQ", "CEO", "CFO", "CTO", "VP",
    "AM", "PM", "EST", "PST", "CST", "ET", "PT", "US", "USA", "UK", "EU", "TBD",
    "CI", "CD", "IL", "ASAP", "OK", "FMLA", "LLC", "INC", "NA", "MSc",
}

_LOCK = threading.Lock()
_HAVE: dict = {"key": None}


def _stamp(*names: str) -> tuple:
    out = []
    for name in names:
        try:
            info = profile.path(name).stat()
            out.append((name, info.st_mtime_ns, info.st_size))
        except OSError:
            out.append((name, 0, 0))
    return tuple(out)


def _evidence() -> dict:
    # Read straight from disk: the profile loader rechecks a file only every
    # few seconds, and a skill kept on the Criteria tab should count at once.
    try:
        with profile.path(EVIDENCE).open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        # A missing or hand-broken evidence file must not take the reader down.
        return {}


def known() -> dict:
    """The vocabulary, what the profile can back up, and the words it uses.

    Cached per version of the four files it reads, so the reader pane pays
    for it once and not on every posting.
    """
    key = _stamp("master.toml", "vocabulary.toml", "targeting.toml", EVIDENCE)
    with _LOCK:
        if _HAVE["key"] == key:
            return _HAVE
    # vocabulary.toml plus the skills library: the library is what lets the
    # reader see Terraform, CompTIA or month-end close at all.
    vocab = library.Reader(vocab_mod.load())
    master = master_mod.load()
    have = set(gap._master_terms(master))
    words = []
    for skill in list(radar_profile.CORE_SKILLS) + list(radar_profile.SUPPORTING_SKILLS):
        have.update(vocab.find(skill))
        words.append(skill)
    found = _evidence().get("skill", [])
    for entry in found:
        tid = entry.get("term", "")
        if tid in vocab.terms:
            have.add(tid)
        # The label too: a library id can change when the library is rebuilt,
        # and the label still finds the term.
        if entry.get("word") or entry.get("label"):
            words.append(entry.get("word") or entry["label"])
    have = vocab.expand(have)

    level = 0
    for edu in master.education:
        for rank, pattern, _label in _DEGREES:
            if pattern.search(edu.degree):
                level = max(level, rank)
                break
    try:
        written = profile.path("master.toml").read_text(encoding="utf-8-sig")
    except OSError:
        written = ""
    written += "\n" + "\n".join(words)
    corpus = written.lower()
    # A library term has no bullet to be tagged on. The profile has it when
    # master.toml or the stack names it anywhere, read in its own case so
    # "SAS" and "SOC 2" are found.
    have.update(vocab.library.find(written))

    known_words = set(vocab.library.not_skills)
    for key in (*vocab.library.blind, *vocab.library.exact):
        known_words.update(key.lower().split())
    aliases = []
    for term in vocab.vocab.terms.values():
        known_words.update(a.lower() for a in term.aliases)
        known_words.add(term.label.lower())
        if term.kind != "soft":
            aliases.append((term.id, tuple(a.lower() for a in term.aliases)))
    foreign = [w.lower() for w in radar_profile.FOREIGN_SKILLS]
    with _LOCK:
        _HAVE.update(key=key, vocab=vocab, have=have, level=level, corpus=corpus,
                     known_words=known_words, aliases=aliases,
                     years=int(radar_profile.YEARS_COMFORTABLE or 0),
                     foreign=foreign,
                     foreign_re=re.compile(
                         r"(?<![a-z0-9])(" + "|".join(re.escape(w) for w in foreign)
                         + r")(?![a-z0-9])") if foreign else None)
        return _HAVE


def _find(k: dict, text: str) -> list[str]:
    """`Vocabulary.find`, with the same answers, for text read in bulk.

    A term's pattern can only match where one of its aliases is a substring,
    and a substring test costs next to nothing, so a term runs its regex only
    on the lines that pass it. Over 800 postings this is the difference
    between eight seconds and under two.
    """
    low = text.lower()
    out = []
    patterns = k["vocab"].vocab._patterns
    for tid, aliases in k["aliases"]:
        if any(a in low for a in aliases) and patterns[tid].search(low):
            out.append(tid)
    # The library looks phrases up in a dict, so it needs no filter first.
    for tid in k["vocab"].library.find(text):
        if tid not in out and k["vocab"].kind(tid) != "soft":
            out.append(tid)
    return out


def _has_word(corpus: str, word: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(word.lower())}(?![a-z0-9])", corpus) is not None


# A benefits list that sits under a requirements heading, or runs on from
# one with no heading of its own. Two of these in one line is a benefits
# line, not a requirement: "Medical, dental and vision, HSA, EAP, PTO".
_BENEFITS = re.compile(
    r"\b(dental|vision|401\s*\(?k\)?|paid time off|pto|life insurance|disability insurance|"
    r"health savings|hsa|fsa|employee assistance|eap|tuition (reimbursement|assistance)|"
    r"parental leave|maternity|paternity|wellness program|telemedicine|holidays|"
    r"retirement plan|pension|commuter benefits|pet insurance)\b", re.I)


def _is_benefits(line: str) -> bool:
    return len({m.group(1).lower() for m in _BENEFITS.finditer(line)}) >= 2


# The posting talking about the employer rather than the candidate. Under a
# heading like "Education", which has no heading after it to close it, the
# first block that sounds like this is where the requirements stopped.
_EMPLOYER_VOICE = re.compile(
    r"\b(we|our|offers?|benefits|salary|pay range|compensation|equal (employment )?opportunity|"
    r"sponsorship|eoe)\b", re.I)


# The legal and pay text every posting closes with. Markdown postings put it
# straight after the last requirement with no heading between, so the first
# sentence of it ends the section, and the requirement it was glued to keeps
# the sentences before it: "Proficient in Microsoft Office. Maximus is an
# equal opportunity employer."
_BOILERPLATE = re.compile(
    r"equal (employment )?opportunity|without regard to|\beoe\b|"
    r"\b(pay|salary|hourly|wage) range|\bbase pay\b|\bstarting pay\b|pay transparency|"
    r"the compensation for|competitive compensation|"
    r"\$\s?\d[\d,.]*\s*(?:-|–|—|to|\?)\s*(?:USD\s*)?\$?\s?\d|"
    r"privacy (notice|policy)|benefits package|\bwe offer\b|"
    r"in addition to your salary|\bapply now\b|transparency notice|"
    r"use of ai in our|arrest or conviction|fair chance|"
    r"[\w.+-]+@[\w-]+\.[a-z]{2,}",
    re.I)
_SENTENCE_END = re.compile(r"[.!?]\s+")


def _before_boilerplate(item: str, names: list[str]) -> str | None:
    """The item cut where boilerplate starts, or None if none does.

    The employer's sentences just before it go too: "...resolve tracking
    issues. As a health care system ... we are asking our employees ...
    Baylor Scott & White Health no longer hires ... We are an equal
    opportunity employer."
    """
    found = _BOILERPLATE.search(item)
    if not found:
        return None
    ends = [m.end() for m in _SENTENCE_END.finditer(item, 0, found.start())]
    voice = re.compile(r"\b(we|our|us)\b" + "".join(rf"|\b{n}\b" for n in names), re.I)
    while len(ends) > 1 and voice.search(item[ends[-2]:ends[-1]]):
        ends.pop()
    if ends:
        return item[:ends[-1]].strip()
    # Tyson's whole list is one run-on item with the pay range at its end.
    # Without a sentence break the cut is at the boilerplate itself, kept
    # only when there is more before it than the start of that sentence.
    head = item[:found.start()].strip()
    return head if len(head) >= 40 else ""


def _lines(blocks: list[dict], company: str = "") -> list[tuple[str, str]]:
    """(section, line) for every line under a requirements heading.

    `section` is required or preferred. A posting with no such heading gives
    nothing back, and the caller says so rather than reading the whole body
    as if every line in it were a requirement.
    """
    out: list[tuple[str, str]] = []
    current = "other"
    lead = ""   # set by a line like "Knowledge of the following preferred:"
    short = False   # under "Education" and the like, until the employer talks
    seen = False
    names = [w for w in re.findall(r"[A-Za-z]{3,}", company)
             if w.lower() not in ("inc", "llc", "the", "and", "company", "group", "corporation")]
    for block in blocks:
        if block["kind"] in ("heading", "end"):
            text = block.get("text", "")
            # "Qualifications:" over "Beyond the basics, what will make you
            # successful" names the section in the heading that was dropped.
            current = (jdmod._classify(text)
                       or jdmod._classify(block.get("over", "")) or "other")
            short = jdmod.subject_heading(text)
            seen = False
            lead = ""
            continue
        if current not in ("required", "preferred"):
            continue
        items = block["items"] if block["kind"] == "list" else [block["text"]]
        if short and seen:
            said = " ".join(items)
            if _EMPLOYER_VOICE.search(said) or any(re.search(rf"\b{n}\b", said) for n in names):
                current = "other"
                continue
        seen = True
        for item in items:
            item = item.strip()
            cut = _before_boilerplate(item, names)
            if cut is not None:
                section, current = lead or current, "other"
                if len(cut) >= 3 and not _is_benefits(cut):
                    out.append((section, cut))
                break
            if len(item) < 3 or _is_benefits(item):
                continue
            section = lead or current
            # "Bachelor's Degree required, in a technical major preferred"
            # is still a required line; the softener is about the major.
            if section == "required" and _SOFTENERS.search(item) \
                    and not _REQUIRED_WORD.search(item):
                section = "preferred"
            if item.endswith(":"):
                lead = section
            out.append((section, item))
    return out


# The majors a degree may be in: "Bachelor's degree in Accounting, Finance,
# Economics or a related field". A list of majors is not a list of skills,
# so it is left out when terms are counted. Only a list that ends the way
# such lists do is cut; "degree in IT and 3 years of SQL" keeps its SQL.
_DEGREE_FIELD = re.compile(
    r"\b(?:degree|bachelor'?s|master'?s|associate'?s|b\.?a\.?|b\.?s\.?|mba|major)\s+"
    r"(?:\w+\s+)?(?:in|of)\s+[^.;:()]{1,160}?"
    r"(?:(?:a\s+|another\s+)?(?:closely\s+)?(?:related|similar|relevant|equivalent)"
    r"(?:\s+(?:technical\s+|quantitative\s+)?(?:field|discipline|area|major|program)s?(?:\s+of\s+study)?)?)",
    re.I)


def _read_line(text: str, k: dict, company: str) -> dict:
    terms = _find(k, _DEGREE_FIELD.sub(" ", text))
    have = [t for t in terms if t in k["have"]]
    missing = [t for t in terms if t not in k["have"]]
    flags: list[dict] = []

    years = [int(m.group(2) or m.group(1)) for m in jdmod._YEARS.finditer(text)]
    years = [y for y in years if 0 < y <= 20]
    if years and max(years) > k["years"]:
        flags.append({"kind": "years", "label": f"{max(years)}+ years of experience",
                      "note": f"your criteria allow up to {k['years']}"})

    asked = [rank for rank, pattern, _l in _DEGREES if pattern.search(text)]
    if asked and min(asked) > k["level"] and not _EQUIVALENT.search(text):
        label = next(l for rank, _p, l in _DEGREES if rank == min(asked))
        flags.append({"kind": "degree", "label": label, "note": "above your highest degree"})

    # Names the vocabulary has no term for. The ones the profile spells out
    # anywhere count as had; the rest are listed, marked as unrecognized.
    others: list[dict] = []
    seen = set()
    skip = {w.lower() for w in re.findall(r"[A-Za-z]+", company)}
    # "FP&A" and "TCP/IP" are whole names the library reads; their halves
    # are not unknown names of their own.
    whole = [m.span() for m in library._WORD.finditer(text)
             if m.group().lower() in k["known_words"]]
    for m in _PRODUCT.finditer(text):
        token = m.group(1)
        low = token.lower()
        if any(a <= m.start() and m.end() <= b for a, b in whole):
            continue
        if (low in seen or token in jdmod._STOP or token in _NOT_PRODUCTS
                or low in k["known_words"] or low in skip):
            continue
        seen.add(low)
        others.append({"word": token, "have": _has_word(k["corpus"], token)})
    if k["foreign_re"] is not None:
        for m in k["foreign_re"].finditer(text.lower()):
            word = m.group(1)
            # A foreign skill the library has a term for is already counted
            # among the missing terms; listing it again would say "unknown".
            if word not in seen and word not in k["known_words"]:
                seen.add(word)
                others.append({"word": word, "have": _has_word(k["corpus"], word)})

    # A years or degree line the profile meets was still read, so it is
    # not listed among the lines with nothing in them.
    checked = bool(years or asked)
    return {"have": have, "missing": missing, "flags": flags, "others": others,
            "checked": checked}


def for_posting(description: str, company: str = "", blocks: list[dict] | None = None) -> dict:
    """One posting's requirements, line by line, against the profile."""
    k = known()
    vocab = k["vocab"]
    blocks = jdstruct.structure(description) if blocks is None else blocks
    lines = []
    asked: dict[str, str] = {}
    for section, text in _lines(blocks, company):
        read = _read_line(text, k, company)
        for tid in read["have"] + read["missing"]:
            if asked.get(tid) != "required":
                asked[tid] = section
        lines.append({"section": section, "text": text, **read})

    def term(tid: str) -> dict:
        return {"id": tid, "label": vocab.label(tid), "kind": vocab.kind(tid),
                "section": asked[tid], "have": tid in k["have"]}

    terms = [term(t) for t in asked]
    gaps = [t for t in terms if not t["have"]]
    flags = [dict(f, section=l["section"]) for l in lines for f in l["flags"]]
    others = {}
    for l in lines:
        for o in l["others"]:
            others.setdefault(o["word"].lower(), dict(o, section=l["section"]))
    return {
        "found": bool(lines),
        "lines": lines,
        "asked": len(terms),
        "have": len(terms) - len(gaps),
        "missing": sorted(gaps, key=lambda t: (t["section"] != "required", t["label"])),
        "labels": {t["id"]: t["label"] for t in terms},
        "flags": flags,
        "others": [o for o in others.values() if not o["have"]],
        "unread": [l["text"] for l in lines
                   if not (l["have"] or l["missing"] or l["flags"] or l["others"]
                           or l["checked"])],
    }


# A job title's family: the role noun and the word before it. "Senior Data
# Analyst II - Remote" and "Data Analyst, Healthcare" are both "data
# analyst"; "DOC - IT Security Analyst 2" is "security analyst".
_ROLE = re.compile(
    r"(analyst|specialist|engineer|developer|scientist|manager|coordinator|administrator|"
    r"technician|consultant|tester|architect|associate|representative|officer|accountant|"
    r"auditor|investigator|assistant|designer|programmer|clerk|strategist|writer)s?", re.I)
_SENIORITY = re.compile(
    r"\b(senior|sr|junior|jr|entry[- ]level|principal|staff|mid[- ]level|level|lead|i{1,3}|iv|"
    r"[1-5]|trainee|intern|apprentice|remote|hybrid|onsite|temporary|contract|part[- ]time|"
    r"full[- ]time)\b\.?", re.I)
_TITLE_PARTS = re.compile(r"\s[-–—|]\s|[(),:|]|\s-\s?")
# A word that names half a phrase on its own: "Help Desk Analyst" is asked
# for as help desk, not as desk.
_TITLE_WHOLE = {"desk": "help desk", "intelligence": "business intelligence", "system": "systems",
                "quality": "quality", "service": "service"}


def title_family(title: str) -> str:
    """The family a job title belongs to, or "" when it names no role."""
    parts = [p for p in _TITLE_PARTS.split(title or "") if p and p.strip()]
    for n, part in enumerate(parts):
        words = re.findall(r"[a-z&+#.]+", _SENIORITY.sub(" ", part.lower()).replace("/", " "))
        roles = [i for i, w in enumerate(words) if _ROLE.fullmatch(w)]
        if not roles:
            continue
        i = roles[0]
        before = [w for w in words[:i] if w.isalpha()]
        if not before and n + 1 < len(parts):
            # "Analyst, Supply Chain": the part after the comma names the field.
            # "Supply Chain Analytics" is a supply chain analyst.
            after = [w for w in re.findall(r"[a-z]+", _SENIORITY.sub(" ", parts[n + 1].lower()))
                     if w not in ("analytics", "and", "of", "the")]
            before = [" ".join(after[-2:])] if after else []
        role = words[i].rstrip("s") if words[i].endswith("s") and not words[i].endswith("ss") else words[i]
        lead = _TITLE_WHOLE.get(before[-1], before[-1]) if before else ""
        return f"{lead} {role}".strip()
    return ""


# What one posting asks for, kept per profile version: Growth counts the
# same postings again for every title filter, and reading them is the slow
# part.
_SUMMARIES: dict = {"key": None, "rows": {}}


def _summary_cached(row: dict, k: dict) -> dict:
    description = row.get("description") or ""
    company = row.get("company") or ""
    if _SUMMARIES["key"] != k["key"]:
        _SUMMARIES.update(key=k["key"], rows={})
    key = (company, hash(description))
    hit = _SUMMARIES["rows"].get(key)
    if hit is None:
        hit = _SUMMARIES["rows"][key] = _summary(description, company, k)
    return hit


def _summary(description: str, company: str, k: dict) -> dict:
    """The parts of `for_posting` the Growth tab counts, without the lines."""
    required: set[str] = set()
    preferred: set[str] = set()
    flags: set[str] = set()
    others: set[str] = set()
    for section, text in _lines(jdstruct.structure(description), company):
        read = _read_line(text, k, company)
        bucket = required if section == "required" else preferred
        bucket.update(read["have"] + read["missing"])
        flags.update(f["label"] for f in read["flags"])
        others.update(o["word"] for o in read["others"] if not o["have"])
    preferred -= required
    if not (required or preferred):
        # No requirements heading. The whole body still says what the job
        # uses, so it counts as asked, but never as a gate.
        preferred = set(_find(k, jdstruct.plain(description)))
    return {"required": required, "preferred": preferred, "flags": flags, "others": others}


_GROWTH_LOCK = threading.Lock()
_GROWTH: dict = {}      # (stamp, profile key, title) -> report, a few at a time
_GROWTH_KEEP = 16


def titles(listed: list[dict], applied: list[dict], n: int = 12) -> list[dict]:
    """The commonest title families, with how many postings each set has."""
    lst = Counter(title_family(r.get("title") or "") for r in listed)
    app = Counter(title_family(r.get("title") or "") for r in applied)
    both = (lst + app)
    both.pop("", None)
    return [{"key": f, "listed": lst[f], "applied": app[f]}
            for f, c in both.most_common() if c >= 3][:n]


def growth(listed: list[dict], applied: list[dict], stamp: tuple, title: str = "") -> dict:
    """Across every posting: what is asked most, and what the profile lacks.

    `listed` is the Jobs list, `applied` the postings applied to, each a dict
    with description, company, title, score and uid. `title` narrows both to
    one title family ("data analyst"). Cached on `stamp` together with the
    profile files, since reading 800 postings takes a second or two and the
    answer only changes when one of them does.
    """
    k = known()
    key = (stamp, k["key"], title)
    with _GROWTH_LOCK:
        if key in _GROWTH:
            return _GROWTH[key]
    families = titles(listed, applied)
    if title:
        listed = [r for r in listed if title_family(r.get("title") or "") == title]
        applied = [r for r in applied if title_family(r.get("title") or "") == title]
    vocab = k["vocab"]

    def count(rows: list[dict]) -> dict:
        req, pref, flag, other = Counter(), Counter(), Counter(), Counter()
        only: Counter = Counter()        # the one required skill a posting lacks
        clear = 0                        # postings whose required skills are all had
        cover: list[float] = []
        examples: dict[str, list] = {}
        for row in rows:
            s = _summary_cached(row, k)
            req.update(s["required"])
            pref.update(s["preferred"])
            flag.update(s["flags"])
            other.update(s["others"])
            lacking = [t for t in s["required"] if t not in k["have"]]
            if s["required"]:
                cover.append(1 - len(lacking) / len(s["required"]))
                if not lacking:
                    clear += 1
            if len(lacking) == 1:
                only[lacking[0]] += 1
            for t in s["required"] | s["preferred"]:
                if t not in k["have"]:
                    examples.setdefault(t, []).append(row)
        return {"n": len(rows), "req": req, "pref": pref, "flag": flag,
                "other": other, "only": only, "clear": clear,
                "cover": sum(cover) / len(cover) if cover else None,
                "with_req": len(cover), "examples": examples}

    lst, app = count(listed), count(applied)

    def pct(n: int, total: int) -> float:
        return round(100 * n / total, 1) if total else 0.0

    rows = []
    for tid in set(lst["req"]) | set(lst["pref"]) | set(app["req"]) | set(app["pref"]):
        if tid not in vocab.terms:
            continue
        ex = sorted(lst["examples"].get(tid, []) + app["examples"].get(tid, []),
                    key=lambda r: -(r.get("score") or 0))
        picked, names = [], set()
        for r in ex:
            name = f"{r.get('company')}|{r.get('title')}"
            if name not in names:
                names.add(name)
                picked.append({"company": r.get("company") or "", "title": r.get("title") or "",
                               "uid": r.get("uid") or "", "score": r.get("score")})
            if len(picked) == 4:
                break
        rows.append({
            "id": tid, "label": vocab.label(tid), "kind": vocab.kind(tid),
            "have": tid in k["have"],
            "listed_required": pct(lst["req"][tid], lst["n"]),
            "listed_any": pct(lst["req"][tid] + lst["pref"][tid], lst["n"]),
            "applied_required": pct(app["req"][tid], app["n"]),
            "applied_any": pct(app["req"][tid] + app["pref"][tid], app["n"]),
            "listed_count": lst["req"][tid] + lst["pref"][tid],
            "applied_count": app["req"][tid] + app["pref"][tid],
            "unlocks_listed": lst["only"][tid],
            "unlocks_applied": app["only"][tid],
            "examples": picked if tid not in k["have"] else [],
        })
    # Demand first: how often the postings you actually chase name it.
    rows.sort(key=lambda r: (-(r["applied_any"] + r["listed_any"]), r["label"]))

    def top(counter: Counter, total: int, n: int = 8) -> list[dict]:
        return [{"label": w, "count": c, "pct": pct(c, total)}
                for w, c in counter.most_common(n) if c >= 2]

    value = {
        "listed": {"n": lst["n"], "with_requirements": lst["with_req"], "clear": lst["clear"],
                   "coverage": round(100 * lst["cover"], 1) if lst["cover"] is not None else None},
        "applied": {"n": app["n"], "with_requirements": app["with_req"], "clear": app["clear"],
                    "coverage": round(100 * app["cover"], 1) if app["cover"] is not None else None},
        "terms": rows,
        "barriers": {"listed": top(lst["flag"], lst["n"]), "applied": top(app["flag"], app["n"])},
        "unrecognized": top(lst["other"] + app["other"], lst["n"] + app["n"], 12),
        "have_count": len(k["have"]),
        "titles": families,
        "title": title,
    }
    with _GROWTH_LOCK:
        if len(_GROWTH) >= _GROWTH_KEEP:
            _GROWTH.pop(next(iter(_GROWTH)))
        _GROWTH[key] = value
    return value
