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
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmake_args = argv[argv.index("--") + 1:] if "--" in argv else []
    argv = argv[:argv.index("--")] if "--" in argv else argv
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--work", type=Path, default=Path.cwd() / "rife-serve-build",
                        help="where the release's tree is cloned and built (default ./rife-serve-build)")
    parser.add_argument("--out", type=Path, help="where the built binary is put (default: beside the binary the engine runs)")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    args = parser.parse_args(argv)

    root = tree(args.work.resolve())
    built = build(root, args.work.resolve() / "build", cmake_args, args.jobs)

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
    if placed.parent != binary.parent:
        print(f"SPRITE_GEN_RIFE_SERVE={placed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
