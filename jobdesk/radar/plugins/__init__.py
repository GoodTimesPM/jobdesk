"""Delivery plugins. Optional ways a finished run leaves the machine.

JobDesk's own window is where a run is read now. Discord and Notion came
first, back when the pipeline was a scheduled task with no screen of its own,
and they still earn their keep -- a phone notification at 7 AM, a tracker
shared with someone else -- but nothing in the radar depends on either one.

So they live here, behind one interface. A plugin is a module with three
names:

    NAME          what to call it in the log
    configured()  True when its credentials are present
    deliver(jobs, stats, new_only, log)   do the work

`deliver()` is only called when `configured()` says yes, and an exception
inside one plugin is logged and swallowed so the next one still runs. A failed
webhook must never cost you the seen.json write that happens after delivery.

To add one, drop a module in this folder and put it in PLUGINS.
"""

from __future__ import annotations

from . import discord, notion

# Notion first: it writes the tracker rows, and if only one of the two gets
# through on a flaky morning it should be the durable one, not the ping.
PLUGINS = (notion, discord)


def deliver(jobs, stats, new_only, log=print) -> None:
    """Hand the run to every configured plugin. Never raises."""
    for plugin in PLUGINS:
        try:
            if not plugin.configured():
                log(f"  {plugin.NAME}: not configured, skipping")
                continue
            plugin.deliver(jobs, stats, new_only, log=log)
        except Exception as exc:  # a delivery is never worth losing a run over
            log(f"  {plugin.NAME}: failed -- {exc.__class__.__name__}: {exc}")
