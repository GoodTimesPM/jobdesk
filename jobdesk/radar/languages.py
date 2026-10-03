"""Spoken-language requirements, and which languages the user speaks.

A posting that needs Spanish is a guaranteed rejection for someone who does
not speak it, however well the title and the stack match. The targeting
profile only knew a handful of phrases ("fluent in spanish", "must be
bilingual") as an 8-point penalty, so "Bilingual Business Operations
Analyst" and "Spanish/English required" reached the board in the 70s.

What the user speaks comes from their own skills in master.toml: a skill
whose term, detail or category names a language, or a `languages` list at
the top level or under `[identity]`. English is always assumed, since the
radar reads English postings.
"""

from __future__ import annotations

import re

from .. import profile as _profile

# Lowercase. Multi-word names first, so "haitian creole" is found before
# "creole". Bare "asl" is left out: too many other things are called that.
LANGUAGES = (
    "american sign language", "sign language", "haitian creole",
    "spanish", "french", "german", "italian", "portuguese", "mandarin",
    "cantonese", "chinese", "japanese", "korean", "vietnamese", "tagalog",
    "filipino", "arabic", "russian", "hindi", "urdu", "bengali", "punjabi",
    "gujarati", "tamil", "telugu", "farsi", "persian", "dari", "pashto",
    "turkish", "polish", "ukrainian", "hebrew", "greek", "creole", "somali",
    "amharic", "tigrinya", "swahili", "yoruba", "igbo", "thai", "khmer",
    "lao", "hmong", "burmese", "nepali", "dutch", "swedish", "norwegian",
    "danish", "finnish", "czech", "hungarian", "romanian", "serbian",
    "croatian", "bosnian", "albanian", "armenian",
)

# Names that cover each other. A posting that asks for "Chinese" is answered
# by Mandarin or Cantonese, and the reverse.
_ALIASES = {
    "chinese": {"mandarin", "cantonese"},
    "mandarin": {"chinese"},
    "cantonese": {"chinese"},
    "tagalog": {"filipino"},
    "filipino": {"tagalog"},
    "farsi": {"persian"},
    "persian": {"farsi"},
    "sign language": {"american sign language"},
    "american sign language": {"sign language"},
    "creole": {"haitian creole"},
    "haitian creole": {"creole"},
}

_NAMES = "|".join(re.escape(n) for n in LANGUAGES)
_LANG = rf"\b({_NAMES})\b"

# A language named as something a person does with it. "Polish the deck" or
# "Spanish Fork, UT" has none of these beside it.
_CUE = (r"\b(?:fluen\w*|bilingual|proficien\w*|speak\w*|native|conversational|"
        r"verbal|written|oral|read\w*|writ\w*|interpret\w*|translat\w*)")
_NEAR = r"(?:\W+\w+){0,3}?\W+"
_SPOKEN = [
    re.compile(_CUE + _NEAR + r"(?:in\W+)?" + _LANG),
    re.compile(_LANG + r"(?:\W+\w+){0,2}?\W+"
               r"(?:speak\w*|speaker|fluen\w*|proficien\w*|language|"
               r"bilingual|required|is\s+required|a\s+must|skills?)\b"),
    re.compile(r"\benglish\s*(?:/|and|&|\+)\s*" + _LANG),
    re.compile(_LANG + r"\s*(?:/|and|&|\+)\s*english\b"),
]

# Bilingual with no language named. Required when a requirement word sits
# beside it, or when it is in the title.
_BILINGUAL = re.compile(r"\bbilingual\b")
_MUST = re.compile(r"(?:required|requirement|must|mandatory|need\w*|"
                   r"essential)")
_SOFT = re.compile(r"(?:preferred|a\s+plus|is\s+a\s+plus|desired|"
                   r"nice\s+to\s+have|bonus|helpful|advantage|encouraged)")
_WINDOW = 60

