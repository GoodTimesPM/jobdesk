"""Defensive HTTP.

The news bot learned this the hard way with Reddit: hammer a public endpoint
from a residential IP and you get the whole IP rate-limited. Every request in
this project goes through here so the politeness rules are impossible to
forget in a new source module.

Rules enforced:
  * a real browser User-Agent (Nasdaq, Yahoo and most ATS CDNs reject the
    default `python-requests/x.y` string outright)
  * a minimum spacing between calls TO THE SAME HOST
  * bounded retries with backoff, and a hard stop on 429 for that host
  * a global timeout, so a hung endpoint can't stall the scheduled run
"""

from __future__ import annotations

import random
import threading
import time
from urllib.parse import urlparse

import requests

# Verify TLS against the Windows certificate store instead of certifi's static
# bundle. Several employer careers sites -- jobs.virginia.gov among them --
# serve only their leaf certificate and expect the client to fetch the missing
# intermediate from the AIA extension. Browsers and curl do that; certifi
# can't, so those hosts fail verification in Python and nowhere else. Optional
# import: without it everything still works except those few hosts.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_TIMEOUT = 25
DEFAULT_SPACING = 1.5      # seconds between calls to one host
MAX_RETRIES = 3

_session = requests.Session()
_session.headers.update({
    "User-Agent": BROWSER_UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
})

_lock = threading.Lock()
_last_call: dict[str, float] = {}
_blocked: set[str] = set()          # hosts that 429'd -- skipped for this run


class RateLimited(Exception):
    """Raised when a host has 429'd and been cut off for the rest of the run."""


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def _wait_turn(host: str, spacing: float) -> None:
    with _lock:
        last = _last_call.get(host, 0.0)
        gap = time.time() - last
        if gap < spacing:
            time.sleep(spacing - gap)
        _last_call[host] = time.time()


def request(method: str, url: str, *, spacing: float = DEFAULT_SPACING,
            timeout: int = DEFAULT_TIMEOUT, retries: int = MAX_RETRIES,
            block_on_429: bool = True,
            **kwargs) -> requests.Response | None:
    """Return a Response, or None if the call failed after retries.

    Never raises for network problems -- a dead source must not take the whole
    run down with it. Sources are expected to treat None as "no jobs today".

    On a 429 the default is to cut the host off for the rest of the run
    (`block_on_429=True`) -- the right call for a source we're politely
    reading. Set `block_on_429=False` for an endpoint whose 429 is benign and
    carries an accurate `retry_after`, e.g. a Discord webhook: then the 429
    Response is returned to the caller to honour the retry hint itself.
    """
    host = _host(url)
    if host in _blocked:
        raise RateLimited(host)

    for attempt in range(retries):
        _wait_turn(host, spacing)
        try:
            resp = _session.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException:
            if attempt == retries - 1:
                return None
            time.sleep(2 ** attempt + random.random())
            continue

        if resp.status_code == 429:
            if not block_on_429:
                return resp
            # Cut this host off for the rest of the run rather than digging in.
            _blocked.add(host)
            raise RateLimited(host)
        if resp.status_code >= 500 and attempt < retries - 1:
            time.sleep(2 ** attempt + random.random())
            continue
        return resp
    return None


def get(url: str, **kwargs) -> requests.Response | None:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs) -> requests.Response | None:
    return request("POST", url, **kwargs)


def get_json(url: str, **kwargs):
    """GET and parse JSON, or None. Sources use this for the happy path."""
    resp = get(url, **kwargs)
    if resp is None or resp.status_code >= 400:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def post_json(url: str, **kwargs):
    resp = post(url, **kwargs)
    if resp is None or resp.status_code >= 400:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def blocked_hosts() -> list[str]:
    return sorted(_blocked)


def reset() -> None:
    """Clear rate-limit state. For tests / repeated runs in one process."""
    _blocked.clear()
    _last_call.clear()
