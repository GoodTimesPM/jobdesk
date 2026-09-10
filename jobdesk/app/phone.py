"""Phone access as one switch, instead of five things you do in order.

Almost everything this file needs already existed. `net.auto()` finds an
address a phone can reach, `access.mint()` makes a token, `autostart.install()`
registers the logon task, `firewall.state()` knows whether the packets get
through, and `qr.svg()` draws the result. What did not exist was a way to get
all of it without leaving JobDesk: the honest instructions were mint a token,
open `.env` in an editor, paste it, save, run a PowerShell command as
administrator, then register a scheduled task. Six steps, two of which are
"edit a credential file correctly" and "elevate", and all six happen at the
desk, which is the machine you are sitting at *because* you are about to walk
away from it.

So: one button. `turn_on()` does what it can, `turn_off()` undoes the one that
matters, and `state()` answers "is this on, and what is the address" well
enough to draw the panel with no other call.

Two rules this module will not bend on, both about the credential file:

  * **It writes a token only when there is not one.** A request that can
    rewrite `.env` is a request that can lock a paired phone out by accident,
    and the accident looks like the feature working. If a token is already
    configured, `turn_on()` uses it and leaves the file untouched.
  * **It appends one line and rewrites nothing.** No parse, no reformat, no
    round trip through a dict. `.env` here holds the Discord webhook and the
    Notion token beside the access token, and a file that is only ever appended
    to cannot lose the line above.

`rotate()` is the deliberate exception to the first rule and honours the
second. Changing the token by accident is the failure the rule guards against;
changing it on purpose is a thing a person needs to be able to do from the
panel, because the alternative is opening a credential file in an editor.

The one thing this cannot do for you is the firewall. Adding a rule needs
administrator rights, and a page that could elevate itself would be worse than
an unreachable phone, so the panel reports the rule and hands over the exact
command. See `firewall.py`, where that argument is made in full.
"""

from __future__ import annotations

import os
import socket
import time

from .. import paths
from . import access, autostart, firewall, net, qr

DEFAULT_PORT = 8765
ENV_PATH = paths.ROOT / ".env"

# When this process came up. `arrivals` is empty both when nothing has ever
# connected and when the server restarted a second ago, and those two readings
# call for opposite reactions, so the panel is given the time to say which.
SINCE = time.time()


def url(port: int = DEFAULT_PORT) -> str | None:
    """The address to open on the phone, token and all, or None.

    None means one of the two halves is missing, no reachable address or no
    token, and `state()` says which.
    """
    key = access.token()
    if not key:
        return None
    try:
        address, _ = net.auto()
    except net.NoAddress:
        return None
    return f"http://{address}:{port}/?{access.PARAM}={key}"


def svg(port: int = DEFAULT_PORT) -> str | None:
    """The URL as a QR code, or None when there is no URL to draw."""
    target = url(port)
    return qr.svg(target, ec="M") if target else None


def _ensure_token() -> tuple[str, bool]:
    """The access token, minting and appending one only if there is none.

    Returns the token and whether this call created it.
    """
    existing = access.token()
    if existing:
        return existing, False

    minted = access.mint()
    # Appended, never rewritten. See the module docstring. The leading newline
    # is conditional because a file that already ends in one would otherwise
    # grow a blank line every time this ran, and a file that does *not* end in
    # one would otherwise get the token glued to the end of the last value.
    body = ""
    try:
        body = ENV_PATH.read_text(encoding="utf-8-sig")
    except OSError:
        pass
    lead = "" if (not body or body.endswith("\n")) else "\n"
    with ENV_PATH.open("a", encoding="utf-8") as handle:
        handle.write(lead + "\n# Phone access. Written by JobDesk.\n"
                     + access.TOKEN_ENV + "=" + minted + "\n")
    # `paths.load_env` uses setdefault, so a stale value already in this
    # process's environment would win over the line just written, and the QR
    # code would carry a token nothing accepts.
    os.environ[access.TOKEN_ENV] = minted
    return minted, True


def rotate(port: int = DEFAULT_PORT) -> dict:
    """Mint a new token, replace the old line in `.env`, redraw the QR code.

    This is the one write here that is not an append, and the rule it appears
    to break, never change a token out from under a device using it, is the
    rule it exists to serve. Rotating *is* the act of logging every paired
    device out at once, and it is what you want the moment a token has been
    read over your shoulder or carried out of the house on a phone that is not
    coming back. So it is its own function behind its own button, and never a
    side effect of the switch.

    Only lines beginning `JOBDESK_ACCESS_TOKEN=` are touched, and the result is
    moved into place atomically. Both matter, because this file also holds the
    Discord webhook and the Notion token, and that is the only copy.
    """
    fresh = access.mint()
    try:
        lines = ENV_PATH.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        lines = []
    prefix = access.TOKEN_ENV + "="
    kept = [ln for ln in lines if not ln.strip().startswith(prefix)]
    kept += ["", "# Phone access. Written by JobDesk.", prefix + fresh]
    tmp = ENV_PATH.with_suffix(ENV_PATH.suffix + ".new")
    tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
    os.replace(tmp, ENV_PATH)
    os.environ[access.TOKEN_ENV] = fresh
    return {**state(port), "minted": True, "rotated": True}


