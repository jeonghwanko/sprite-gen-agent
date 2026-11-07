# SPDX-License-Identifier: Apache-2.0
# Modified for sprite-gen-agent (only the reading half of upstream 4.0.0's held_fill, for inspect-holds; see NOTICE).
"""Held cells: neighbour cells of a cut that show the same picture, found and read.

A clip may show a frame twice or three times in a row: a video model that stalls, a rate carried to another by
repeating frames (20 fps in 24 repeats one in six), a cut mapped to a higher rate by taking the nearer frame. Played,
the motion stops on that picture and jumps on: one pose, two or three cells. `same_steps` and `reading` find them: the
cells, the poses (the cells less the ones that show their neighbour's picture again) and each run of cells showing one
picture (`held_runs`, its first cell and how many).

The same picture is a step at most CHANGED_MAX of whose subject's pixels (premultiplied) change by more than
PIXEL_SAME, read twice: on the analysis thumbnail `video-loop` cuts by and on the frames as cut. A textured figure shown
three times, encoded by libx264 at crf 18, 23 and 28 and keyed by `video-frames`, changes no thumbnail pixel of a frame
shown again by more than 0.008, 0.020 and 0.051, and 0.012 %, 0.049 % and 0.11 % of its frame's subject pixels by more
than PIXEL_SAME. An arm 8 px thick on a 120 x 140 figure grown by one pixel changes 1.2 % of the thumbnail's subject
pixels (0.38 % of the frame's); a walker going away in a frame 280 px wide changes 4.9 % of the frame's in a step that
moves none of the thumbnail's. A small part's move of a fraction of a pixel reads as the same picture, as a hold plays.

This is the reading half of upstream sprite-gen 4.0.0's module, which `inspect-holds` (`sprite_gen.qa.holds`) reads by.
Upstream's other half makes held cells again with RIFE inside `video-loop` (`--fill-held`); this fork does not have it,
so no cut here changes: these functions only read."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PIL import Image

from sprite_gen._deps import np

PIXEL_SAME = 0.1  # a thumbnail pixel (premultiplied, 0..1) that changes by more than this has changed
CHANGED_MAX = 0.005  # the same picture: at most this share of the subject's thumbnail pixels changed


def _thumbs(frames: Sequence[Image.Image]) -> np.ndarray:
    from sprite_gen.video.loop import _small_features  # the thumbnail video-loop cuts by

    return np.stack([_small_features(f).reshape(-1, 4) for f in frames])


def premultiplied(frame: Image.Image) -> np.ndarray:
    a = np.asarray(frame.convert("RGBA"), dtype=np.float32) / 255.0
    return np.concatenate([a[..., :3] * a[..., 3:], a[..., 3:]], axis=-1)


def _same(a: np.ndarray, b: np.ndarray) -> bool:
    """Whether at most CHANGED_MAX of the subject's pixels (alpha over half in either) change by more than PIXEL_SAME:
    premultiplied RGBA, 0..1, pixels along the last axis."""
    subject = int((np.maximum(a[..., 3], b[..., 3]) > 0.5).sum())
    changed = int((np.abs(a - b).max(axis=-1) > PIXEL_SAME).sum())
    return changed <= CHANGED_MAX * subject


def same_steps(frames: Sequence[Image.Image]) -> list[bool]:
    """For each step of `frames` (cell k to k + 1), whether the two show the same picture: on the thumbnail and on the
    frames as cut (see the module)."""
    t = _thumbs(frames)
    return [_same(t[k], t[k + 1]) and _same(premultiplied(frames[k]), premultiplied(frames[k + 1]))
            for k in range(len(frames) - 1)]


def same_picture(frame: Image.Image, others: Sequence[Image.Image]) -> bool:
    """Whether `frame` shows the same picture as one of `others`: each pair read as `same_steps` reads a step."""
    return any(same_steps([other, frame])[0] for other in others)


def runs(same: Sequence[bool]) -> list[dict[str, int]]:
    """Each run of cells showing one picture: its first cell and how many (two or more). Not read across a wrap."""
    found, k = [], 0
    while k < len(same):
        if not same[k]:
            k += 1
            continue
        start = k
        while k < len(same) and same[k]:
            k += 1
        found.append({"start": start, "length": k - start + 1})
    return found


def reading(same: Sequence[bool], *, wrap: bool = False) -> dict[str, Any]:
    """The cells, the poses and the held runs of a cut whose steps are `same`. `wrap`: a loop whose last cell shows its
    first cell's picture again, one pose less, said `wrap_held` (the runs are not read across the wrap)."""
    held = runs(same)
    cells = len(same) + 1
    poses = cells - sum(r["length"] - 1 for r in held)
    return {"cells": cells, "poses": poses - 1 if wrap and poses > 1 else poses, "held_runs": held,
            **({"wrap_held": True} if wrap and poses > 1 else {})}


def wrap_same(frames: Sequence[Image.Image]) -> bool:
    """Whether a loop's last cell shows its first cell's picture again: the step across its wrap, read as `same_steps`
    reads a step."""
    return len(frames) > 1 and same_steps([frames[-1], frames[0]])[0]
