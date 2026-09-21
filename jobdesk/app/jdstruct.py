"""Turning a stored job description back into something readable.

A JD arrives in one of three shapes and the page has to render all three:

1. **HTML.** Greenhouse, Lever and Ashby hand back the employer's markup, so
   the structure is already there in `<p>`, `<ul><li>` and a bold paragraph
   standing in for a heading. This is the easy case and the best one.
2. **Text with line breaks.** Someone pasted it, or a fetch flattened the tags
   but kept the newlines. Bullets still start with a dash or a dot.
3. **One run-on paragraph.** Workday is the reason this module exists. Its API
   returns the whole posting with every newline collapsed, so "Primary
   Responsibilities Design, develop, implement, and support integrations..."
   is a single 6,000-character line, and the table rendered it as the grey wall
   of text this replaces.

A posting can also arrive with its bullets intact as characters but its line
breaks gone, which is what Adzuna passes on. That is shape 3 with the answer
already written into it, and it is handled by splitting on the dot rather than
by guessing at sentences.

For shape 3 the headings are recovered by name, since there is a fixed
vocabulary of them and every ATS template draws from it. Inside a section whose
heading marks a list, each sentence is shown as its own item. That last step is
the one heuristic here worth stating out loud: the sentences *were* list items
before the newlines were stripped, and putting one per line reconstructs the
shape rather than inventing it. Nothing is reworded and nothing is dropped, so
the worst case is a paragraph shown as several short lines.

The output is a list of blocks the page renders directly:

    {"kind": "heading", "text": ...}
    {"kind": "para",    "text": ...}
    {"kind": "list",    "items": [...]}
"""

from __future__ import annotations

import html
import re

# --- shape detection -------------------------------------------------------

_HAS_TAGS = re.compile(r"<(p|div|ul|ol|li|br|h[1-6]|strong)\b", re.I)
_SCRIPT = re.compile(r"<(script|style|noscript|svg)\b.*?</\1>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")

# Our own markers, carried through the tag strip so the line parser can tell a
# heading and a list item apart from a paragraph without re-reading the HTML.
_H = "\x01"
_B = "\x02"

_BULLET_CHARS = "-*•·●▪‣⁃∙◦"
_BULLET = re.compile(r"^\s*[" + re.escape(_BULLET_CHARS) + r"]\s+")
_NUMBERED = re.compile(r"^\s*\(?\d{1,2}[.)]\s+")

# Bullets that survived the flattening as characters in the middle of a line:
#
#   Include: • Design, develop, and maintain Power BI reports • Build
#   reporting solutions on established Dataverse data models • ...
#
# Adzuna does this to every posting it passes on, and the list-from-sentences
# heuristic below cannot help: the sentences are already marked as items, they
# just have no line breaks around them. A dot bullet in running prose is not a
# thing people write, so splitting on it reconstructs the list rather than
# guessing at one.
#
# The hyphen and the asterisk are deliberately not in here. They start a bullet
# at the beginning of a line and mean nothing of the sort inside one, where
# they are ranges and footnotes and multiplication. Nor is the middle dot,
# which is how a posting writes "Richmond · VA · Full-time".
_INLINE_BULLET = re.compile(r"\s*[•●▪‣⁃∙◦]+\s*")

# --- headings --------------------------------------------------------------

# The vocabulary. Every ATS template draws its section names from roughly this
# set, which is what makes recovering them from flattened text possible at all.
_HEADING_PHRASES = (
    "a day in the life", "about the company", "about the role", "about the team",
    "about this role", "about us", "additional information", "basic qualifications",
    "benefits and perks", "benefits", "bonus points", "compensation and benefits",
    "compensation", "core responsibilities", "duties and responsibilities",
    "education and experience", "eeo statement",
    "equal employment opportunity", "equal opportunity employer",
    "essential duties and responsibilities", "essential duties",
    "essential functions", "experience required", "how you will make an impact",
    "job description", "job duties", "job requirements", "job summary",
    "key responsibilities", "knowledge skills and abilities",
    "minimum qualifications", "nice to have", "our mission", "overview",
    "pay range", "perks and benefits", "physical requirements",
    "position overview", "position summary", "preferred qualifications",
    "preferred skills", "primary responsibilities", "qualifications",
    "required qualifications", "required skills and experience",
    "required skills", "requirements", "responsibilities",
    "role responsibilities", "salary range", "skills and qualifications",
    "the opportunity", "the role", "what we offer", "what we are looking for",
    "what we're looking for", "what you bring", "what you will be doing",
    "what you will do", "what you'll bring", "what you'll do", "what you need",
    "who we are", "who you are", "why join us", "work environment",
    "your impact", "your responsibilities",
)


