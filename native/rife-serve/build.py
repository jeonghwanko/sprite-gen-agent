# SPDX-License-Identifier: Apache-2.0
"""Build rife-ncnn-vulkan-serve, the resident binary (`sprite_gen.video.rife.Served`), from rife-ncnn-vulkan 20221029's
own tree: the release's commit, its ncnn submodule's commit, and serve.cpp (beside this file) added as a second
executable of its CMake project — rife.cpp, warp.cpp, the shaders and ncnn are the binary's. docs/loop-repair.md section 1,
"The resident binary", has what it costs and why.

    .venv/bin/python native/rife-serve/build.py                      # beside the located binary
    .venv/bin/python native/rife-serve/build.py --out DIR -- -DVulkan_INCLUDE_DIR=… -DVulkan_LIBRARY=…

It needs git, CMake, a C++ compiler and the Vulkan headers and loader the release's own build needs (on macOS, the Vulkan
SDK's MoltenVK, `-- -DUSE_STATIC_MOLTENVK=ON`); what follows `--` goes to CMake as it is. It ends with a frame made
through the built binary, which the engine checks against the binary's own frame byte for byte (`Served`): a build
whose frame differs is refused there, so it is refused here.

    python native/rife-serve/build.py --out dist --no-check --zip -- …   # a release asset, where no GPU is (CI)

`--zip` packs the built binary as this repository's releases ship it and `sprite-gen rife install` unpacks it
(`rife_install.serve_zip_name`): `<name>/rife-ncnn-vulkan-serve[.exe]` and `<name>/NOTICE.txt`, the notices and
licences of what it is built from. `--no-check` makes no frame and needs no installed binary: a build made where there
is no GPU is checked where there is one before it is published, and again by the engine on every machine it runs on.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = "https://github.com/nihui/rife-ncnn-vulkan.git"
RELEASE = "20221029"
# The release's commit and its ncnn submodule's: another tree builds other code, whose frames the engine refuses.
RELEASE_COMMIT = "a7532fc3f9f8f008cd6eecd6f2ffe2a9698e0cf7"
NCNN_COMMIT = "b4ba207c18d3103d6df890c0e3a97b469b196b26"
TARGET = "rife-ncnn-vulkan-serve"
CMAKE_TARGET = (f"\nadd_executable({TARGET} serve.cpp rife.cpp warp.cpp)\n"
                f"add_dependencies({TARGET} generate-spirv)\n"
                f"target_link_libraries({TARGET} ${{RIFE_LINK_LIBRARIES}})\n")
# The release zip's platform names (`rife.platform_release`), read here without the engine: a CI build runs this file
# on an interpreter the engine does not support.
PLATFORMS = {"win32": "windows", "darwin": "macos", "linux": "ubuntu"}
# What the built binary contains, by the licence file of each in the release's tree (relative to its root), and this
# repository's own licence for serve.cpp.
LICENCES = (("rife-ncnn-vulkan (RIFE, MIT)", "LICENSE"),
            ("ncnn (BSD 3-Clause, with its third-party notices)", "src/ncnn/LICENSE.txt"),
            ("glslang, built into ncnn", "src/ncnn/glslang/LICENSE.txt"),
            ("libwebp (BSD 3-Clause), linked by the release's CMake project", "src/libwebp/COPYING"))
ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


def _head(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def tree(work: Path) -> Path:
    """The release's tree in `work`: cloned with its submodules where it is not there, and refused where its commit or its
    ncnn's is another."""
    root = work / "rife-ncnn-vulkan"
    if not root.exists():
        work.mkdir(parents=True, exist_ok=True)
        _run("git", "clone", "--depth", "1", "--branch", RELEASE, "--recurse-submodules", "--shallow-submodules",
             REPOSITORY, str(root))
    for repo, want in ((root, RELEASE_COMMIT), (root / "src" / "ncnn", NCNN_COMMIT)):
        got = _head(repo)
        if got != want:
            raise SystemExit(f"build: {repo} is at {got}, not {want} ({REPOSITORY} {RELEASE}): its frames would be another "
                             "build's; remove it, or name an empty --work")
    return root


def build(root: Path, build_dir: Path, cmake_args: list[str], jobs: int) -> Path:
    """serve.cpp added to the tree's CMake project and its target built: the executable's path."""
    src = root / "src"
    shutil.copyfile(HERE / "serve.cpp", src / "serve.cpp")
    lists = src / "CMakeLists.txt"
    text = lists.read_text(encoding="utf-8")
    if TARGET not in text:
        lists.write_text(text + CMAKE_TARGET, encoding="utf-8")
    _run("cmake", "-S", str(src), "-B", str(build_dir), "-DCMAKE_BUILD_TYPE=Release",
         # the release's CMake files predate CMake 4, which refuses a minimum below 3.5 unless told
         "-DCMAKE_POLICY_VERSION_MINIMUM=3.5", *cmake_args)
    _run("cmake", "--build", str(build_dir), "--config", "Release", "--target", TARGET, "--parallel", str(jobs))
    name = TARGET + (".exe" if sys.platform == "win32" else "")
    for found in (build_dir / name, build_dir / "Release" / name):
        if found.is_file():
            return found
    raise SystemExit(f"build: CMake finished, and no {name} is in {build_dir}")


