# SPDX-License-Identifier: Apache-2.0
"""A command prints UTF-8 on a stream opened in a legacy code page: a Windows console or pipe on cp949
could not encode the em dash, Korean or a path a command prints, and the command died at the print."""

import ast
import io
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("entry", [["-m", "sprite_gen.background.tile"], ["-m", "sprite_gen.cli", "background-tile"]],
                         ids=["module", "sprite-gen"])
def test_a_command_on_a_cp949_stream_prints_its_em_dash_as_utf8(tmp_path, entry):
    source = tmp_path / "external.png"
    Image.new("RGBA", (24, 13), (32, 71, 117, 97)).save(source)
    proc = subprocess.run(
        [sys.executable, *entry, "--source", str(source), "--out", str(tmp_path / "tile.png"),
         "--period", "8", "--overlap", "3"],
        capture_output=True, timeout=20, env={**os.environ, "PYTHONIOENCODING": "cp949"},
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert "background-tile: pass —" in proc.stdout.decode("utf-8")


def test_only_a_stream_that_is_not_utf8_is_reopened(monkeypatch):
    from sprite_gen.util import console

    legacy = io.TextIOWrapper(io.BytesIO(), encoding="cp949")
    already = io.TextIOWrapper(io.BytesIO(), encoding="UTF8", errors="surrogateescape")
    monkeypatch.setattr(sys, "stdout", legacy)
    monkeypatch.setattr(sys, "stderr", already)
    console.utf8_streams()
    assert (legacy.encoding, legacy.errors) == ("utf-8", "strict")
    assert (already.encoding, already.errors) == ("UTF8", "surrogateescape")
    plain = io.StringIO()  # no reconfigure: left as it is
    monkeypatch.setattr(sys, "stdout", plain)
    console.utf8_streams()
    assert sys.stdout is plain


def test_every_entry_reopens_its_streams_first():
    """Every `main()` of the package, the `sprite-gen` one among them, calls `utf8_streams()` first."""
    missing = []
    for path in sorted((ROOT / "sprite_gen").rglob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not (isinstance(node, ast.FunctionDef) and node.name == "main"):
                continue
            body = node.body[1:] if ast.get_docstring(node) is not None else node.body
            call = body[0].value if body and isinstance(body[0], ast.Expr) else None
            if not (isinstance(call, ast.Call) and getattr(call.func, "id", None) == "utf8_streams"):
                missing.append(str(path.relative_to(ROOT)))
    assert not missing


def _calls():
    """Every call in the package, its scripts and its tests, with where it is."""
    for root in ("sprite_gen", "scripts", "tests"):
        for path in sorted((ROOT / root).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call):
                    yield f"{path.relative_to(ROOT).as_posix()}:{node.lineno}", node


def test_every_text_file_is_read_and_written_as_utf8():
    """A bare `read_text()`, `write_text(s)` or text-mode `open(p)` uses the locale's code page: on a cp949 Windows a
    report holding an em dash or a Korean prompt file stopped the run (`UnicodeDecodeError`), and a write of Korean text
    failed the same way. The tests read what the package writes, so they are held to it too: four of them failed on a
    cp949 Windows that way while CI, on a UTF-8 Linux, passed."""
    bare = []
    for where, node in _calls():
        keywords = {k.arg for k in node.keywords}
        if "encoding" in keywords or None in keywords:
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in ("read_text", "write_text"):
            positional = 0 if node.func.attr == "read_text" else 1  # read_text(encoding) / write_text(data, encoding)
            if len(node.args) <= positional:
                bare.append(where)
        elif isinstance(node.func, ast.Name) and node.func.id == "open" and len(node.args) < 4:  # open(file, mode, buffering, encoding)
            mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
            if mode is None or (isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" not in mode.value):
                bare.append(where)
    assert not bare


def test_every_child_s_output_is_read_as_utf8():
    """`text=True` without `encoding=` reads a child's output in the locale's code page. A sprite-gen child writes UTF-8
    (`utf8_streams`), and so do ffmpeg, node, git and the claude CLI; on a cp949 Windows their em dash, Korean reply or
    Korean path stopped the read (`UnicodeDecodeError`) or came back garbled."""
    bare = []
    for where, node in _calls():
        keywords = {k.arg: k.value for k in node.keywords}
        if "encoding" in keywords or None in keywords:
            continue
        if any(isinstance(keywords.get(flag), ast.Constant) and keywords[flag].value is True
               for flag in ("text", "universal_newlines")):
            bare.append(where)
    assert not bare
