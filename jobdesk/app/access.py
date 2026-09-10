"""Who may reach JobDesk, once it stops being reachable only from here.

For its whole life this server bound `127.0.0.1`, and that was the entire
access control story. Nothing off this machine could open a socket to it, so
there was nothing to check.

Phone access breaks the second half of that sentence. The moment the server
binds anything but loopback, "nothing off this machine" stops being true, and
JobDesk is not a small thing to hand a stranger: it holds a resume, a phone
number, an email address, every application sent and every posting considered.

So there is exactly one new rule, and it fails closed:

    binding a non-loopback address requires JOBDESK_ACCESS_TOKEN to be set.

Not a warning, not a default that can be left in place -- the server refuses
to start. A JobDesk quietly serving a resume to whoever else is on the coffee
shop's wifi is the one failure here that cannot be walked back.

The token is a bearer secret and is treated as one: compared in constant time,
carried in an HttpOnly cookie so no script on the page can read it back out,
and never logged. It is not a password and there are no accounts. One person,
one secret; a rotation is a new line in `.env`.

The intended transport is a tailnet -- Tailscale, WireGuard, whatever puts the
phone and the desktop on one private network -- not a port forwarded from a
router. On a tailnet the network is already doing the hard half of the work
and this token is the second lock. On the open internet it would be the only
one, in front of a page that knows where you live.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
import time

from .. import paths

TOKEN_ENV = "JOBDESK_ACCESS_TOKEN"

# HttpOnly, so `document.cookie` cannot read it and a script on the page cannot
# post it somewhere. SameSite=Lax, so another site cannot ride it. No `Secure`:
# a tailnet address is plain http, and setting Secure would mean the cookie is
# never stored and the phone loops through the link forever.
COOKIE = "jobdesk_key"
COOKIE_MAX_AGE_S = 60 * 60 * 24 * 90

# The query parameter the QR code carries. One use only: the first request from
# a phone, which trades it for the cookie and then redirects to a clean URL, so
# the token stops living in the address bar and in the browser's history.
PARAM = "k"


def token() -> str | None:
    """The configured access token, or None if there is not one."""
    paths.load_env(".env.app", ".env")
    value = os.environ.get(TOKEN_ENV, "")
    return value.strip() or None


def mint() -> str:
    """A new token worth pasting into `.env`. 32 bytes, url-safe."""
    return secrets.token_urlsafe(32)


def is_loopback(host: str) -> bool:
    """True for the addresses that mean 'this machine and nowhere else'.

    `localhost` is included by name because that is how people type it, and
    anything that will not parse as an address is treated as *not* loopback --
    the unknown case has to fail towards asking for a token.
    """
    host = (host or "").strip().strip("[]")
    if host.lower() in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def matches(supplied: str | None) -> bool:
    """Constant-time comparison against the configured token.

    False when nothing is configured, which is the safe answer: the only code
    path that reaches this has already decided a token is required.
    """
    real = token()
    if not real or not supplied:
        return False
    return secrets.compare_digest(supplied, real)


class Unconfigured(RuntimeError):
    """Raised when a non-loopback bind is asked for with no token set."""


def check(host: str) -> bool:
    """Decide whether this bind needs a token, refusing the unsafe combination.

    Returns True when the server should enforce the token, False when it is
    loopback-only and nothing changes. Raises `Unconfigured` rather than
    starting an open server.
    """
    if is_loopback(host):
        return False
    if not token():
        raise Unconfigured(
            f"binding {host} would put JobDesk on the network, and "
            f"{TOKEN_ENV} is not set.\n\n"
            f"Add this line to {paths.ROOT / '.env'} and try again:\n\n"
            f"    {TOKEN_ENV}={mint()}\n\n"
            "Or turn phone access on from the Setup tab, which writes the "
            "line, opens the firewall and draws the QR code in one step."
        )
    return True


def cookie_header(value: str) -> str:
    """The Set-Cookie line that turns one scanned link into a paired device."""
    return (f"{COOKIE}={value}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age={COOKIE_MAX_AGE_S}")


def from_request(cookie_line: str, query_token: str | None) -> str | None:
    """The token a request is presenting, from the URL first then the cookie.

    URL first on purpose: that is the order that lets a rotated token take
    effect by rescanning the new QR code, rather than by clearing site data on
    a phone.
    """
    if query_token:
        return query_token
    for part in (cookie_line or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value or None
    return None


# -- who has actually arrived ----------------------------------------------
#
# The failure this exists for has no error message at either end. The server
# binds a network address, answers on it from this machine, reports the
# firewall rule as present, and the phone still sits on a blank tab until it
# gives up. Every fact the panel had was a fact about the desktop, and the
# desktop was fine. The one fact nobody was recording is the one that splits
# the problem in half: has a request from another device reached this process
# at all?
#
# If none has, the packets are dying before the server sees them and the
# causes are all network-shaped -- a phone on a different subnet, a router
# isolating wireless clients from wired ones, a VPN on the phone routing every
# address out to the internet. If one has and it was turned away, the network
# is fine and the token is wrong, which is a different fix and a much smaller
# one.

_ARRIVALS: dict[str, dict] = {}
ARRIVALS_MAX = 8
SINCE = time.time()


def note_arrival(host: str, accepted: bool) -> None:
    """Record that a request from off this machine reached the gate.

    Keyed by address, so a phone reloading forty times is one entry that counts
    to forty rather than forty entries. Loopback is not recorded: it is this
    machine, and this machine reaching itself was never in question.
    """
    if not host or is_loopback(host):
        return
    seen = _ARRIVALS.get(host)
    if seen is None:
        if len(_ARRIVALS) >= ARRIVALS_MAX:
            oldest = min(_ARRIVALS, key=lambda k: _ARRIVALS[k]["at"])
            _ARRIVALS.pop(oldest, None)
        seen = _ARRIVALS[host] = {"host": host, "first": time.time(),
                                  "at": 0.0, "hits": 0, "accepted": 0,
                                  "refused": 0}
    seen["at"] = time.time()
    seen["hits"] += 1
    seen["accepted" if accepted else "refused"] += 1
    seen["ok"] = accepted


def arrivals() -> list[dict]:
    """Every device off this machine that has reached the gate, newest first."""
    return sorted(_ARRIVALS.values(), key=lambda r: r["at"], reverse=True)
