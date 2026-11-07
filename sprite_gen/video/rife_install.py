# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen rife install` — put the pinned RIFE where the engine finds it.

Downloads the rife-ncnn-vulkan release zip for this platform (pinned release and SHA-256,
docs/loop-repair.md section 1), checks the hash before anything is unpacked, and keeps only what
the engine runs: the binary, the model `rife-v4.6/`, the licence and readme (and on Windows the
OpenMP runtime the binary loads). The install lands in `rife.install_root()` — the user data
directory — which `rife.locate()` searches after SPRITE_GEN_RIFE and PATH. `--dir` installs
somewhere else and prints the SPRITE_GEN_RIFE line that points the engine at it.

Unpacked into a staging directory and renamed into place, so an interrupted install never leaves
a half-written RIFE where the engine looks; the model's two files are checked against their pinned
SHA-256 (`rife.MODEL_SHA256`) before it is. The Windows zip's `flownet.param` has CRLF line ends: it
is checked, and installed, with LF ones (`rife.pinned_bytes`), the bytes the other zips ship, which
the report names under `line_ends_turned`. Running it again over a finished install of the same
zip does nothing but the check. The check makes one frame between two discs through the
installed binary, so a machine that has the files but no working Vulkan finds out here.

With the binary it puts the resident binary (`rife.SERVE`, docs/loop-repair.md section 1, "The resident binary")
beside it, where this repository has released one for the platform and pinned its zip's SHA-256 (`SERVE_ZIP_SHA256`;
Windows and Linux, not macOS), and checks it as the engine does: one frame of the check pair through it and through
the binary, byte for byte. None pinned, a download that fails, a zip of another hash or a frame of other bytes leaves
none beside the binary and fails nothing: the binary runs once per run, as before. `--no-serve` skips it;
`--serve-zip FILE` installs it from an asset already downloaded, checked against the pin the same way.

`--backend torch` (the default where SPRITE_GEN_RIFE_BACKEND names torch) installs the same zip for the
torch backend, which reads the model only (`rife.locate_model()`): a Linux machine the binary does not run
on (aarch64) is not refused, and the check frame is made through PyTorch on SPRITE_GEN_RIFE_DEVICE
(`pip install "sprite-gen[torch]"` first; an image built without a GPU checks with SPRITE_GEN_RIFE_DEVICE=cpu).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as platform_mod
import shutil
import stat
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.video import rife
from sprite_gen.util.console import utf8_streams

# SHA-256 of each release zip, measured on the downloaded files (docs/loop-repair.md section 1).
ZIP_SHA256 = {
    "macos": "4a63a1f3c9c715773c57d2ee51df1b315ed20cd6c63103e45c483ecc4400b595",
    "ubuntu": "1e2c7ee7fa7daa326542d50622f0afedc80cf6f1858bda411d16385ffa5cdf68",
    "windows": "d8e4d772d26cd8006ef0ad0bc82eb191b53c68677d1ae2f42506d74cbbbea606",
}
# The machines each zip's binary runs on (`platform.machine()`, lower case). The macOS binary is
# universal; the Linux and Windows ones are x86-64 only.
ZIP_MACHINES = {"macos": None, "ubuntu": {"x86_64", "amd64"}, "windows": {"amd64", "x86_64"}}
URL = "https://github.com/nihui/rife-ncnn-vulkan/releases/download/{release}/{name}"
MARKER = ".sprite-gen-install.json"
# The resident binary (`rife.SERVE`, native/rife-serve/serve.cpp) as this repository's releases ship it: built from the
# release's tree by .github/workflows/rife-serve.yml (native/rife-serve/build.py --zip), checked on a real GPU of each
# platform, then pinned here by the SHA-256 of its zip (docs/loop-repair.md section 1, "The resident binary"). A platform
# with no pin gets none, and the binary runs once per run as before. macOS is not offered: no binary built today makes
# the release's macOS binary's frames, so the engine would refuse it there.
SERVE_TAG = f"rife-serve-{rife.RELEASE}-1"
SERVE_URL = "https://github.com/jeonghwanko/sprite-gen-agent/releases/download/{tag}/{name}"
# rife-serve-20221029-1, built by rife-serve run 42326667616 from bb0ed0e2 and checked against the release binary on
# 2026-11-07 — its check frame and 66 frames between 11 real probe pairs, all 132 runs byte for byte: windows on an NVIDIA
# RTX 3050 Laptop GPU (driver 545.92, Windows 11), ubuntu on an NVIDIA RTX 4060 (driver 580, Ubuntu 24.04).
SERVE_ZIP_SHA256: dict[str, str] = {
    "windows": "a1c86bd2883dabd937659f91d785e9083d269216863e4bd8b6380e05bc5346d8",
    "ubuntu": "a819a0d14a14001cb35932e3dbc4da8c48f1b34592dd889b90465dcba312de85",
}
SERVE_PLATFORMS = ("windows", "ubuntu")
SERVE_MARKER = ".sprite-gen-serve.json"
SERVE_NOTICE = rife.SERVE + ".NOTICE.txt"
CHUNK = 1 << 20
TIMEOUT_SECONDS = 60


