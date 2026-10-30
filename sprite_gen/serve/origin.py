# SPDX-License-Identifier: Apache-2.0
"""Which POSTs the webviews refuse: one a browser sent from another page.

Both webviews listen on localhost and their POST routes write the run, buy generations
(`/api/reroll`), mount folders or build runs. A page on any other site can send such a POST
to localhost without a CORS preflight — a body-less POST, or one typed `text/plain` — and the
browser delivers it; only the answer is hidden from that page. A browser names the page a
request came from in `Origin` on every POST, so a POST whose origin is not this server's own
address is refused before any route runs. A request with no `Origin` is not a browser's
(curl, a script, the tests) and goes through as before.
"""

from __future__ import annotations

from email.message import Message
from urllib.parse import urlparse


def cross_origin(headers: Message) -> bool:
    """True when a browser sent this request from a page that is not this server's.

    `Origin: null` (a sandboxed frame, a `file:` page) has no address and is refused too."""
    origin = headers.get("Origin")
    if origin is None:
        return False
    return urlparse(origin).netloc.lower() != (headers.get("Host") or "").lower()
