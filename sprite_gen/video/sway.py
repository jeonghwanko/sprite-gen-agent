# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen video-sway` — the Sway layer: a soft part of a looping body sways with the body's motion.

Its old name, `video-follow` (`sprite_gen.video.follow`, `follow_loop`), still runs it and gives the same
result. The loop's record of it is read under `sway` or `follow`; this release still writes it under
`follow`, and the strip as cut is still `follow.source.png`, the names a consumer reads them by.

A clip model draws a walk's body moving, but a soft part that hangs off it (a chest, a belly,
a pouch on a strap) mostly moves with the body as one piece, even when the prompt asks it to
bounce: at sprite size it reads as rigid. This puts that follow-through back after the loop is
cut.

Each frame of the strip is a cell of one cycle. The body's motion is read off the cells: how far
each cell's body lies from the first cell's. The silhouette is worn down from a quarter to half
of its depth in the first cell, so what swings on the body — a tail, a ponytail, an ear, a sword
or a rod held up, the legs and arms — is worn away and the head and torso are left; each cell is
laid where it overlaps the first cell most, worn alike, a pixel counting once for every level it
is still in, and then laid again on what the cells so laid have in common: what more than half of
them have at one place, so a part as thick as the body that is elsewhere in another cell (ears as
wide as the head, up in one cell and on the face in the next) does not pull that cell onto itself.
(Read off the top of the body instead, the motion was whatever came to the top: a
tail tip or a raised sword made it jump by the part's whole swing from one cell to the next.) A part
hung on the body is a damped mass: its offset x from where the body carries it answers the body's own
acceleration, x'' + 2·ζ·ω·x' + ω²·x = −body''. The loop repeats, so the answer is the periodic
steady state, solved per harmonic of the cycle: no start-up, no kick at a foot strike, and the
last frame leads into the first. `--gain` scales that physical answer and nothing else; it is
not normalised to a target size.

The part is an ellipse in the first cell (`--region cx,cy,rx,ry`, cell pixels), carried with
the body's bob. Inside it every pixel is moved by the offset times a weight that is 1 at the
centre and falls to 0 at the rim (cos²); outside it no pixel changes. The move is sampled as
premultiplied bilinear colour, so an edge never picks up the colour under a transparent pixel.
A move so large that the weight's slope folds the picture over (offset × π / (2 · radius) ≥ 1)
is refused. `--on-fold lower` gives each region the largest gain that does not fold it instead
(the offset grows in a straight line with the gain, so that gain is known without a search), and
never one under 1, the mass as measured: a region too small for that is held — it does not move,
and the record names it — and the strip is refused only when every region is.

The strip as it was before is kept as `follow.source.png` beside it; running the command again
reads from there, so a second follow-through never moves a moved strip. `video-loop` (a new
cut) and `video-cycle-align` (a new cycle) remove it, and the alignment records that the
follow-through was cleared. docs/video-pipeline.md section 6.

The regions are where somebody looked: in the first cell of the strip they were read on. An alignment
turns a loop to start elsewhere, and its first cell is then another; read again there, the motion is
taken from another cell 0 and the regions land on another place of the body. `--read-on <sha256>` names
the strip (as cut, before any follow-through) the regions were read on. That strip is this loop's own —
`same`, read as ever — or the cut this loop was aligned from (`video-cycle-align` records it, with its
reading and where every new cell is from): then the cut's reading is carried to each cell from the cut's
cell it is, the regions stay where they were read, and the damped mass is solved on the new order, length
and frame time (`transported`). A cell the alignment made between two of the cut's, or one that is not
the cut's cell pixel for pixel (a crop or a scale changed, and nothing records how), has no reading to
carry: refused `uncertain`, never carried by the nearest whole cell. Any other strip is refused
`no-match`. Without `--read-on` nothing of this is read or written.

`--stretch-floor D` asks every region's move to leave each pixel at least D of its area. The weight's
steepest slope is π/2 over each radius, so a move (ox, oy) leaves 1 − (π/2)·hypot(ox/rx, oy/ry) of it —
by the way the region moves, where the fold rule reads the smaller radius whichever way it moves. Each
region takes the largest gain, in steps of GAIN_STEP, that keeps the floor in every cell, and never one
above what the fold rule gave it: the floor only lowers. Under it the fold rule's handling stands (under 1
held, every region held refused; `--on-fold refuse` refuses). The record names the floor and its policy.

What carries a region is the lay of the body's outline (`carry_basis`): whose pixels are inside the
carried ellipse is not read, and nothing here moves a region on a guess of it.

A strip holds 64 cells and 32,000 px across at most, so a cycle of large frames is cut down to fit it. `--frames
<folder> --out <new folder>` sways a cycle's frames themselves — a loop's `cycle/`, or what `sprite-gen breathe` wrote
from it — at their own size and count, into a new folder of the same frames under the same names and `sway.json`, as
`sprite-gen breathe` writes one. It is the same ask answered by the same code, cell for cell (`solve_cells`): a strip
cut from those frames at scale 1 with none left out, its regions moved by its crop origin, moves its cells as these
are moved, at the frame time its meta writes — rounded to hundredths of a ms, as `--fps` is read here.

