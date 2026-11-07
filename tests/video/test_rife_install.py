# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen rife install` without a network: a release-shaped zip built here, its SHA-256 put
in place of the pinned one. The zip is checked before anything is unpacked, only what the engine
runs is kept, the install lands where `rife.locate()` looks last, and a second run does nothing."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

from sprite_gen import cli
from sprite_gen.video import rife, rife_install

PLATFORM = rife.platform_release() or "ubuntu"
TOP = f"rife-ncnn-vulkan-{rife.RELEASE}-{PLATFORM}"
BINARY = rife.installed_binary(Path("."), PLATFORM).name


# The fixture zip's model files, which its pin names in place of rife-v4.6's (`rife.MODEL_SHA256`).
MODEL_FILES = {"flownet.param": b"param\n", "flownet.bin": b"bin\n"}


def _release_zip(tmp_path: Path, *, model: bool = True, platform: str = PLATFORM,
                 files: dict[str, bytes] = MODEL_FILES) -> Path:
    path = tmp_path / rife_install.zip_name(platform)
    top, binary = f"rife-ncnn-vulkan-{rife.RELEASE}-{platform}", rife.installed_binary(Path("."), platform).name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{top}/", "")
        zf.writestr(f"{top}/{binary}", "#!/bin/sh\nexit 0\n")
        zf.writestr(f"{top}/LICENSE", "MIT\n")
        zf.writestr(f"{top}/README.md", "readme\n")
        if model:
            for name, data in files.items():
                zf.writestr(f"{top}/{rife.MODEL}/{name}", data)
        zf.writestr(f"{top}/rife-v4/flownet.bin", "another model\n")  # not what the engine runs
        zf.writestr(f"{top}/{rife.MODEL}/../../escape.txt", "outside\n")
        zf.writestr("elsewhere/rife-ncnn-vulkan", "not in the release folder\n")
    return path


