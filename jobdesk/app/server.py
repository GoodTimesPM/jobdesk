"""The local web server. Routes, static files, and the JSON envelope.

Loopback only, one user, no framework and no build step. `http.server` is
enough for a page that talks to one person on one machine, and it means a
fresh clone runs the UI with nothing installed.

Everything under /api/ returns JSON and is handled in `api.py`. Everything
else is a file out of `static/`. That is the entire routing rule.

Why HTML and not a desktop toolkit: the wizard has to accept a resume file,
and the tables have to render a few thousand rows with sorting and filtering.
A browser engine does both without being asked. `desktop.py` then wraps this
same server in a real window, so the desktop app and the browser tab are one
program and not two.
"""

from __future__ import annotations

import json
import mimetypes
import socket
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from . import access, api, net

STATIC = Path(__file__).resolve().parent / "static"
HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# Set by `serve`/`start_background` when the bind is not loopback. False means
# nothing off this machine can open a socket to us and the gate is dead code;
# True means every request is checked. It is module state rather than a handler
# argument because `ThreadingHTTPServer` constructs the handler class, not an
# instance we can pass anything to.
REQUIRE_TOKEN = False

# A request body is a resume or a form. Anything larger is a mistake or an
# attack, and reading it into memory unbounded is how a local server becomes
# a local denial of service.
MAX_BODY = 8 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "JobDesk"
    protocol_version = "HTTP/1.1"

    # BaseHTTPRequestHandler logs every request to stderr, which turns the
    # console into a scroll of 200s and buries the one line that matters.
    def log_message(self, fmt, *args):  # noqa: A002 - stdlib signature
        pass

    # -- plumbing ----------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The page never talks to anything but this server, so nothing here
        # needs a CDN, and saying so out loud means a stray <script src> from
        # a future edit fails loudly instead of quietly phoning home.
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError(f"request body is {length} bytes, over the "
                             f"{MAX_BODY} byte limit")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"body is not JSON: {exc}") from exc

    # -- the gate ----------------------------------------------------------

    def _peer(self) -> str:
        try:
            return self.client_address[0]
        except (AttributeError, IndexError):
            return ""

    def _allowed(self, parsed) -> bool:
        """Whether this request may proceed, and the paired-device side effect.

        Loopback-only servers never get here with `REQUIRE_TOKEN` set, so the
        cost on the ordinary desktop path is one boolean.

        A request that arrives with the right token in the URL is a phone that
        just scanned the QR code. It gets the cookie and a redirect to the same
        path without the token, so the secret stops living in the address bar
        and in the browser's history. After that the cookie carries it and the
        URL is clean.
        """
        if not REQUIRE_TOKEN:
            return True
        peer = self._peer()
        # A connection opened on this machine to our own LAN address arrives
        # with that address as its source, not 127.0.0.1. Treating that as a
        # stranger is how a server locks out the desktop it is running on.
        if net.is_this_machine(peer):
            return True

        query = parse_qs(parsed.query)
        supplied = access.from_request(self.headers.get("Cookie", ""),
                                       (query.get(access.PARAM) or [None])[0])
        ok = access.matches(supplied)
        access.note_arrival(peer, ok)
        if not ok:
            # 401 and a sentence, not a login form. There is one secret and it
            # lives in a QR code; a page with a password box would imply there
            # is something else to try.
            body = (b"JobDesk is not open to this device. Scan the QR code on "
                    b"the Setup tab of the desktop to pair it.")
            self._send(401, body, "text/plain; charset=utf-8")
            return False
        if query.get(access.PARAM):
            clean = parsed.path or "/"
            rest = {k: v for k, v in query.items() if k != access.PARAM}
            if rest:
                from urllib.parse import urlencode
                clean += "?" + urlencode(rest, doseq=True)
            self.send_response(303)
            self.send_header("Location", clean)
            self.send_header("Set-Cookie", access.cookie_header(supplied))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        return True

    # -- routing -----------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if not self._allowed(parsed):
            return
        if parsed.path.startswith("/api/"):
            self._dispatch("GET", parsed.path, parse_qs(parsed.query), {})
        else:
            self._static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self._allowed(parsed):
            return
        if not parsed.path.startswith("/api/"):
            self._json(404, {"error": "not found"})
            return
        try:
            body = self._body()
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._dispatch("POST", parsed.path, parse_qs(parsed.query), body)

    def _dispatch(self, method: str, path: str, query: dict, body: dict) -> None:
        handler = api.ROUTES.get((method, path))
        if handler is None:
            self._json(404, {"error": f"no route for {method} {path}"})
            return
        try:
            result = handler(query, body)
            if isinstance(result, api.Raw):
                self.send_response(200)
                self.send_header("Content-Type", result.content_type)
                self.send_header("Content-Length", str(len(result.body)))
                if result.filename:
                    # `attachment` rather than `inline`: a phone browser asked
                    # to display a .docx shows a blank tab, and the thing
                    # anyone wants with a packet file is a copy of it.
                    self.send_header("Content-Disposition",
                                     f'attachment; filename="{result.filename}"')
                self.end_headers()
                self.wfile.write(result.body)
                return
            self._json(200, result)
        except api.BadRequest as exc:
            # The user asked for something impossible and can fix it. Say what.
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            # Anything else is a bug in here, and the traceback belongs in the
            # console where it can be read, not swallowed into a 500 body.
            traceback.print_exc()
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def _manifest(self) -> None:
        """The home-screen manifest, built per request rather than served flat.

        `start_url` carries the access token when the request came from
        somewhere other than this machine. That is not a leak: this route sits
        behind the gate, so the only device that can read it is one already
        holding the token in a cookie. It is there because iOS gives a
        home-screen web app its own cookie jar, separate from the Safari tab
        the QR code was scanned in. Without the token in `start_url` the icon
        on the home screen opens to "JobDesk is not open to this device", which
        is the pairing working exactly as designed and looking exactly like it
        is broken.
        """
        key = None if net.is_this_machine(self._peer()) else access.token()
        start = f"/?{access.PARAM}={key}" if key else "/"
        body = json.dumps({
            "name": "JobDesk",
            "short_name": "JobDesk",
            "description": "The job radar, the resume engine and the packets, "
                           "on the phone you carry.",
            "start_url": start,
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#12151c",
            "theme_color": "#12151c",
            "icons": [
                {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png",
                 "purpose": "any"},
                {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
                 "purpose": "any"},
                {"src": "/icon-maskable-512.png", "sizes": "512x512",
                 "type": "image/png", "purpose": "maskable"},
            ],
        }).encode("utf-8")
        self._send(200, body, "application/manifest+json; charset=utf-8")

    def _static(self, path: str) -> None:
        if path == "/manifest.webmanifest":
            self._manifest()
            return
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC / rel).resolve()
        # A path that escapes static/ is a traversal attempt even on loopback.
        if not target.is_file() or STATIC.resolve() not in target.parents:
            self._json(404, {"error": "not found"})
            return
        kind, _ = mimetypes.guess_type(target.name)
        self._send(200, target.read_bytes(), kind or "application/octet-stream")


