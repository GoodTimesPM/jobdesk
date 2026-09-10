"""Which address on this machine a phone can actually reach.

`--host` used to be something you looked up and typed. That is fine once and
wrong forever after: an address is a fact about the machine at the moment it
boots, and the one place it has to be right is a task that runs at logon with
nobody watching. A scheduled task holding a literal `100.x.y.z` fails silently
on the first day that address changes, and the failure looks like "the phone
stopped working" rather than "the bind failed".

So `--host auto` resolves at launch, every launch, and it is deliberately picky
about what it will accept:

  * a **tailnet** address (`100.64.0.0/10`, the CGNAT range Tailscale and
    friends hand out) is preferred, because that network is already private and
    the access token is a second lock rather than the only one;
  * a **private LAN** address (RFC 1918) is the fallback, and it is a real
    fallback rather than a consolation — a phone and a desktop on the same home
    wifi is the ordinary case — but it is announced differently, because "your
    home network" and "the coffee shop's network" are the same sentence to a
    laptop;
  * anything else, including a public address, is **never** chosen. If neither
    of the two above exists, `auto()` raises. Guessing here would put the ledger
    on whatever network happened to be attached.

`0.0.0.0` remains available and remains spelled out in full. It is the one
answer this module will not reach on its own, because "every interface" is a
decision, not a discovery.
"""

from __future__ import annotations

import ipaddress
import socket
import time

TAILNET = ipaddress.ip_network("100.64.0.0/10")

# Spelled out rather than asked for with `.is_private`, which is a much broader
# question than it sounds: Python counts the documentation and benchmarking
# ranges as private too, so `203.0.113.7` and `198.18.0.1` both answer True. An
# address being reserved is not the same as it being your house, and the whole
# point of this module is refusing to guess about that.
RFC1918 = tuple(ipaddress.ip_network(n) for n in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class NoAddress(RuntimeError):
    """Raised when nothing on this machine is a safe thing to bind."""


def _candidates() -> list[str]:
    """Every IPv4 address this machine answers to, best effort.

    Two sources, because neither is complete on Windows. `getaddrinfo` on the
    hostname finds the addresses DNS knows about and routinely misses a
    Tailscale interface; the UDP trick finds whichever interface the default
    route would use and misses everything else. No packet is sent — connecting a
    UDP socket only picks a route.
    """
    found: list[str] = []

    def add(value: str) -> None:
        if value and value not in found:
            found.append(value)

    try:
        for *_, sockaddr in socket.getaddrinfo(socket.gethostname(), None,
                                               socket.AF_INET):
            add(sockaddr[0])
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.2)
            s.connect(("10.255.255.255", 1))
            add(s.getsockname()[0])
    except OSError:
        pass

    return found


def tailnet() -> str | None:
    """This machine's tailnet address, or None if it is not on one."""
    for value in _candidates():
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version == 4 and address in TAILNET:
            return value
    return None


def lan() -> str | None:
    """This machine's RFC 1918 address, or None.

    Only the three ranges a home or office network actually hands out. Loopback
    and link-local fall outside them anyway, which is correct twice over: an
    address another device cannot reach is not an answer to this question.
    """
    for value in _candidates():
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version == 4 and any(address in net for net in RFC1918):
            return value
    return None


# `_candidates` opens a socket and asks DNS, and `is_this_machine` is called
# from a route the console polls every 1.5 seconds. What it answers changes only
# when an interface comes or goes, so it is cached for a minute rather than
# recomputed per request.
_LOCAL_CACHE: tuple[float, frozenset[str]] | None = None
_LOCAL_TTL_S = 60.0


def local_addresses() -> frozenset[str]:
    """Every address this machine answers to, cached for a minute."""
    global _LOCAL_CACHE
    now = time.monotonic()
    if _LOCAL_CACHE is not None and now - _LOCAL_CACHE[0] < _LOCAL_TTL_S:
        return _LOCAL_CACHE[1]
    found = frozenset(_candidates())
    _LOCAL_CACHE = (now, found)
    return found


def is_this_machine(host: str) -> bool:
    """True when an inbound peer address belongs to the machine we run on.

    Loopback is the obvious case and not the only one. When the server binds a
    tailnet or LAN address, a connection opened *on this machine* to that
    address gets that same address as its source — the kernel picks the
    interface it is routing out of — so the peer the server sees is its own
    address, not `127.0.0.1`. A check that only knows about loopback reads that
    as a stranger, which is how the console managed to lock out the desktop it
    was running on.

    This does not widen what a remote device can claim. A peer address is where
    the TCP handshake's replies go, so a machine across the network cannot
    present this machine's own address and still complete a connection: the
    replies would be delivered here rather than to it.

    An address that will not parse is not this machine, same as everywhere else
    in this module — the unknown case fails towards asking for a token.
    """
    host = (host or "").strip().strip("[]")
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        if ipaddress.ip_address(host).is_loopback:
            return True
    except ValueError:
        return False
    return host in local_addresses()


def auto() -> tuple[str, str]:
    """The address to bind and which kind it is: `("100.1.2.3", "tailnet")`.

    Raises `NoAddress` rather than returning a public address or a guess.
    """
    address = tailnet()
    if address:
        return address, "tailnet"
    address = lan()
    if address:
        return address, "lan"
    raise NoAddress(
        "no tailnet or private LAN address on this machine, so there is "
        "nothing safe to bind.\n\n"
        "Either connect to a network — Tailscale is the one worth having, "
        "since it works off your home wifi too — or pass --host with the "
        "address you mean."
    )


def resolve(host: str | None) -> tuple[str, str]:
    """Turn whatever `--host` said into an address and a label.

    Anything that is not the literal string `auto` is passed straight through;
    this is a convenience, not a policy layer. `access.check` still decides
    whether the resulting bind is allowed.
    """
    if host is None:
        return "127.0.0.1", "loopback"
    if host.strip().lower() == "auto":
        return auto()
    host = host.strip()
    if host in ("0.0.0.0", "::"):
        return host, "every interface"
    return host, "given"


def advice(kind: str) -> str:
    """One line about what the chosen network means. Empty when it means nothing."""
    # A tailnet bind stays deliberately silent. Every caller prints this as a
    # warning, and "this is working correctly" printed behind a warning sign is
    # how a panel teaches people to stop reading it. The good news about a
    # tailnet is said by the Tailscale block instead, where it is not a warning.
    if kind == "lan":
        return ("this address only exists inside your building. The phone can "
                "reach it on the same wifi and nowhere else — not on cellular, "
                "not from work. It is also your local network rather than a "
                "private one, so the token is the only lock. Tailscale fixes "
                "both.")
    if kind == "every interface":
        return ("0.0.0.0 is every network this machine ever joins, including "
                "ones you did not choose. Prefer a tailnet address.")
    return ""
