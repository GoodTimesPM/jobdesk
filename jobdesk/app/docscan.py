"""Reading a dropped-in document for skills the profile does not show yet.

Setup reads one resume. People have more than that: a capstone deck, a
certificate, a notebook, a screenshot of a course page, code. This reads any
of those and lists the skills the shared vocabulary finds in it, each with
the line it came from, so the person decides what is true before anything is
kept. No AI is involved. Text comes out of the file by its format, images go
through the OCR engine Windows ships with, and skills are the same matcher
the resume engine tailors with.

What is kept goes to `evidence.toml` in the profile folder, a file this
module owns. targeting.toml and master.toml are never written here. A kept
skill counts as had in the missing-qualifications list and the Growth tab;
adding it to the score is a separate step on the Criteria page, through the
same Save every other criteria change goes through.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
import threading
import tomllib
import zipfile
from datetime import date
from pathlib import Path

from .. import profile
from . import resume_import, skillgap

EVIDENCE = skillgap.EVIDENCE
# The server takes 8 MB of JSON, and base64 makes a file a third bigger.
MAX_BYTES = 6 * 1024 * 1024

_TEXT = {".txt", ".md", ".text", ".csv", ".log", ".rtf", ".tex"}
_IMAGES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff"}
# A file's own language is evidence even when its text never names it: a
# .sql file is SQL whether or not it says so.
_CODE = {
    ".py": "python", ".ipynb": "python", ".sql": "sql", ".ps1": "powershell",
    ".js": "javascript", ".mjs": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".jsx": "javascript", ".html": "html", ".htm": "html", ".css": "css", ".r": "r",
    ".java": "java", ".cs": "c#", ".cpp": "c++", ".c": "c", ".go": "golang",
    ".rb": "ruby", ".sh": "bash", ".bat": "batch", ".dax": "dax", ".m": "power query",
    ".json": "", ".xml": "", ".yaml": "", ".yml": "", ".toml": "",
}
_CERT = re.compile(r"\b(certif\w*|license[sd]?|licensure|credential)\b", re.I)
_LOCK = threading.Lock()


class Unreadable(ValueError):
    """The file cannot be read. The message says what to do instead."""


# --------------------------------------------------------------- extracting

def _zip_text(data: Path, prefix: str, tag: str) -> str:
    try:
        with zipfile.ZipFile(data) as z:
            names = sorted((n for n in z.namelist() if n.startswith(prefix) and n.endswith(".xml")),
                           key=lambda n: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", n)])
            parts = []
            for name in names:
                xml = z.read(name).decode("utf-8", "replace")
                # One line per paragraph, so a matched skill's line reads as
                # the slide wrote it.
                for para in re.split(r"</a:p>|</p>|</si>", xml):
                    runs = re.findall(rf"<{tag}[^>]*>([^<]*)</{tag}>", para)
                    if runs:
                        parts.append(html.unescape("".join(runs)))
            return "\n".join(parts)
    except zipfile.BadZipFile as exc:
        raise Unreadable(f"that file would not open ({exc})") from exc


def _ocr(path: Path) -> str:
    script = Path(__file__).with_name("ocr.ps1")
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
             "-Path", str(path)],
            capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unreadable(f"reading the text in that image failed ({exc})") from exc
    if done.returncode == 2:
        raise Unreadable("Windows has no text recognition language installed. Add one in "
                         "Settings > Time & language > Language, or drop in a PDF instead.")
    if done.returncode:
        raise Unreadable("that image would not open. Save it as .png or .jpg and try again.")
    return done.stdout.decode("utf-8", "replace")


def _pdf(path: Path) -> tuple[str, str]:
    try:
        import fitz
    except ImportError as exc:
        raise Unreadable("reading a PDF needs PyMuPDF: pip install -r requirements.txt") from exc
    try:
        with fitz.open(str(path)) as doc:
            text = "\n".join(page.get_text("text") for page in doc)
            if len(text.strip()) >= 40:
                return text, "pdf"
            # A scan, which is what most certificates are. Read the first few
            # pages as pictures.
            parts = []
            for page in list(doc)[:3]:
                with tempfile.TemporaryDirectory() as tmp:
                    image = Path(tmp) / "page.png"
                    page.get_pixmap(dpi=200).save(str(image))
                    parts.append(_ocr(image))
            return "\n".join(parts), "scanned pdf"
    except Unreadable:
        raise
    except Exception as exc:
        raise Unreadable(f"that PDF would not open ({exc})") from exc


def extract(name: str, path: Path) -> tuple[str, str]:
    """(text, what kind of file it was read as)."""
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        return _pdf(path)
    if suffix == ".docx":
        try:
            return resume_import._read_docx(path), "word"
        except resume_import.UnreadableResume as exc:
            raise Unreadable(str(exc)) from exc
    if suffix == ".pptx":
        return _zip_text(path, "ppt/slides/slide", "a:t"), "powerpoint"
    if suffix == ".xlsx":
        return _zip_text(path, "xl/sharedStrings", "t"), "excel"
    if suffix in _IMAGES:
        return _ocr(path), "image"
    if suffix == ".ipynb":
        try:
            nb = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except ValueError as exc:
            raise Unreadable(f"that notebook is not valid JSON ({exc})") from exc
        cells = nb.get("cells", [])
        return "\n".join("".join(c.get("source", [])) if isinstance(c.get("source"), list)
                         else str(c.get("source", "")) for c in cells), "notebook"
    if suffix in _TEXT or suffix in _CODE:
        return path.read_text(encoding="utf-8", errors="replace"), "code" if suffix in _CODE else "text"
    if suffix in (".doc", ".ppt", ".xls"):
        raise Unreadable(f"{suffix} is an old Office format. Open it and save it as "
                         f"{suffix}x, then drop that in.")
    raise Unreadable(f"{suffix or 'that file'} is not a format JobDesk can read. Use a PDF, "
                     f"Word, PowerPoint, Excel, image, text or code file.")


# ---------------------------------------------------------------- scanning

def _snippet(text: str, pattern: re.Pattern) -> str:
    for line in text.splitlines():
        if pattern is not None and pattern.search(line):
            line = " ".join(line.split())
            return line if len(line) <= 160 else line[:157] + "..."
    return ""


def scan(name: str, data: bytes) -> dict:
    """What one file shows, against what the profile already has."""
    if not data:
        raise Unreadable(f"{name} is empty")
    if len(data) > MAX_BYTES:
        raise Unreadable(f"{name} is over 6 MB. Drop in a smaller export of it.")
    suffix = Path(name).suffix.lower() or ".txt"
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    temp = Path(handle.name)
    try:
        handle.write(data)
        handle.close()
        text, kind = extract(name, temp)
    finally:
        temp.unlink(missing_ok=True)

    k = skillgap.known()
    vocab = k["vocab"]
    found: dict[str, dict] = {}
    for tid in skillgap._find(k, text):
        found[tid] = {"term": tid, "label": vocab.label(tid), "kind": vocab.kind(tid),
                      "have": tid in k["have"],
                      "snippet": _snippet(text, vocab.pattern(tid))}
    language = _CODE.get(suffix, "")
    if language:
        hits = skillgap._find(k, language)
        for tid in hits:
            found.setdefault(tid, {"term": tid, "label": vocab.label(tid), "kind": vocab.kind(tid),
                                   "have": tid in k["have"],
                                   "snippet": f"written in {language} ({name})"})
        if not hits:
            found[language] = {"word": language, "label": language, "kind": "tool",
                               "have": skillgap._has_word(k["corpus"], language),
                               "snippet": f"written in {language} ({name})"}

    # Names the vocabulary does not know, said at least twice so a stray
    # capital is not offered as a skill.
    counts: dict[str, int] = {}
    for m in skillgap._PRODUCT.finditer(text):
        token = m.group(1)
        low = token.lower()
        if (token in skillgap._NOT_PRODUCTS or low in k["known_words"]
                or token in skillgap.jdmod._STOP):
            continue
        counts[token] = counts.get(token, 0) + 1
    for token, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if n < 2 or len(found) > 80:
            continue
        found.setdefault(token.lower(), {
            "word": token, "label": token, "kind": "name",
            "have": skillgap._has_word(k["corpus"], token),
            "snippet": _snippet(text, re.compile(rf"(?<![a-z0-9]){re.escape(token.lower())}(?![a-z0-9])"))})

    certs = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if 6 <= len(line) <= 140 and _CERT.search(line) and line not in certs:
            certs.append(line)
    # The word the Criteria stack would use for it, if the person adds it
    # to the score: the alias the file itself used, so "Airflow/dbt" found
    # through "dbt" adds dbt, not Airflow.
    for item in found.values():
        term = vocab.terms.get(item.get("term", ""))
        pattern = vocab.pattern(item["term"]) if term else None
        hit = pattern.search(text) if pattern is not None else None
        item["stack"] = (hit.group(0).lower() if hit else term.aliases[0]) if term else item["label"].lower()
    items = sorted(found.values(), key=lambda f: (f["have"], f["kind"] == "name", f["label"].lower()))
    return {"filename": name, "read_as": kind, "chars": len(text),
            "items": items, "certificates": certs[:10]}


# ------------------------------------------------------------- keeping it

def _path() -> Path:
    return profile.path(EVIDENCE)


def load() -> dict:
    try:
        with _path().open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return {"skill": [], "certificate": []}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise Unreadable(f"{EVIDENCE} could not be read ({exc}). Fix or delete it.") from exc
    return {"skill": list(data.get("skill", [])), "certificate": list(data.get("certificate", []))}


def _q(value) -> str:
    # A JSON string is a valid TOML basic string, escapes and all.
    return json.dumps(str(value), ensure_ascii=False)


def _write(data: dict) -> None:
    lines = [
        "# Skills and certificates found in documents added on the Criteria tab.",
        "# JobDesk writes this file. Each entry was ticked by you after reading the",
        "# line it came from. A skill here counts as one you have in the missing",
        "# qualifications list and on the Growth tab. It does not change the score",
        "# or the resume; add it to your stack in Criteria for that.",
        "",
    ]
    for entry in data["skill"]:
        lines.append("[[skill]]")
        for key in ("term", "word", "label", "source", "added", "snippet"):
            if entry.get(key):
                lines.append(f"{key} = {_q(entry[key])}")
        lines.append("")
    for entry in data["certificate"]:
        lines.append("[[certificate]]")
        for key in ("text", "source", "added"):
            if entry.get(key):
                lines.append(f"{key} = {_q(entry[key])}")
        lines.append("")
    path = _path()
    temp = path.with_suffix(".toml.tmp")
    temp.write_text("\n".join(lines), encoding="utf-8")
    temp.replace(path)


def _key(entry: dict) -> str:
    if entry.get("term"):
        return "t:" + entry["term"]
    return "w:" + (entry.get("word") or "").lower()


def keep(skills: list[dict], certificates: list[dict]) -> dict:
    """Add what the person ticked. Returns the whole file as it now stands."""
    k = skillgap.known()
    today = date.today().isoformat()
    with _LOCK:
        data = load()
        have = {_key(e) for e in data["skill"]}
        for item in skills:
            term = str(item.get("term") or "")
            word = str(item.get("word") or "").strip()[:60]
            if term and term not in k["vocab"].terms:
                continue
            if not term and not word:
                continue
            entry = {"term": term, "word": "" if term else word,
                     "label": k["vocab"].label(term) if term else word,
                     "source": str(item.get("source") or "")[:120],
                     "added": today, "snippet": str(item.get("snippet") or "")[:200]}
            if _key(entry) not in have:
                have.add(_key(entry))
                data["skill"].append(entry)
        texts = {c.get("text") for c in data["certificate"]}
        for cert in certificates:
            text = " ".join(str(cert.get("text") or "").split())[:160]
            if text and text not in texts:
                texts.add(text)
                data["certificate"].append({"text": text, "source": str(cert.get("source") or "")[:120],
                                            "added": today})
        _write(data)
        return data


def forget(term: str = "", word: str = "", text: str = "") -> dict:
    with _LOCK:
        data = load()
        if term or word:
            key = _key({"term": term, "word": word})
            data["skill"] = [e for e in data["skill"] if _key(e) != key]
        if text:
            data["certificate"] = [c for c in data["certificate"] if c.get("text") != text]
        _write(data)
        return data
