"""Getting the job description text, in order of least effort.

1. Job Radar already has it -- it fetched the body when it scored the posting.
2. Fetch the URL once. One page, one request, a real User-Agent, no crawl.
   This is the same page you are about to open in a browser anyway.
3. Ask for a paste. Opens Notepad on a prepared file and waits; that beats
   reading a multi-line paste from a Windows console, which mangles long text.

Step 3 is always available, which is the important property: no posting is
un-appliable because a board renders its JD in JavaScript.
"""

from __future__ import annotations

import re
import subprocess
import webbrowser
from pathlib import Path

from . import config

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_SCRIPT = re.compile(r"<(script|style|noscript|svg)\b.*?</\1>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_BLANK = re.compile(r"\n{3,}")
_SPACES = re.compile(r"[ \t]+")

_ENTITIES = {
    "&amp;": "&", "&nbsp;": " ", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&#39;": "'", "&rsquo;": "'", "&lsquo;": "'", "&ldquo;": '"',
    "&rdquo;": '"', "&mdash;": "-", "&ndash;": "-", "&bull;": "\n- ",
    "&#x27;": "'", "&#x2F;": "/",
}

_TEMPLATE = """\
# Paste the full job description below this line, then SAVE and CLOSE Notepad.
# Everything from the posting: responsibilities, requirements, preferred
# qualifications. Leaving the headings in is what lets the engine tell a
# requirement apart from a nice-to-have.
# Lines starting with # are ignored.
# ---------------------------------------------------------------------------

"""


def html_to_text(html: str) -> str:
    text = _SCRIPT.sub(" ", html)
    text = re.sub(r"<(br|/p|/div|/li|/h[1-6])\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<li\b[^>]*>", "\n- ", text, flags=re.I)
    text = _TAG.sub(" ", text)
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    text = text.replace("•", "\n- ").replace("\r", "")
    text = _SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK.sub("\n\n", text).strip()


def fetch(url: str, timeout: int = 20) -> tuple[str, str]:
    """One polite GET. Returns (text, note) -- text is "" on any failure."""
    if not url.lower().startswith(("http://", "https://")):
        return "", "not an http(s) URL"
    try:
        import requests
    except ImportError:
        return "", "requests isn't installed, so no fetch was attempted"
    try:
        response = requests.get(
            url, timeout=timeout,
            headers={"User-Agent": _UA,
                     "Accept": "text/html,application/xhtml+xml",
                     "Accept-Language": "en-US,en;q=0.9"},
        )
    except Exception as exc:                       # requests raises its own tree
        return "", f"fetch failed ({type(exc).__name__})"
    if response.status_code != 200:
        return "", f"fetch returned HTTP {response.status_code}"
    text = html_to_text(response.text)
    if len(text) < config.MIN_JD_CHARS:
        return "", (f"the page came back with only {len(text)} characters of "
                    f"text -- almost certainly rendered by JavaScript")
    return text, f"fetched {len(text)} characters from the posting"


def paste(url: str = "", prefill: str = "") -> str:
    """Open the posting, then collect the JD through Notepad.

    `subprocess.run` on notepad blocks until the window is closed, which is
    the whole trick: it gives a real editor for a long paste and a natural
    "I'm done" signal, with no dependency and no console mangling.
    """
    path: Path = config.SCRATCH / "paste_jd.txt"
    path.write_text(_TEMPLATE + prefill, encoding="utf-8")
    if url:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    subprocess.run(["notepad.exe", str(path)], check=False)
    body = path.read_text(encoding="utf-8-sig", errors="replace")
    kept = [ln for ln in body.splitlines() if not ln.lstrip().startswith("#")]
    return "\n".join(kept).strip()


def obtain(*, cached: str = "", url: str = "", allow_fetch: bool = True,
           allow_paste: bool = True, echo=print) -> tuple[str, str]:
    """The whole ladder. Returns (text, how)."""
    if cached and len(cached) >= config.MIN_JD_CHARS:
        return cached, f"Job Radar's cached JD ({len(cached)} chars)"

    if allow_fetch and url:
        echo(f"  fetching the posting...")
        text, note = fetch(url)
        echo(f"  {note}")
        if text:
            return text, note

    if allow_paste:
        echo("  opening the posting in your browser and Notepad for the paste")
        echo("  (paste the JD, save, close Notepad to continue)")
        text = paste(url, prefill=cached)
        if len(text) >= config.MIN_JD_CHARS:
            return text, f"pasted by hand ({len(text)} chars)"
        if text:
            return text, (f"pasted by hand, but only {len(text)} chars -- "
                          f"short JDs tailor badly")
    return "", "no JD text available"
