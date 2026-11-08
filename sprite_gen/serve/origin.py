# SPDX-License-Identifier: Apache-2.0
"""Which POSTs the webviews refuse: one a browser sent from another page.

Both webviews listen on localhost and their POST routes write the run, buy generations
(`/api/reroll`), mount folders or build runs. A page on any other site can send such a POST
to localhost without a CORS preflight — a body-less POST, or one typed `text/plain` — and the
browser delivers it; only the answer is hidden from that page. A browser names the page a
request came from in `Origin` on every POST, so a POST whose origin is not this server's own
address is refused before any route runs. A request with no `Origin` is not a browser's
(curl, a script, the tests) and goes through as before.

`Origin` matching `Host` is not enough on its own: a page whose name the attacker re-points at
127.0.0.1 (DNS rebinding) sends both as its own name. So a browser's POST must also name this
server by an address no DNS answer can move — `localhost`, a `*.localhost` name, or an IP
literal (`127.0.0.1`, `[::1]`, the LAN address of a `--host 0.0.0.0` server).
"""

from __future__ import annotations

import ipaddress
import os
import time
from email.message import Message
from typing import Any
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

# A refused POST's body is read before the answer, up to this much: closing a socket with
# unread bytes makes Windows reset it, and the browser sees a dropped connection, not the 403.
_DRAIN_LIMIT = 1 << 20


def _pinned_host(hostname: str) -> bool:
    """A host name no DNS answer can re-point: localhost, *.localhost, or an IP literal."""
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return True


def cross_origin(headers: Message) -> bool:
    """True when a browser sent this request from a page that is not this server's.

    `Origin: null` (a sandboxed frame, a `file:` page) has no address and is refused too."""
    origin = headers.get("Origin")
    if origin is None:
        return False
    host = (headers.get("Host") or "").lower()
    if urlparse(origin).netloc.lower() != host:
        return True
    return not _pinned_host(urlparse(f"//{host}").hostname or "")


def env_number(name: str, default: float, kind: type = int) -> Any:
    """`kind(os.environ[name])`, or `default` when it is unset; a value that is not a non-negative number of that
    kind stops with SystemExit naming the variable, not a ValueError traceback on import."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return kind(default)
    try:
        value = kind(raw)
    except ValueError:
        value = -1
    if value < 0:
        raise SystemExit(f"{name}={raw!r}: expected a non-negative {'integer' if kind is int else 'number'}")
    return value


# The largest POST body either webview reads. The biggest legitimate one is `/api/gif`: up to 200
# frames as PNG data URLs. Anything larger is refused before it is read into memory.
MAX_BODY = env_number("SPRITE_GEN_SERVE_MAX_BODY", 256 << 20)

# A refused body's bytes are read and dropped for this long after the answer is sent, so the client finishes
# sending and reads the 413 instead of a reset connection. Nothing is kept, so its size is not bounded, only its time.
_DISCARD_SECONDS = 10.0


class BodyRefused(ValueError):
    """A POST body the server will not read: a malformed or too large `Content-Length`."""

    def __init__(self, message: str, status: int, length: int = 0):
        super().__init__(message)
        self.status = status
        self.length = length


def body_length(headers: Message, limit: int | None = None) -> int:
    """The request's `Content-Length`, checked: BodyRefused (400) if it is not digits only (RFC 9110 8.6),
    (413) if it is over `limit` (default MAX_BODY)."""
    limit = MAX_BODY if limit is None else limit
    raw = (headers.get("Content-Length") or "0").strip()
    if not (raw.isascii() and raw.isdigit()):
        raise BodyRefused(f"invalid Content-Length: {raw!r}", 400)
    length = int(raw)
    if length > limit:
        raise BodyRefused(f"request body too large: {length} bytes (limit {limit})", 413, length)
    return length


def discard_refused_body(handler: BaseHTTPRequestHandler, refused: BodyRefused) -> None:
    """After a refusal has been sent: read and drop the body the client is still sending, for up to
    `_DISCARD_SECONDS`, then close. Closing with the body unread resets the socket, and a client still writing
    its body sees a broken pipe instead of the answer. A body with no trustworthy length (400) is not read."""
    handler.close_connection = True
    left = refused.length
    deadline = time.monotonic() + _DISCARD_SECONDS
    try:
        handler.connection.settimeout(1.0)
        while left > 0 and time.monotonic() < deadline:
            chunk = handler.rfile.read1(min(left, 1 << 16))
            if not chunk:
                break
            left -= len(chunk)
    except OSError:
        pass


def drain_body(handler: BaseHTTPRequestHandler) -> None:
    """Read (and drop) the body of a POST about to be refused, up to `_DRAIN_LIMIT`."""
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return
    if 0 < length <= _DRAIN_LIMIT:
        handler.rfile.read(length)