def _free_port(start: int) -> int:
    """The first port at or after `start` nothing is listening on.

    A stale JobDesk left running in another window should not be a startup
    error. Taking the next port and printing it is friendlier than refusing.
    """
    for port in range(start, start + 20):
        with socket.socket() as probe:
            if probe.connect_ex((HOST, port)) != 0:
                return port
    raise SystemExit(f"no free port in {start}-{start + 19}")


def _bind(host, port: int):
    """Resolve the host, set the gate, bind, and hand back the local URL.

    One function because the three steps are one decision. `access.check`
    raises rather than returning when a network bind has no token behind it,
    so there is no arrangement of arguments that starts an open server.

    The URL returned is always the loopback one. It is what the desktop window
    and the browser tab open, and it stays correct whatever was bound -- the
    phone's URL is a different question, with a token in it, and `phone.py`
    answers that one.
    """
    global REQUIRE_TOKEN
    address, kind = net.resolve(host)
    REQUIRE_TOKEN = access.check(address)
    port = _free_port(port)
    httpd = ThreadingHTTPServer((address, port), Handler)
    return httpd, f"http://{HOST}:{port}/", kind


def serve_extra(address: str, port: int) -> ThreadingHTTPServer:
    """Bind a second address in this same process, on a daemon thread.

    The desktop window is already up and holding loopback when someone turns
    phone access on, and the alternative to this is telling them to close it
    and start it again with a flag. Two `ThreadingHTTPServer`s over one handler
    class is the cheap way to have both: loopback for the window that is open,
    the network address for the phone.

    Turning the gate on is not optional and not conditional. From here on every
    request is checked -- including the ones arriving on loopback, which pass
    by peer address rather than by token (see `Handler._allowed`), so the
    window the button was pressed in keeps working and a stranger does not.
    """
    global REQUIRE_TOKEN
    access.check(address)                # raises when there is no token
    httpd = ThreadingHTTPServer((address, port), Handler)
    REQUIRE_TOKEN = True
    threading.Thread(target=httpd.serve_forever, name="jobdesk-http-net",
                     daemon=True).start()
    return httpd


def start_background(port: int = DEFAULT_PORT, host=None):
    """Bind a port on a daemon thread and return the URL it answers on.

    For the desktop window, which needs the server up before it can point a
    window at it and needs the main thread free for the GUI event loop, which
    on Windows will only run there.
    """
    httpd, url, _ = _bind(host, port)
    threading.Thread(target=httpd.serve_forever, name="jobdesk-http",
                     daemon=True).start()
    return url, httpd


def serve(port: int = DEFAULT_PORT, open_browser: bool = True, host=None) -> None:
    httpd, url, kind = _bind(host, port)
    bound = httpd.server_address
    print(f"JobDesk is at {url}")
    if kind != "loopback":
        print(f"On the network at http://{bound[0]}:{bound[1]}/ ({kind}) -- "
              f"a device other than this one needs the token as well.")
        note = net.advice(kind)
        if note:
            print(f"Note: {note}")
    print("Ctrl-C to stop.")
    if open_browser:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