def zip_name(platform: str) -> str:
    return f"{rife.BINARY}-{rife.RELEASE}-{platform}.zip"


def _platform(platform: str | None, backend: str = "ncnn") -> str:
    name = platform or rife.platform_release()
    if name not in ZIP_SHA256:
        raise SystemExit(f"rife install: rife-ncnn-vulkan {rife.RELEASE} ships no build for {sys.platform}; build it from "
                         f"github.com/nihui/rife-ncnn-vulkan and set SPRITE_GEN_RIFE to the binary (docs/loop-repair.md)")
    machines = ZIP_MACHINES[name]
    machine = platform_mod.machine().lower()
    if backend == "ncnn" and platform is None and machines is not None and machine not in machines:
        raise SystemExit(f"rife install: the {name} build of rife-ncnn-vulkan {rife.RELEASE} is x86-64 only and this machine "
                         f"is {machine}; build it from github.com/nihui/rife-ncnn-vulkan and set SPRITE_GEN_RIFE to the binary")
    return name


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    """`url` to `dest`, with a progress line on stderr every tenth of the way."""
    request = urllib.request.Request(url, headers={"User-Agent": "sprite-gen"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response, dest.open("wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done, shown = 0, 0
        for block in iter(lambda: response.read(CHUNK), b""):
            out.write(block)
            done += len(block)
            if total and done * 10 // total > shown:
                shown = done * 10 // total
                print(f"rife install: {done / 1e6:.0f} / {total / 1e6:.0f} MB", file=sys.stderr, flush=True)


def _kept(member: str, top: str, platform: str) -> bool:
    """Whether a zip member is part of what the engine runs (the rest are other models)."""
    if not member.startswith(top + "/") or member.endswith("/"):
        return False
    rel = member[len(top) + 1:]
    if any(part in ("", ".", "..") for part in rel.split("/")):
        return False
    if rel.startswith(rife.MODEL + "/"):
        return "/" not in rel[len(rife.MODEL) + 1:]
    if "/" in rel:
        return False
    return rel in (rife.BINARY, rife.BINARY + ".exe", "LICENSE", "README.md") or (platform == "windows" and rel.lower().endswith(".dll"))


def _unpack(zip_path: Path, staging: Path, platform: str) -> list[str]:
    top = f"{rife.BINARY}-{rife.RELEASE}-{platform}"
    kept: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if not _kept(info.filename, top, platform):
                continue
            rel = info.filename[len(top) + 1:]
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out, CHUNK)
            kept.append(rel)
    binary = staging / rife.installed_binary(Path("."), platform).name
    model = staging / rife.MODEL
    if not binary.is_file() or not (model / "flownet.param").is_file() or not (model / "flownet.bin").is_file():
        raise SystemExit(f"rife install: {zip_path.name} has no {top}/{binary.name} with {rife.MODEL}/flownet.param and flownet.bin")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return sorted(kept)


def _as_pinned(model: Path, files: dict[str, bytes]) -> list[str]:
    """The model's files in `model` written over with the bytes `rife.model_bytes` checked (`rife.pinned_bytes`: the
    Windows zip's flownet.param with LF line ends), so an install holds the pinned bytes on every platform; the files
    written. Turning CRLF to LF only ever shortens a file, so a file the size of its checked bytes is those bytes."""
    turned = []
    for name, data in files.items():
        path = model / name
        if path.stat().st_size != len(data):
            path.write_bytes(data)
            turned.append(f"{rife.MODEL}/{name}")
    return turned


def _complete(target: Path, platform: str, sha: str) -> bool:
    try:
        marker = json.loads((target / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    binary = target / rife.installed_binary(Path("."), platform).name
    return (marker.get("sha256") == sha and binary.is_file()
            and all((target / rife.MODEL / f).is_file() for f in ("flownet.param", "flownet.bin")))


def _disc(cx: int) -> Image.Image:
    a = np.zeros((64, 96, 4), dtype=np.uint8)
    yy, xx = np.mgrid[:64, :96]
    a[(yy - 32) ** 2 + (xx - cx) ** 2 <= 12 ** 2] = (200, 60, 40, 255)
    return Image.fromarray(a, "RGBA")


def check(binary: Path, model: Path, *, backend: str = "ncnn") -> dict[str, Any]:
    """One frame half way between two discs through `binary`, or through the torch backend from `model`: the disc must
    land in the middle."""
    try:
        made = rife.Rife(binary, model) if backend == "ncnn" else rife.Rife(runner=rife.torch_runner(model))
        mid = made(_disc(30), _disc(66), 0.5)
    except rife.RifeUnavailable as exc:
        linux = (" — on Linux without a GPU install `libvulkan1 mesa-vulkan-drivers`"
                 if backend == "ncnn" and sys.platform.startswith("linux") else "")
        raise SystemExit(f"rife install: installed, but the check frame failed: {exc}{linux}") from exc
    xs = np.nonzero(np.asarray(mid)[..., 3] > 127)[1]
    centre = float(xs.mean()) if xs.size else None
    if centre is None or abs(centre - 48) > 4:
        raise SystemExit(f"rife install: installed, but the check frame is wrong (disc centre {centre}, expected 48)")
    return {"passed": True, "disc_centre_x": round(centre, 2)}


def serve_zip_name(platform: str) -> str:
    """The release asset of the resident binary for a platform (native/rife-serve/build.py `zip_name`)."""
    return f"{rife.SERVE}-{rife.RELEASE}-{platform}.zip"


def _serve_name(platform: str) -> str:
    return rife.SERVE + (".exe" if platform == "windows" else "")


def _serve_complete(target: Path, platform: str, sha: str) -> bool:
    try:
        marker = json.loads((target / SERVE_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return marker.get("sha256") == sha and (target / _serve_name(platform)).is_file()


def _serve_unpack(zip_path: Path, work: Path, platform: str) -> tuple[Path, Path]:
    """The resident binary and its NOTICE.txt out of its release asset into `work`: nothing else in it is read."""
    top = serve_zip_name(platform)[:-len(".zip")]
    wanted = {f"{top}/{_serve_name(platform)}": work / _serve_name(platform), f"{top}/NOTICE.txt": work / SERVE_NOTICE}
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        missing = sorted(set(wanted) - names)
        if missing:
            raise ValueError(f"{zip_path.name} has no {', '.join(missing)}")
        for member, dest in wanted.items():
            with zf.open(member) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out, CHUNK)
    serve = wanted[f"{top}/{_serve_name(platform)}"]
    serve.chmod(serve.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return serve, wanted[f"{top}/NOTICE.txt"]


def _serve_remove(target: Path, platform: str) -> None:
    """The resident binary, its marker and its notice gone from `target`. Windows lets go of a process's executable a
    moment after it ends (and a scanner may hold a new one): a removal it refuses is tried again for a few seconds."""
    for name in (_serve_name(platform), SERVE_MARKER, SERVE_NOTICE):
        for attempt in range(20):
            try:
                (target / name).unlink(missing_ok=True)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.25)


def serve_check(serve: Path, binary: Path, model: Path) -> dict[str, Any]:
    """The engine's own check of a resident binary (`rife.Served`): one frame of the check pair through it and through
    `binary`, byte for byte. Raises `rife.RifeUnavailable` where they differ or it does not start."""
    served = rife.Served(serve, binary, model)
    try:
        served.frame(*rife.inputs(*(Image.fromarray(x, "RGB").convert("RGBA") for x in rife.check_pair())), rife.CHECK_T)
    finally:
        served.close()
    return {"passed": True, "gpu": served.gpu, "augmentation": " ".join(rife.tta()) or "none"}


def install_serve(target: Path, platform: str, *, zip_path: Path | None = None, force: bool = False,
                  run_check: bool = True) -> dict[str, Any]:
    """The resident binary beside the installed binary in `target`, from its pinned release asset; what happened. It is
    never what fails an install: a platform with no pin, a download that fails, a zip whose SHA-256 is another or a
    binary whose check frame is not the binary's leaves none beside the binary (`installed: none` or `refused`, with
    `reason`), and the binary runs once per run as before. One refused by its check is removed, so the engine never
    finds one it would refuse."""
    report: dict[str, Any] = {"zip": serve_zip_name(platform), "tag": SERVE_TAG}
    if platform not in SERVE_PLATFORMS:
        return {**report, "installed": "none", "reason": f"none is offered for {platform}: a binary built today does not "
                "make the release's frames there (docs/loop-repair.md section 1, \"The resident binary\")"}
    sha = SERVE_ZIP_SHA256.get(platform)
    if not sha:
        return {**report, "installed": "none", "reason": f"no release of it is pinned for {platform} yet"}
    binary = target / rife.installed_binary(Path("."), platform).name
    serve = target / _serve_name(platform)
    if not force and sha and _serve_complete(target, platform, sha):
        report["installed"] = "already"
    else:
        with tempfile.TemporaryDirectory(prefix=".rife-serve-", dir=target.parent) as td:
            work = Path(td)
            try:
                if zip_path is None:
                    url = SERVE_URL.format(tag=SERVE_TAG, name=serve_zip_name(platform))
                    print(f"rife install: downloading {url}", file=sys.stderr, flush=True)
                    zip_path = work / serve_zip_name(platform)
                    _download(url, zip_path)
                    report["source"] = url
                else:
                    report["source"] = str(zip_path)
                got = _sha256(zip_path)
                if got != sha:
                    return {**report, "installed": "none",
                            "reason": f"{zip_path.name} has SHA-256 {got}, expected {sha}; not installed"}
                staged, notice = _serve_unpack(zip_path, work, platform)
                _serve_remove(target, platform)
                notice.replace(target / SERVE_NOTICE)
                staged.replace(serve)
                (target / SERVE_MARKER).write_text(json.dumps({"tag": SERVE_TAG, "zip": serve_zip_name(platform),
                                                               "sha256": sha}) + "\n", encoding="utf-8")
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                _serve_remove(target, platform)
                return {**report, "installed": "none", "reason": f"{type(exc).__name__}: {exc}; not installed"}
        report["installed"] = "now"
    report["path"] = str(serve)
    if run_check:
        try:
            report["check"] = serve_check(serve, binary, target / rife.MODEL)
        except rife.RifeUnavailable as exc:
            _serve_remove(target, platform)
            report.pop("path")
            return {**report, "installed": "refused", "reason": f"{exc}; removed, so the binary runs once per run"}
    named = os.environ.get("SPRITE_GEN_RIFE_SERVE")
    if named is not None:
        report["note"] = (f"SPRITE_GEN_RIFE_SERVE={named!r} is set, so the engine runs " +
                          ("the binary once per run" if not named else named) + " instead of this one")
    return report


def install(*, dest: Path | None = None, zip_path: Path | None = None, platform: str | None = None,
            force: bool = False, run_check: bool = True, backend: str | None = None,
            serve: bool = True, serve_zip: Path | None = None) -> dict[str, Any]:
    try:
        backend = backend or rife.backend()
    except rife.RifeUnavailable as exc:
        raise SystemExit(f"rife install: {exc}") from exc
    if backend not in rife.BACKENDS:
        raise SystemExit(f"rife install: backend {backend!r}: name ncnn or torch")
    name = _platform(platform, backend)
    sha = ZIP_SHA256[name]
    root = (dest or rife.install_root()).expanduser().resolve()
    binary = rife.installed_binary(root, name)
    target = binary.parent
    report: dict[str, Any] = {"backend": backend, "release": rife.RELEASE, "model": rife.MODEL, "platform": name,
                              "zip": zip_name(name), "sha256": sha, "dir": str(target), "binary": str(binary)}
    if not force and _complete(target, name, sha):
        report["installed"] = "already"
    else:
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".rife-install-", dir=root) as td:
            work = Path(td)
            if zip_path is None:
                url = URL.format(release=rife.RELEASE, name=zip_name(name))
                print(f"rife install: downloading {url}", file=sys.stderr, flush=True)
                zip_path = work / zip_name(name)
                _download(url, zip_path)
                report["source"] = url
            else:
                report["source"] = str(zip_path)
            got = _sha256(zip_path)
            if got != sha:
                raise SystemExit(f"rife install: {zip_path.name} has SHA-256 {got}, expected {sha}; nothing was installed")
            staging = work / target.name
            report["files"] = _unpack(zip_path, staging, name)
            try:
                model = rife.model_bytes(staging / rife.MODEL)
            except rife.RifeUnavailable as exc:
                raise SystemExit(f"rife install: {exc}; nothing was installed") from exc
            report["line_ends_turned"] = _as_pinned(staging / rife.MODEL, model)
            (staging / MARKER).write_text(json.dumps({"release": rife.RELEASE, "zip": zip_name(name), "sha256": sha}) + "\n",
                                          encoding="utf-8")
            if target.exists():
                shutil.rmtree(target)
            staging.rename(target)
        report["installed"] = "now"
    if run_check:
        report["check"] = check(binary, target / rife.MODEL, backend=backend)
    if backend == "ncnn" and serve:
        report["serve"] = install_serve(target, name, zip_path=serve_zip, force=force, run_check=run_check)
    if backend == "torch":
        # What the torch backend will read: SPRITE_GEN_RIFE_MODEL before the install root.
        try:
            found = str(rife.locate_model())
        except rife.RifeUnavailable:
            found = None
        report["engine_finds"] = found
        if found != str((target / rife.MODEL).resolve()):
            report["note"] = (f"the torch backend reads SPRITE_GEN_RIFE_MODEL before {rife.install_root()}; to read this "
                              f"install set SPRITE_GEN_RIFE_MODEL={target / rife.MODEL}" + (f" (it now reads {found})" if found else ""))
        return report
    # What the engine will run: SPRITE_GEN_RIFE and PATH are searched before the install root.
    try:
        found = str(rife.locate()[0])
    except rife.RifeUnavailable:
        found = None
    report["engine_finds"] = found
    if found != str(binary.resolve()):
        report["note"] = (f"the engine looks in {rife.install_root()} only after SPRITE_GEN_RIFE and PATH; to run this "
                          f"install set SPRITE_GEN_RIFE={binary}" + (f" (it now finds {found})" if found else ""))
    return report


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True, metavar="<action>")
    p = sub.add_parser("install", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", type=Path, help=f"install root (default: the user data directory, {rife.install_root()}; "
                                            "another directory needs SPRITE_GEN_RIFE, which the command prints)")
    p.add_argument("--zip", type=Path, help=f"use this already-downloaded release zip ({zip_name('<platform>')}) instead of downloading it; "
                                            "its SHA-256 is checked all the same")
    p.add_argument("--force", action="store_true", help="unpack again over a finished install")
    p.add_argument("--no-check", action="store_true", help="skip the check frame made through the installed binary "
                                                           "(and through the resident binary)")
    p.add_argument("--no-serve", action="store_true", help=f"install no resident binary ({rife.SERVE}) beside it; the binary "
                                                           "then runs once per run")
    p.add_argument("--serve-zip", type=Path, help=f"use this already-downloaded resident binary release asset "
                                                  f"({serve_zip_name('<platform>')}) instead of downloading it; its SHA-256 "
                                                  "is checked against the pin all the same")
    p.add_argument("--backend", choices=rife.BACKENDS, help="what will run RIFE: ncnn (the binary) or torch (the model only, "
                                                            "its check frame through PyTorch on SPRITE_GEN_RIFE_DEVICE); "
                                                            "default SPRITE_GEN_RIFE_BACKEND, else ncnn")


def run(**kwargs: object) -> int:
    if kwargs.get("action") != "install":
        raise SystemExit(f"rife: unknown action {kwargs.get('action')!r}; expected install")
    report = install(dest=kwargs.get("dir"), zip_path=kwargs.get("zip"), force=bool(kwargs.get("force")),  # type: ignore[arg-type]
                     run_check=not kwargs.get("no_check"), backend=kwargs.get("backend"),  # type: ignore[arg-type]
                     serve=not kwargs.get("no_serve"), serve_zip=kwargs.get("serve_zip"))  # type: ignore[arg-type]
    if report.get("note"):
        print(f"rife install: note: {report['note']}", file=sys.stderr)
    served = report.get("serve") or {}
    if served.get("installed") in ("none", "refused"):
        print(f"rife install: the resident binary ({rife.SERVE}): {served['installed']} — {served.get('reason')}",
              file=sys.stderr)
    if served.get("note"):
        print(f"rife install: note: {served['note']}", file=sys.stderr)
    print(json.dumps({k: v for k, v in report.items() if k != "files"}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(prog="sprite-gen rife", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
