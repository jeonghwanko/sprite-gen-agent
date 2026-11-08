# SPDX-License-Identifier: Apache-2.0
"""What bounds the webviews' work: a POST body's size, a pipeline step's time, and the digest cache.

- A `Content-Length` over the limit is refused (413) before the body is read; one that is not a
  number is refused (400) instead of raising inside the handler.
- A pipeline step that never finishes is stopped, and the route answers 504 naming it.
- The content-digest cache stays bounded however many re-bakes a long-lived server sees.
"""

from __future__ import annotations

import http.client
import json
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from sprite_gen.curate import curation
from sprite_gen.serve import origin, serve_compose, serve_curation


@contextmanager
def _serving(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def _post_raw(port: int, path: str, content_length: str, body: bytes = b"") -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.putrequest("POST", path)
        conn.putheader("Content-Length", content_length)
        conn.endheaders()
        if body:
            conn.send(body)
        res = conn.getresponse()
        return res.status, json.loads(res.read())
    finally:
        conn.close()


def _curation_handler(run_dir: Path):
    return type("H", (serve_curation.CurationHandler,), {"run_dir": run_dir, "lang": "en"})


@pytest.mark.parametrize("path", ["/api/curation", "/api/compare-gif"])
def test_curation_refuses_a_body_over_the_limit_before_reading_it(tmp_path, monkeypatch, path):
    monkeypatch.setattr(origin, "MAX_BODY", 1024)
    with _serving(_curation_handler(tmp_path)) as port:
        status, payload = _post_raw(port, path, str(10 << 20))
    assert status == 413
    assert "too large" in payload["error"]


def test_curation_refuses_a_content_length_that_is_not_a_number(tmp_path):
    with _serving(_curation_handler(tmp_path)) as port:
        status, payload = _post_raw(port, "/api/curation", "lots")
    assert status == 400
    assert "Content-Length" in payload["error"]


def test_compose_refuses_a_body_over_the_limit(monkeypatch):
    monkeypatch.setattr(origin, "MAX_BODY", 1024)
    monkeypatch.setattr(serve_compose.ComposeHandler, "mount_root", None, raising=False)
    with _serving(serve_compose.ComposeHandler) as port:
        status, payload = _post_raw(port, "/api/mount", str(10 << 20))
    assert status == 413


def test_body_length_reads_a_valid_header():
    assert origin.body_length({"Content-Length": "12"}, limit=100) == 12
    assert origin.body_length({}, limit=100) == 0
    with pytest.raises(origin.BodyRefused) as refused:
        origin.body_length({"Content-Length": "-1"}, limit=100)
    assert refused.value.status == 400


def test_a_step_that_never_finishes_is_stopped_and_answers_504(tmp_path, monkeypatch):
    monkeypatch.setattr(serve_curation, "STEP_SECONDS", 0.5)
    monkeypatch.setattr(serve_curation, "qualified", lambda name: "never_ending_step")
    (tmp_path / "never_ending_step.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    result = serve_curation._run_module("compose_atlas", tmp_path)
    assert result["ok"] is False and result["timeout"] is True
    assert "compose_atlas" in result["error"]
    assert serve_curation._step_status(result) == 504


def test_digest_cache_stays_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(curation, "_CONTENT_DIGEST_CACHE_MAX", 3)
    monkeypatch.setattr(curation, "_CONTENT_DIGEST_CACHE", type(curation._CONTENT_DIGEST_CACHE)())
    files = []
    for i in range(6):
        f = tmp_path / f"f{i}.bin"
        f.write_bytes(bytes([i]) * 10)
        files.append(f)
        assert curation._file_content_digest(f) is not None
    assert len(curation._CONTENT_DIGEST_CACHE) == 3
    # The newest entries are the ones kept.
    assert {Path(k[0]).name for k in curation._CONTENT_DIGEST_CACHE} == {"f3.bin", "f4.bin", "f5.bin"}


def test_correction_loop_stops_a_provider_that_never_finishes(fixture_run_dir: Path, tmp_path: Path):
    from sprite_gen.qa import correction_loop

    with pytest.raises(SystemExit, match="no result in 0.5 s"):
        correction_loop.run_loop(
            fixture_run_dir, out_dir=tmp_path / "loop", max_passes=2, min_attempts=2,
            provider_command=f'"{sys.executable}" -c "import time; time.sleep(60)"',
            provider_timeout=0.5,
        )


def test_a_stopped_step_takes_the_provider_it_started_with_it(tmp_path):
    from sprite_gen import local_tool

    late = tmp_path / "written-after-the-stop"
    grandchild = f"import time; time.sleep(1.5); open({str(late)!r}, 'w').close()"
    child = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {grandchild!r}]); time.sleep(60)"
    with pytest.raises(subprocess.TimeoutExpired):
        local_tool.run_tree([sys.executable, "-c", child], seconds=0.5)
    time.sleep(2.5)
    assert not late.exists(), "the provider the step started kept running after the step was stopped"


def test_a_provider_step_stopped_says_it_may_have_been_charged(tmp_path, monkeypatch):
    monkeypatch.setattr(serve_curation, "STEP_SECONDS", 0.5)
    monkeypatch.setattr(serve_curation, "qualified", lambda name: "never_ending_step")
    (tmp_path / "never_ending_step.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    result = serve_curation.run_reroll(tmp_path, "idle")
    assert result["timeout"] is True and result["provider_submitted"] is True and result["retryable"] is False
    assert "charged" in result["error"]
    plain = serve_curation._run_module("compose_atlas", tmp_path)
    assert plain["provider_submitted"] is False and plain["retryable"] is True
