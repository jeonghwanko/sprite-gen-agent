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
