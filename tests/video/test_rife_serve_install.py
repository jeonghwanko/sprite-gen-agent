# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen rife install` puts the resident binary (`rife.SERVE`) beside the binary from this repository's release
asset — where one is pinned for the platform, its zip's SHA-256 is the pin, and its check frame is the binary's — and
in every other case leaves none and fails nothing, so the binary runs once per run as before. The asset is the one
native/rife-serve/build.py `--zip` packs; no network, no GPU (the check is stood in for, or made through the stand-ins
of test_rife_serve)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

from sprite_gen import cli
from sprite_gen.video import rife, rife_install
from tests.video.test_rife_install import MODEL_FILES, _release_zip
from tests.video.test_rife_serve import _binary, _serve

PLATFORM = "windows" if sys.platform == "win32" else "ubuntu"  # the platforms a resident binary is offered for
TOP = f"rife-ncnn-vulkan-{rife.RELEASE}-{PLATFORM}"
SERVE = rife.SERVE + (".exe" if PLATFORM == "windows" else "")
ROOT = Path(__file__).resolve().parents[2]


def _build_module():
    spec = importlib.util.spec_from_file_location("rife_serve_build", ROOT / "native" / "rife-serve" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _release_tree(where: Path) -> Path:
    """The licence files of the release's tree that a packed asset's NOTICE.txt carries."""
    for _, rel in _build_module().LICENCES:
        (where / rel).parent.mkdir(parents=True, exist_ok=True)
        (where / rel).write_text(f"the licence of {rel}\n", encoding="utf-8")
    return where


def _asset(tmp_path: Path, payload: bytes = b"a resident binary\n", platform: str = PLATFORM) -> Path:
    """A release asset packed by build.py's own `package`, from a stand-in build."""
    build = _build_module()
    out = tmp_path / "asset"
    out.mkdir(parents=True, exist_ok=True)
    built = tmp_path / "built" / SERVE
    built.parent.mkdir(parents=True, exist_ok=True)
    built.write_bytes(payload)
    (out / SERVE).write_bytes(payload)
    return build.package(built, _release_tree(tmp_path / "tree"), out, platform)


@pytest.fixture
def installed(monkeypatch, tmp_path):
    """The release zip pinned and installed under tmp_path, its check frame not made: (zip, the install's folder)."""
    (tmp_path / "zips").mkdir()
    path = _release_zip(tmp_path / "zips", platform=PLATFORM)
    monkeypatch.setitem(rife_install.ZIP_SHA256, PLATFORM, hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(rife, "MODEL_SHA256", {name: hashlib.sha256(data).hexdigest() for name, data in MODEL_FILES.items()},
                        raising=False)
    monkeypatch.setattr(rife_install, "SERVE_ZIP_SHA256", {})
    for name in ("SPRITE_GEN_RIFE_BACKEND", "SPRITE_GEN_RIFE", "SPRITE_GEN_RIFE_MODEL", "SPRITE_GEN_RIFE_SERVE",
                 "SPRITE_GEN_RIFE_TTA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SPRITE_GEN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    return path, tmp_path / "data" / "rife" / TOP


def _pin(monkeypatch, asset: Path) -> str:
    sha = hashlib.sha256(asset.read_bytes()).hexdigest()
    monkeypatch.setitem(rife_install.SERVE_ZIP_SHA256, PLATFORM, sha)
    return sha


def _install(zip_path: Path, **kwargs):
    return rife_install.install(zip_path=zip_path, platform=PLATFORM, run_check=False, **kwargs)


def test_none_pinned_leaves_none_and_fails_nothing(installed):
    zip_path, target = installed
    report = _install(zip_path)
    assert report["installed"] == "now"
    assert report["serve"]["installed"] == "none" and "no release of it is pinned" in report["serve"]["reason"]
    assert not (target / SERVE).exists() and rife.serve_executable(target / rife.installed_binary(Path("."), PLATFORM).name) is None


def test_the_pinned_asset_build_py_packs_is_put_beside_the_binary_with_its_notice(installed, monkeypatch, tmp_path):
    zip_path, target = installed
    asset = _asset(tmp_path)
    assert asset.name == rife_install.serve_zip_name(PLATFORM) == _build_module().zip_name(PLATFORM)
    sha = _pin(monkeypatch, asset)
    report = _install(zip_path, serve_zip=asset)
    served = report["serve"]
    assert served["installed"] == "now" and served["path"] == str(target / SERVE) and "check" not in served
    assert (target / SERVE).read_bytes() == b"a resident binary\n" and os.access(target / SERVE, os.X_OK)
    notice = (target / rife_install.SERVE_NOTICE).read_text(encoding="utf-8")
    assert rife.RELEASE in notice and "the licence of src/ncnn/LICENSE.txt" in notice and "Apache License" in notice
    assert json.loads((target / rife_install.SERVE_MARKER).read_text(encoding="utf-8"))["sha256"] == sha
    assert sorted(p.name for p in target.parent.iterdir()) == [TOP]  # no staging left behind

    again = _install(zip_path, serve_zip=asset)
    assert again["installed"] == "already" and again["serve"]["installed"] == "already"
    assert _install(zip_path, serve_zip=asset, force=True)["serve"]["installed"] == "now"


def test_build_py_packs_the_same_zip_from_the_same_binary(tmp_path):
    first = _asset(tmp_path / "one").read_bytes()
    assert _asset(tmp_path / "two").read_bytes() == first
    with zipfile.ZipFile(_asset(tmp_path / "three")) as zf:
        top = rife_install.serve_zip_name(PLATFORM)[:-len(".zip")]
        assert sorted(zf.namelist()) == [f"{top}/NOTICE.txt", f"{top}/{SERVE}"]
        assert zf.getinfo(f"{top}/{SERVE}").external_attr >> 16 & 0o111


def test_an_asset_whose_hash_is_not_the_pin_is_not_installed(installed, monkeypatch, tmp_path):
    zip_path, target = installed
    monkeypatch.setitem(rife_install.SERVE_ZIP_SHA256, PLATFORM, "0" * 64)
    report = _install(zip_path, serve_zip=_asset(tmp_path))
    assert report["installed"] == "now" and report["serve"]["installed"] == "none"
    assert "SHA-256" in report["serve"]["reason"] and "0" * 64 in report["serve"]["reason"]
    assert not (target / SERVE).exists() and not (target / rife_install.SERVE_MARKER).exists()


def test_an_asset_without_the_binary_is_not_installed(installed, monkeypatch, tmp_path):
    zip_path, target = installed
    bad = tmp_path / rife_install.serve_zip_name(PLATFORM)
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr(f"{rife_install.serve_zip_name(PLATFORM)[:-4]}/NOTICE.txt", "notice\n")
    _pin(monkeypatch, bad)
    report = _install(zip_path, serve_zip=bad)
    assert report["serve"]["installed"] == "none" and SERVE in report["serve"]["reason"]
    assert not (target / SERVE).exists()


def test_one_whose_check_frame_is_refused_is_removed_so_the_engine_never_finds_it(installed, monkeypatch, tmp_path):
    zip_path, target = installed
    asset = _asset(tmp_path)
    _pin(monkeypatch, asset)

    def refused(serve, binary, model):
        assert serve == target / SERVE and binary.parent == target and model == target / rife.MODEL
        raise rife.RifeUnavailable("its check frame differs from the binary's on 475 of 4032 pixels, by up to 1")

    monkeypatch.setattr(rife_install, "serve_check", refused)
    monkeypatch.setattr(rife_install, "check", lambda *a, **k: {"passed": True})
    report = rife_install.install(zip_path=zip_path, platform=PLATFORM, serve_zip=asset)
    assert report["check"] == {"passed": True}
    assert report["serve"]["installed"] == "refused" and "475 of 4032" in report["serve"]["reason"]
    assert "path" not in report["serve"]
    for name in (SERVE, rife_install.SERVE_MARKER, rife_install.SERVE_NOTICE):
        assert not (target / name).exists()


def test_one_whose_check_frame_passes_is_kept_and_named(installed, monkeypatch, tmp_path):
    zip_path, target = installed
    asset = _asset(tmp_path)
    _pin(monkeypatch, asset)
    monkeypatch.setattr(rife_install, "serve_check", lambda *a: {"passed": True, "gpu": "Stand-in GPU", "augmentation": "-x -z"})
    monkeypatch.setattr(rife_install, "check", lambda *a, **k: {"passed": True})
    report = rife_install.install(zip_path=zip_path, platform=PLATFORM, serve_zip=asset)
    assert report["serve"]["installed"] == "now" and report["serve"]["check"]["gpu"] == "Stand-in GPU"
    assert (target / SERVE).is_file()


def test_the_check_is_the_engines_own_byte_for_byte(monkeypatch, tmp_path):
    """`serve_check` through the stand-ins of test_rife_serve: one that hands back the binary's frame passes, one that
    hands back another is refused."""
    binary, _ = _binary(tmp_path)
    monkeypatch.delenv("SPRITE_GEN_RIFE_TTA", raising=False)
    model = binary.parent / rife.MODEL
    (tmp_path / "same").mkdir()
    (tmp_path / "other").mkdir()
    same = _serve(tmp_path / "same", tmp_path / "same.txt")
    assert rife_install.serve_check(same, binary, model) == {"passed": True, "gpu": "Stand-in GPU", "augmentation": "-x -z"}
    other = _serve(tmp_path / "other", tmp_path / "other.txt", how="second")
    with pytest.raises(rife.RifeUnavailable, match=r"check frame .* differs"):
        rife_install.serve_check(other, binary, model)


def test_macos_is_offered_none(tmp_path):
    report = rife_install.install_serve(tmp_path, "macos")
    assert report["installed"] == "none" and "does not make the release's frames" in report["reason"]


def test_no_serve_installs_none(installed, monkeypatch, tmp_path):
    zip_path, _ = installed
    _pin(monkeypatch, _asset(tmp_path))
    assert "serve" not in _install(zip_path, serve=False)


@pytest.mark.skipif(rife.platform_release() != PLATFORM, reason="the command installs this machine's own platform")
def test_the_command_line_names_what_became_of_it(installed, monkeypatch, tmp_path, capsys):
    zip_path, target = installed
    asset = _asset(tmp_path)
    monkeypatch.setitem(rife_install.SERVE_ZIP_SHA256, PLATFORM, "0" * 64)
    rc = cli.main(["rife", "install", "--zip", str(zip_path), "--serve-zip", str(asset), "--no-check"])
    out, err = capsys.readouterr()
    assert rc == 0 and json.loads(out)["serve"]["installed"] == "none"
    assert f"the resident binary ({rife.SERVE}): none" in err
    rc = cli.main(["rife", "install", "--zip", str(zip_path), "--no-serve", "--no-check"])
    out, _ = capsys.readouterr()
    assert rc == 0 and "serve" not in json.loads(out)


def test_the_pins_name_released_zips_of_the_platforms_offered():
    """What is pinned is one SHA-256 a platform the resident binary is offered for, of the tag the URL is made from."""
    assert set(rife_install.SERVE_ZIP_SHA256) <= set(rife_install.SERVE_PLATFORMS) and "macos" not in rife_install.SERVE_ZIP_SHA256
    for sha in rife_install.SERVE_ZIP_SHA256.values():
        assert len(sha) == 64 and set(sha) <= set("0123456789abcdef")
    assert rife_install.SERVE_TAG.startswith(f"rife-serve-{rife.RELEASE}-")
    assert rife_install.SERVE_URL.format(tag=rife_install.SERVE_TAG, name=rife_install.serve_zip_name("windows")) == (
        "https://github.com/jeonghwanko/sprite-gen-agent/releases/download/"
        f"rife-serve-{rife.RELEASE}-1/rife-ncnn-vulkan-serve-{rife.RELEASE}-windows.zip")


def test_a_pinned_platform_downloads_its_zip_from_the_tags_release(installed, monkeypatch, tmp_path):
    zip_path, target = installed
    asset = _asset(tmp_path)
    _pin(monkeypatch, asset)
    fetched = []

    def download(url, dest):
        fetched.append(url)
        dest.write_bytes(asset.read_bytes())

    monkeypatch.setattr(rife_install, "_download", download)
    report = _install(zip_path)
    url = rife_install.SERVE_URL.format(tag=rife_install.SERVE_TAG, name=rife_install.serve_zip_name(PLATFORM))
    assert fetched == [url] and report["serve"]["source"] == url
    assert report["serve"]["installed"] == "now" and (target / SERVE).is_file()
