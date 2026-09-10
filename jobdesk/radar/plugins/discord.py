"""Discord delivery (the push surface, plan item 3).

A webhook, deliberately -- not a bot with a gateway connection like the news
bot. This pipeline is run-once and stateless: it starts, works, and exits, so
there is no long-lived process to hold a connection. A webhook is a plain POST
through the same `http.py` choke point every other request uses, which means
the rate-limiting and real User-Agent come for free and no new dependency is
added (still just `requests`).

Set DISCORD_WEBHOOK_URL in .env to turn this on. Unset, push() is a no-op and
the run just writes Notion + the digest as before -- the surface is additive,
never required.

Use a webhook on a channel of its own, separate from the DOMAIN EXPANSION news
feed, so job pings don't bury the news (and vice versa).
"""

from __future__ import annotations

import os
import time

from .. import config, render
from ..models import Job

_ENV_WEBHOOK = "DISCORD_WEBHOOK_URL"


def push(jobs: list[Job], stats: dict, new_only: list[Job], log=print) -> int:
    """Post the run to Discord. Returns the number of messages sent.

    Returns 0 without touching the network when the webhook is unset or when
    there is nothing at or above C-tier -- a quiet run stays quiet.
    """
    webhook = os.getenv(_ENV_WEBHOOK)
    if not webhook:
        return 0

    messages = render.discord_messages(jobs, stats, new_only)
    if not messages:
        log("  Discord: nothing above C-tier, no ping sent")
        return 0

    from .. import http as _http     # local import keeps the module importable
    url = f"{webhook}?wait=true"     # without requests for the no-webhook path
    sent = 0
    # ?wait=true makes Discord answer 200 with the created message instead of a
    # bare 204, so a failure shows up in resp.status_code instead of passing
    # silently. block_on_429=False so a benign webhook 429 comes back as a
    # Response we can retry with the real retry_after, rather than blocking
    # discord.com for the rest of the run.
    for i, payload in enumerate(messages):
        resp = _http.post(url, json=payload, spacing=0.8, timeout=20,
                          block_on_429=False)
        if resp is not None and resp.status_code == 429:
            retry = _retry_after(resp)
            log(f"  Discord: rate-limited, waiting {retry:.1f}s")
            time.sleep(retry)
            resp = _http.post(url, json=payload, spacing=0.8, timeout=20,
                              block_on_429=False)
        if resp is not None and resp.status_code < 300:
            sent += 1
        else:
            code = getattr(resp, "status_code", "?")
            detail = resp.text[:200] if resp is not None else "no response"
            log(f"  Discord: message {i + 1}/{len(messages)} failed ({code}) -- {detail}")
    log(f"  Discord: sent {sent}/{len(messages)} message(s)")
    return sent


def _retry_after(resp) -> float:
    """Seconds to wait after a 429, from the body or the header. Bounded."""
    try:
        val = float(resp.json().get("retry_after", 0))
    except (ValueError, AttributeError):
        val = 0.0
    if not val:
        try:
            val = float(resp.headers.get("Retry-After", 0))
        except (ValueError, TypeError):
            val = 0.0
    return min(max(val, 1.0), 30.0)


def configured() -> bool:
    config.load_env()
    return bool(os.getenv(_ENV_WEBHOOK))


# The plugin interface (see plugins/__init__.py). `push` keeps its own name and
# return value because the manual `tests/test_radar.py discord` check reads the
# message count back.
NAME = "Discord"


def deliver(jobs: list[Job], stats: dict, new_only: list[Job], log=print) -> None:
    push(jobs, stats, new_only, log=log)