def _reachable(address: str, port: int) -> bool:
    """Whether something is already answering on that address and port."""
    try:
        with socket.socket() as probe:
            probe.settimeout(0.4)
            return probe.connect_ex((address, port)) == 0
    except OSError:
        return False


def _neighbourhood(address: str | None) -> str:
    """The first three octets of an IPv4 address, as a prefix to compare with.

    Deliberately not a subnet mask. Reading the real prefix length means asking
    Windows, and getting it wrong in the other direction is worse: telling
    someone their phone is on the wrong network when it is not sends them off
    to reconfigure a router that was fine. Three octets is what a home router
    hands out, and the panel says so as a likelihood rather than a rule.
    """
    parts = (address or "").split(".")
    return ".".join(parts[:3]) + "." if len(parts) == 4 else ""


def state(port: int = DEFAULT_PORT) -> dict:
    """Everything the panel draws, in one call.

    Nothing here raises. A machine with no tailnet and no LAN is a normal
    machine on a plane, and the panel's job in that case is to say so rather
    than to be an error.
    """
    task = autostart.describe()
    key = access.token()

    address = kind = None
    problem = ""
    try:
        address, kind = net.auto()
    except net.NoAddress as exc:
        problem = str(exc)

    return {
        "on": bool(task),
        "port": port,
        "token_set": bool(key),
        "address": address,
        "kind": kind,
        "advice": net.advice(kind) if kind else "",
        "url": url(port),
        # The QR code travels with the state, rather than being added by the
        # route that reads it. `turn_on`, `turn_off` and `rotate` all return
        # `{**state(port), ...}`, and when the code lived in the route only the
        # GET carried it: the panel drew a code on the way in and then blanked
        # it the instant you pressed the switch, which is the one moment the
        # code is wanted. Anything that reports the state now reports the code.
        "svg": svg(port),
        # A task Task Scheduler will kill after three days is installed but not
        # doing the job, and the panel should not call that "on" without saying
        # so. See `autostart.unlimited`.
        "unlimited": autostart.unlimited(task) if task else True,
        "state": task.get("state", "") if task else "",
        "last_run": task.get("last_run", "") if task else "",
        "serving": bool(address) and _reachable(address, port),
        # The half `serving` cannot see. That probe runs on this machine, and a
        # packet from this machine never meets the firewall, so a port that
        # answers here can still be a port the phone's request dies in front
        # of, with no error at either end. See `firewall.py`.
        "firewall": firewall.state(port),
        "firewall_fix": firewall.rule_command(port),
        "problem": problem,
        # Every other field here is a fact about this machine, and this machine
        # being healthy is exactly the state a phone that cannot connect leaves
        # it in. This one is a fact about the phone: has anything off this
        # machine reached the server at all. See `access.note_arrival`.
        "arrivals": access.arrivals(),
        "since": SINCE,
        "neighbourhood": _neighbourhood(address),
    }


def turn_on(port: int = DEFAULT_PORT) -> dict:
    """Mint a token if needed, bind the network address, install the task.

    Raises `net.NoAddress` when there is nothing safe to bind, which is the one
    failure worth stopping for: everything else here would succeed and produce
    a switch that is on and unreachable.
    """
    address, kind = net.auto()          # raises before anything is written
    _, minted = _ensure_token()

    # The running process binds the address itself. Installing the task alone
    # is not enough: it launches a *second* process, which finds this one
    # holding the port and stands down, which is correct, and which leaves the
    # network address unbound until the next time this process is not running.
    served = _reachable(address, port)
    if not served:
        try:
            from . import server
            server.serve_extra(address, port)
            served = True
        except OSError:
            # Something else has the address. The task below will try again at
            # the next logon, and the panel reports `serving: false` rather
            # than claiming the switch is working.
            served = False

    autostart.install(host="auto", port=port)
    return {**state(port), "minted": minted, "address": address, "kind": kind,
            "serving": served}


def turn_off(port: int = DEFAULT_PORT) -> dict:
    """Remove the logon task. The token and any paired phone survive.

    The address this process already bound stays bound until it exits, which is
    honest rather than sloppy: closing a socket out from under a phone that is
    mid-request is not an improvement, and "off" here means JobDesk stops
    coming up on the network, not that the window you are looking at
    disappears.
    """
    autostart.remove()
    return {**state(port), "minted": False}