def platform() -> str:
    """This machine's release platform name (`windows`, `macos`, `ubuntu`)."""
    for prefix, name in PLATFORMS.items():
        if sys.platform.startswith(prefix):
            return name
    raise SystemExit(f"build: rife-ncnn-vulkan {RELEASE} ships no build for {sys.platform}")


def zip_name(name: str) -> str:
    """The release asset's file name for a platform; `rife_install.serve_zip_name` names the same."""
    return f"{TARGET}-{RELEASE}-{name}.zip"


def _commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(HERE), "rev-parse", "HEAD"], check=True, capture_output=True,
                              text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return os.environ.get("GITHUB_SHA", "unknown")


def notice(root: Path) -> str:
    """NOTICE.txt of a release asset: what the binary is built from, and the licence of each part, whole."""
    parts = [f"{TARGET} {RELEASE}: the resident RIFE of sprite-gen-agent (docs/loop-repair.md section 1, "
             f"\"The resident binary\").\n\n"
             f"Built from rife-ncnn-vulkan {RELEASE} ({REPOSITORY}, commit {RELEASE_COMMIT}) with its ncnn (commit "
             f"{NCNN_COMMIT}), and native/rife-serve/serve.cpp of sprite-gen-agent "
             f"(https://github.com/jeonghwanko/sprite-gen-agent, commit {_commit()}), added to the release's CMake project "
             "as a second executable. serve.cpp is Apache License 2.0; the rest is the release's, unchanged, under the "
             "licences below.\n"]
    for title, rel in (("sprite-gen-agent: serve.cpp (Apache License 2.0)", None), *LICENCES):
        path = HERE.parent.parent / "LICENSE" if rel is None else root / rel
        parts.append(f"\n{'=' * 78}\n{title}\n{'=' * 78}\n\n{path.read_text(encoding='utf-8', errors='replace').strip()}\n")
    return "".join(parts)


def package(built: Path, root: Path, out: Path, name: str) -> Path:
    """The release asset in `out`: the binary (debug symbols stripped on Linux, as the release's own is) and NOTICE.txt
    under one folder named as the zip is, with fixed times and modes so the same binary packs the same zip."""
    binary = out / built.name
    if name == "ubuntu" and shutil.which("strip") and binary.read_bytes()[:4] == b"\x7fELF":
        _run("strip", "-g", str(binary))
    top = zip_name(name)[:-len(".zip")]
    dest = out / zip_name(name)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for arcname, data, mode in ((f"{top}/{built.name}", binary.read_bytes(), 0o755),
                                    (f"{top}/NOTICE.txt", notice(root).encode("utf-8"), 0o644)):
            info = zipfile.ZipInfo(arcname, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100000 | mode) << 16
            zf.writestr(info, data)
    return dest


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmake_args = argv[argv.index("--") + 1:] if "--" in argv else []
    argv = argv[:argv.index("--")] if "--" in argv else argv
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--work", type=Path, default=Path.cwd() / "rife-serve-build",
                        help="where the release's tree is cloned and built (default ./rife-serve-build)")
    parser.add_argument("--out", type=Path, help="where the built binary is put (default: beside the binary the engine runs)")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    parser.add_argument("--zip", action="store_true", help=f"also pack it as a release asset in --out ({zip_name('<platform>')})")
    parser.add_argument("--no-check", action="store_true",
                        help="make no check frame (where there is no GPU, as in CI); needs --out")
    args = parser.parse_args(argv)
    if args.no_check and args.out is None:
        parser.error("--no-check needs --out: without the engine's binary there is no beside")

    root = tree(args.work.resolve())
    built = build(root, args.work.resolve() / "build", cmake_args, args.jobs)

    if args.no_check:
        out = args.out.expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built, out / built.name)
        print(f"built {out / built.name} (no check frame made)")
        if args.zip:
            asset = package(built, root, out, platform())
            print(f"packed {asset}, sha256 {hashlib.sha256(asset.read_bytes()).hexdigest()}")
        return 0

    from sprite_gen.video import rife

    binary, model = rife.locate()
    out = (args.out or binary.parent).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    placed = out / built.name
    shutil.copy2(built, placed)
    print(f"built {placed}")

    # the engine's own check, through the engine: a frame through the resident binary, checked against the binary's
    served = rife.Served(placed, binary, model)
    try:
        served.frame(*rife.inputs(*(rife.Image.fromarray(x, "RGB").convert("RGBA") for x in rife.check_pair())), rife.CHECK_T)
    except rife.RifeUnavailable as exc:
        raise SystemExit(f"build: built, and the engine refuses it: {exc}") from exc
    finally:
        served.close()
    print(f"checked: its frame is {binary.name}'s, byte for byte (augmentation {' '.join(rife.tta()) or 'none'}, "
          f"GPU {served.gpu})")
    if args.zip:
        asset = package(built, root, out, platform())
        print(f"packed {asset}, sha256 {hashlib.sha256(asset.read_bytes()).hexdigest()}")
    if placed.parent != binary.parent:
        print(f"SPRITE_GEN_RIFE_SERVE={placed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
