# SPDX-License-Identifier: Apache-2.0
"""Writing and checking a loop's animation: img2webp, verification, refusal and the loop report.

Moved out of `loop.py` unchanged; `loop` re-exports every name, so `loop.<name>` still works."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen import local_tool
from sprite_gen._deps import np
from sprite_gen.spec.runio import atomic_write_text, new_stage
from sprite_gen.video.loop_params import (
    CYCLE_REFUSED_DIR,
    CYCLE_SOURCE_DIR,
    FOLLOW_SOURCE,
)


def img2webp_supports_exact(binary: str | None = None) -> bool:
    """libwebp added `-exact` to img2webp in 1.5.0 (checked against the 1.4.0 and 1.5.0 release
    binaries, 2026-09-09); older builds (Ubuntu 24.04: 1.3.x) reject the flag with "Unknown option"
    and would rewrite RGB under alpha 0. Detected from `-h`, never assumed."""
    binary = binary or shutil.which("img2webp")
    if not binary:
        return False
    try:
        proc = subprocess.run([binary, "-h"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "-exact" in (proc.stdout + proc.stderr)


def write_webp(frames: list[Image.Image], out: Path, *, delay_ms: int, loop: int = 0) -> None:
    """Animated WebP through libwebp's img2webp with -exact (Pillow's animated writer drops `exact`
    and rewrites RGB under alpha 0 to white; measured 2026-09-08). The frames reach img2webp as PNGs in a
    folder of this call's own beside `out` (`runio.new_stage`: `.<out>.<pid>-<random>.frames`), removed once
    it has run: a folder already there, an input's or another run's, is never written into or removed."""
    img2webp = shutil.which("img2webp")
    if not img2webp:
        raise SystemExit("video-loop: `img2webp` not found on PATH — install libwebp (brew install webp) for exact-alpha WebP")
    if not img2webp_supports_exact(img2webp):
        raise SystemExit(
            "video-loop: this img2webp has no `-exact` option (libwebp < 1.5; Ubuntu 24.04 ships 1.3.x) — "
            "install libwebp >= 1.5 (brew install webp, or the official binaries from storage.googleapis.com/downloads.webmproject.org)"
        )
    workdir = new_stage(out, ".frames")
    try:
        paths = []
        for k, im in enumerate(frames):
            p = workdir / f"webp-{k:03d}.png"
            im.save(p, compress_level=0)  # read once by img2webp, which encodes the pixels whatever the PNG's level
            paths.append(str(p))
        proc = local_tool.run([img2webp, "-loop", str(loop), "-lossless", "-exact", "-d", str(delay_ms), *paths, "-o", str(out)],
                              seconds=local_tool.TOOL_SECONDS, what="video-loop", capture_output=True, text=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if proc.returncode != 0 or not out.is_file():
        raise SystemExit(f"video-loop: img2webp failed: {proc.stderr.strip()[:300]}")


def verify_animation(path: Path, *, expect_frames: int, check_stale: bool, expected_loop: int | None = 0,
                     background: str | None = None) -> dict[str, Any]:
    # Closed on the way out: `video-follow` checks its animations before it moves them over the loop's.
    with Image.open(path) as im:
        n = getattr(im, "n_frames", 1)
        loop = im.info.get("loop")
        corners_ok = True
        background_pixel = Image.new("RGBA", (1, 1), background).getpixel((0, 0)) if background is not None else None
        background_ok = True
        opaque = True
        stale = 0
        for k in range(n):
            im.seek(k)
            f: Any = im.convert("RGBA")
            w, h = f.size
            corners_ok &= all(f.getpixel(c)[3] == 0 for c in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)))
            if background_pixel is not None:
                background_ok &= all(f.getpixel(c) == background_pixel for c in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)))
                opaque &= f.getchannel("A").getextrema() == (255, 255)
            if check_stale:
                packed = np.asarray(f).view("<u4")[..., 0]  # RGBA as one word: under 2**24 is alpha 0
                stale += int(np.count_nonzero(packed < 1 << 24)) - int(np.count_nonzero(packed == 0))
        report = {"file": path.name, "format": im.format, "n_frames": n, "loop": loop, "size": list(im.size), "corners_transparent": corners_ok, "stale_rgb_under_alpha0": stale, "bytes": path.stat().st_size}
        if background is not None:
            report.update(background=background, opaque=opaque)
    problems = []
    if n != expect_frames:
        problems.append(f"{n} frames, expected {expect_frames}")
    if loop != expected_loop:
        problems.append(f"loop={loop!r}, expected {expected_loop!r}")
    if background is not None:
        if not opaque:
            problems.append("a frame is not opaque on its declared background")
        if not background_ok:
            problems.append("a corner does not match the declared background")
    elif not corners_ok:
        problems.append("a corner is not transparent")
    if stale:
        problems.append(f"{stale} transparent pixels carry RGB")
    if problems:
        raise SystemExit(f"video-loop: {path.name} failed verification: {'; '.join(problems)}")
    return report


def handed_over(out_dir: Path, name: str) -> list[Path]:
    """What a cut that passed hands over in `out_dir`: its frames (`cycle/`), the strip and its metadata, the GIF and
    the WebP, and what a later alignment and follow-through keep beside them (`cycle.source/`, `follow.source.png`)."""
    return [out_dir / "cycle", out_dir / CYCLE_SOURCE_DIR, out_dir / FOLLOW_SOURCE, out_dir / f"{name}.strip.png",
            out_dir / f"{name}.strip.json", out_dir / f"{name}.gif", out_dir / f"{name}.webp"]


def refuse(target: Path, out_dir: Path, name: str, report: dict[str, Any], *, cut_written: bool = False) -> None:
    """Refuse the cut: take back all an earlier cut handed over in `out_dir` (`handed_over`), so a caller that does not
    read the exit code finds nothing to pass on, and write the report (`status: failed`). A cut this run wrote stays as
    `cycle.refused/`, in place of an earlier one."""
    out_dir = out_dir.expanduser().resolve()
    shutil.rmtree(out_dir / CYCLE_REFUSED_DIR, ignore_errors=True)
    if cut_written:
        (out_dir / "cycle").rename(out_dir / CYCLE_REFUSED_DIR)
    for path in handed_over(out_dir, name):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    write_loop_report(target, report)


def write_loop_report(target: Path, payload: dict[str, Any]) -> None:
    """Strict JSON, including refusals with undefined (zero-motion) ratios."""
    def finite(value: Any) -> Any:
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {key: finite(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [finite(item) for item in value]
        return value

    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, json.dumps(finite(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n")
