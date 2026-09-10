"""Digest rendering -- console, Markdown, and Discord.

Same split as the news bot's `report.py`: the pipeline produces a scored,
deduped list, and rendering is a pure function of that list. A new surface
(Discord below, a tray toast later) is a function here, not a change upstream.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from .models import Job

TIER_LABEL = {
    "A": "A - apply today",
    "B": "B - strong, worth reading",
    "C": "C - maybe, if the day is quiet",
    "D": "D - probably not",
    "F": "F - no",
}

# Discord embed colours per tier (the left stripe), so A-tier reads as "go"
# at a glance on a phone. Decimal ints, as the webhook API wants.
_TIER_COLOR = {
    "A": 0x2ECC71,   # green
    "B": 0x3498DB,   # blue
    "C": 0xF1C40F,   # yellow
    "D": 0xE67E22,   # orange
    "F": 0x95A5A6,   # gray
}


def _group(jobs: list[Job]) -> dict[str, list[Job]]:
    out: dict[str, list[Job]] = {}
    for job in jobs:
        out.setdefault(job.tier, []).append(job)
    return out


# Flags worth showing as a chip, in the order they should read, mapped to
# their short label. Everything else in `job.flags` stays out of the chip row
# -- the point of chips is that they are scannable, which stops being true
# past about six of them.
_CHIP_FLAGS = [
    ("entry-level", "Entry level"),
    ("no-experience-required", "Will train"),
    ("equivalency-accepted", "Degree accepted"),
    ("low-competition", "Low competition"),
    ("high-competition", "Crowded"),
    ("staffing-agency", "Via agency"),
    ("hybrid", "Hybrid"),
    ("stretch-experience", "Stretch"),
    ("advanced-degree-req", "Wants Master's"),
    ("off-hours-availability", "Off-hours"),
    ("second-language-required", "Bilingual"),
    ("physical-labor", "Physical"),
    ("driving-required", "Driving"),
    ("ghost-suspect", "Possible ghost"),
    ("stale", "Stale"),
]


def chips(job: Job) -> list[str]:
    """The scannable attribute row, hiring.cafe's job-card idea.

    Their cards lead with typed attributes -- Remote, $70-90k, Entry Level --
    rather than prose, which is why a page of results can be read at a glance.
    Our `reasons` are full sentences explaining the score, which is the right
    thing for auditing a verdict and the wrong thing for triaging twenty of
    them. This is the complement, not a replacement: chips to decide what to
    open, reasons to understand why it scored.
    """
    out: list[str] = []
    if job.remote or "remote" in job.location.lower():
        out.append("Remote")
    elif job.location:
        out.append(job.location)
    if job.salary_text:
        out.append(job.salary_text)
    if job.job_family and job.job_family not in ("unclassified", "other-field"):
        out.append(job.job_family)

    flags = set(job.flags)
    for flag, label in _CHIP_FLAGS:
        if flag in flags:
            out.append(label)
    return out


def console(jobs: list[Job], stats: dict, new_only: list[Job]) -> str:
    lines: list[str] = []
    add = lines.append
    add("=" * 72)
    add(f"  JOB RADAR - {datetime.now():%A %d %B %Y, %H:%M}")
    add("=" * 72)

    total = sum(stats.get("sources", {}).values())
    add(f"  {total} postings pulled | {len(jobs)} after dedupe | "
        f"{len(new_only)} new since last run")
    if stats.get("rate_limited"):
        add(f"  rate-limited: {', '.join(stats['rate_limited'])}")
    if stats.get("errors"):
        add(f"  {len(stats['errors'])} source error(s) - see the log")
    add("")

    grouped = _group(new_only)
    for tier in ("A", "B", "C"):
        bucket = grouped.get(tier)
        if not bucket:
            continue
        add(f"--- {TIER_LABEL[tier]} ({len(bucket)}) " + "-" * 24)
        for job in bucket:
            add("")
            add(f"  [{job.score}] {job.title}")
            add(f"        {job.company} | {job.location or 'location n/a'}"
                + (f" | {job.salary_text}" if job.salary_text else ""))
            age = job.age_days
            meta = [job.source]
            if age is not None:
                meta.append(f"{age:.0f}d old")
            add(f"        {' | '.join(meta)}")
            # ASCII separator on purpose. The Windows console codepage cannot
            # encode a middot, and `main.log` swallows UnicodeEncodeError --
            # so a prettier bullet here would silently drop the digest line
            # rather than fail loudly. Markdown and Discord are UTF-8 and do
            # use the nicer separator.
            add(f"        {' | '.join(chips(job))}")
            add(f"        why: {'; '.join(job.reasons[:4])}")
            add(f"        {job.url}")
        add("")

    if not any(grouped.get(t) for t in ("A", "B", "C")):
        add("  Nothing above C-tier today. That is a normal outcome -- the")
        add("  filter is doing its job. Check data/digests for the full list.")
    add("=" * 72)
    return "\n".join(lines)


def markdown(jobs: list[Job], stats: dict, new_only: list[Job]) -> str:
    lines: list[str] = []
    add = lines.append
    add(f"# Job Radar - {datetime.now():%Y-%m-%d %H:%M}")
    add("")
    total = sum(stats.get("sources", {}).values())
    add(f"**{total}** pulled - **{len(jobs)}** after dedupe - "
        f"**{len(new_only)}** new since last run")
    add("")

    grouped = _group(new_only)
    for tier in ("A", "B", "C", "D"):
        bucket = grouped.get(tier)
        if not bucket:
            continue
        add(f"## {TIER_LABEL[tier]} ({len(bucket)})")
        add("")
        for job in bucket:
            add(f"### [{job.score}] [{job.title}]({job.url})")
            bits = [job.company, job.location or "location n/a"]
            if job.salary_text:
                bits.append(job.salary_text)
            age = job.age_days
            if age is not None:
                bits.append(f"{age:.0f}d old")
            bits.append(job.source)
            add("*" + " - ".join(bits) + "*")
            add("")
            add("`" + "`  `".join(chips(job)) + "`")
            add("")
            if job.flags:
                add(f"> flags: {', '.join(job.flags)}")
                add("")
            add("- " + "\n- ".join(job.reasons[:6]))
            add("")
        add("")

    add("## Run detail")
    add("")
    add("| source | postings |")
    add("| --- | --- |")
    for name, count in sorted(stats.get("sources", {}).items(),
                              key=lambda kv: -kv[1]):
        add(f"| {name} | {count} |")
    if stats.get("errors"):
        add("")
        add("### Errors")
        for err in stats["errors"]:
            add(f"- `{err.splitlines()[0][:160]}`")
    return "\n".join(lines)


# -- Discord --------------------------------------------------------------
#
# Webhook, not a bot: this pipeline is run-once and stateless, so there's no
# process to hold a gateway connection. A webhook POST goes through the same
# http.py choke point as every other request and needs no extra dependency.
#
# Design for the phone. One embed per job (A/B/C only), coloured by tier, so a
# green card is "apply today" without reading a word. The title links straight
# to the posting; tap it, you're on the apply page.

_MAX_EMBEDS_PER_MESSAGE = 10     # Discord's hard cap
_MAX_JOBS_TO_PUSH = 20           # a wall of 40 embeds helps no one; digest has the rest
_EMBED_DESC_LIMIT = 4096
_FIELD_VALUE_LIMIT = 1024


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _valid_link(url: str) -> bool:
    """Discord rejects an embed whose `url` is not a well-formed URL, and one
    bad embed 400s the whole batch (up to 10 jobs). Guard it: require http(s)
    and a host with a dot, so a malformed source URL just drops the hyperlink
    instead of dropping nine good postings alongside it.
    """
    try:
        parts = urlparse(url or "")
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and "." in parts.netloc


def _job_embed(job: Job) -> dict:
    """One posting as a Discord embed dict."""
    fields = [{
        "name": "Company",
        "value": _clip(job.company or "?", _FIELD_VALUE_LIMIT),
        "inline": True,
    }, {
        "name": "Location",
        "value": _clip(job.location or "n/a", _FIELD_VALUE_LIMIT),
        "inline": True,
    }]
    if job.salary_text:
        fields.append({"name": "Salary",
                       "value": _clip(job.salary_text, _FIELD_VALUE_LIMIT),
                       "inline": True})
    meta = [job.source]
    age = job.age_days
    if age is not None:
        meta.append(f"{age:.0f}d old")
    if job.flags:
        meta.extend(job.flags)

    # Chips lead, prose follows: on a phone the embed description is the only
    # part read before deciding whether to tap through.
    row = chips(job)
    body = ("**" + "** · **".join(row) + "**\n") if row else ""
    body += "; ".join(job.reasons[:4])

    embed = {
        "title": _clip(f"[{job.score}] {job.title}", 256),
        "color": _TIER_COLOR.get(job.tier, _TIER_COLOR["F"]),
        "description": _clip(body, _EMBED_DESC_LIMIT),
        "fields": fields,
        "footer": {"text": _clip(" | ".join(meta), 2048)},
    }
    if _valid_link(job.url):
        embed["url"] = job.url
    return embed


def discord_messages(jobs: list[Job], stats: dict, new_only: list[Job]) -> list[dict]:
    """Webhook payloads to POST, in order. Empty list = nothing worth pinging.

    A header message (plain content) leads, then embeds batched 10 at a time.
    Returns [] when there is nothing at or above C-tier, so a quiet run stays
    quiet instead of pinging the channel with "nothing today".
    """
    worth = [j for j in new_only if j.tier in ("A", "B", "C")]
    if not worth:
        return []
    worth.sort(key=lambda j: -j.score)
    worth = worth[:_MAX_JOBS_TO_PUSH]

    total = sum(stats.get("sources", {}).values())
    n_a = sum(1 for j in worth if j.tier == "A")
    header = (f"**Job Radar** - {datetime.now():%A %d %b, %H:%M}\n"
              f"{total} pulled - {len(jobs)} after dedupe - "
              f"**{len(worth)}** worth reading"
              + (f" (**{n_a}** apply-today)" if n_a else ""))
    if len(new_only) > len(worth):
        header += f"\n_{len(new_only) - len(worth)} more below C-tier in the digest._"

    messages: list[dict] = [{"content": header}]
    for i in range(0, len(worth), _MAX_EMBEDS_PER_MESSAGE):
        batch = worth[i:i + _MAX_EMBEDS_PER_MESSAGE]
        messages.append({"embeds": [_job_embed(j) for j in batch]})
    return messages
