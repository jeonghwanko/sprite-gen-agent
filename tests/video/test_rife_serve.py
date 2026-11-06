# SPDX-License-Identifier: Apache-2.0
"""The resident binary (`rife.Served`, native/rife-serve/serve.cpp): found beside the binary or by SPRITE_GEN_RIFE_SERVE,
started once and asked frame after frame, each augmentation checked against the binary first, and a failure of it a RIFE
that fails — never the binary run instead. Stand-ins speak its protocol here; the real one is measured in
docs/loop-repair.md section 1, "The resident binary"."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen.video import rife


def _stand_in(path: Path, python: str) -> Path:
    """An executable that runs `python` with this interpreter: a shebang script, or on Windows, which runs none, a .cmd."""
    script = path.with_suffix(".py")
    script.write_text(python, encoding="utf-8")
    if sys.platform == "win32":
        cmd = path.with_suffix(".cmd")
        cmd.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        return cmd
    path.write_text(f"#!/bin/sh\nexec \"{sys.executable}\" \"{script}\" \"$@\"\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _binary(tmp_path: Path) -> tuple[Path, Path]:
    """A stand-in binary in its own directory, with a model beside it, that logs its flags and hands back its first
    frame: (binary, log)."""
    where = tmp_path / "bin"
    (where / rife.MODEL).mkdir(parents=True)
    for name in ("flownet.param", "flownet.bin"):
        (where / rife.MODEL / name).write_bytes(name.encode())
    log = tmp_path / "binary.txt"
    binary = _stand_in(where / rife.BINARY, f"import shutil, sys\nargs = sys.argv[1:]\n"
                       f"open({str(log)!r}, 'a').write(' '.join(a for a in args if a in ('-x', '-z')) + '|' + "
                       "args[args.index('-s') + 1] + '\\n')\n"
                       "shutil.copyfile(args[args.index('-0') + 1], args[args.index('-o') + 1])\n")
    return binary, log


SERVE = """\
import os, struct, sys, time
LOG, HOW = {log!r}, {how!r}
out, inp = sys.stdout.buffer, sys.stdin.buffer
name = b"Stand-in GPU"
own = (1 if "-x" in sys.argv else 0) | (2 if "-z" in sys.argv else 0)
out.write(b"SGRS" + struct.pack("<II", {version}, len(name)) + name)
out.flush()
asked = 0
while True:
    head = inp.read(20)
    if not head:
        break
    w, h = struct.unpack("<II", head[4:12])
    flags = struct.unpack("<I", head[16:20])[0]
    a, b = inp.read(w * h * 3), inp.read(w * h * 3)
    asked += 1
    with open(LOG, "a") as log:
        log.write(f"{{w}} {{h}} {{head[12:16].hex()}} {{flags}} {{own}} {{os.getpid()}}\\n")
    if flags != own:
        message = b"this process makes frames with other augmentation flags"
        out.write(b"SGRA" + struct.pack("<iI", 1, len(message)) + message)
        out.flush()
        continue
    if HOW == "sleep":
        time.sleep(5)
    if HOW == "end" and asked == 3 and not os.path.exists(LOG + ".ended"):
        open(LOG + ".ended", "w").close()
        sys.stderr.write("lost the device\\n")
        sys.stderr.flush()
        sys.exit(3)
    if HOW == "refuse-late" and struct.unpack("<f", head[12:16])[0] > 0.9:
        message = b"invalid timestep argument, must be 0~1"
        out.write(b"SGRA" + struct.pack("<iI", 1, len(message)) + message)
    else:
        made = b if HOW == "second" else a
        out.write(b"SGRA" + struct.pack("<iI", 0, len(made)) + made)
    out.flush()