# Job words only French uses. "Analyste" and "d'affaires" never appear in an
# English title, and "technicien" is not how anyone spells technician.
_FRENCH_TITLE = re.compile(
    r"\b(?:analyste|d['’]affaires|gestionnaire|conseill[eè]re?|"
    r"d[ée]veloppeu(?:r|se)|ing[ée]nieure?|technicienn?e?|"
    r"coordonnat(?:eur|rice)|charg[ée]e? de|adjointe?|"
    r"sp[ée]cialiste|responsable de|agente? de)\b")

_memo: dict = {"data": None, "spoken": frozenset()}


def spoken() -> frozenset[str]:
    """Every language the user's profile says they speak, English included."""
    data = _profile.load("master.toml")
    if data is _memo["data"]:
        return _memo["spoken"]
    texts: list[str] = []
    for skill in data.get("skill", []):
        if isinstance(skill, dict):
            texts += [str(skill.get(k) or "") for k in ("term", "detail",
                                                        "category")]
    for holder in (data, data.get("identity") or {}):
        listed = holder.get("languages") or []
        texts += [str(x) for x in (listed if isinstance(listed, list)
                                   else [listed])]
    hay = " ".join(texts).lower()
    found = {"english"} | {m.group(1) for m in re.finditer(_LANG, hay)}
    for name in list(found):
        found |= _ALIASES.get(name, set())
    _memo["data"], _memo["spoken"] = data, frozenset(found)
    return _memo["spoken"]


def _soft_near(text: str, start: int, end: int) -> bool:
    return bool(_SOFT.search(text[max(0, start - 25):end + _WINDOW]))


def required(title: str, binding: str, tail: str) -> tuple[list[str], list[str]]:
    """Languages a posting asks for, as (required, preferred).

    `binding` is the requirement half of the body and `tail` the preferred
    half (score._binding_text and _preferred_tail). A bare "bilingual" comes
    back as "bilingual".
    """
    title, binding, tail = title.lower(), binding.lower(), tail.lower()
    must: list[str] = []
    nice: list[str] = []

    def add(bucket: list[str], name: str) -> None:
        if name not in must and name not in nice:
            bucket.append(name)

    # A language in the title is the job.
    for m in re.finditer(_LANG, title):
        add(must, m.group(1))
    # So is a title written in one. "Analyste d'affaires technique /
    # Technical Business Analyst" is a Quebec posting, wherever the board
    # says it is.
    if _FRENCH_TITLE.search(title):
        add(must, "french")
    if _BILINGUAL.search(title):
        add(must, "bilingual")

    for text, soft_half in ((binding, False), (tail, True)):
        named_at: list[tuple[int, int]] = []
        for rx in _SPOKEN:
            for m in rx.finditer(text):
                name = m.group(1)
                named_at.append(m.span())
                soft = soft_half or _soft_near(text, *m.span())
                add(nice if soft else must, name)
        for m in _BILINGUAL.finditer(text):
            if any(s <= m.start() < e for s, e in named_at):
                continue
            around = text[max(0, m.start() - _WINDOW):m.end() + _WINDOW]
            if soft_half or _soft_near(text, *m.span()):
                add(nice, "bilingual")
            elif _MUST.search(around):
                add(must, "bilingual")
    return must, nice


_ASKS = re.compile(_LANG + r"|\bbilingual\b|\bfluen")


def asks(phrase: str) -> bool:
    """Whether a phrase is about speaking a language. score.py uses it to
    leave such dealbreaker phrases to the language gate, which has already
    zeroed any posting that needs one the user does not speak."""
    return bool(_ASKS.search(phrase.lower()))


def unspoken(names: list[str]) -> list[str]:
    """The names in `names` the user does not speak. "bilingual" counts as
    spoken when the profile has a language besides English."""
    have = spoken()
    out = []
    for name in names:
        if name == "bilingual":
            if len(have - {"english"}) == 0:
                out.append(name)
        elif name not in have:
            out.append(name)
    return out
