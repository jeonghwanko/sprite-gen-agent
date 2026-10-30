# SPDX-License-Identifier: Apache-2.0
"""The composition canvas (`sprite-gen compose`) over HTTP: every route a browser reaches, and where each stops.

The client names filesystem paths here — the folder to mount, a folder to list, an image to show, the files
a built row takes — so every read is confined to the mounted root and every write to an empty output folder.
These held only by reading the code before; they are the routes a later refactor of the handler must keep.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen.serve import serve_compose


@contextmanager
def _serving():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(serve_compose.ComposeHandler))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def _call(port: int, method: str, path: str, body: dict | None = None) -> tuple[int, bytes, str]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, res.read(), res.headers.get("Content-Type", "")
    except urllib.error.HTTPError as err:
        return err.code, err.read(), err.headers.get("Content-Type", "")


def _json(port: int, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    status, data, _ = _call(port, method, path, body)
    return status, json.loads(data)


def _q(path: Path) -> str:
    return urllib.parse.quote(str(path))


@pytest.fixture
def library(tmp_path: Path, monkeypatch):
    """A mounted library: two images, a subfolder, a hidden file and a non-image, beside a folder outside it."""
    root = tmp_path / "library"
    (root / "walk").mkdir(parents=True)
    for name in ("b.png", "a.png"):
        Image.new("RGBA", (8, 8), (200, 40, 40, 255)).save(root / name)
    Image.new("RGBA", (8, 8), (40, 200, 40, 255)).save(root / "walk" / "w0.png")
    (root / ".hidden.png").write_bytes(b"x")
    (root / "notes.txt").write_text("not an image", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    Image.new("RGBA", (8, 8)).save(outside / "secret.png")
    monkeypatch.setattr(serve_compose.ComposeHandler, "mount_root", None, raising=False)
    monkeypatch.setattr(serve_compose.ComposeHandler, "lang", "en", raising=False)
    with _serving() as port:
        yield port, root.resolve(), outside.resolve(), tmp_path


def test_state_and_browse_before_any_mount(library):
    port, *_ = library
    assert _json(port, "GET", "/api/state") == (200, {"lang": "en", "mount": None})
    assert _json(port, "GET", "/api/browse")[0] == 409
    assert _json(port, "GET", "/api/browse-img?path=x.png")[0] == 409


def test_mount_lists_folders_then_images_and_nothing_else(library):
    port, root, *_ = library
    status, listed = _json(port, "POST", "/api/mount", {"dir": str(root)})
    assert status == 200 and listed["mount"] == str(root)
    assert [(e["name"], e["type"]) for e in listed["entries"]] == [("walk", "dir"), ("a.png", "image"), ("b.png", "image")]
    assert _json(port, "GET", "/api/state")[1]["mount"] == str(root)


@pytest.mark.parametrize(("body", "status"), [({}, 400), ({"dir": "   "}, 400), ({"dir": "/no/such/folder/here"}, 404)])
def test_mount_refuses_what_is_not_a_folder(library, body, status):
    port, *_ = library
    assert _json(port, "POST", "/api/mount", body)[0] == status
    assert _json(port, "GET", "/api/state")[1]["mount"] is None


def test_mount_refuses_a_body_that_is_not_json(library):
    port, *_ = library
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/mount", data=b"{not json", method="POST")
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=30)
    assert err.value.code == 400


def test_browse_and_images_stay_inside_the_mounted_root(library):
    port, root, outside, _ = library
    _json(port, "POST", "/api/mount", {"dir": str(root)})
    status, sub = _json(port, "GET", f"/api/browse?dir={_q(root / 'walk')}")
    assert status == 200 and [e["name"] for e in sub["entries"]] == ["w0.png"]
    assert _json(port, "GET", f"/api/browse?dir={_q(outside)}")[0] == 403
    assert _json(port, "GET", f"/api/browse?dir={_q(root / '..')}")[0] == 403
    assert _json(port, "GET", f"/api/browse?dir={_q(root / 'missing')}")[0] == 404
    status, data, ctype = _call(port, "GET", f"/api/browse-img?path={_q(root / 'a.png')}")
    assert status == 200 and ctype == "image/png" and data == (root / "a.png").read_bytes()
    assert _call(port, "GET", f"/api/browse-img?path={_q(outside / 'secret.png')}")[0] == 403
    assert _call(port, "GET", f"/api/browse-img?path={_q(root / 'walk' / '..' / '..' / 'outside' / 'secret.png')}")[0] == 403
    assert _call(port, "GET", f"/api/browse-img?path={_q(root / 'notes.txt')}")[0] == 404


def test_the_spa_and_its_assets_and_nothing_beside_them(library):
    port, *_ = library
    status, page, ctype = _call(port, "GET", "/")
    assert status == 200 and ctype.startswith("text/html") and page == (serve_compose.COMPOSER_DIR / "index.html").read_bytes()
    assert _call(port, "GET", "/composer.css")[0] == 200
    assert _call(port, "GET", "/composer/composer.css")[0] == 200
    assert _call(port, "GET", "/composer/%2e%2e/serve_compose.py")[0] == 404
    assert _call(port, "GET", "/src/%2e%2e/%2e%2e/serve_compose.py")[0] == 404
    assert _call(port, "GET", "/nowhere")[0] == 404


def test_build_refuses_before_it_writes(library):
    port, root, outside, tmp = library
    rows = [{"name": "idle", "cells": [{"path": str(root / "a.png")}]}]
    assert _json(port, "POST", "/api/build", {"outDir": str(tmp / "run"), "rows": rows})[0] == 409  # nothing mounted
    _json(port, "POST", "/api/mount", {"dir": str(root)})
    assert _json(port, "POST", "/api/build", {"rows": rows})[0] == 400
    full = tmp / "full"
    full.mkdir()
    (full / "keep.txt").write_text("mine", encoding="utf-8")
    status, refused = _json(port, "POST", "/api/build", {"outDir": str(full), "rows": rows})
    assert status == 409 and refused["code"] == "out-dir-not-empty"
    assert sorted(p.name for p in full.iterdir()) == ["keep.txt"]
    stolen = [{"name": "idle", "cells": [{"path": str(outside / "secret.png")}]}]
    assert _json(port, "POST", "/api/build", {"outDir": str(tmp / "run"), "rows": stolen})[0] == 403
    assert _json(port, "POST", "/api/build", {"outDir": str(tmp / "run"), "rows": [{"name": "x", "cells": [{"path": str(root / "notes.txt")}]}]})[0] == 404
    assert _json(port, "POST", "/api/build", {"outDir": str(tmp / "run"), "rows": [{"name": "x", "cells": []}]})[0] == 400
    assert not (tmp / "run").exists()


def test_build_makes_a_run_dir_of_the_rows(library):
    port, root, _, tmp = library
    _json(port, "POST", "/api/mount", {"dir": str(root)})
    rows = [{"name": "Idle Row", "cells": [{"path": str(root / "a.png")}, {"path": str(root / "b.png")}]},
            {"name": "idle row", "cells": [{"path": str(root / "walk" / "w0.png")}]}]
    status, built = _json(port, "POST", "/api/build", {"outDir": str(tmp / "run"), "rows": rows})
    assert status == 200, built
    assert built["runDir"] == str((tmp / "run").resolve())
    assert len(built["states"]) == 2 and len(set(built["states"])) == 2  # two rows never collide onto one state
    assert (tmp / "run" / "sprite-request.json").is_file()


def test_open_curation_refuses_a_folder_that_is_not_a_run(library):
    port, root, *_ = library
    status, refused = _json(port, "POST", "/api/open-curation", {"runDir": str(root)})
    assert status == 404 and "no sprite-request.json" in refused["error"]