@pytest.fixture
def pinned(monkeypatch, tmp_path):
    """The fixture zip, with its SHA-256 and its model's as the pinned ones, and the engine looking only at tmp_path."""
    (tmp_path / "zips").mkdir()
    path = _release_zip(tmp_path / "zips")
    monkeypatch.setitem(rife_install.ZIP_SHA256, PLATFORM, hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(rife, "MODEL_SHA256", {name: hashlib.sha256(data).hexdigest() for name, data in MODEL_FILES.items()},
                        raising=False)
    # the binary's install only: the resident binary pinned beside it would be downloaded (test_rife_serve_install)
    monkeypatch.setattr(rife_install, "SERVE_ZIP_SHA256", {})
    monkeypatch.delenv("SPRITE_GEN_RIFE_BACKEND", raising=False)
    monkeypatch.setenv("SPRITE_GEN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("SPRITE_GEN_RIFE", raising=False)
    monkeypatch.delenv("SPRITE_GEN_RIFE_MODEL", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    return path


def test_install_keeps_what_the_engine_runs_where_locate_finds_it(pinned, tmp_path):
    with pytest.raises(rife.RifeNotInstalled):
        rife.locate()
    report = rife_install.install(zip_path=pinned, platform=PLATFORM, run_check=False)
    target = tmp_path / "data" / "rife" / TOP
    assert report["installed"] == "now" and report["dir"] == str(target.resolve())
    assert sorted(report["files"]) == sorted([BINARY, "LICENSE", "README.md", f"{rife.MODEL}/flownet.bin", f"{rife.MODEL}/flownet.param"])
    assert not (target / "rife-v4").exists() and not (tmp_path / "data" / "escape.txt").exists()
    assert os.access(target / BINARY, os.X_OK) and (target / BINARY).stat().st_mode & stat.S_IXUSR
    assert json.loads((target / rife_install.MARKER).read_text())["sha256"] == rife_install.ZIP_SHA256[PLATFORM]
    assert rife.locate() == ((target / BINARY).resolve(), (target / rife.MODEL).resolve())
    assert report["engine_finds"] == str((target / BINARY).resolve()) and "note" not in report
    assert [p.name for p in (tmp_path / "data" / "rife").iterdir()] == [TOP]  # no staging or download left behind


def test_a_zip_whose_hash_differs_installs_nothing(pinned, tmp_path, monkeypatch):
    monkeypatch.setitem(rife_install.ZIP_SHA256, PLATFORM, "0" * 64)
    with pytest.raises(SystemExit, match="expected 0000"):
        rife_install.install(zip_path=pinned, platform=PLATFORM, run_check=False)
    assert not (tmp_path / "data" / "rife" / TOP).exists()


def test_a_zip_without_the_model_is_refused(pinned, tmp_path, monkeypatch):
    (tmp_path / "bare").mkdir()
    bare = _release_zip(tmp_path / "bare", model=False)
    monkeypatch.setitem(rife_install.ZIP_SHA256, PLATFORM, hashlib.sha256(bare.read_bytes()).hexdigest())
    with pytest.raises(SystemExit, match="flownet.param"):
        rife_install.install(zip_path=bare, platform=PLATFORM, run_check=False)
    assert not (tmp_path / "data" / "rife" / TOP).exists()


def test_a_second_run_does_nothing_and_force_unpacks_again(pinned, tmp_path):
    rife_install.install(zip_path=pinned, platform=PLATFORM, run_check=False)
    stray = tmp_path / "data" / "rife" / TOP / "stray"
    stray.write_text("x")
    assert rife_install.install(platform=PLATFORM, run_check=False)["installed"] == "already"  # no zip, no download
    assert stray.exists()
    assert rife_install.install(zip_path=pinned, platform=PLATFORM, force=True, run_check=False)["installed"] == "now"
    assert not stray.exists()


def test_the_download_is_checked_like_a_given_zip(pinned, monkeypatch, tmp_path):
    monkeypatch.setattr(rife_install, "URL", pinned.parent.as_uri() + "/{name}")
    report = rife_install.install(platform=PLATFORM, run_check=False)
    assert report["installed"] == "now" and report["source"] == pinned.as_uri()
    assert rife.locate()[0].parent.name == TOP


def test_another_dir_names_the_variable_that_points_the_engine_at_it(pinned, tmp_path, capsys):
    rc = cli.main(["rife", "install", "--zip", str(pinned), "--dir", str(tmp_path / "elsewhere"), "--no-check"])
    out, err = capsys.readouterr()
    assert rc == 0 and json.loads(out)["installed"] == "now"
    assert f"SPRITE_GEN_RIFE={tmp_path / 'elsewhere' / TOP / BINARY}" in err
    with pytest.raises(rife.RifeNotInstalled):
        rife.locate()


# Windows runs the .exe it installs, never a shebang script, so a binary that starts and
# reports its own failure cannot be stood in for there; the next test covers Windows.
@pytest.mark.skipif(sys.platform == "win32", reason="a shebang script cannot stand in for rife-ncnn-vulkan.exe")
def test_the_check_frame_fails_on_a_binary_that_does_not_run(pinned, tmp_path):
    rife_install.install(zip_path=pinned, platform=PLATFORM, run_check=False)
    target = tmp_path / "data" / "rife" / TOP
    (target / BINARY).write_text("#!/bin/sh\necho no vulkan >&2\nexit 1\n")
    with pytest.raises(SystemExit, match="check frame failed.*no vulkan"):
        rife_install.install(platform=PLATFORM)


def test_the_check_frame_fails_on_a_binary_that_cannot_be_started(pinned, tmp_path):
    """A damaged or wrong-platform build: the OS refuses to start it (ENOEXEC, WinError 193)."""
    rife_install.install(zip_path=pinned, platform=PLATFORM, run_check=False)
    target = tmp_path / "data" / "rife" / TOP
    (target / BINARY).write_bytes(b"\x00 not a program\n")
    with pytest.raises(SystemExit, match="check frame failed.*could not be started"):
        rife_install.install(platform=PLATFORM)


def test_a_platform_without_a_release_build_is_refused(monkeypatch):
    monkeypatch.setattr(rife, "platform_release", lambda: "")
    with pytest.raises(SystemExit, match="ships no build"):
        rife_install.install(run_check=False)


@pytest.mark.parametrize("backend", ["ncnn", "torch"])
def test_a_zip_whose_model_is_not_the_pinned_one_installs_nothing(pinned, tmp_path, monkeypatch, backend):
    """Its hash checked, the zip unpacked, the model's two files are checked too (`rife.MODEL_SHA256`), for either backend:
    a model that is not the one pinned is named with what it is and what was expected, and nothing is installed."""
    monkeypatch.setattr(rife, "MODEL_SHA256", {**rife.MODEL_SHA256, "flownet.bin": "0" * 64}, raising=False)
    with pytest.raises(SystemExit, match=r"flownet.bin has sha256 [0-9a-f]{64}, expected 0{64}.*nothing was installed"):
        rife_install.install(zip_path=pinned, platform=PLATFORM, run_check=False, backend=backend)
    assert not (tmp_path / "data" / "rife" / TOP).exists()


# The fixture model as each release zip ships it: the Windows zip's flownet.param with CRLF line ends, the others' with LF
# (`rife.MODEL_TEXT`); flownet.bin alike in all three.
PARAM_LF = b"7767517\n2 2\nInput in0 0 1 in0\nConvolution conv0 1 1 in0 out0 0=8 1=3\n"
LINE_ENDS = {"lf": PARAM_LF, "crlf": PARAM_LF.replace(b"\n", b"\r\n")}


def _pinned_to(monkeypatch, tmp_path: Path, param: bytes, *, bin_: bytes = MODEL_FILES["flownet.bin"]) -> Path:
    """A release zip whose model is `param` and `bin_`, the zip pinned, the model's pins those of PARAM_LF and
    MODEL_FILES' flownet.bin."""
    (tmp_path / "model-zip").mkdir()
    path = _release_zip(tmp_path / "model-zip", files={"flownet.param": param, "flownet.bin": bin_})
    monkeypatch.setitem(rife_install.ZIP_SHA256, PLATFORM, hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(rife, "MODEL_SHA256", {"flownet.param": hashlib.sha256(PARAM_LF).hexdigest(),
                                               "flownet.bin": hashlib.sha256(MODEL_FILES["flownet.bin"]).hexdigest()})
    return path


@pytest.mark.parametrize("ends", sorted(LINE_ENDS))
def test_the_param_installs_as_the_pinned_lf_bytes_from_a_zip_with_either_line_ends(pinned, tmp_path, monkeypatch, ends):
    """The Windows zip ships flownet.param with CRLF line ends, the macOS and Ubuntu zips with LF (sha256 28df14d5… and
    72456959…, measured 2026-11-07): the same text, checked with LF ones and installed so — the same bytes on every
    platform, which the engine reads back as pinned and a second run finds complete. A zip with LF has nothing turned."""
    zipped = _pinned_to(monkeypatch, tmp_path, LINE_ENDS[ends])
    report = rife_install.install(zip_path=zipped, platform=PLATFORM, run_check=False)
    model = tmp_path / "data" / "rife" / TOP / rife.MODEL
    assert report["installed"] == "now"
    assert report["line_ends_turned"] == ([f"{rife.MODEL}/flownet.param"] if ends == "crlf" else [])
    assert (model / "flownet.param").read_bytes() == PARAM_LF
    assert (model / "flownet.bin").read_bytes() == MODEL_FILES["flownet.bin"]
    assert rife.model_bytes(model) == {"flownet.param": PARAM_LF, "flownet.bin": MODEL_FILES["flownet.bin"]}
    assert rife.locate() == ((tmp_path / "data" / "rife" / TOP / BINARY).resolve(), model.resolve())
    assert rife_install.install(platform=PLATFORM, run_check=False)["installed"] == "already"


def test_a_crlf_param_whose_text_is_not_the_pinned_one_installs_nothing(pinned, tmp_path, monkeypatch):
    """Line ends are all that is turned: a CRLF param of another text is refused, named with the sha256 it has read with LF
    line ends, the one expected and the file's own."""
    other = LINE_ENDS["crlf"].replace(b"0=8", b"0=9")
    zipped = _pinned_to(monkeypatch, tmp_path, other)
    lf, own = hashlib.sha256(other.replace(b"\r\n", b"\n")).hexdigest(), hashlib.sha256(other).hexdigest()
    with pytest.raises(SystemExit, match=rf"flownet.param has sha256 {lf}, expected {rife.MODEL_SHA256['flownet.param']} "
                                         rf"\(.*\), read with its CRLF line ends as LF \(the file as it is: sha256 {own}\).*"
                                         "nothing was installed"):
        rife_install.install(zip_path=zipped, platform=PLATFORM, run_check=False)
    assert not (tmp_path / "data" / "rife" / TOP).exists()


def test_flownet_bin_is_weights_and_never_has_its_line_ends_turned(pinned, tmp_path, monkeypatch):
    """A CR LF pair in flownet.bin is two bytes of a number: a bin that would be the pinned one with CRLF turned to LF is
    another model, refused as it is."""
    zipped = _pinned_to(monkeypatch, tmp_path, PARAM_LF, bin_=MODEL_FILES["flownet.bin"].replace(b"\n", b"\r\n"))
    with pytest.raises(SystemExit, match=r"flownet.bin has sha256 [0-9a-f]{64}, expected [0-9a-f]{64} \([^)]*\); install"):
        rife_install.install(zip_path=zipped, platform=PLATFORM, run_check=False)
    assert not (tmp_path / "data" / "rife" / TOP).exists()


class _Halfway:
    """A torch runner stood in: the frame between is the first disc moved half the 36 px to the second (the check's discs)."""

    def __init__(self, model: Path):
        self.model = model

    def frame(self, colour, cover, t):
        return np.roll(colour[0], 18, axis=1), np.roll(cover[0], 18, axis=1)

    def identity(self):
        return {"backend": "torch"}

    def describe(self):
        return {"kind": "rife-torch", "device": "cpu", "model": self.model.name}


def test_the_torch_backend_installs_where_the_binary_does_not_run_and_checks_its_frame_through_torch(monkeypatch, tmp_path, capsys):
    """`--backend torch` (or SPRITE_GEN_RIFE_BACKEND=torch, which it defaults to) reads the zip's model only: on a Linux
    machine its x86-64 binary does not run on, it is not refused as the binary's install is; the model's files are checked;
    the check frame is made through the torch backend from the model just installed — the binary in it never runs (it makes
    no frame) — and the report names the model the engine will read."""
    monkeypatch.setattr(rife, "platform_release", lambda: "ubuntu")
    monkeypatch.setattr(rife_install.platform_mod, "machine", lambda: "aarch64")
    monkeypatch.setenv("SPRITE_GEN_DATA_DIR", str(tmp_path / "data"))
    for name in ("SPRITE_GEN_RIFE", "SPRITE_GEN_RIFE_MODEL", "SPRITE_GEN_RIFE_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "zips").mkdir()
    zipped = _release_zip(tmp_path / "zips", platform="ubuntu")
    monkeypatch.setitem(rife_install.ZIP_SHA256, "ubuntu", hashlib.sha256(zipped.read_bytes()).hexdigest())
    monkeypatch.setattr(rife, "MODEL_SHA256", {name: hashlib.sha256(data).hexdigest() for name, data in MODEL_FILES.items()},
                        raising=False)
    with pytest.raises(SystemExit, match="x86-64 only and this machine is aarch64"):
        rife_install.install(zip_path=zipped, run_check=False)
    asked = []
    monkeypatch.setattr(rife, "torch_runner", lambda model=None: asked.append(model) or _Halfway(model), raising=False)
    report = rife_install.install(zip_path=zipped, backend="torch")
    model = (tmp_path / "data" / "rife" / f"rife-ncnn-vulkan-{rife.RELEASE}-ubuntu" / rife.MODEL).resolve()
    assert report["backend"] == "torch" and report["installed"] == "now" and asked == [model]
    assert report["check"] == {"passed": True, "disc_centre_x": 48.0}
    assert report["engine_finds"] == str(model) and "note" not in report
    monkeypatch.setenv("SPRITE_GEN_RIFE_BACKEND", "torch")
    assert cli.main(["rife", "install", "--zip", str(zipped)]) == 0
    assert json.loads(capsys.readouterr().out)["backend"] == "torch" and len(asked) == 2
