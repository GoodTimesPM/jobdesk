"""Discord delivery for overdue follow-ups (plan item 10's push surface).

A webhook, same shape as jobdesk/radar/discord.py -- copied rather than
imported, per job-search/PROJECT.md's sub-project isolation rule. This
pipeline is run-once and stateless (the CLI, or the scheduled `auto` task
three times a day), so there is no long-lived process to hold a bot
connection.

Set DISCORD_WEBHOOK_URL in this project's own .env to turn this on -- its
own var, not the radar's, for the same reason this project has its own
SWITCH.txt. Unset, push() is a no-op and nothing here touches the network;
`log` still surfaces overdue follow-ups on screen exactly as before.
"""

from __future__ import annotations

import os

from . import config
from .applog import Application

_ENV_WEBHOOK = "DISCORD_WEBHOOK_URL"


def push(due: list[Application], echo=print) -> list[Application]:
    """Post one message per overdue follow-up. Returns the ones actually sent.

    The caller is expected to pass `Log.unpinged_follow_ups_due()`, not the
    raw due list, and to call `Log.mark_follow_up_pinged()` on whatever comes
    back -- that is what keeps a follow-up firing once per due date rather
    than once per run. This function itself makes no assumption about that;
    it just sends what it's given.
    """
    if not due:
        return []
    config.load_env()
    webhook = os.getenv(_ENV_WEBHOOK)
    if not webhook:
        return []

    try:
        import requests
    except ImportError:
        echo("  Discord: requests isn't installed, follow-up push skipped")
        return []

    sent: list[Application] = []
    for app in due:
        payload = {
            "content": (f":alarm_clock: Follow-up due -- **{app.company}** "
                        f"-- {app.role}\napplied {app.applied_on}, follow up "
                        f"due {app.follow_up_due}\n{app.id}"),
        }
        try:
            resp = requests.post(webhook, json=payload, timeout=10)
        except Exception as exc:                    # requests raises its own tree
            echo(f"  Discord: follow-up push failed ({type(exc).__name__})")
            continue
        if resp.status_code < 300:
            sent.append(app)
        else:
            echo(f"  Discord: follow-up push failed ({resp.status_code})")
    if sent:
        echo(f"  Discord: {len(sent)}/{len(due)} follow-up ping(s) sent")
    return sent


def is_configured() -> bool:
    config.load_env()
    return bool(os.getenv(_ENV_WEBHOOK))