def _phrase_pattern(phrase: str) -> str:
    """One heading phrase as a regex, tolerant of how it was typed.

    Spaces match hyphens too, and a straight apostrophe matches a curly one or
    none at all, so "What You'll Do", "What Youll Do" and "What You’ll Do"
    are the same heading.
    """
    parts = [re.escape(word).replace("'", "['’]?") for word in phrase.split()]
    return r"[\s\-]+".join(parts)


# Longest first, so "What You'll Bring" never matches as "What You".
#
# The alternation is case-insensitive and the rest of the pattern deliberately
# is not. `re.I` over the whole expression was a real bug: it let the `[A-Z(]`
# lookahead match a lowercase letter, so "translate business requirements into
# scalable solutions" split into a heading and a sentence fragment.
_HEADING_RE = re.compile(
    r"(?<![A-Za-z])((?i:"
    + "|".join(_phrase_pattern(p)
               for p in sorted(_HEADING_PHRASES, key=len, reverse=True))
    # A digit is as good a start to a section as a capital letter: "Required
    # Qualifications 3+ years of SQL" is the single most common way a flattened
    # posting opens a requirements list.
    + r"))\s*:?\s*(?=[A-Z(0-9]|$)"
)

# A heading that promises a list. Sentences under one of these get shown one
# per line; sentences under "About Us" stay a paragraph, because they are one.
_LIST_HEADING = re.compile(
    r"responsibilit|qualification|requirement|skills|duties|experience|"
    r"what you|you will|benefits|perks|we offer|nice to have|bonus|"
    r"essential|competenc|abilities",
    re.I,
)

# --- text repair -----------------------------------------------------------

# Postings arrive with a cp1252 apostrophe that lost its encoding somewhere
# upstream, so `Toast's` is stored as `Toast�s`. Between two letters the
# original character is always an apostrophe; anywhere else it is unknowable
# and gets dropped rather than guessed at.
_BAD_APOSTROPHE = re.compile(r"(?<=[A-Za-z])�(?=[a-z])")
_SENTENCE = re.compile(r"(?<=[.!?])[ \t]+(?=[\"'(“]?[A-Z0-9])")
_SPACES = re.compile(r"[ \t ]+")


def clean(text: str) -> str:
    """Whitespace and mojibake, without touching a single word."""
    text = _BAD_APOSTROPHE.sub("’", text or "")
    text = text.replace("�", "").replace("\r", "")
    return _SPACES.sub(" ", text).strip()


# --- HTML ------------------------------------------------------------------

def _from_html(raw: str) -> list[str]:
    """Markup to marked-up lines. Structure survives; tags do not."""
    text = _SCRIPT.sub(" ", raw)

    # A paragraph that is entirely bold is a heading. Every ATS rich-text
    # editor produces them, because a person writing a posting reaches for the
    # bold button rather than for a heading level.
    text = re.sub(r"<(p|div)[^>]*>\s*<(strong|b)[^>]*>(.*?)</\2>\s*:?\s*</\1>",
                  "\n" + _H + r"\3\n", text, flags=re.I | re.S)
    text = re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]\s*>", "\n" + _H + r"\1\n",
                  text, flags=re.I | re.S)
    text = re.sub(r"<li[^>]*>", "\n" + _B, text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|ul|ol|tr|h[1-6])\s*>", "\n", text, flags=re.I)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    return [clean(line) for line in text.split("\n")]


# --- flattened text --------------------------------------------------------

def _unflatten(text: str) -> list[str]:
    """Recover headings from a posting whose newlines were stripped.

    A real heading keeps its title casing when the newline around it is lost,
    and an incidental mention of the same words does not: "Primary
    Responsibilities Design, develop..." is a heading, "gather business
    requirements and translate them" is a sentence. Capitalisation is the only
    signal left once the line breaks are gone, so it is the one used.
    """
    def mark(match: re.Match) -> str:
        phrase = match.group(1).strip()
        if not phrase[:1].isupper():
            return match.group(0)
        return "\n" + _H + phrase + "\n"

    return [clean(line) for line in _HEADING_RE.sub(mark, text).split("\n")]


