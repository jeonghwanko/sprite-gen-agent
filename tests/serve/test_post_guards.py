# SPDX-License-Identifier: Apache-2.0
"""What every webview POST goes through before and around its route.

- A POST a browser sent from another site is refused (403) before any route runs: those routes
  write the run, buy generations, mount folders. A page elsewhere can send a body-less or
  `text/plain` POST to localhost with no CORS preflight, so the server itself has to say no.
- A route that refuses by `SystemExit` still answers (500 with the reason), as every GET route
  does — it once dropped the connection with no response at all.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from sprite_gen.serve import serve_compose, serve_curation


@contextmanager
def _serving(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def _post(port: int, path: str, headers: dict[str, str] | None = None, body: bytes = b"") -> tuple[int, dict]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method="POST", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


@pytest.fixture
def curation(tmp_path: Path, monkeypatch):
    calls: list[Path] = []

    def export(run_dir: Path):
        calls.append(run_dir)
        raise SystemExit("export refused: no frames")

    monkeypatch.setattr(serve_curation, "run_export", export)
    monkeypatch.setattr(serve_curation.CurationHandler, "run_dir", tmp_path, raising=False)
    monkeypatch.setattr(serve_curation.CurationHandler, "lang", "en", raising=False)
    with _serving(serve_curation.CurationHandler) as port:
        yield port, calls


def test_a_route_that_refuses_by_systemexit_still_answers(curation):
    port, calls = curation
    status, payload = _post(port, "/api/export")
    assert status == 500
    assert payload == {"error": "export refused: no frames"}
    assert len(calls) == 1


@pytest.mark.parametrize("origin", ["http://evil.example", "null", "http://127.0.0.1:1"])
def test_curation_refuses_a_post_from_another_page_before_its_route(curation, origin):
    port, calls = curation
    status, payload = _post(port, "/api/export", {"Origin": origin, "Content-Type": "text/plain"}, b"x")
    assert status == 403
    assert payload["error"] == "cross-origin POST refused"
    assert calls == []


def test_curation_takes_a_post_from_its_own_page(curation):
    port, calls = curation
    status, _payload = _post(port, "/api/export", {"Origin": f"http://127.0.0.1:{port}"})
    assert status == 500  # the route ran (and refused, as the fixture makes it)
    assert len(calls) == 1


def test_compose_refuses_a_post_from_another_page(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(serve_compose.ComposeHandler, "mount_root", None, raising=False)
    body = json.dumps({"dir": str(tmp_path)}).encode()
    with _serving(serve_compose.ComposeHandler) as port:
        status, payload = _post(port, "/api/mount", {"Origin": "http://evil.example"}, body)
        assert status == 403 and payload["error"] == "cross-origin POST refused"
        assert serve_compose.ComposeHandler.mount_root is None
        status, _payload = _post(port, "/api/mount", {"Origin": f"http://127.0.0.1:{port}"}, body)
        assert status == 200
        assert serve_compose.ComposeHandler.mount_root == tmp_path.resolve()


def test_curation_refuses_a_post_from_a_name_rebound_to_this_server(curation):
    """DNS rebinding: the attacker's own name, re-pointed at 127.0.0.1, sends it as both Origin and
    Host. Equal is not enough; a browser's POST must name the server by localhost or an IP literal."""
    port, calls = curation
    name = f"rebind.attacker.example:{port}"
    status, payload = _post(port, "/api/export", {"Origin": f"http://{name}", "Host": name}, b"{}")
    assert status == 403 and payload["error"] == "cross-origin POST refused"
    assert calls == []


@pytest.mark.parametrize(("origin", "host", "refused"), [
    (None, "127.0.0.1:8765", False),                        # not a browser: curl, scripts
    (None, "rebind.attacker.example:8765", False),
    ("http://127.0.0.1:8765", "127.0.0.1:8765", False),
    ("http://localhost:8765", "localhost:8765", False),
    ("http://LOCALHOST:8765", "localhost:8765", False),
    ("http://curator.localhost:8765", "curator.localhost:8765", False),
    ("http://[::1]:8765", "[::1]:8765", False),
    ("http://192.168.30.23:8765", "192.168.30.23:8765", False),  # --host 0.0.0.0, opened by LAN address
    ("http://localhost:8765", "127.0.0.1:8765", True),      # another origin, though the same machine
    ("http://127.0.0.1:1", "127.0.0.1:8765", True),
    ("http://evil.example", "127.0.0.1:8765", True),
    ("null", "127.0.0.1:8765", True),
    ("http://rebind.attacker.example:8765", "rebind.attacker.example:8765", True),
    ("http://127.0.0.1:8765", None, True),
])
def test_which_requests_are_cross_origin(origin, host, refused):
    from email.message import Message

    from sprite_gen.serve.origin import cross_origin

    headers = Message()
    if origin is not None:
        headers["Origin"] = origin
    if host is not None:
        headers["Host"] = host
    assert cross_origin(headers) is refused
