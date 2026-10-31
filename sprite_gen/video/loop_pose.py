# SPDX-License-Identifier: Apache-2.0
"""Where the figure stands in a cut: foot and body centres, drift, the wrap offset, ramps and standing height.

Moved out of `loop.py` unchanged; `loop` re-exports every name, so `loop.<name>` still works."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.video.loop_params import BODY_ANCHOR_BAND, BODY_ANCHOR_SEARCH, FOOT_BAND


def _scrub(image: Image.Image) -> int:
    px: Any = image.load()
    n = 0
    for y in range(image.height):
        for x in range(image.width):
            r, g, b, a = px[x, y]
            if a == 0 and (r or g or b):
                px[x, y] = (0, 0, 0, 0)
                n += 1
    return n


def foot_centre(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Mean x of the opaque pixels in the lowest FOOT_BAND of the subject's own bbox —
    the ground-contact line, which is what a placed sprite is judged by."""
    a = np.asarray(image.getchannel("A"))
    band = max(1, round((box[3] - box[1]) * FOOT_BAND))
    rows = a[box[3] - band : box[3], box[0] : box[2]]
    ys, xs = np.nonzero(rows >= 8)
    if xs.size == 0:
        return (box[0] + box[2]) / 2
    return float(box[0] + xs.mean())


def body_centre(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Mean x of every opaque pixel — the whole body's mass, which a gait swings far less
    than it swings the foot line."""
    a = np.asarray(image.getchannel("A"))[box[1] : box[3], box[0] : box[2]]
    xs = np.nonzero(a >= 8)[1]
    return float(box[0] + xs.mean()) if xs.size else (box[0] + box[2]) / 2


def drift_reference(frames: list[Image.Image], boxes: list[tuple[int, int, int, int]]) -> tuple[list[float], float, float]:
    """Per-frame alignment reference for `--anchor feet`.

    Drift is a slow translation; a gait is periodic. The cycle holds whole periods, so a
    straight line fitted to the body's centre across it carries the drift and not the
    step. Only that line is removed: the reference is the mean foot line riding the
    drift. Pinning each frame's own foot line instead pins the planted foot — which
    changes every step — and makes a body that stood still lurch by the stride.
    Returns (reference x per frame, drift removed in px, foot-line sway in px)."""
    feet = np.array([foot_centre(im, b) for im, b in zip(frames, boxes)])
    centres = np.array([body_centre(im, b) for im, b in zip(frames, boxes)])
    t = np.arange(len(frames), dtype=float)
    slope = float(np.polyfit(t, centres, 1)[0]) if len(frames) > 1 else 0.0
    trend = slope * t
    ref = float(np.mean(feet - trend)) + trend
    return [float(r) for r in ref], abs(slope * (len(frames) - 1)), float(np.ptp(feet - trend))


def body_wrap_offset(frames: list[Image.Image], *, band: float = BODY_ANCHOR_BAND, search: int = BODY_ANCHOR_SEARCH) -> int:
    """Horizontal offset (px) that best lays the LAST frame's head and torso over the FIRST's.

    A gait clip drifts a few pixels over one cycle, and the last-to-first wrap shows that
    drift as a sideways jump. The legs are mid-stride and the tail mid-swing at both ends,
    so they cannot say where the body is; the top of the first frame's box (head, torso,
    `band` of its height) can. Whole-pixel registration over that region, over a small
    horizontal search, is the one measurement `--anchor body` makes — it is then spread
    as a ramp across the cycle, never applied frame by frame (per-frame fitting turns a
    head bob into a full-body shiver, measured 2026-09-22)."""
    if len(frames) < 2:
        return 0
    first = np.asarray(frames[0], dtype=np.float32)
    last = np.asarray(frames[-1], dtype=np.float32)
    alpha = first[:, :, 3] >= 8
    rows = np.where(alpha.any(axis=1))[0]
    cols = np.where(alpha.any(axis=0))[0]
    if len(rows) == 0 or len(cols) == 0:
        return 0
    top, bottom, left, right = rows[0], rows[-1], cols[0], cols[-1]
    band_rows = slice(top, top + max(1, int((bottom - top) * band)))
    ref = first[band_rows, left : right + 1].reshape(-1, 4)
    cols = np.arange(left, right + 1)
    width = last.shape[1]

    def cost(dx: int) -> float:
        # The region of `last` rolled by dx, read in place: its columns taken round the frame's edge as np.roll does.
        return float(np.abs(ref - last[band_rows][:, (cols - dx) % width].reshape(-1, 4)).mean())

    return min(range(-search, search + 1), key=cost)


def ramp_frames(frames: list[Image.Image], wrap_dx: int) -> list[Image.Image]:
    """Shift frame k by round(wrap_dx * k / L) so the cycle's end returns to its start."""
    L = len(frames)
    if wrap_dx == 0 or L < 2:
        return frames
    out = []
    for k, im in enumerate(frames):
        dx = int(round(wrap_dx * k / L))
        if dx == 0:
            out.append(im)
            continue
        shifted = Image.new("RGBA", im.size, (0, 0, 0, 0))
        shifted.paste(im, (dx, 0))
        out.append(shifted)
    return out


def standing_height(path: Path, frame: int = 0) -> int:
    """The subject's height in clip frame `frame`: the clip's first frame, the base still's pose as filmed.

    Every clip starts from its still (image-to-video), so this is the same pose in every
    state of one character — which is what one `--body-height` across states has to measure
    to give that character one size. A walk or run whose cut is filmed at another size than the
    clip's first frame is read on the cut's first frame instead (run_loop).
    """
    box = Image.open(path).convert("RGBA").getchannel("A").point(lambda v: 255 if v >= 8 else 0).getbbox()
    if box is None:
        which = "the clip's first frame" if frame == 0 else f"frame {frame}, the cut's first"
        raise SystemExit(f"video-loop: {path.name}, {which}, has no subject to measure the standing height on (--body-height)")
    return box[3] - box[1]