def _looks_like_heading(line: str) -> bool:
    """A standalone line that is a section title rather than a sentence."""
    if not line or len(line) > 80:
        return False
    stripped = line.rstrip(":").strip()
    if not stripped or stripped[-1] in ".!?,;":
        return False
    if _HEADING_RE.fullmatch(stripped) or _HEADING_RE.fullmatch(stripped + " "):
        return True
    if line.rstrip().endswith(":") and len(stripped.split()) <= 9:
        return True
    # ALL CAPS on its own line, with at least one real word in it.
    letters = [c for c in stripped if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters) and len(letters) > 3


# --- assembly --------------------------------------------------------------

def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text) if s.strip()]


def _blocks(lines: list[str]) -> list[dict]:
    out: list[dict] = []
    pending: list[str] = []
    # Whether the heading currently in force promises a list. Sentence-splitting
    # a run-on paragraph is only defensible underneath one of those.
    listy = False

    def flush() -> None:
        if pending:
            out.append({"kind": "list", "items": list(pending)})
            pending.clear()

    for line in lines:
        if not line:
            continue
        if line.startswith(_H):
            flush()
            title = line[1:].strip().rstrip(":").strip()
            if title:
                out.append({"kind": "heading", "text": title})
                listy = bool(_LIST_HEADING.search(title))
            continue
        if line.startswith(_B) or _BULLET.match(line) or _NUMBERED.match(line):
            item = _NUMBERED.sub("", _BULLET.sub("", line.lstrip(_B)))
            if item.strip():
                pending.append(item.strip())
            continue
        # A line carrying its own bullets. Two of them, because one dot in a
        # line is as likely to be decoration as a list of exactly one item.
        pieces = _INLINE_BULLET.split(line)
        if len(pieces) >= 3:
            lead, items = pieces[0].strip(), [p.strip() for p in pieces[1:] if p.strip()]
            flush()
            if lead:
                # The words ahead of the first bullet title the list, unless
                # there is already a heading over it: "RESPONSIBILITIES
                # Include: * ..." splits into both, and the one that names the
                # section is the one that was there first. Promoting the
                # second would cost the first, since _tidy reads two headings
                # in a row as the first having titled nothing.
                titled = bool(out) and out[-1]["kind"] == "heading"
                if _looks_like_heading(lead) and not titled:
                    out.append({"kind": "heading", "text": lead.rstrip(":").strip()})
                    listy = bool(_LIST_HEADING.search(lead))
                else:
                    out.append({"kind": "para", "text": lead})
            pending.extend(items)
            continue

        if _looks_like_heading(line):
            flush()
            out.append({"kind": "heading", "text": line.rstrip(":").strip()})
            listy = bool(_LIST_HEADING.search(line))
            continue
        flush()
        # The run-on case: several sentences under a heading that names a list
        # were a list before the newlines were stripped out of them.
        parts = _sentences(line)
        if listy and len(parts) >= 3 and len(line) > 200:
            out.append({"kind": "list", "items": parts})
        else:
            out.append({"kind": "para", "text": line})

    flush()
    return out


def _tidy(blocks: list[dict]) -> list[dict]:
    """Drop empties, and headings that turned out to head nothing."""
    kept: list[dict] = []
    for block in blocks:
        if block["kind"] == "list":
            items = [i for i in block["items"] if len(i) > 1]
            if items:
                kept.append({"kind": "list", "items": items})
        elif block.get("text"):
            kept.append(block)
    while kept and kept[-1]["kind"] == "heading":
        kept.pop()
    out: list[dict] = []
    for block in kept:
        # Two headings in a row means the first one titled nothing.
        if out and out[-1]["kind"] == "heading" and block["kind"] == "heading":
            out[-1] = block
            continue
        out.append(block)
    return out


def structure(raw: str) -> list[dict]:
    """The whole thing. Raw stored JD in, renderable blocks out."""
    if not raw or not raw.strip():
        return []
    if _HAS_TAGS.search(raw):
        lines = _from_html(raw)
    elif raw.count("\n") >= 3:
        lines = [clean(line) for line in html.unescape(raw).split("\n")]
    else:
        # Workday hands back text with the entities still in it -- "2&#43;
        # years" -- because it escaped the markup it had already stripped.
        lines = _unflatten(clean(html.unescape(raw)))
    return _tidy(_blocks(lines))


def plain(raw: str) -> str:
    """The same content as flat text, for anything that wants a string."""
    parts: list[str] = []
    for block in structure(raw):
        if block["kind"] == "heading":
            parts.append("\n" + block["text"].upper())
        elif block["kind"] == "list":
            parts.extend("- " + item for item in block["items"])
        else:
            parts.append(block["text"])
    return "\n".join(parts).strip()