A breathing idle's head stays where it is and its body moves a pixel or two: read off the outline, that motion is
whole-pixel steps, and the damped mass answers their jerks, not the breath. `--breathe <breathe.json>` sways the part
on the breath instead: the Breathe layer's record of the breath the frames were given (`sprite-gen breathe` writes it
beside them), its phases checked against those its config gives. The chest rises lift × w, w the breath of the record's
in-breath share (its config's `inhale`, the phase the in-breath peaks at); the part rises as far, late by `--lag` of a
breath (the time warp the Breathe layer's shoulders are late by), and lies the difference behind:
offset = gain × lift × (w − w late), against the way the chest rises. Where the breath rests the offset is 0 — the first
cell, and where the loop closes — and a cell whose every offset is 0 is the cell as it was, byte for byte.

A loop set's way in or out of its loop carries on the loop's breath from a cell of it: its record says the loop's
length and the cell it starts at (`sprite-gen breathe --loop-cells N --start-cell K`), and its cell i has the phase of
the loop's cell (K + i) mod N. An offset is its phase's alone, so that cell is offset as the loop's cell is, from the
stretch's first cell on — there is nothing to wind up. The fold rule and the floor read the loop's whole breath, every
cell of it, so the stretch moves by the gain the loop moves by and the record's reach is the loop's. Its regions were
read on the loop's first cell, which is not one of its frames: `--reference` names that cell, the body is read from it
and the regions are carried from there, as on the loop's own cells.

A region's fifth number, flat, is the share of the way from its centre to its rim that moves in full — the Breathe
layer's chest weight (`breathe.flat_cos2`) — so a large part moves as one piece instead of pressed in at its middle;
the fold rule and the floor read it by the radius left (1 − flat of it). `--gain-across` moves across by a gain of its
own; a gain the fold rule or the floor lowers lowers both in proportion.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.curate.curation import BREATHE_SMOOTH_SHAPE_RANGES, state_breathe
from sprite_gen.effects import breathe as breathe_mod
from sprite_gen.effects.anatomy import Anatomy
from sprite_gen.frames import folder as folder_mod
from sprite_gen.spec.runio import atomic_write_text
from sprite_gen.util.gif_utils import save_clean_gif
from sprite_gen.video import loop as loop_mod
from sprite_gen.util.console import utf8_streams

VERB = "video-sway"
OLD_VERB = "video-follow"  # the old name, kept: it runs this and says its own name
# How the motion is read and a region moved, by name. `video-sway-inspect` writes it beside what it read, so
# what somebody saw under one way of reading is not taken for another's: a change that gives the same strip,
# regions and settings another carry, offset or picture takes a new name.
POLICY = "common-lay/damped-mass/cos2-bilinear/1"
BREATH_POLICY = "common-lay/breath-late/cos2-bilinear/1"  # the offset from the breath (`--breathe`), not the body's motion
FREQ_DEFAULT = 2.4  # Hz: a slow, soft part
ZETA_DEFAULT = 0.6  # damping ratio: it lags and settles, with no ringing
GAIN_DEFAULT = 2.5  # times the physical answer
BREATH_GAIN_DEFAULT = 1.0  # times the breath's rise less the part's own, late by --lag: under the breath's own rise
LAG_DEFAULT = 0.15  # of one breath: the part peaks this much of a breath after the chest
LAG_MAX = 1 / math.pi  # at it the late breath's time stops; past it, it runs backwards (breathe.smooth_shoulder_wave)
FLAT_RANGE = BREATHE_SMOOTH_SHAPE_RANGES["chest_flat"]  # a region's flat share: the Breathe layer's chest_flat, one weight
FRAMES_RECORD = "sway.json"  # beside the frames `--frames ... --out` writes
GAIN_MEASURED = 1.0  # the mass as measured: `--on-fold lower` does not go under it
GAIN_STEP = 0.01  # a lowered gain is a whole number of these, so the report's `gain` is the `--gain` to pass
ON_FOLD_MODES = ("refuse", "lower")
HARMONICS = 6  # of the cycle, for the body's motion: a step's shape, not its noise
ALPHA_SOLID = 128
WORN_FROM, WORN_TO = 0.25, 0.5  # of the body's depth in the first cell: the levels its silhouette is worn down to
SOURCE = loop_mod.FOLLOW_SOURCE
# How a reading made on the cut is carried to a loop aligned from it (`--read-on`): per whole cell of the cut, by the
# alignment's record of where each cell is from, and only to a cell that is that cell pixel for pixel.
TRANSPORT_POLICY = "cut-reading/whole-cell/1"
# How `--stretch-floor` reads a region's least area: the cos² weight's closed form, by direction, never above the
# fold rule's gain.
STRETCH_POLICY = "stretch-floor/cos2-direction/1"
# What carries a region into a cell: the lay of the body's outline. Whose pixels the carried ellipse holds is not read.
CARRY_BASIS = {"reading": "outline-lay", "region": "unverified"}
READ_ON = re.compile(r"[0-9a-f]{64}")  # a strip's sha256, as `--read-on` names it


def parse_region(text: str) -> tuple[float, ...]:
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--region wants cx,cy,rx,ry[,flat] (four or five numbers), got {text!r}") from exc
    if len(values) not in (4, 5):
        raise argparse.ArgumentTypeError(f"--region wants cx,cy,rx,ry[,flat] (four or five numbers), got {text!r}")
    try:
        return region_of(values)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--region {text!r}: {exc}") from exc


def region_of(values) -> tuple[float, ...]:
    """A region as the engine reads it: cx, cy, rx, ry, and its flat share where it has one. A flat share of 0 is no
    flat share: the region is read, moved and recorded as one of four numbers."""
    values = [float(v) for v in values]
    if values[2] <= 0 or values[3] <= 0:
        raise ValueError(f"radii must be positive, got {values[2]:g},{values[3]:g}")
    if len(values) == 5:
        if not FLAT_RANGE[0] <= values[4] <= FLAT_RANGE[1]:  # a NaN is not
            raise ValueError(f"flat is the share of the way from the centre to the rim that moves in full, "
                             f"{FLAT_RANGE[0]:g} to {FLAT_RANGE[1]:g}; got {values[4]:g}")
        if values[4] == 0:
            values = values[:4]
    return tuple(values)


def flat_of(region: tuple[float, ...]) -> float:
    """A region's flat share: the share of the way from its centre to its rim that moves in full (0: none)."""
    return region[4] if len(region) > 4 else 0.0


def region_radius(region: tuple[float, ...]) -> float:
    """The radius the fold rule reads a region by: its smaller radius, less its flat share — the weight falls from 1 to 0
    over what is left, and steepest there."""
    return min(region[2], region[3]) * (1 - flat_of(region))


def parse_read_on(text: str) -> str:
    if not READ_ON.fullmatch(text):
        raise argparse.ArgumentTypeError(f"--read-on wants the sha256 of the strip the regions were read on (64 lowercase "
                                         f"hexadecimal digits), got {text!r}")
    return text


def _run_depth(solid: np.ndarray, axis: int) -> np.ndarray:
    """Per solid pixel, how far along `axis` the nearest clear pixel is (1 beside one; past the cell's edge is clear)."""
    m = np.moveaxis(solid, axis, -1)
    n = m.shape[-1]
    at = np.broadcast_to(np.arange(n), m.shape)
    before = np.maximum.accumulate(np.where(m, -1, at), axis=-1)
    after = np.minimum.accumulate(np.where(m, n, at)[..., ::-1], axis=-1)[..., ::-1]
    return np.moveaxis(np.where(m, np.minimum(at - before, after - at), 0), -1, axis)


def wear(solid: np.ndarray, r: int) -> np.ndarray:
    """`solid` worn down by r: the pixels whose square of side 2·r + 1 around them is solid."""
    return _run_depth(_run_depth(solid, 1) > r, 0) > r


def depth(solid: np.ndarray) -> int:
    """How deep the body is at its deepest: the most it is worn down by and still has a pixel, plus one."""
    lo, hi = 0, (min(solid.shape) + 1) // 2  # worn by lo something is left; worn by hi nothing is
    if not solid.any():
        return 0
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if wear(solid, mid).any() else (lo, mid)
    return lo + 1


def _levels(solid: np.ndarray, worn: range) -> tuple[np.ndarray, int, int] | None:
    """`solid` worn down by each of `worn`, as one number per pixel: how many of those levels the pixel is still
    in. Each level lies inside the one before, so level i is where that number is over i. Cut to the box of the
    first level, with that box's top and left; None when nothing is left of `solid` worn down by the first."""
    level = wear(solid, worn[0])
    if not level.any():
        return None
    ys, xs = np.nonzero(level.any(axis=1))[0], np.nonzero(level.any(axis=0))[0]
    level = level[ys[0]: ys[-1] + 1, xs[0]: xs[-1] + 1]
    inside = level.astype(np.int32)
    for _ in worn[1:]:
        # Worn down by one more pixel: a square of side 2·r + 3 is one of side 2·r + 1 grown by one each way.
        across = np.pad(level, 1)
        level = across[1:-1, :-2] & across[1:-1, 1:-1] & across[1:-1, 2:]
        down = np.pad(level, 1)
        level = down[:-2, 1:-1] & down[1:-1, 1:-1] & down[2:, 1:-1]
        inside += level
    return inside, int(ys[0]), int(xs[0])


def _fft_length(n: int) -> int:
    """The first length from n on with no prime factor over 5: an FFT of it is fast."""
    while True:
        m = n
        for p in (2, 3, 5):
            while m % p == 0:
                m //= p
        if m == 1:
            return n
        n += 1


def _overlay(onto: tuple[list[np.ndarray], int, int], levels: tuple[np.ndarray, int, int]) -> tuple[int, int]:
    """The shift (dx, dy) of a cell from what it is laid on: where `onto` — per level a whole number at every
    pixel, with its top and left — moved by it, lands on the cell's own levels most, the numbers that land on
    the cell's pixels summed at every level and the levels summed."""
    (a, ya, xa), (b, yb, xb) = onto, levels
    # Long enough that no move wraps round (a.h + b.h - 1 each way, and so on), and fast.
    height, width = a[0].shape[0] + b.shape[0] - 1, a[0].shape[1] + b.shape[1] - 1
    shape = (_fft_length(height), _fft_length(width))
    # overlap[i, j]: what of `onto` lands on the cell's same level moved by (j - (a.w - 1), i - (a.h - 1)),
    # summed over the levels, for every move that meets.
    spectrum = sum(np.fft.rfft2((b > i).astype(np.float64), shape) * np.fft.rfft2(la[::-1, ::-1].astype(np.float64), shape)
                   for i, la in enumerate(a))
    # Sums of whole numbers are whole numbers: rounded, the FFT's own rounding (not the same on every machine)
    # is gone, so a tie is a tie on every machine, and it goes to the move nearest none.
    overlap = np.rint(np.fft.irfft2(spectrum, shape)[:height, :width]).astype(np.int64)
    shifts = [(int(j) - (a[0].shape[1] - 1) + xb - xa, int(i) - (a[0].shape[0] - 1) + yb - ya)
              for i, j in np.argwhere(overlap == overlap.max())]  # type: ignore[call-overload]
    return min(shifts, key=lambda s: (s[0] ** 2 + s[1] ** 2, s[1], s[0]))


def _common(levels: list[tuple[np.ndarray, int, int]], lays: list[tuple[int, int]],
            count: int) -> tuple[list[np.ndarray], int, int] | None:
    """What the cells' bodies have in common, each cell moved back by its lay: per level (`count` of them) and
    pixel, how many more of the cells are in that level there than are not (none, where no more are), cut to
    the box of what is left, with that box's top and left; None when no pixel is in more than half of the cells.

    The head and torso are in every cell at one place and count the whole number of cells. What swings and is
    thick enough to outlast the wearing — an ear as wide as the head, up in some cells and flopped onto the
    face in others; a leg forward and back — is at any one place in half of the cells or fewer, and counts
    nothing. A thick part that keeps one place in most of the cells counts as the body does: outlines alone
    do not tell the two apart."""
    tops = [top - dy for (_, top, _), (_, dy) in zip(levels, lays)]
    lefts = [left - dx for (_, _, left), (dx, _) in zip(levels, lays)]
    top, left = min(tops), min(lefts)
    height = max(t + inside.shape[0] for t, (inside, _, _) in zip(tops, levels)) - top
    width = max(l + inside.shape[1] for l, (inside, _, _) in zip(lefts, levels)) - left
    cells_in = np.zeros((count, height, width), np.int32)
    level = np.arange(count)[:, None, None]
    for (inside, _, _), t, l in zip(levels, tops, lefts):
        cells_in[:, t - top: t - top + inside.shape[0], l - left: l - left + inside.shape[1]] += inside > level
    more = np.maximum(0, 2 * cells_in - len(levels))
    # Each level lies inside the one before, in every cell: the first level's box holds them all.
    ys, xs = np.nonzero(more[0].any(axis=1))[0], np.nonzero(more[0].any(axis=0))[0]
    if not len(ys):
        return None
    return list(more[:, ys[0]: ys[-1] + 1, xs[0]: xs[-1] + 1]), top + int(ys[0]), left + int(xs[0])


def body_motion(cells: list[Image.Image]) -> tuple[np.ndarray, np.ndarray, int, list[int]]:
    """Per cell: how far its body lies from cell 0's, down and across (px); the body's height in cell 0,
    and the first and last level its silhouette is worn down to.

    The top of the body is whatever comes to the top — a tail tip, a ponytail, an ear, a sword held
    up — and read there the motion jumps from cell to cell. What swings on the body is thinner than
    the body it swings on: worn down by a quarter of the body's depth it is gone, and what is left
    is the head and torso. Each cell is laid where its silhouette, worn down by every whole number
    of pixels from a quarter to half of that depth, overlaps cell 0's, worn alike, most, the
    overlaps of every level summed: a pixel counts once for each level it is still in, so the
    deepest of the body counts most and nothing that comes and goes outweighs it, and a torso that
    is shallower in one cell (an arm swung away from it) still counts at its shallower levels.

    That lay answers to everything left in cell 0, and a part as thick as the body is left: where
    ears as wide as the head stand up in cell 0 and flop onto the face in another cell, the cell
    is laid ears on ears, the body tens of pixels off. So the cells are laid a second time, on what
    they have in common as first laid (`_common`), and that lay, less cell 0's own, is the motion:
    the ears are at one place in too few cells to count, and the cell lies head on head and torso
    on torso."""
    solids = []
    for k, cell in enumerate(cells):
        solid = np.asarray(cell.getchannel("A")) >= ALPHA_SOLID
        if not solid.any():
            raise ValueError(f"cell {k} has no solid body")
        solids.append(solid)
    ys = np.nonzero(solids[0].any(axis=1))[0]
    height0 = int(ys[-1] - ys[0])
    deepest = depth(solids[0])
    first = int(WORN_FROM * deepest)
    worn = range(first, max(first + 1, int(WORN_TO * deepest)))
    levels = []
    for k, solid in enumerate(solids):
        level = _levels(solid, worn)
        if level is None:
            raise ValueError(f"cell {k} has no body as deep as a quarter of cell 0's ({worn[0]} px)")
        levels.append(level)
    inside0, top0, left0 = levels[0]
    lays = [_overlay(([inside0 > i for i in range(len(worn))], top0, left0), level) for level in levels]
    common = _common(levels, lays, len(worn))
    if common is None:
        raise ValueError("no part of the body is at one place in more than half of the cells")
    lays = [_overlay(common, level) for level in levels]
    dx0, dy0 = lays[0]
    return (np.asarray([dy - dy0 for _, dy in lays], float), np.asarray([dx - dx0 for dx, _ in lays], float),
            height0, [worn[0], worn[-1]])


def _cells(strip: Image.Image, n: int, w: int, h: int) -> list[Image.Image]:
    return [strip.crop((k * w, 0, (k + 1) * w, h)) for k in range(n)]


def _cell_sha256(cell: Image.Image) -> str:
    """One cell's pixels, RGBA row by row: a cell drawn alike is the same one, whatever wrote its file."""
    return hashlib.sha256(cell.tobytes()).hexdigest()


def cut_origin(strip_bytes: bytes, meta: dict[str, Any]) -> dict[str, Any]:
    """What `video-cycle-align` keeps of a loop's strip as cut when it first aligns it (`cycle_align.origin`), so a
    follow-through whose regions were read on that cut can be carried to the aligned loop (`--read-on`): the strip's
    sha256 (the file's bytes, as `--read-on` names it), its cell size, scale and crop origin, which frame of the
    cycle each of its cells is (`sample_indices`), each cell's pixels by sha256, and the body's motion as read on
    it (`reading`: per cell how far its body lies from cell 0's, across and down, by `POLICY`). A strip whose body
    cannot be read keeps `reading` None and says why. Reads the bytes given and nothing else."""
    strip = Image.open(io.BytesIO(strip_bytes)).convert("RGBA")
    n, w, h = int(meta["frames"]), int(meta["w"]), int(meta["h"])
    rect, samples = meta.get("source_rect"), meta.get("sample_indices")
    origin: dict[str, Any] = {
        "strip_sha256": hashlib.sha256(strip_bytes).hexdigest(), "frames": n, "w": w, "h": h, "scale": meta.get("scale"),
        "crop_origin": None if rect is None else list(rect[:2]),
        # A cut made before its strip recorded the frames it took holds every frame of its cycle, or it was subsampled
        # and which ones is not known.
        "sample_indices": list(samples) if samples is not None else (list(range(n)) if n == meta.get("cycle_frames") else None),
    }
    if strip.size != (w * n, h):
        return origin | {"cells_sha256": None, "reading": None,
                         "reading_why": f"the strip is {strip.size[0]}x{strip.size[1]}, its meta says {w * n}x{h}"}
    cells = _cells(strip, n, w, h)
    origin["cells_sha256"] = [_cell_sha256(cell) for cell in cells]
    try:
        rows, cols, height0, worn = body_motion(cells)
    except ValueError as exc:
        return origin | {"reading": None, "reading_why": str(exc)}
    origin["reading"] = {"policy": POLICY, "carry_px": [[c, r] for c, r in zip(cols.tolist(), rows.tolist())],
                         "body_px": height0, "body_worn_px": worn}
    return origin


def read_on_relation(meta: dict[str, Any], strip_sha256: str, read_on: str, *, verb: str = VERB) -> str:
    """How the strip the regions were read on (`read_on`) stands to this loop: its own strip as cut (`same`), or the
    cut it was aligned from (`transported`); refused `no-match` otherwise."""
    if read_on == strip_sha256:
        return "same"
    origin = (meta.get("cycle_align") or {}).get("origin")
    if origin is not None and origin.get("strip_sha256") == read_on:
        return "transported"
    aligned_from = f"the cut it was aligned from ({origin['strip_sha256']})" if origin else "a cut it was aligned from (none is recorded)"
    raise SystemExit(f"{verb}: --read-on {read_on}: no-match — the regions were read on a strip that is neither this loop's "
                     f"strip as cut ({strip_sha256}) nor {aligned_from}; locate them again on this strip")


def transported_motion(meta: dict[str, Any], cells: list[Image.Image], read_on: str, *,
                       verb: str = VERB) -> tuple[np.ndarray, np.ndarray, int, list[int], list[int]]:
    """The cut's reading carried to `cells`, the strip of a loop aligned from the cut `read_on` names: per cell how far
    the body of the cut's cell it is lies from the cut's cell 0 (down, then across, as `body_motion` gives them), the
    body's height and wear as read on the cut, and which cell of the cut each cell is.

    The alignment records where each cell is from (`cycle_align.cells_from`). A cell made between two of the cut's
    has no reading, and is not given its nearer one's; a cell the record calls the cut's must be that cell pixel for
    pixel, so a crop, a scale or a drawing changed since — and not recorded as a transform — is caught here. Either
    is refused `uncertain`."""
    record = meta.get("cycle_align") or {}
    origin, cells_from = record["origin"], record.get("cells_from")

    def uncertain(why: str) -> SystemExit:
        return SystemExit(f"{verb}: --read-on {read_on} is the cut this loop was aligned from, but uncertain: {why}; the "
                          "reading is not carried — only a whole cell of the cut carries it, and none is taken by rounding")

    reading = origin.get("reading")
    if reading is None:
        raise uncertain(f"the cut's reading was not recorded ({origin.get('reading_why')})")
    if reading.get("policy") != POLICY:
        raise uncertain(f"the cut was read as {reading.get('policy')}, and this engine reads as {POLICY}")
    if not isinstance(cells_from, list) or len(cells_from) != len(cells):
        raise uncertain(f"the alignment records where {len(cells_from) if isinstance(cells_from, list) else 'no'} cells are "
                        f"from, and the strip has {len(cells)}")
    for k, entry in enumerate(cells_from):
        if "source" not in entry:
            i, j = entry["between"]
            raise uncertain(f"cell {k} is made between the cut's frames {i} and {j} (t {entry['t']:g})")
    (w, h), rect = cells[0].size, meta.get("source_rect")
    now = (w, h, meta.get("scale"), None if rect is None else list(rect[:2]))
    then = (origin["w"], origin["h"], origin["scale"], origin["crop_origin"])
    # A cut made before the strip recorded its crop says nothing of where it was cropped: there the cells' pixels
    # below are the whole test, as they are for every cell anyway.
    if now[:3] != then[:3] or (then[3] is not None and now[3] != then[3]):
        raise uncertain(f"the cut's crop or scale changed (cells {then[0]}x{then[1]} at scale {then[2]} from {then[3]}, now "
                        f"{w}x{h} at scale {now[2]} from {now[3]}) and no transform is recorded")
    frames, digests = origin["sample_indices"], origin["cells_sha256"]
    cut_cells = []
    for k, (cell, entry) in enumerate(zip(cells, cells_from)):
        i = entry["source"]
        if frames is None or i not in frames:
            raise uncertain(f"cell {k} is frame {i} of the cut's cycle, which the cut's strip does not hold")
        j = frames.index(i)
        if _cell_sha256(cell) != digests[j]:
            raise uncertain(f"cell {k} is not the cut's cell {j} as drawn: its pixels differ")
        cut_cells.append(j)
    carry = reading["carry_px"]
    return (np.asarray([carry[j][1] for j in cut_cells], float), np.asarray([carry[j][0] for j in cut_cells], float),
            int(reading["body_px"]), list(reading["body_worn_px"]), cut_cells)


def sway_offsets(motion: np.ndarray, fps: float, *, freq: float, zeta: float, gain: float) -> np.ndarray:
    """The part's offset from where the body carries it, per cell, in the cycle's steady state."""
    n = len(motion)
    spectrum = np.fft.rfft(motion - motion.mean())
    k = np.arange(len(spectrum))
    spectrum[k > HARMONICS] = 0
    omega = 2 * math.pi * fps / n * k  # each harmonic's angular frequency
    w = 2 * math.pi * freq
    # x = H·y with y the body's position: x'' + 2ζw x' + w² x = −y''  ⇒  H = Ω² / (w² − Ω² + 2iζwΩ)
    response = omega ** 2 / (w ** 2 - omega ** 2 + 2j * zeta * w * omega)
    return gain * np.fft.irfft(spectrum * response, n)


def fold_ratio(reach: float, radius: float) -> float:
    """How near a move of `reach` px is to folding a region whose smaller radius is `radius`: at 1
    the steepest part of the weight (cos², slope π / (2 · radius)) stops the picture, past it the
    picture runs backwards."""
    return reach * math.pi / (2 * radius)


def gain_limit(reach_per_gain: float, radius: float) -> float | None:
    """The gain at which a region of that radius folds; no gain folds it when the body is still."""
    return 2 * radius / (math.pi * reach_per_gain) if reach_per_gain > 0 else None


def lowered_gain(gain: float, reach_per_gain: float, radius: float) -> float:
    """The largest gain, in steps of GAIN_STEP and no more than `gain`, that `fold_ratio` passes."""
    limit = gain_limit(reach_per_gain, radius)
    if limit is None:
        return gain
    steps = math.ceil(limit / GAIN_STEP) - 1  # under the limit, never on it
    # The step count is rounded from floats: the one test that decides is `fold_ratio` itself.
    while steps > 0 and fold_ratio(steps * GAIN_STEP * reach_per_gain, radius) >= 1:
        steps -= 1
    return min(gain, round(steps * GAIN_STEP, 2))


def stretch_per_gain(radii: tuple[float, float], across: np.ndarray, down: np.ndarray, flat: float = 0.0) -> tuple[float, int]:
    """How much of a pixel's area a region's move takes per unit of gain, at its worst cell, and that cell: the cos²
    weight is steepest at π/2 over each radius, so a move (ox, oy) leaves 1 − (π/2)·hypot(ox/rx, oy/ry) of the area
    (`across`, `down`: the move at gain 1, per cell); a region with a flat share falls over what is left of each radius,
    1 − flat of it, and leaves 1 − (π/2)/(1 − flat)·hypot(ox/rx, oy/ry)."""
    rx, ry = radii
    per_cell = (math.pi / 2) / (1 - flat) * np.hypot(across / rx, down / ry)
    k = int(np.argmax(per_cell))
    return float(per_cell[k]), k


def floor_gain(gain: float, per_gain: float, floor: float) -> float:
    """The largest gain, in steps of GAIN_STEP and no more than `gain`, whose move leaves `floor` of a pixel's area or
    more (1 − gain · `per_gain` ≥ `floor`)."""
    if per_gain <= 0 or 1 - gain * per_gain >= floor:
        return gain
    steps = math.floor((1 - floor) / per_gain / GAIN_STEP) + 1
    # The step count is rounded from floats: the one test that decides is the area left at the gain written.
    while steps > 0 and 1 - round(steps * GAIN_STEP, 2) * per_gain < floor:
        steps -= 1
    return min(gain, round(steps * GAIN_STEP, 2))


def _sample(premultiplied: np.ndarray, sx: np.ndarray, sy: np.ndarray) -> np.ndarray:
    h, w = premultiplied.shape[:2]
    x0 = np.clip(np.floor(sx).astype(int), 0, w - 2)
    y0 = np.clip(np.floor(sy).astype(int), 0, h - 2)
    fx = np.clip(sx - x0, 0, 1)[..., None]
    fy = np.clip(sy - y0, 0, 1)[..., None]
    a, b = premultiplied[y0, x0], premultiplied[y0, x0 + 1]
    c, d = premultiplied[y0 + 1, x0], premultiplied[y0 + 1, x0 + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def region_weight(xx: np.ndarray, yy: np.ndarray, region: tuple[float, ...],
                  shift: tuple[float, float]) -> np.ndarray:
    """Per pixel of a cell, how much of a region's move it takes: 1 at the centre of the ellipse carried by
    `shift` — and over its flat share of the way to the rim, where it has one — 0 at its rim (cos²) and outside it:
    the Breathe layer's chest weight (`breathe.flat_cos2`). The pixels a region covers are those where this is over 0."""
    cx, cy, rx, ry = region[:4]
    r = np.sqrt(((xx - cx - shift[0]) / rx) ** 2 + ((yy - cy - shift[1]) / ry) ** 2)
    return breathe_mod.flat_cos2(r, flat_of(region))


def move_field(xx: np.ndarray, yy: np.ndarray, moves: list[tuple[list[tuple[float, ...]], tuple[float, float]]],
               shift: tuple[float, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Per pixel of a cell: whether a region of `moves`, carried by `shift`, covers it, and how far it is moved
    across and down. None when no region covers a pixel of the cell."""
    weights = []
    for regions, _ in moves:
        weight = np.zeros(xx.shape, np.float32)
        for region in regions:
            weight = np.maximum(weight, region_weight(xx, yy, region, shift))
        weights.append(weight)
    inside = np.logical_or.reduce([weight > 0 for weight in weights])  # type: ignore[call-overload]
    if not inside.any():
        return None
    largest = np.argmax(np.stack([math.hypot(*offset) * weight for (_, offset), weight in zip(moves, weights)]), axis=0)[None]
    move_x = np.take_along_axis(np.stack([offset[0] * weight for (_, offset), weight in zip(moves, weights)]), largest, 0)[0]
    move_y = np.take_along_axis(np.stack([offset[1] * weight for (_, offset), weight in zip(moves, weights)]), largest, 0)[0]
    return inside, move_x, move_y


def move_regions(cell: Image.Image, moves: list[tuple[list[tuple[float, ...]], tuple[float, float]]],
                 shift: tuple[float, float]) -> Image.Image:
    """`cell` with each group of regions moved by its own offset (dx, dy), their centres carried by `shift`.

    The groups' offsets are one motion times their gains, so where regions of different groups
    overlap the larger move wins: the move is the largest of the groups' weighted moves, which is
    nowhere steeper than the steepest group's own. Where every offset is 0 the cell is as it was, byte for
    byte — the colour under its clear pixels too, which a move clears."""
    if all(dx == 0 and dy == 0 for _, (dx, dy) in moves):
        return cell.copy()
    src = np.asarray(cell.convert("RGBA"), dtype=np.float32)
    h, w = src.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    field = move_field(xx, yy, moves, shift)
    if field is None:
        return cell.copy()
    inside, move_x, move_y = field
    pm = src.copy()
    pm[..., :3] *= pm[..., 3:4] / 255
    moved = _sample(pm, xx - move_x, yy - move_y)
    alpha = np.clip(np.round(moved[..., 3:4]), 0, 255)
    # Colour is read back from the premultiplied mix, and none is kept where the coverage
    # rounds to nothing (the WebP check refuses colour under alpha 0).
    moved[..., :3] = np.where(alpha > 0, moved[..., :3] * 255 / np.maximum(moved[..., 3:4], 1e-3), 0)
    moved[..., 3:4] = alpha
    out = np.where(inside[..., None], np.clip(np.round(moved), 0, 255), src).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


@dataclass(frozen=True, eq=False)
class Breath:
    """The breath a sway follows (`--breathe`): the Breathe layer's record of it — `sprite-gen breathe` writes
    breathe.json beside the frames it breathed — read and checked, and how late the part follows it (`--lag`)."""

    path: Path
    sha256: str  # of the record's bytes
    phases: list[float]  # per cell, the breath's phase
    beat: dict[str, Any]  # the breath count, or the cell of each in-breath's peak
    lift: float  # px the chest rises at the top of an in-breath (breathe.smooth_lift)
    heading: float  # degrees from straight up the chest rises toward (its chest_dir)
    inhale: float  # share of one breath the in-breath takes (its inhale): the phase the chest peaks at
    lag: float  # of one breath
    start_cell: int = 0  # the loop's cell the cells start at (its start_cell)
    loop_cells: int | None = None  # the cells of the loop they are a stretch of (its loop_cells); None: they are the loop
    loop: list[float] | None = None  # the loop's phases (breathe.breathe_loop); None: the cells' own

    def unit(self, phases: list[float] | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Per cell (or per phase of `phases`), the part's offset from where the breath carries it at gain 1, across and
        down: the chest rises `lift` × w (breathe.smooth_wave, of the record's in-breath share), the part as far late by
        `lag` (the time warp the Breathe layer's shoulders are late by, breathe.smooth_shoulder_wave), and the part lies
        the difference behind, against the way the chest rises. 0 where the breath rests — the loop's first cell, and
        where it closes. A phase's offset is the phase's alone, so a stretch's cell is offset as the loop's cell of its
        phase is, from its first cell on: nothing is wound up before it."""
        wave, late = breathe_mod.smooth_wave, breathe_mod.smooth_shoulder_wave
        share = np.asarray([wave(p, self.inhale) - late(p, self.lag, self.inhale)
                            for p in (self.phases if phases is None else phases)])
        heading = math.radians(self.heading)
        return self.lift * share * -math.sin(heading) + 0.0, self.lift * share * math.cos(heading)

    def whole(self) -> tuple[np.ndarray, np.ndarray]:
        """The offset at gain 1 over the whole breath the cells are swayed on, per phase: the loop's every cell for a
        stretch of a loop, so the fold rule and the floor read the stretch as they read its loop; else the cells'."""
        return self.unit(self.loop)

    def record(self) -> dict[str, Any]:
        return {"kind": "breath", "breathe_sha256": self.sha256, "lag": self.lag, "lift_px": round(self.lift, 4),
                "heading_deg": self.heading, "inhale": self.inhale, **self.beat, "start_cell": self.start_cell,
                "loop_cells": self.loop_cells}


def read_breath(path: Path, lag: float | None, *, verb: str = VERB) -> Breath:
    """The breath of a Breathe layer's record (breathe.json): a smooth breath, whose phases are the ones its config
    gives after the sidecar's checks (`state_breathe`, `breathe.fit_breathe_pattern`), whose chest rises as the anatomy it
    was baked with says, on the curve of its in-breath share (`inhale`; a record written before the key was there says
    the default by leaving it out, as the sidecar does). A record of a stretch of a loop says the loop's length and the
    cell it starts at (`loop_cells`, `start_cell`; one written before the keys were there is the loop itself from its
    first cell). A record written by hand for a breath made elsewhere is read alike and must say the same — its in-breath
    share and its place in the loop among it."""
    lag = LAG_DEFAULT if lag is None else float(lag)
    if not 0 < lag < LAG_MAX:  # a NaN is not
        raise SystemExit(f"{verb}: --lag is the share of a breath the part is late by, above 0 and under 1/π ({LAG_MAX:.4f}): at "
                         f"1/π the late breath stops, past it it runs backwards; got {lag:g}")
    path = Path(path).expanduser()
    try:
        data = path.read_bytes()
        report = json.loads(data.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"{verb}: --breathe {path}: not read ({exc})") from exc
    if not (isinstance(report, dict) and isinstance(report.get("config"), dict) and isinstance(report.get("phases"), list)
            and isinstance((report.get("resolved") or {}).get("anatomy"), dict)):
        raise SystemExit(f"{verb}: --breathe {path}: not a Breathe layer's record — sprite-gen breathe writes config, phases "
                         "and resolved.anatomy into breathe.json")
    mode = report["config"].get("mode") or "pixel"
    if mode != "smooth":
        raise SystemExit(f"{verb}: --breathe {path}: a {mode} breath — the part follows a smooth breath, which rests at the "
                         "first cell (a pixel breath's travelling wave moves it)")
    try:
        # A dict: the config is a dict (checked above), so state_breathe has a breathe entry to read.
        cfg: dict[str, Any] = state_breathe({"states": {"frames": {"breathe": report["config"]}}}, "frames")  # type: ignore[assignment]
    except SystemExit as exc:
        raise SystemExit(f"{verb}: --breathe {path}: its config is refused as a sidecar's would be — {exc}") from exc
    phases = report["phases"]
    if report.get("frames") != len(phases):
        raise SystemExit(f"{verb}: --breathe {path}: it says {report.get('frames')} frames and holds {len(phases)} phases")
    if breathe_mod.fit_breathe_pattern(len(phases), cfg) != phases:
        raise SystemExit(f"{verb}: --breathe {path}: its phases are not the ones its config gives for {len(phases)} cells "
                         "(breathe.fit_breathe_pattern) — the record was changed after it was written")
    try:
        anatomy = Anatomy.from_dict(report["resolved"]["anatomy"])
    except (KeyError, TypeError) as exc:
        raise SystemExit(f"{verb}: --breathe {path}: its resolved.anatomy is not one sprite-gen breathe writes ({exc})") from exc
    beat = {"breath_peaks": list(cfg["breath_peaks"])} if cfg.get("breath_peaks") else {"breaths": cfg["breaths"]}
    loop = breathe_mod.breathe_loop(len(phases), cfg)
    return Breath(path, hashlib.sha256(data).hexdigest(), [float(p) for p in phases], beat,
                  breathe_mod.smooth_lift(anatomy, cfg), float(cfg["chest_dir"]), float(cfg["inhale"]), lag,
                  cfg["start_cell"], cfg["loop_cells"], None if loop == phases else loop)


@dataclass(frozen=True, eq=False)
class Ask:
    """What a sway is asked, read and checked: the regions and how they move. What it is asked of — a loop's strip
    (`Request`) or a folder of a cycle's frames (`sway_frames`) — is apart from it."""

    regions: list[tuple[float, ...]]
    gain: float
    freq: float | None  # the damped mass's; None on the breath
    zeta: float | None
    on_fold: str
    read_on: str | None = None  # the sha256 of the strip as cut the regions were read on; None: this strip's, as ever
    stretch_floor: float | None = None  # the least area a moved pixel keeps; None: the fold rule alone
    gain_across: float | None = None  # the gain across asked; None: --gain, across as down
    breath: Breath | None = None  # the breath the part follows; None: the body's motion, answered by the damped mass

    @property
    def across(self) -> float:
        """The across move's gain over the down move's: `--gain-across` over `--gain`, 1 without it."""
        return 1.0 if self.gain_across is None or self.gain == 0 else self.gain_across / self.gain

    @property
    def policy(self) -> str:
        return POLICY if self.breath is None else BREATH_POLICY


def read_ask(regions: list[tuple[float, ...]], *, gain: float | None = None, freq: float | None = None,
             zeta: float | None = None, on_fold: str = "refuse", read_on: str | None = None,
             stretch_floor: float | None = None, breathe: Path | None = None, lag: float | None = None,
             gain_across: float | None = None, verb: str = VERB) -> Ask:
    """A sway's ask, read and checked. A number left out (None) is its default: the damped mass's gain, freq and zeta,
    or, on the breath (`breathe`), the breath's gain and lag."""
    # The numbers go into the record, which is JSON, and into the cells' arithmetic: a numpy number from a
    # caller (an int64 has no JSON form) is read as the float the command line gives, so every caller of the
    # same numbers writes the same strip and record.
    try:
        regions = [region_of(region) for region in regions]
    except ValueError as exc:
        raise SystemExit(f"{verb}: --region {exc}") from exc
    if not regions:
        raise SystemExit(f"{verb}: at least one --region cx,cy,rx,ry")
    if breathe is None and lag is not None:
        raise SystemExit(f"{verb}: --lag is how late the part follows the breath; it needs --breathe")
    if breathe is not None and (freq is not None or zeta is not None):
        raise SystemExit(f"{verb}: --freq and --zeta are the damped mass's; on the breath (--breathe) the part is late by --lag")
    gain = (GAIN_DEFAULT if breathe is None else BREATH_GAIN_DEFAULT) if gain is None else float(gain)
    if breathe is None:
        freq = FREQ_DEFAULT if freq is None else float(freq)
        zeta = ZETA_DEFAULT if zeta is None else float(zeta)
    if gain < 0 or (breathe is None and (freq <= 0 or not 0 < zeta)):  # type: ignore[operator]
        raise SystemExit(f"{verb}: --gain must be 0 or more, --freq and --zeta above 0")
    if gain_across is not None:
        gain_across = float(gain_across)
        if not gain_across >= 0:  # a NaN is not
            raise SystemExit(f"{verb}: --gain-across must be 0 or more")
        if gain == 0 and gain_across != 0:
            raise SystemExit(f"{verb}: --gain 0 leaves the cells as they were, and --gain-across is read against it")
    if on_fold not in ON_FOLD_MODES:
        raise SystemExit(f"{verb}: unknown --on-fold {on_fold!r}; expected one of {', '.join(ON_FOLD_MODES)}")
    if read_on is not None and not READ_ON.fullmatch(str(read_on)):
        raise SystemExit(f"{verb}: --read-on wants the sha256 of the strip the regions were read on (64 lowercase "
                         f"hexadecimal digits), got {read_on!r}")
    if stretch_floor is not None:
        stretch_floor = float(stretch_floor)
        if not 0 <= stretch_floor < 1:  # a NaN is neither
            raise SystemExit(f"{verb}: --stretch-floor is the least area a moved pixel keeps, at least 0 and under 1; got {stretch_floor:g}")
    breath = None if breathe is None else read_breath(breathe, lag, verb=verb)
    return Ask(regions, gain, freq, zeta, on_fold, None if read_on is None else str(read_on), stretch_floor, gain_across,
               breath)


@dataclass(frozen=True, eq=False)
class Request:
    """A sway asked of a loop directory, read and checked; nothing is written by reading it."""

    loop_dir: Path
    name: str
    meta_path: Path
    meta_bytes: bytes
    meta: dict[str, Any]
    ask: Ask

    @property
    def strip_path(self) -> Path:
        return self.loop_dir / f"{self.name}.strip.png"

    @property
    def source_path(self) -> Path:
        return self.loop_dir / SOURCE


def read_request(loop_dir: Path, regions: list[tuple[float, ...]], *, gain: float | None = None,
                 freq: float | None = None, zeta: float | None = None, on_fold: str = "refuse", verb: str = VERB,
                 read_on: str | None = None, stretch_floor: float | None = None, breathe: Path | None = None,
                 lag: float | None = None, gain_across: float | None = None) -> Request:
    loop_dir = loop_dir.expanduser().resolve()
    metas = sorted(loop_dir.glob("*.strip.json"))
    if len(metas) != 1:
        raise SystemExit(f"{verb}: {loop_dir}: expected one <name>.strip.json from video-loop, found {len(metas)}")
    meta_path = metas[0]
    name = meta_path.name[: -len(".strip.json")]
    meta_bytes = meta_path.read_bytes()
    meta = json.loads(meta_bytes.decode("utf-8"))
    if meta.get("kind") == "one-shot" or meta.get("loop") is False:
        raise SystemExit(f"{verb}: a one-shot plays once; the follow-through is a loop's steady state")
    ask = read_ask(regions, gain=gain, freq=freq, zeta=zeta, on_fold=on_fold, read_on=read_on, stretch_floor=stretch_floor,
                   breathe=breathe, lag=lag, gain_across=gain_across, verb=verb)
    return Request(loop_dir, name, meta_path, meta_bytes, meta, ask)


@dataclass(frozen=True, eq=False)
class Answer:
    """What an ask comes to on a cycle's cells: the body's motion, each region's gain, and every cell's move.

    `video-sway` writes it over the loop or into a new folder of frames; `video-sway-inspect` writes it down and
    changes nothing. All of them read the carry, the offsets and the moved cells here, and nowhere else."""

    ask: Ask
    cells: list[Image.Image]
    cols: np.ndarray  # per cell, how far its body lies from cell 0's (or the reference frame's): across
    rows: np.ndarray  # and down
    height0: int
    worn: list[int]
    requested: float
    reach_requested: float
    reach_per_gain: float
    radii: list[float]
    gains: list[float]  # per region; 0 where it is held
    held: list[bool]
    ratios: list[float]
    moves: dict[float, tuple[np.ndarray, np.ndarray]]  # per gain a region moves by, the offset per cell: across, down
    reaches: dict[float, float]
    gain: float  # the largest any region moves by; dx, dy and reach are that gain's
    dx: np.ndarray
    dy: np.ndarray
    reach: float
    groups: list[tuple[list[tuple[float, ...]], tuple[np.ndarray, np.ndarray]]]
    read_on: dict[str, Any] | None = None  # how the strip the regions were read on stands to this one (`--read-on`)
    stretch: dict[str, Any] | None = None  # the floor asked, and what it did to each region (`--stretch-floor`)
    reference: dict[str, Any] | None = None  # the frame the regions were read on, when not cell 0 (`--reference`)

    def carry(self, k: int) -> tuple[float, float]:
        """How far cell k's body lies from cell 0's, across and down: every region's ellipse is carried by it. Carried
        from the cut, the cells' motion is already from the cut's cell 0, where the regions were read; read from a
        reference frame, it is already from that frame."""
        if self.reference is not None or (self.read_on is not None and self.read_on["relation"] == "transported"):
            return self.cols[k], self.rows[k]
        return self.cols[k] - self.cols[0], self.rows[k] - self.rows[0]

    def moves_in(self, k: int) -> list[tuple[list[tuple[float, ...]], tuple[float, float]]]:
        """Cell k's groups of regions, each with its offset in that cell."""
        return [(group, (mx[k], my[k])) for group, (mx, my) in self.groups]

    def moved(self, k: int) -> Image.Image:
        return move_regions(self.cells[k], self.moves_in(k), self.carry(k))

    def record(self, source: str = SOURCE) -> dict[str, Any]:
        """The sway as the strip's meta records it, `source` the strip as cut kept beside it (`video-sway` adds its
        animations' checks); a frames folder's records it alike, the folder its source."""
        ask, requested = self.ask, self.requested
        drive = ({"freq_hz": ask.freq, "zeta": ask.zeta, "harmonics": HARMONICS} if ask.breath is None
                 else {"drive": ask.breath.record()})
        across = ({} if ask.gain_across is None else
                  {"gain_across": round(self.gain * ask.across, 4), "gain_across_requested": ask.gain_across})
        # Read from a reference frame, the body's bob spans that frame too (at 0): it is where the regions were read.
        cols, rows = (self.cols, self.rows) if self.reference is None else (np.append(self.cols, 0.0), np.append(self.rows, 0.0))
        return {
            "regions": [list(r) for r in ask.regions], "gain": self.gain, **across, **drive,
            "source": source, "body_px": self.height0, "body_worn_px": self.worn,
            "body_bob_px": [round(float(np.ptp(cols)), 2), round(float(np.ptp(rows)), 2)],
            "dx_px": np.round(self.dx, 2).tolist(), "dy_px": np.round(self.dy, 2).tolist(),
            "reach_px": round(self.reach, 2),
            "gain_requested": requested, "on_fold": ask.on_fold,
            "fold": {
                "lowered": any(g != requested for g in self.gains), "ratio": round(max(self.ratios), 3),
                "reach_requested_px": round(self.reach_requested, 2), "reach_per_gain_px": round(self.reach_per_gain, 3),
                # Where the regions took gains of their own, each says the gain it moved by and whether it
                # was held still (gain 0); where they all took one, the strip's `gain` is every region's.
                "regions": [{"radius_px": radius, "ratio": round(ratio, 3),
                             "gain_limit": None if (limit := gain_limit(self.reach_per_gain, radius)) is None else round(limit, 3)}
                            | ({} if len(set(self.gains)) == 1 else {"gain": g, "held": hold})
                            for radius, ratio, g, hold in zip(self.radii, self.ratios, self.gains, self.held)],
            },
        } | ({} if self.read_on is None else {"read_on": self.read_on}) | ({} if self.stretch is None else {"stretch": self.stretch}) \
          | ({} if self.read_on is None and self.stretch is None else {"carry_basis": dict(CARRY_BASIS)}) \
          | ({} if self.reference is None else {"reference": self.reference})


def solve(request: Request, strip: Image.Image, *, strip_name: str, verb: str = VERB,
          strip_sha256: str | None = None) -> Answer:
    """The answer to `request` on `strip`, the loop's strip as cut (an RGBA picture; `strip_name` names its file
    in a refusal, `strip_sha256` is its file's, read where the request names the strip its regions were read on): its
    cells answered as any cycle's cells are (`solve_cells`), at the frame time its meta records. Reads nothing else and
    writes nothing."""
    meta, ask = request.meta, request.ask
    n, w, h = int(meta["frames"]), int(meta["w"]), int(meta["h"])
    if strip.size != (w * n, h):
        raise SystemExit(f"{verb}: {strip_name} is {strip.size[0]}x{strip.size[1]}, the strip meta says {w * n}x{h}; "
                         "cut the loop again (video-loop)")
    cells = _cells(strip, n, w, h)
    read_on: dict[str, Any] | None = None
    if ask.read_on is not None:
        if strip_sha256 is None:
            raise ValueError("solve: a request with read_on needs the strip's sha256")
        read_on = {"sha256": ask.read_on, "relation": read_on_relation(meta, strip_sha256, ask.read_on, verb=verb)}

    def transported() -> tuple[np.ndarray, np.ndarray, int, list[int]]:
        # The cut's own reading, cell for cell: the regions are where they were read, on the cut's cell 0, and the
        # damped mass answers that motion in this loop's order, length and frame time.
        rows, cols, height0, worn, cut_cells = transported_motion(meta, cells, ask.read_on, verb=verb)  # type: ignore[arg-type]
        read_on.update(policy=TRANSPORT_POLICY, cut_cells=cut_cells)  # type: ignore[union-attr]
        return rows, cols, height0, worn

    carried = read_on is not None and read_on["relation"] == "transported"
    return solve_cells(ask, cells, float(meta["delay_ms"]), verb=verb, read_on=read_on,
                       read_body=transported if carried else None)


def _unit_move(ask: Ask, rows: np.ndarray, cols: np.ndarray, frame_ms: float | None) -> tuple[np.ndarray, np.ndarray]:
    """Per cell, the part's offset at gain 1, across and down, the across times its share of the gain (`Ask.across`):
    on the breath its offset from the breath (`Breath.unit`), or else the damped mass's answer to the body's motion at
    the cells' frame time."""
    if ask.breath is not None:
        across, down = ask.breath.unit()
    else:
        fps = 1000.0 / frame_ms  # type: ignore[operator]
        across = sway_offsets(cols, fps, freq=ask.freq, zeta=ask.zeta, gain=1.0)  # type: ignore[arg-type]
        down = sway_offsets(rows, fps, freq=ask.freq, zeta=ask.zeta, gain=1.0)  # type: ignore[arg-type]
    return ask.across * across, down


def _whole_move(ask: Ask, move: tuple[np.ndarray, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """The move at gain 1 the fold rule and the floor read: on the breath of a stretch of a loop (the record's
    start_cell and loop_cells — a loop set's way in or out), the loop's whole breath (`Breath.whole`), so the stretch
    moves by the gain its loop moves by and their seams do not jump; else the cells' own `move`."""
    if ask.breath is None or ask.breath.loop is None:
        return move
    across, down = ask.breath.whole()
    return ask.across * across, down


def solve_cells(ask: Ask, cells: list[Image.Image], frame_ms: float | None, *, verb: str = VERB,
                read_on: dict[str, Any] | None = None, read_body: Any = None,
                reference: tuple[Image.Image, dict[str, Any]] | None = None) -> Answer:
    """The answer to `ask` on a cycle's cells (RGBA pictures of one size, in order; `frame_ms` one frame's time, which
    the damped mass alone reads): a loop's strip cut into its cells (`solve`), or a folder of the cycle's frames
    (`sway_frames`) — one answer, whatever the cells came from. The body's motion is read on the cells (`body_motion`)
    unless `read_body` gives it (a reading carried from the cut, `--read-on`); with a `reference` (the frame the regions
    were read on and its record, `--reference`) it is read from that frame, laid first. Reads nothing else and writes
    nothing."""
    regions, gain, on_fold = ask.regions, ask.gain, ask.on_fold
    w, h = cells[0].size
    for cx, cy, *_ in regions:
        if not (0 <= cx < w and 0 <= cy < h):
            raise SystemExit(f"{verb}: --region centre {cx:g},{cy:g} is outside the {w}x{h} cell")
    if ask.breath is not None and len(ask.breath.phases) != len(cells):
        raise SystemExit(f"{verb}: --breathe {ask.breath.path} breathes {len(ask.breath.phases)} cells and there are "
                         f"{len(cells)} to sway: the part follows the breath cell for cell")
    if read_body is not None:
        rows, cols, height0, worn = read_body()
    else:
        try:
            rows, cols, height0, worn = body_motion(cells if reference is None else [reference[0], *cells])
        except ValueError as exc:
            raise SystemExit(f"{verb}: {exc}") from exc
        if reference is not None:  # each cell from the reference, which is not swayed
            rows, cols = rows[1:], cols[1:]
    across, down = _unit_move(ask, rows, cols, frame_ms)
    whole_across, whole_down = _whole_move(ask, (across, down))

    def move(g: float) -> tuple[np.ndarray, np.ndarray]:
        """The offset per cell at gain g, across and down: g times the move at gain 1."""
        return g * across, g * down

    def reach_at(g: float) -> float:
        """The largest offset at gain g over the whole move the fold rule reads (`_whole_move`)."""
        return float(np.max(np.hypot(g * whole_across, g * whole_down)))

    reach = reach_at(gain)
    radii = [region_radius(region) for region in regions]
    smallest = min(radii)
    requested, reach_requested = gain, reach
    # The offset is the gain times the answer at gain 1, so one move decides every gain.
    reach_per_gain = float(np.max(np.hypot(whole_across, whole_down)))
    gains, held = [requested] * len(regions), [False] * len(regions)
    if fold_ratio(reach, smallest) >= 1:
        if on_fold == "refuse":
            raise SystemExit(f"{verb}: a move of {reach:.1f} px folds a region of radius {smallest:g} px over itself; "
                             "lower --gain or give the region larger radii")
        # A gain for each region: every part hangs on the same body and answers the same motion, but
        # how far it moves before it folds is its own size, so a small part (an ear) does not hold a
        # large one (a chest) back. A region that does not fold keeps the gain asked for.
        folds = [fold_ratio(reach, radius) >= 1 for radius in radii]
        gains = [lowered_gain(requested, reach_per_gain, radius) if fold else requested for radius, fold in zip(radii, folds)]
        # One that would have to go under the mass as measured does not move at all; the strip is
        # refused only when that is every region.
        held = [fold and g < GAIN_MEASURED for fold, g in zip(folds, gains)]
        if all(held):
            k = radii.index(max(radii))  # the largest folds at the largest gain: if it is held, every region is
            every = f" (the largest of {len(regions)} regions: every one folds)" if len(regions) > 1 else ""
            raise SystemExit(f"{verb}: a move of {reach:.1f} px folds a region of radius {radii[k]:g} px over itself{every}, and "
                             f"--on-fold lower would have to go under --gain {GAIN_MEASURED:g} (the mass as measured moves "
                             f"{reach_per_gain:.1f} px; the largest gain that does not fold is {gains[k]:g}); give the region larger radii")
        gains = [0.0 if hold else g for g, hold in zip(gains, held)]
    stretch = None
    if ask.stretch_floor is not None:
        gains, held, stretch = _keep_floor(ask, radii, gains, held, (whole_across, whole_down), verb)
    # One move per gain: a region's offset is its gain times the answer at gain 1. The strip's `gain`
    # is the largest any region moves by, and `dx_px`/`dy_px` and `reach_px` are that gain's (the reach,
    # like the fold, over the whole move the fold rule reads).
    moves = {g: move(g) for g, hold in zip(gains, held) if not hold}
    reaches = {g: reach_at(g) for g in moves}
    gain = max(moves)
    dx, dy = moves[gain]
    reach = reaches[gain]
    ratios = [0.0 if hold else fold_ratio(reaches[g], radius) for radius, g, hold in zip(radii, gains, held)]
    groups = [([r for r, rg, hold in zip(regions, gains, held) if rg == g and not hold], offset) for g, offset in moves.items()]
    return Answer(ask, cells, cols, rows, height0, worn, requested, reach_requested, reach_per_gain, radii, gains, held,
                  ratios, moves, reaches, gain, dx, dy, reach, groups, read_on, stretch,
                  None if reference is None else reference[1])


def _keep_floor(ask: Ask, radii: list[float], gains: list[float], held: list[bool],
                move: tuple[np.ndarray, np.ndarray], verb: str) -> tuple[list[float], list[bool], dict[str, Any]]:
    """The fold rule's gains (`gains`, 0 where `held`) lowered where a region's move would leave a pixel less than
    `--stretch-floor` of its area (`move`: across and down per cell at gain 1), and the record of it. A region is never
    raised; one the floor takes under GAIN_MEASURED is held as the fold rule holds one, and the strip is refused when
    every region is — or, under `--on-fold refuse`, when the floor lowers any."""
    floor, requested = ask.stretch_floor, ask.gain
    per = [stretch_per_gain((region[2], region[3]), *move, flat=flat_of(region)) for region in ask.regions]
    floored = [floor_gain(requested, q, floor) for q, _ in per]  # type: ignore[arg-type]
    lowered = [g if hold else min(g, f) for g, f, hold in zip(gains, floored, held)]
    by_floor = [not hold and g < rule for g, rule, hold in zip(lowered, gains, held)]
    if any(by_floor) and ask.on_fold == "refuse":
        least = min(1 - requested * q for (q, _), by in zip(per, by_floor) if by)
        raise SystemExit(f"{verb}: --gain {requested:g} leaves a moved pixel {least:.3f} of its area, under --stretch-floor {floor:g}; "
                         "lower --gain, pass --on-fold lower or give the region larger radii")
    now_held = [hold or (by and g < GAIN_MEASURED) for hold, by, g in zip(held, by_floor, lowered)]
    if all(now_held):
        k = radii.index(max(radii))
        raise SystemExit(f"{verb}: --stretch-floor {floor:g} leaves every region held: to keep it each would move under --gain "
                         f"{GAIN_MEASURED:g} (the mass as measured; the largest region at {lowered[k]:g}); lower the floor or give "
                         "the regions larger radii")
    final = [0.0 if hold else g for g, hold in zip(lowered, now_held)]
    return final, now_held, {
        "policy": STRETCH_POLICY, "floor": floor,
        "regions": [{"per_gain": round(q, 6), "least_cell": cell, "rule_gain": rule, "floor_gain": f, "gain": g, "held": hold,
                     "least_area": 1.0 if hold else round(1 - g * q, 4), "under_measured": not hold and g < GAIN_MEASURED}
                    for (q, cell), rule, f, g, hold in zip(per, gains, floored, final, now_held)],
    }


def sway_loop(loop_dir: Path, regions: list[tuple[float, ...]], *, gain: float | None = None,
              freq: float | None = None, zeta: float | None = None, board: Path | None = None,
              on_fold: str = "refuse", read_on: str | None = None, stretch_floor: float | None = None,
              breathe: Path | None = None, lag: float | None = None, gain_across: float | None = None,
              verb: str = VERB) -> dict[str, Any]:
    request = read_request(loop_dir, regions, gain=gain, freq=freq, zeta=zeta, on_fold=on_fold, read_on=read_on,
                           stretch_floor=stretch_floor, breathe=breathe, lag=lag, gain_across=gain_across, verb=verb)
    loop_dir, name, meta_path, meta = request.loop_dir, request.name, request.meta_path, request.meta
    strip_path = request.strip_path
    source = request.source_path
    if not source.exists():
        shutil.copyfile(strip_path, source)
    data = source.read_bytes()
    answer = solve(request, Image.open(io.BytesIO(data)).convert("RGBA"), strip_name=source.name,
                   strip_sha256=hashlib.sha256(data).hexdigest(), verb=verb)
    cells = answer.cells
    n, (w, h) = len(cells), cells[0].size
    out = [answer.moved(k) for k in range(n)]
    joined = Image.new("RGBA", (w * n, h), (0, 0, 0, 0))
    for k, c in enumerate(out):
        joined.alpha_composite(c, (k * w, 0))
    delay_ms = max(20, round(float(meta["delay_ms"])))
    gif_path, webp_path = loop_dir / f"{name}.gif", loop_dir / f"{name}.webp"
    # Written and checked beside the loop first, each under its own name, and moved over the loop's files
    # only once the record is written too: a run that fails on the way (an animation that fails its check, a
    # record that does not serialise) leaves the strip, its animations and its record as they were. Staged as
    # files, not as payloads in memory (runio.atomic_write_set): img2webp writes a file, and the check reads one.
    stage = Path(tempfile.mkdtemp(prefix=".follow.", dir=loop_dir))
    try:
        staged = {path: stage / path.name for path in (strip_path, gif_path, webp_path, meta_path)}
        joined.save(staged[strip_path])
        save_clean_gif(out, staged[gif_path], duration_ms=delay_ms, loop=0, alpha_threshold=128)
        loop_mod.write_webp(out, staged[webp_path], delay_ms=delay_ms)
        # a cell left as it was beside its twin (a filmed ghost given way to the frame beside it) is one frame of both
        shown = loop_mod.shown_runs(out)
        record = {
            **answer.record(),
            "gif": loop_mod.verify_animation(staged[gif_path], expect_frames=shown, check_stale=False),
            "webp": loop_mod.verify_animation(staged[webp_path], expect_frames=shown, check_stale=True),
        }
        # One record, under the key consumers read it by (loop_mod.SWAY_RECORD); one read under the other key goes.
        kept = {k: v for k, v in meta.items() if k not in loop_mod.SWAY_RECORD_KEYS or k == loop_mod.SWAY_RECORD}
        atomic_write_text(staged[meta_path], json.dumps({**kept, loop_mod.SWAY_RECORD: record}, indent=2) + "\n")
        for path, written in staged.items():  # the record last, as it was written last
            os.replace(written, path)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    if board is not None:
        _board(cells, out, answer.dy, board)
        record["board"] = str(board)
    return {"strip": str(strip_path), **record}


def sway_frames(frames: Path, out: Path, regions: list[tuple[float, ...]], *, fps: float | None = None,
                gain: float | None = None, freq: float | None = None, zeta: float | None = None,
                board: Path | None = None, on_fold: str = "refuse", read_on: str | None = None,
                stretch_floor: float | None = None, breathe: Path | None = None, lag: float | None = None,
                gain_across: float | None = None, reference: Path | None = None, verb: str = VERB) -> dict[str, Any]:
    """The sway of a folder of a cycle's frames — a loop's `cycle/`, or what `sprite-gen breathe` wrote from it — at their
    own size and count, past the strip's caps on cells and width: the same frames under the same names, the regions
    (in frame pixels) moved, and `sway.json` beside them, written whole into a new folder (`frames.folder`); the frames
    folder is read and not written. Cell for cell it is the strip's sway (`solve_cells`): a strip cut from these frames
    at scale 1, none left out, its regions moved by its crop origin, at the frame time its meta writes (`frame_ms` of
    `--fps`), moves its cells as these are moved.

    With `reference` (a loop set's first cell, for a stretch of the loop whose regions were read on that cell) the
    regions are read on it and not on the first frame: each frame's body is read from it and the regions carried
    from there, as they are on the loop's own cells."""
    ask = read_ask(regions, gain=gain, freq=freq, zeta=zeta, on_fold=on_fold, read_on=read_on, stretch_floor=stretch_floor,
                   breathe=breathe, lag=lag, gain_across=gain_across, verb=verb)
    if ask.read_on is not None:
        raise SystemExit(f"{verb}: --read-on names the strip as cut that the regions were read on; frames are read as they are")
    if ask.breath is not None and fps is not None:
        raise SystemExit(f"{verb}: --fps is the damped mass's frame time; on the breath (--breathe) the part keeps the breath's beat")
    frames = Path(frames)
    folder = folder_mod.open_frames(frames, Path(out), verb)
    cells = folder.images
    frame_ms = None
    if ask.breath is None:
        frame_ms = folder_mod.cycle_frame_ms(frames, len(cells), fps, verb)
        if frame_ms is None:
            raise SystemExit(f"{verb}: --frames {frames}: the damped mass answers the body's motion in time — give --fps (a "
                             "video-loop cycle/ folder has its own in the loop's strip meta), or sway on the breath (--breathe)")
    basis, held = folder_mod.reference_frame(reference, folder, verb)
    answer = solve_cells(ask, cells, frame_ms, verb=verb, reference=None if held is None else (basis, held))
    moved = [answer.moved(k) for k in range(len(cells))]
    record = {"frames": len(cells), "frame_ms": frame_ms, **answer.record(source=str(frames))}
    folder_mod.publish(folder, moved, FRAMES_RECORD, record)
    if board is not None:
        _board(cells, moved, answer.dy, board)
        record["board"] = str(board)
    return {"out": str(folder.out), **record}


def _board(before: list[Image.Image], after: list[Image.Image], dy: np.ndarray, path: Path) -> None:
    """Before and after, side by side on white, at the frames where the part sits lowest and highest."""
    picks = [int(np.argmax(dy)), int(np.argmin(dy))]
    w, h = before[0].size
    board = Image.new("RGB", (w * 4 + 30, h), (128, 128, 128))
    x = 0
    for k in picks:
        for cell in (before[k], after[k]):
            ground = Image.new("RGBA", (w, h), (255, 255, 255, 255))
            ground.alpha_composite(cell)
            board.paste(ground.convert("RGB"), (x, 0))
            x += w + 10
    path.parent.mkdir(parents=True, exist_ok=True)
    board.save(path)


LOOP_DIR_HELP = "a video-loop output directory (after video-cycle-align, if the set is aligned)"


def add_request_arguments(parser: argparse.ArgumentParser, *, loop_dir: bool = True) -> None:
    """What a sway is asked with; `video-sway-inspect` takes the same of a loop directory and is asked alike
    (`video-sway` puts `--loop-dir` beside `--frames` itself)."""
    if loop_dir:
        parser.add_argument("--loop-dir", required=True, type=Path, help=LOOP_DIR_HELP)
    parser.add_argument("--region", action="append", type=parse_region, required=True,
                        help="cx,cy,rx,ry[,flat]: an ellipse over the soft part in the first cell, in cell pixels (repeatable); flat, "
                             f"{FLAT_RANGE[0]:g} to {FLAT_RANGE[1]:g}, is the share of the way from its centre to its rim that moves in "
                             "full, the Breathe layer's chest_flat (0 or left out: the weight falls from the centre)")
    parser.add_argument("--gain", type=float, default=None,
                        help=f"times the physical answer (default {GAIN_DEFAULT:g}; 1 is the mass as measured, 0 leaves the strip as it "
                             f"was); on the breath (--breathe), times the breath's rise less the part's own (default {BREATH_GAIN_DEFAULT:g})")
    parser.add_argument("--gain-across", type=float, default=None,
                        help="the gain across, apart from --gain, which is then the gain down (default: --gain); a region whose gain "
                             "--on-fold lower or --stretch-floor lowers moves across lowered in the same proportion")
    parser.add_argument("--on-fold", choices=ON_FOLD_MODES, default="refuse",
                        help=f"a move that folds a region over itself: refuse (default), or lower — each region takes the largest gain that does "
                             f"not fold it, never under {GAIN_MEASURED:g} (the mass as measured): a region that would have to is held still and "
                             "named in the record, and the strip is refused only when every region is")
    parser.add_argument("--freq", type=float, default=None, help=f"the part's own frequency in Hz (default {FREQ_DEFAULT:g})")
    parser.add_argument("--zeta", type=float, default=None, help=f"damping ratio (default {ZETA_DEFAULT:g}: lags and settles, no ringing)")
    parser.add_argument("--breathe", type=Path, default=None, metavar="BREATHE.JSON",
                        help="sway on the breath: the record sprite-gen breathe writes beside the frames it breathed (smooth). The part "
                             "follows the chest's rise late by --lag instead of answering the body's motion as a damped mass: its offset is "
                             "gain x the chest's rise x (w - w late), w the breath of the record's in-breath share (its config's "
                             "inhale), against the way the chest rises, 0 where the breath rests (the loop's first cell). The record's "
                             "phases must be the ones its config gives, one for each cell swayed; a stretch of a loop (its loop_cells, "
                             "start_cell) is folded as its whole loop is")
    parser.add_argument("--lag", type=float, default=None,
                        help=f"on the breath: the share of a breath the part peaks after the chest, above 0 and under 1/π (default {LAG_DEFAULT:g})")
    parser.add_argument("--read-on", type=parse_read_on, default=None, metavar="SHA256",
                        help="the sha256 of the strip as cut (before any follow-through) the regions were read on: this loop's own is "
                             "read as without it (relation same); the cut this loop was aligned from (video-cycle-align records it) has "
                             "its reading carried cell for cell (transported); a made cell or a cell geometry nothing records is "
                             "refused uncertain, any other strip no-match. Without it nothing of this is read or written")
    parser.add_argument("--stretch-floor", type=float, default=None, metavar="D",
                        help="the least area, 0 to under 1, a moved pixel keeps, read by the way each region moves: each region takes "
                             "the largest gain that keeps it, never above the fold rule's; under it the fold rule's handling stands "
                             f"(held under {GAIN_MEASURED:g}, every region held refused; --on-fold refuse refuses). No default: without "
                             "it the fold rule alone")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--loop-dir", type=Path, help=LOOP_DIR_HELP)
    where.add_argument("--frames", type=Path,
                       help="a folder of a cycle's frames (*.png in name order, one size: a video-loop cycle/, or what sprite-gen "
                            "breathe wrote from it), swayed at their own size and count into --out — past the strip's caps on cells "
                            "and width; the regions are in frame pixels")
    parser.add_argument("--out", type=Path, help=f"with --frames: a new folder for the swayed frames (same names) and {FRAMES_RECORD}")
    parser.add_argument("--fps", type=float, default=None,
                        help="with --frames: their rate, for the damped mass (a video-loop cycle/ folder has its own in the loop's strip "
                             "meta); a frame's time is rounded to hundredths of a ms, as a strip's meta writes it")
    parser.add_argument("--reference", type=Path, default=None, metavar="FRAME.png",
                        help="with --frames: the frame the regions were read on, when it is not the first of them — a loop set's "
                             "first cell, for a way in or out of the loop (sprite-gen breathe --loop-cells --start-cell): each "
                             "frame's body is read from it and the regions carried from there (one size with the frames)")
    add_request_arguments(parser, loop_dir=False)
    parser.add_argument("--board", type=Path, help="write a before/after picture at the frames where the part sits lowest and highest")


def fold_notes(record: dict[str, Any], verb: str = VERB) -> list[str]:
    """What to say of a record whose gain was lowered or whose regions were held, a line each."""
    fold, notes = record["fold"], []
    if "stretch" in record:  # a floor was asked: each region says what lowered it, the fold or the floor
        floor = record["stretch"]["floor"]
        for region, entry, folded in zip(record["regions"], record["stretch"]["regions"], fold["regions"]):
            name = "--region " + ",".join(f"{v:g}" for v in region)
            if entry["held"]:
                why = (f"folds at --gain {folded['gain_limit']:g}" if entry["rule_gain"] == 0 else
                       f"keeps --stretch-floor {floor:g} only at --gain {entry['floor_gain']:g}")
                notes.append(f"{verb}: {name} {why}, under {GAIN_MEASURED:g} (the mass as measured): it is held, and does not move")
            elif entry["gain"] != record["gain_requested"]:
                by = f"--stretch-floor {floor:g}" if entry["gain"] < entry["rule_gain"] else "the fold"
                notes.append(f"{verb}: {name} moves at --gain {entry['gain']:g}, lowered from {record['gain_requested']:g} by {by}")
        return notes
    if "held" in fold["regions"][0]:  # the regions took gains of their own
        for region, entry in zip(record["regions"], fold["regions"]):
            name = "--region " + ",".join(f"{v:g}" for v in region)
            if entry["held"]:
                notes.append(f"{verb}: {name} folds at --gain {entry['gain_limit']:g}, under {GAIN_MEASURED:g} (the mass as measured): "
                             "it is held, and does not move")
            elif entry["gain"] != record["gain_requested"]:
                notes.append(f"{verb}: --gain {record['gain_requested']:g} folds {name} (a move of {fold['reach_requested_px']:g} px); "
                             f"lowered to --gain {entry['gain']:g} for it")
    elif fold["lowered"]:
        notes.append(f"{verb}: --gain {record['gain_requested']:g} folds a region (a move of {fold['reach_requested_px']:g} px); "
                     f"lowered to --gain {record['gain']:g}")
    return notes


def run(**kwargs: object) -> int:
    verb = str(kwargs.get("verb") or VERB)
    asked = {"gain": kwargs.get("gain"), "freq": kwargs.get("freq"), "zeta": kwargs.get("zeta"), "board": kwargs.get("board"),
             "on_fold": str(kwargs.get("on_fold") or "refuse"), "read_on": kwargs.get("read_on"),
             "stretch_floor": kwargs.get("stretch_floor"), "breathe": kwargs.get("breathe"), "lag": kwargs.get("lag"),
             "gain_across": kwargs.get("gain_across")}
    if kwargs.get("frames") is not None:
        if kwargs.get("out") is None:
            raise SystemExit(f"{verb}: --frames needs --out, a new folder for the swayed frames")
        result = sway_frames(Path(str(kwargs["frames"])), Path(str(kwargs["out"])), list(kwargs["region"]),  # type: ignore[call-overload]
                             fps=kwargs.get("fps"), reference=kwargs.get("reference"), verb=verb, **asked)  # type: ignore[arg-type]
    else:
        for flag in ("out", "fps", "reference"):
            if kwargs.get(flag) is not None:
                raise SystemExit(f"{verb}: --{flag} goes with --frames; a loop directory is swayed where it is")
        result = sway_loop(Path(str(kwargs["loop_dir"])), list(kwargs["region"]), verb=verb, **asked)  # type: ignore[arg-type, call-overload]
    fold = result["fold"]
    own = "held" in fold["regions"][0]  # the regions took gains of their own
    for note in fold_notes(result, verb):
        print(note, file=sys.stderr)
    where = "out" if "out" in result else "strip"
    print(json.dumps({k: result[k] for k in (where, "regions", "gain", "gain_requested", "on_fold", "body_bob_px", "reach_px")}
                     | ({"region_gains": [entry["gain"] for entry in fold["regions"]]} if own else {})
                     | ({"drive": result["drive"]["kind"]} if "drive" in result else {})
                     | ({"read_on": result["read_on"]["relation"]} if "read_on" in result else {})
                     | ({"stretch_floor": result["stretch"]["floor"]} if "stretch" in result else {})
                     | ({"board": result["board"]} if "board" in result else {}), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None, verb: str = VERB) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(prog=f"sprite-gen {verb}", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)), verb=verb)


# The old names, kept: the same functions, called as they always were (the old verb in what they say).
def follow_loop(loop_dir: Path, regions: list[tuple[float, ...]], **kwargs: Any) -> dict[str, Any]:
    return sway_loop(loop_dir, regions, **{"verb": OLD_VERB, **kwargs})


follow_offsets = sway_offsets


if __name__ == "__main__":
    raise SystemExit(main())