"""


def _serve(where: Path, log: Path, how: str = "first", version: int = rife.SERVE_PROTOCOL) -> Path:
    return _stand_in(where / rife.SERVE, SERVE.format(log=str(log), how=how, version=version))


def _setup(monkeypatch, tmp_path: Path, how: str = "first", version: int = rife.SERVE_PROTOCOL) -> tuple[Path, Path, Path]:
    """A stand-in binary and a stand-in resident binary beside it, the binary named by SPRITE_GEN_RIFE: (binary, its log,
    the resident binary's log)."""
    binary, binary_log = _binary(tmp_path)
    serve_log = tmp_path / "serve.txt"
    _serve(binary.parent, serve_log, how, version)
    monkeypatch.setenv("SPRITE_GEN_RIFE", str(binary))
    monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL", raising=False)
    monkeypatch.delenv("SPRITE_GEN_RIFE_SERVE", raising=False)
    monkeypatch.delenv("SPRITE_GEN_RIFE_TTA", raising=False)
    monkeypatch.delenv("SPRITE_GEN_RIFE_BACKEND", raising=False)
    monkeypatch.delenv("SPRITE_GEN_RIFE_EXECUTOR", raising=False)
    if sys.platform == "win32":
        # beside the binary the engine looks for the .exe; the .cmd stand-in is named instead
        monkeypatch.setenv("SPRITE_GEN_RIFE_SERVE", str(binary.parent / (rife.SERVE + ".cmd")))
    return binary, binary_log, serve_log


def _disc(cx: int, size: tuple[int, int] = (64, 48)) -> Image.Image:
    w, h = size
    ys, xs = np.mgrid[0:h, 0:w]
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[(xs - cx) ** 2 + (ys - 24) ** 2 <= 10 ** 2] = (200, 60, 30, 255)
    return Image.fromarray(rgba, "RGBA")


def _asked(log: Path) -> list[list[str]]:
    return [line.split() for line in log.read_text().splitlines()] if log.exists() else []


def test_the_resident_binary_beside_the_binary_is_taken(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    found = rife.Rife()
    assert isinstance(found.runner, rife.Served) and found.describe()["kind"] == rife.SERVE


@pytest.mark.skipif(sys.platform == "win32", reason="beside the binary the engine looks for the .exe")
def test_none_beside_the_binary_calls_the_binary_once_per_run(monkeypatch, tmp_path):
    binary, _ = _binary(tmp_path)
    monkeypatch.setenv("SPRITE_GEN_RIFE", str(binary))
    monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL", raising=False)
    monkeypatch.delenv("SPRITE_GEN_RIFE_SERVE", raising=False)
    monkeypatch.delenv("SPRITE_GEN_RIFE_BACKEND", raising=False)
    monkeypatch.delenv("SPRITE_GEN_RIFE_EXECUTOR", raising=False)
    assert isinstance(rife.Rife().runner, rife.Binary)


def test_sprite_gen_rife_serve_set_empty_calls_the_binary_once_per_run(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setenv("SPRITE_GEN_RIFE_SERVE", "")
    assert isinstance(rife.Rife().runner, rife.Binary)


def test_a_resident_binary_named_and_not_there_is_refused_not_passed_over(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setenv("SPRITE_GEN_RIFE_SERVE", str(tmp_path / "nowhere" / rife.SERVE))
    with pytest.raises(rife.RifeNotInstalled, match=r"not an executable file .*SPRITE_GEN_RIFE_SERVE= \(empty\)"):
        rife.Rife()


def test_one_process_per_augmentation_makes_every_frame_checked_once_against_the_binary(monkeypatch, tmp_path):
    _, binary_log, serve_log = _setup(monkeypatch, tmp_path)
    interpolate = rife.Rife()
    for t in (0.25, 0.5, 0.75):
        interpolate(_disc(20), _disc(44), t)
    w, h = rife.CHECK_SIZE
    check = [str(w), str(h), np.float32(rife.CHECK_T).tobytes().hex(), "3", "3"]
    asked = _asked(serve_log)
    assert asked[0][:5] == check and len(asked) == 1 + 6
    assert all(a[:2] == ["64", "48"] and a[3:5] == ["3", "3"] for a in asked[1:]) and len({a[5] for a in asked}) == 1
    assert _asked(binary_log) == [["-x", "-z|0.3750"]]
    assert interpolate.counts() == {"runs": 6, "serve_starts": 1, "binary_calls": 1}

    # the passes run with no augmentation, in a process of their own, checked as it starts; the read's frame in the first
    interpolate.read(_disc(20), _disc(44), 0.5)
    asked = _asked(serve_log)
    assert [a[3:5] for a in asked[7:]] == [["3", "3"]] * 2 + [["0", "0"]] * (1 + rife.PASS_CALLS)
    assert asked[8][5] == asked[0][5] and len({a[5] for a in asked[9:]}) == 1 and asked[9][5] != asked[0][5]
    assert _asked(binary_log)[1] == ["|0.3750"]
    assert interpolate.counts() == {"runs": 6 + 2 + rife.PASS_CALLS, "serve_starts": 2, "binary_calls": 2}


@pytest.mark.parametrize("t, sent", [(1 / 3, "0.3333"), (0.56249, "0.5625"), (0.65, "0.6500")])
def test_the_fraction_sent_is_the_float_the_binary_reads_from_its_four_decimals(monkeypatch, tmp_path, t, sent):
    _, _, serve_log = _setup(monkeypatch, tmp_path)
    rife.Rife()(_disc(20), _disc(44), t)
    assert _asked(serve_log)[-1][2] == np.float32(float(sent)).tobytes().hex()


@pytest.mark.parametrize("size", [(64, 48), (37, 53)])
def test_a_frame_through_it_is_the_frame_the_binary_makes(monkeypatch, tmp_path, size):
    """Both stand-ins hand back their first image, so what differs between the two frames is what carries the pixels:
    their order, their shape, a frame wider than it is tall and one taller, and the passes turned."""
    binary, _, _ = _setup(monkeypatch, tmp_path)
    a, b = _disc(20, size), _disc(30, size)
    served, called = rife.Rife(), rife.Rife(binary, binary.parent / rife.MODEL)
    assert np.array_equal(np.asarray(served(a, b, 0.4)), np.asarray(called(a, b, 0.4)))
    made, apart = served.read(a, b, 0.4)
    want, want_apart = called.read(a, b, 0.4)
    assert np.array_equal(np.asarray(made), np.asarray(want)) and np.array_equal(apart, want_apart)


def test_a_resident_binary_whose_check_frame_differs_is_refused(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, how="second")
    interpolate = rife.Rife()
    with pytest.raises(rife.RifeUnavailable, match=r"check frame \(augmentation -x -z\) differs from .* on \d+ of 4032 pixels"):
        interpolate(_disc(20), _disc(44), 0.5)
    assert not interpolate.runner.running


def test_a_request_it_refuses_is_a_rife_that_failed_and_it_serves_on(monkeypatch, tmp_path):
    _, _, serve_log = _setup(monkeypatch, tmp_path, how="refuse-late")
    interpolate = rife.Rife()
    with pytest.raises(rife.RifeUnavailable, match=r"failed: invalid timestep argument"):
        interpolate(_disc(20), _disc(44), 0.99)
    interpolate(_disc(20), _disc(44), 0.5)
    assert interpolate.runner.starts == 1 and len(_asked(serve_log)) == 4  # the check, the refused run, the frame's two


def test_a_resident_binary_that_ends_is_a_rife_that_failed_and_the_next_run_starts_it_again(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, how="end")
    interpolate = rife.Rife()
    with pytest.raises(rife.RifeUnavailable, match=r"ended \(exit 3\): lost the device"):
        interpolate(_disc(20), _disc(44), 0.5)  # the check, the colour run, and the coverage run it ends on
    interpolate(_disc(20), _disc(44), 0.5)
    assert interpolate.runner.starts == 2


def test_a_run_past_its_time_is_stopped_and_a_rife_that_failed(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, how="sleep")
    monkeypatch.setattr(rife, "CALL_TIMEOUT_SECONDS", 0.5)
    interpolate = rife.Rife()
    with pytest.raises(rife.RifeUnavailable, match=r"made no frame in 0.5 s and was stopped"):
        interpolate(_disc(20), _disc(44), 0.5)
    assert not interpolate.runner.running


def test_another_protocol_is_refused_as_it_starts(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, version=rife.SERVE_PROTOCOL + 1)
    with pytest.raises(rife.RifeUnavailable, match=rf"speaks protocol {rife.SERVE_PROTOCOL + 1}, this engine"):
        rife.Rife()(_disc(20), _disc(44), 0.5)


def test_what_its_frames_depend_on_is_held_while_it_runs_and_named_apart_from_the_binary(monkeypatch, tmp_path):
    binary, _, _ = _setup(monkeypatch, tmp_path)
    interpolate = rife.Rife()
    before = interpolate.identity()
    interpolate(_disc(20), _disc(44), 0.5)
    held = interpolate.runner.identity()
    assert set(held) == {"backend", "serve", "binary", "model", "model_files"} and interpolate.identity() == before
    (binary.parent / rife.MODEL / "flownet.bin").write_bytes(b"another model")
    assert interpolate.runner.identity() == held  # what it loaded, for its life
    interpolate.runner.close()
    assert interpolate.runner.identity() != held  # read anew once it is not running
    assert interpolate.identity() != rife.Rife(binary, binary.parent / rife.MODEL).identity()
