# SPDX-License-Identifier: Apache-2.0
"""Split a transparent UI kit sheet drawn on the fixed layout into nine-slice elements.

docs/ui-kit.md is the contract. One table (`LAYOUT`) is the layout: `gen --layout-guide ui-kit`
draws it (`draw_guide`) and says it (`guide_text`), and the splitter cuts along it. A sheet's shape
says which part of the table it holds (`SHEETS`): a square sheet the seven frames, a 2:3 sheet the
same frames at the same places and the fills, slot, badge, pips and joystick below them. The sheet's
alpha haze is cleared first (`alpha_floor.clear_haze`). The drawing is then read once, exactly
(`_judge`): the readings of its parts under the drifts the cut considers that keep the parts
clearest of their boxes' edges, and the cut is the one reading within 1 px of the least, or the
sheet is refused (`_decide`). Each element's insets and content padding are measured on its own
pixels; a fill is fitted to its bar frame's content area. The frames are required; an optional
element whose box is empty is left out and named under `missing`. A drawing that drifted further
than a cut accepts, an empty frame box, a drawing that fits the layout as well in two ways, a box
whose parts are centred outside it, or a drawing that lies on the layout under no drift refuses the
sheet (`Refusal`): no element is cut under a name the drawing does not give it. The
report names each element PNG by its path from the report's own directory (`report_file`,
`element_path`). A kit is written whole into a new or empty output directory (`_write_kit`): staged
in a hidden directory beside it and renamed into place in one step. That rename, or with the report
outside the directory the link that names the report, publishes the kit: a run that fails or is
interrupted before it leaves nothing, one after it the whole kit, and a killed run, or one
interrupted again while it cleans up, never part of one. A directory that holds anything is refused.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import heapq
import io
import itertools
import json
import math
import os
import secrets
import shutil
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, cast

from PIL import Image, ImageDraw

from sprite_gen._deps import np
from sprite_gen.frames.alpha_floor import clear_haze
from sprite_gen.spec.runio import inside_folder, same_folder
from sprite_gen.util.console import utf8_streams

REFERENCE = 1024
GAP = 48
MIN_PART = 16
MARGIN = 2
# Padding: the interior runs on while each step to the next pixel stays small and the pixel stays near
# the centre, so a smooth gradient face is interior and a border's sharp edge ends it.
PADDING_STEP = 16
PADDING_SPAN = 96
# A round part whose cut is more than this opaque outside the ellipse its bounding box holds was drawn on a
# square plate, not as a circle (docs/ui-kit.md "Layout").
ROUND_CORNER_FILL = 0.25
# Reading the drawing (docs/ui-kit.md "Reading the drawing"). The drift a cut accepts: along each axis a scale within
# DRIFT_SCALE of 1 and an offset within DRIFT_OFFSET px at 1024. The cut considers the drifts within that range and
# DRIFT_MARGIN of it beyond either end (a scale from 0.875 to 1.125, an offset of at most 120 px at 1024), so that a
# drawing drawn at the edge of the range is read under its own drift and a reading just beyond it is still a rival. A
# part keeps clear of its box's edges, as the guide asks, when it lies CLEARANCE px inside them, and two readings whose
# costs differ by no more than TIE px fit the drawing equally well. A least-squares fit is taken from FIT_MIN_BOXES or
# more boxes that hold parts.
DRIFT_SCALE = 0.1
DRIFT_OFFSET = 2 * GAP
DRIFT_MARGIN = 0.25
FIT_MIN_BOXES = 3
CLEARANCE = 1.0
TIE = 1.0
# The search (`_judge`) solves a span of drifts whole once its undecided parts can be read in at most LEAF_WAYS ways, or
# once it moves no box more than LEAF_WIDTH px. Its work bound is SEARCH_NODES spans, well above what drawn sheets need:
# past it, a sheet every reading of which is proven to cost more than TIE is refused as off the layout.
LEAF_WAYS = 48
LEAF_WIDTH = 1e-3
SEARCH_NODES = 6000
GUIDE_BACKGROUND = (0xF6, 0xF6, 0xF6)
GUIDE_BOX = (0x2F, 0x80, 0xED)
STRETCHES = ("xy", "x", "none")
SIDES = ("left", "right", "top", "bottom")
REPORT_KIND = "sprite-gen-ui-kit"
# A kit's staging directory takes the output directory's name with one rename. POSIX rename(2) takes the name of an
# absent or empty directory and fails on any other; Windows takes no existing directory's name, so an empty output
# directory is removed just before the rename there (Python docs, os.rename).
RENAME_ONTO_EMPTY_DIR = os.name != "nt"


@dataclass(frozen=True)
class Slot:
    name: str
    box: tuple[int, int, int, int]
    stretch: str
    where: str
    what: str
    # Padding is measured only for an element that holds content (text, digits, an icon, a bar's fill);
    # the others get 0 on every side, as insets are 0 along an axis the element keeps.
    content: bool = True
    # A fill: the bar frame whose content area it fills (docs/ui-kit.md "Fills").
    inside: str | None = None
    # A round part: the guide draws a circle in its box, and a cut with filled corners is flagged.
    round: bool = False
    # An optional element: when its box is empty the kit is made without it and the report names it under
    # `missing`; an empty box of a required element refuses the sheet (docs/ui-kit.md "Missing elements").
    optional: bool = False


LAYOUT = (
    Slot("panel", (48, 48, 576, 400), "xy", "the large box at the top left",
         "a large ornate panel frame with an empty centre"),
    Slot("button", (672, 48, 304, 128), "xy", "the upper box at the top right",
         "a wide button in its normal state"),
    Slot("button_pressed", (672, 224, 304, 128), "xy", "the box below it",
         "the same button pressed in"),
    Slot("card", (704, 400, 272, 576), "xy", "the tall box at the right",
         "a tall card frame with an empty centre"),
    Slot("hp_bar_frame", (48, 496, 608, 112), "x", "the long box at the left below the panel",
         "an empty horizontal health bar frame"),
    Slot("xp_bar_frame", (48, 656, 608, 112), "x", "the long box below it",
         "an empty horizontal experience bar frame"),
    Slot("timer_badge", (48, 816, 160, 160), "none", "the small square box at the bottom left",
         "a small round badge with an empty centre"),
    # The band below the square, on the 2:3 sheet only.
    Slot("hp_bar_fill", (48, 1024, 608, 64), "x", "the long thin box at the top left of the band",
         "the health bar's fill alone, a bare strip of colour with no frame, rim or end pieces around it",
         content=False, inside="hp_bar_frame", optional=True),
    Slot("xp_bar_fill", (48, 1136, 608, 64), "x", "the long thin box below it",
         "the experience bar's fill alone, the same kind of bare strip in its own colour", content=False,
         inside="xp_bar_frame", optional=True),
    Slot("slot", (48, 1248, 192, 192), "xy", "the square box below them at the left",
         "a square item slot frame with an empty centre", optional=True),
    Slot("badge", (288, 1248, 176, 112), "xy", "the small wide box to its right",
         "a small counter plate with an empty centre", optional=True),
    Slot("pip_on", (512, 1248, 96, 96), "none", "the small circle to the right of that",
         "a small round indicator dot, lit", content=False, round=True, optional=True),
    Slot("pip_off", (512, 1392, 96, 96), "none", "the small circle below it",
         "the same dot unlit", content=False, round=True, optional=True),
    Slot("joystick_base", (704, 1024, 272, 272), "none", "the large circle at the right of the band",
         "a round touch joystick base", content=False, round=True, optional=True),
    Slot("joystick_knob", (768, 1344, 144, 144), "none", "the smaller circle below it",
         "the joystick's round knob", content=False, round=True, optional=True),
)
FRAME_COUNT = 7
# Sheet shape -> (the size the layout's boxes are given at, the slots it holds). The 2:3 sheet keeps the
# square's seven boxes where they are and adds the band below them, so a square sheet cuts as it always has.
SHEETS = {
    "square": ((REFERENCE, REFERENCE), LAYOUT[:FRAME_COUNT]),
    "tall": ((REFERENCE, REFERENCE * 3 // 2), LAYOUT),
}


def sheet_layout(size: tuple[int, int]) -> tuple[tuple[int, int], tuple[Slot, ...]]:
    """The reference size and the slots of a sheet of ``size``: square, or 2:3, each within 1 %."""
    width, height = size
    for (ref_w, ref_h), slots in SHEETS.values():
        if abs(width * ref_h - height * ref_w) <= 0.01 * max(width * ref_h, height * ref_w):
            return (ref_w, ref_h), slots
    raise ValueError(f"a UI kit sheet must be square (1:1, the frames) or 2:3 (the whole kit); "
                     f"this one is {width}x{height}")


def boxes(size: tuple[int, int]) -> dict[str, tuple[int, int, int, int]]:
    """The boxes a sheet of ``size`` holds, as (x, y, w, h) in its pixels, in layout order."""
    (ref_w, ref_h), slots = sheet_layout(size)
    width, height = size
    sx, sy = width / ref_w, height / ref_h
    return {slot.name: (round(slot.box[0] * sx), round(slot.box[1] * sy),
                        round(slot.box[2] * sx), round(slot.box[3] * sy)) for slot in slots}


def _rect(x: int, y: int, w: int, h: int) -> dict[str, int]:
    return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}


def _element_png(name: str) -> str:
    """The file name of an element's PNG in the output directory."""
    return f"{name}.png"


def _places(slots: tuple[Slot, ...]) -> str:
    return "; ".join(f"in {slot.where}, {slot.what}" for slot in slots)


def guide_text(size: tuple[int, int] = (REFERENCE, REFERENCE)) -> str:
    """The prompt's half of the guide for a sheet of ``size``: what to draw in each box and how, in layout order."""
    _, slots = sheet_layout(size)
    if len(slots) == FRAME_COUNT:
        return (
            "Layout: the last attached image is a layout guide, not part of the artwork. It shows "
            f"{len(slots)} blue boxes on a grey sheet. Draw exactly one user interface element inside each box, "
            "filling most of its box without touching the box's edges, and leave everything outside the boxes "
            f"empty and transparent: {_places(slots)}. Every frame, button and bar has straight, even edges "
            "between its corners, so it can be stretched along them: put ornaments only in the corners, and on "
            "the bars only at their two ends. Leave every centre empty: no text, no letters, no numbers, no icons "
            "and no fill inside the bars. Do not reproduce the layout guide itself: no boxes, guide lines, guide "
            "colors or the guide's grey background may appear in the output."
        )
    frames, band = slots[:FRAME_COUNT], slots[FRAME_COUNT:]
    return (
        "Layout: the last attached image is a layout guide, not part of the artwork. It shows "
        f"{len(slots)} blue boxes and circles on a grey sheet: {len(frames)} in the square at the top and "
        f"{len(band)} in the band below it. Draw exactly one user interface element inside each box and circle, "
        "filling most of it without touching its edges, and leave everything outside them empty and transparent. "
        "In the square "
        f"at the top: {_places(frames)}. In the band below it: {_places(band)}. Every frame, button, bar, fill, "
        "slot and plate has straight, even edges between its corners, so it can be stretched along them: put "
        "ornaments only in the corners, and on the bars and the fills only at their two ends. Each fill is one "
        "solid strip with no frame of its own, the same colour all along its length (it may shade from its top "
        "edge to its bottom edge), with no marks, segments or highlights along it. Leave the centre of every "
        "frame, button, bar frame, slot and plate empty: no text, no letters, no numbers, no icons and no fill "
        "inside the bar frames. The two dots, the joystick base and the knob are circles drawn on the guide's "
        "circles and seen straight from the front, with nothing square around them: no plate, tile or backing, "
        "so the corners around each circle stay empty and transparent; the two dots are the same size. Do not "
        "reproduce the layout guide itself: no boxes, circles, guide lines, guide colors or the guide's grey "
        "background may appear in the output."
    )


def draw_guide(path: Path, size: tuple[int, int] = (REFERENCE, REFERENCE)) -> dict[str, Any]:
    """Draw the layout guide at ``path`` and answer its cell: the kind, the size and every box."""
    width, height = size
    image = Image.new("RGB", (width, height), GUIDE_BACKGROUND)
    draw = ImageDraw.Draw(image)
    line = max(2, round(3 * width / REFERENCE))
    scaled = boxes(size)
    shapes = {slot.name: draw.ellipse if slot.round else draw.rectangle for slot in LAYOUT}
    for name, (x, y, w, h) in scaled.items():
        shapes[name]((x, y, x + w - 1, y + h - 1), outline=GUIDE_BOX, width=line)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return {"kind": "ui-kit", "width": width, "height": height,
            "boxes": {name: _rect(*box) for name, box in scaled.items()}}


def _premultiplied(image: Image.Image) -> Any:
    pixels = np.asarray(image.convert("RGBA")).astype(np.float32)
    pixels[..., :3] *= pixels[..., 3:] / 255
    return pixels


def _stretch_band(pixels: Any) -> tuple[int, int, list[str]]:
    """Fixed slices before and after the stretch band along dimension 1 (docs/ui-kit.md "Insets")."""
    height, width, _ = pixels.shape
    lo = int(width * 0.4)
    hi = max(int(width * 0.6), lo + 1)
    reference = np.median(pixels[:, lo:hi], axis=1)
    error = np.abs(pixels - reference[:, None, :]).max(axis=-1)
    spread = error[:, lo:hi].max(axis=1)
    plain = ~(error > (2 * spread + 8)[:, None]).any(axis=0)
    start = lo
    while start > 0 and plain[start - 1]:
        start -= 1
    end = hi
    while end < width and plain[end]:
        end += 1
    notes = []
    if spread.max() > max(64.0, 4 * float(np.median(spread))):
        notes.append("the middle of the element is not uniform (an ornament in the middle of an edge?); "
                     "stretching would smear it")
    if (start == lo and lo > 0) or (end == hi and hi < width):
        notes.append("an ornament reaches the middle fifth of the element; the insets may not cover it")
    before = min(start + MARGIN, lo) if start > 0 else 0
    after = min(width - end + MARGIN, width - hi) if end < width else 0
    return before, after, notes


def measure_insets(image: Image.Image, stretch: str) -> tuple[dict[str, int], list[str]]:
    """Nine-slice insets in the element's pixels, measured along the axes ``stretch`` allows, and review notes."""
    if stretch not in STRETCHES:
        raise ValueError(f"stretch must be one of {', '.join(STRETCHES)}")
    pixels = _premultiplied(image)
    insets = {"left": 0, "right": 0, "top": 0, "bottom": 0}
    notes: list[str] = []
    if stretch in ("xy", "x"):
        insets["left"], insets["right"], found = _stretch_band(pixels)
        notes += [f"x: {note}" for note in found]
    if stretch == "xy":
        insets["top"], insets["bottom"], found = _stretch_band(pixels.transpose(1, 0, 2))
        notes += [f"y: {note}" for note in found]
    return insets, notes


def _interior_run(line: Any, centre: Any, middle: int) -> tuple[int, int]:
    near = np.abs(line - centre).max(axis=-1) <= PADDING_SPAN
    smooth = np.abs(np.diff(line, axis=0)).max(axis=-1) <= PADDING_STEP
    if not near[middle]:
        return middle, middle + 1
    start, end = middle, middle + 1
    while start > 0 and near[start - 1] and smooth[start - 1]:
        start -= 1
    while end < len(near) and near[end] and smooth[end - 1]:
        end += 1
    return start, end


def measure_padding(image: Image.Image) -> tuple[dict[str, int], list[str]]:
    """Distances from each edge to the element's empty interior (docs/ui-kit.md "Padding"), and review notes."""
    pixels = _premultiplied(image)
    height, width, _ = pixels.shape
    cy, cx = height // 2, width // 2
    rows, cols = slice(max(0, cy - 2), cy + 3), slice(max(0, cx - 2), cx + 3)
    centre = np.median(pixels[rows, cols].reshape(-1, 4), axis=0)
    across, down = np.median(pixels[rows], axis=0), np.median(pixels[:, cols], axis=1)
    x0, x1 = _interior_run(across, centre, cx)
    y0, y1 = _interior_run(down, centre, cy)
    interior_values = np.concatenate((across[x0:x1], down[y0:y1]))
    low, high = interior_values.min(axis=0) - PADDING_STEP, interior_values.max(axis=0) + PADDING_STEP

    def interior(x: int, y: int) -> bool:
        return bool(((pixels[y, x] >= low) & (pixels[y, x] <= high)).all())

    shrink = 0
    while True:
        left, top, right, bottom = x0 + shrink, y0 + shrink, x1 - 1 - shrink, y1 - 1 - shrink
        if left >= right or top >= bottom:
            left, right = min(left, cx), max(right, cx)
            top, bottom = min(top, cy), max(bottom, cy)
            break
        if all(interior(x, y) for x, y in ((left, top), (right, top), (left, bottom), (right, bottom))):
            break
        shrink += 1
    padding = {"left": min(left + MARGIN, cx), "right": min(width - 1 - right + MARGIN, width - 1 - cx),
               "top": min(top + MARGIN, cy), "bottom": min(height - 1 - bottom + MARGIN, height - 1 - cy)}
    notes = []
    if 4 * (width - padding["left"] - padding["right"]) < width or 4 * (height - padding["top"] - padding["bottom"]) < height:
        notes.append("the content area is less than a quarter of the element; its centre is probably not empty")
    return padding, notes


def measure_round(image: Image.Image) -> list[str]:
    """Review notes for a round part: its cut's corners, outside the ellipse its bounding box holds, are empty."""
    alpha = np.asarray(image.convert("RGBA"))[..., 3]
    height, width = alpha.shape
    yy, xx = np.indices((height, width))
    outside = ((xx + 0.5 - width / 2) / (width / 2)) ** 2 + ((yy + 0.5 - height / 2) / (height / 2)) ** 2 > 1
    if outside.any() and (alpha[outside] >= 128).mean() > ROUND_CORNER_FILL:
        return ["its corners are filled: it was drawn on a square plate or backing, not as a circle; regenerate it"]
    return []


def _runs(mask: Any) -> list[tuple[int, int, int]]:
    runs = []
    for y in np.flatnonzero(mask.any(axis=1)):
        edges = np.diff(np.concatenate(([0], mask[y].astype(np.int8), [0])))
        for x0, x1 in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            runs.append((int(y), int(x0), int(x1)))
    return runs


def _parts(mask: Any) -> list[dict[str, Any]]:
    """8-connected parts of ``mask``: their runs, pixel count, bounding box and centroid."""
    runs = _runs(mask)
    parent = list(range(len(runs)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    previous: list[int] = []
    current: list[int] = []
    row = None
    for index, (y, x0, x1) in enumerate(runs):
        if y != row:
            previous = current if row == y - 1 else []
            current, row = [], y
        for other in previous:
            _, ox0, ox1 = runs[other]
            if ox0 <= x1 and x0 <= ox1:  # touching rows; one column apart counts (diagonal)
                parent[find(other)] = find(index)
        current.append(index)
    groups: dict[int, list[int]] = {}
    for index in range(len(runs)):
        groups.setdefault(find(index), []).append(index)
    parts: list[dict[str, Any]] = []
    for members in groups.values():
        spans = [runs[i] for i in members]
        count = sum(x1 - x0 for _, x0, x1 in spans)
        sum_x = sum((x0 + x1 - 1) * (x1 - x0) / 2 for _, x0, x1 in spans)
        sum_y = sum(y * (x1 - x0) for y, x0, x1 in spans)
        x_min, x_max = min(x0 for _, x0, _ in spans), max(x1 for _, _, x1 in spans)
        y_min, y_max = min(y for y, _, _ in spans), max(y for y, _, _ in spans) + 1
        parts.append({"runs": spans, "pixels": count, "centroid": (sum_x / count, sum_y / count),
                      "bbox": (x_min, y_min, x_max, y_max)})
    parts.sort(key=lambda part: (part["bbox"][1], part["bbox"][0]))
    return parts


def _bbox(parts: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    """The bounding box (x0, y0, x1, y1) of ``parts``."""
    return (min(part["bbox"][0] for part in parts), min(part["bbox"][1] for part in parts),
            max(part["bbox"][2] for part in parts), max(part["bbox"][3] for part in parts))


def _off_centre(assigned: dict[str, list[dict[str, Any]]],
                scaled: dict[str, tuple[float, float, float, float]]) -> list[str]:
    """The boxes, in layout order, whose parts are centred outside the box: in the gap beside it or further, where the
    drawing does not say which element they are."""
    off = []
    for name, parts in assigned.items():
        if parts:
            x, y, w, h = scaled[name]
            x0, y0, x1, y1 = _bbox(parts)
            if not (x <= (x0 + x1) / 2 <= x + w and y <= (y0 + y1) / 2 <= y + h):
                off.append(name)
    return off


def _fit(assigned: dict[str, list[dict[str, Any]]],
         scaled: dict[str, tuple[float, float, float, float]]) -> tuple[float, float, float, float] | None:
    """The drift (sx, tx, sy, ty) that moves the layout's box centres closest (least squares) to the centres of what
    each box holds; None from fewer than FIT_MIN_BOXES boxes that hold parts."""
    centres, drawn = [], []
    for name, parts in assigned.items():
        if parts:
            x, y, w, h = scaled[name]
            x0, y0, x1, y1 = _bbox(parts)
            centres.append((x + w / 2, y + h / 2))
            drawn.append(((x0 + x1) / 2, (y0 + y1) / 2))
    if len(centres) < FIT_MIN_BOXES:
        return None
    centres_array, drawn_array = np.asarray(centres), np.asarray(drawn)
    fit: list[float] = []
    for axis in (0, 1):
        box, held = centres_array[:, axis], drawn_array[:, axis]
        if np.ptp(box) < 1:  # every box centred on one line along this axis: an offset alone
            fit += [1.0, float(np.mean(held - box))]
        else:
            scale, offset = np.polyfit(box, held, 1)
            fit += [float(scale), float(offset)]
    sx, tx, sy, ty = fit
    return sx, tx, sy, ty


def _drift_limits(unit: float) -> tuple[float, float]:
    """How far the scale of a drift the cut considers may lie from 1, and its offset from 0, along each axis (``unit``
    sheet pixels to a layout pixel at 1024): the range a cut accepts and DRIFT_MARGIN of it beyond either end."""
    return DRIFT_SCALE * (1 + DRIFT_MARGIN), DRIFT_OFFSET * unit * (1 + DRIFT_MARGIN)


def _considered(drift: tuple[float, float, float, float], unit: float) -> bool:
    """Whether the cut considers ``drift`` (`_drift_limits`)."""
    scale, offset = _drift_limits(unit)
    sx, tx, sy, ty = drift
    return max(abs(sx - 1), abs(sy - 1)) <= scale and max(abs(tx), abs(ty)) <= offset


@dataclass(frozen=True)
class Reading:
    """A reading of the drawing (docs/ui-kit.md "Reading the drawing"). ``drift`` (sx, tx, sy, ty) moves each layout
    box (x, y, w, h) to (x sx + tx, y sy + ty, w sx, h sy). ``boxes`` names the box each part goes to (None for a part
    in no box), in the order of the parts read. ``cost``, in sheet pixels, is the least cost of a reading that puts the
    parts so; ``drift`` reads the drawing so, at that cost to within a millionth of a pixel."""

    drift: tuple[float, float, float, float]
    boxes: tuple[str | None, ...]
    cost: float


@dataclass(frozen=True)
class Judgment:
    """What the cut's search found in a drawing (`_judge`): the ``parts`` read; the least cost of any reading the cut
    considers (``least``) and of one whose every required box holds a part (``least_valid``), exact when the search
    ran to its end and otherwise proven lower bounds; the readings the cut is decided among (``candidates``), cheapest
    first (for a drawing the search stopped on as ambiguous, those it had found); when the search stopped at its work
    bound, the cost every reading is proven to exceed (``proven``); and the spans of drifts it took (``nodes``)."""

    parts: list[dict[str, Any]]
    least: float
    least_valid: float
    candidates: tuple[Reading, ...]
    proven: float | None
    nodes: int
    drawing: _Drawing


class Refusal(ValueError):
    """A sheet `split_ui_kit` refuses (docs/ui-kit.md "Reading the drawing"): its message, its ``reason``
    (``drifted``, ``required``, ``ambiguous``, ``off-centre`` or ``off-layout``), the ``parts`` read and the ``least``
    cost of a reading (`Judgment`); a drawing that fits the layout in two ways also gives the two readings
    (``witnesses``)."""

    def __init__(self, message: str, reason: str, judgment: Judgment, witnesses: tuple[Reading, ...] = ()) -> None:
        super().__init__(message)
        self.reason = reason
        self.parts = judgment.parts
        self.least = judgment.least
        self.witnesses = witnesses


Row = tuple[float, float, float]  # (a, b, c): a s + b t <= c, a half-plane of the drifts (s, t) along one axis
Span = tuple[float, float, float, float]  # (s0, s1, t0, t1): the drifts along one axis, s0 <= s <= s1, t0 <= t <= t1


class _Axis:
    """The layout's boxes and the parts along one axis: each box's edges (``e0``, ``e1``) and its edges widened by the
    reach (``lo``, ``hi``), as a row of one value a box; each part's centroid (``c``) and its bounding box's edges
    (``p0``, ``p1``, exclusive), as a column of one value a part."""

    def __init__(self, low: Any, high: Any, reach: float, centre: Any, first: Any, last: Any) -> None:
        self.e0, self.e1 = low[None, :], high[None, :]
        self.lo, self.hi = self.e0 - reach, self.e1 + reach
        self.c, self.p0, self.p1 = centre[:, None], first[:, None], last[:, None]
        self.extent = float(self.hi.max(initial=1.0))

    def held_at(self, s: float, t: float) -> Any:
        """[part, box]: the box, moved by (s, t) and widened, holds the part's centroid (its low edge in, its high edge
        out, as `_parts` gives a bounding box)."""
        return (self.lo * s + t <= self.c) & (self.c < self.hi * s + t)

    def short_at(self, s: float, t: float) -> Any:
        """[part, box]: how far the part's two sides fall short of lying CLEARANCE inside the box moved by (s, t)."""
        return (np.maximum(0, self.e0 * s + t + CLEARANCE - self.p0)
                + np.maximum(0, self.p1 - (self.e1 * s + t) + CLEARANCE))

    def status(self, span: Span, rows: Any) -> tuple[Any, Any]:
        """[part, box] for the parts ``rows`` (with ends one a span, [span, part, box]): the widened box holds the
        centroid at every drift of ``span``, and at none. Every box lies more than the reach in from the sheet's edges,
        so its widened edges are positive: least at (s0, t0) and greatest at (s1, t1)."""
        s0, s1, t0, t1 = span
        c = self.c[rows]
        return ((self.lo * s1 + t1 <= c) & (c < self.hi * s0 + t0),
                (c < self.lo * s0 + t0) | (c >= self.hi * s1 + t1))

    def least_short(self, span: Span, rows: Any, boxes: Any = None) -> Any:
        """A lower bound, over the drifts of ``span``, of `short_at` (each side at its own corner): [part, box] for the
        parts ``rows``, or, given each one's box (``boxes``), [part]; with ends one a span, a span axis first."""
        s0, s1, t0, t1 = span
        if boxes is None:
            e0, e1, p0, p1 = self.e0, self.e1, self.p0[rows], self.p1[rows]
        else:
            e0, e1, p0, p1 = self.e0[0, boxes], self.e1[0, boxes], self.p0[rows, 0], self.p1[rows, 0]
        return np.maximum(0, e0 * s0 + t0 + CLEARANCE - p0) + np.maximum(0, p1 - (e1 * s1 + t1) + CLEARANCE)

    def hinges(self, parts: Any, boxes: Any) -> Any:
        """The rows (a, b, c) whose max(0, a s + b t + c) are the shortfalls of the sides of ``parts`` in ``boxes``."""
        n = len(parts)
        out = np.empty((2 * n, 3))
        out[:n, 0], out[:n, 1], out[:n, 2] = self.e0[0, boxes], 1.0, CLEARANCE - self.p0[parts, 0]
        out[n:, 0], out[n:, 1], out[n:, 2] = -self.e1[0, boxes], -1.0, self.p1[parts, 0] + CLEARANCE
        return out

    def within(self, part: int, box: int) -> list[Row]:
        """The rows that keep the part's centroid in the widened box: both edges in, so that at an edge both readings
        are admitted, each at the cost it reaches beside the edge."""
        c = float(self.c[part, 0])
        return [(float(self.lo[0, box]), 1.0, c), (-float(self.hi[0, box]), -1.0, -c)]

    def beside(self, part: int, box: int, span: Span) -> list[Row]:
        """The rows, one a way, that keep the part's centroid out of the widened box at some drift of ``span``: below
        its low edge, or at or past its high edge."""
        s0, s1, t0, t1 = span
        c, lo, hi = float(self.c[part, 0]), float(self.lo[0, box]), float(self.hi[0, box])
        return ([(-lo, -1.0, -c)] if lo * s1 + t1 >= c else []) + ([(hi, 1.0, c)] if hi * s0 + t0 <= c else [])


def _least_on_axis(span: Span, rows: list[Row], hinges: Any) -> tuple[float, tuple[float, float] | None]:
    """The least, over the drifts (s, t) of ``span`` that keep every row of ``rows``, of the sum over the rows (a, b,
    c) of ``hinges`` of max(0, a s + b t + c), and a drift that reaches it; (inf, None) when no drift keeps the rows.
    The sum is convex and linear between the lines on which a row binds or a hinge turns, so it is least where two of
    them cross. A hinge that does not turn within ``span`` is linear there and draws no line."""
    s0, s1, t0, t1 = span
    bounds = np.array([(-1.0, 0.0, -s0), (1.0, 0.0, s1), (0.0, -1.0, -t0), (0.0, 1.0, t1), *rows])
    at = hinges[:, :1] * np.array([s0, s0, s1, s1]) + hinges[:, 1:2] * np.array([t0, t1, t0, t1]) + hinges[:, 2:]
    rising = at.min(axis=1, initial=np.inf) >= 0
    turning = hinges[~rising & (at.max(axis=1, initial=-np.inf) > 0)]
    linear = hinges[rising].sum(axis=0)
    lines = np.concatenate((bounds, turning * (1, 1, -1)))
    a, b, c = lines.T
    i, j = np.triu_indices(len(lines), 1)
    det = a[i] * b[j] - a[j] * b[i]
    crossing = np.abs(det) > 1e-12
    i, j, det = i[crossing], j[crossing], det[crossing]
    s, t = (c[i] * b[j] - c[j] * b[i]) / det, (a[i] * c[j] - a[j] * c[i]) / det
    kept = (bounds[:, :1] * s + bounds[:, 1:2] * t <= bounds[:, 2:] + 1e-9 * (1 + np.abs(bounds[:, 2:]))).all(axis=0)
    if not kept.any():
        return math.inf, None
    s, t = s[kept], t[kept]
    value = linear[0] * s + linear[1] * t + linear[2]
    if len(turning):
        value = value + np.maximum(0, turning[:, :1] * s + turning[:, 1:2] * t + turning[:, 2:]).sum(axis=0)
    k = int(np.argmin(value))
    return max(0.0, float(value[k])), (float(s[k]), float(t[k]))


def _middle(span: Span, rows: list[Row]) -> tuple[float, float] | None:
    """The mean of the corners of the drifts of ``span`` that keep every row: inside them all when they leave room."""
    s0, s1, t0, t1 = span
    lines = np.array([(-1.0, 0.0, -s0), (1.0, 0.0, s1), (0.0, -1.0, -t0), (0.0, 1.0, t1), *rows])
    a, b, c = lines.T
    i, j = np.triu_indices(len(lines), 1)
    det = a[i] * b[j] - a[j] * b[i]
    crossing = np.abs(det) > 1e-12
    i, j, det = i[crossing], j[crossing], det[crossing]
    s, t = (c[i] * b[j] - c[j] * b[i]) / det, (a[i] * c[j] - a[j] * c[i]) / det
    kept = (lines[:, :1] * s + lines[:, 1:2] * t <= lines[:, 2:] + 1e-9 * (1 + np.abs(lines[:, 2:]))).all(axis=0)
    return (float(s[kept].mean()), float(t[kept].mean())) if kept.any() else None


def _room(span: Span, rows: list[Row]) -> bool:
    """Whether the drifts of ``span`` that keep ``rows`` leave room: one drift keeps every row with a margin. The rows'
    edges are taken closed so that a reading's least cost is reached, but a box's high edge, and its low edge for a part
    kept out of it, are open: rows that leave room only on an edge, such as a part kept out of two widened boxes that
    touch, give a reading no drift reads."""
    middle = _middle(span, rows)
    if middle is None:
        return False
    if not rows:
        return True
    a, b, c = np.array(rows).T
    return bool((c - a * middle[0] - b * middle[1]).min() > 1e-9)


class _Drawing:
    """The parts and the layout's boxes of a sheet, as arrays: the reading at a drift, and the bounds the search takes
    over a box of drifts (``span``: sx0, sx1, tx0, tx1, sy0, sy1, ty0, ty1)."""

    def __init__(self, parts: list[dict[str, Any]], size: tuple[int, int]) -> None:
        layout = boxes(size)
        _, slots = sheet_layout(size)
        self.parts, self.layout, self.names = parts, layout, list(layout)
        self.required = frozenset(index for index, slot in enumerate(slots) if not slot.optional)
        self.unit = size[0] / REFERENCE
        reach = GAP / 2 * self.unit
        box = np.asarray(list(layout.values()), dtype=float)
        centroid = np.asarray([part["centroid"] for part in parts], dtype=float).reshape(-1, 2)
        self.bbox = bbox = np.asarray([part["bbox"] for part in parts], dtype=float).reshape(-1, 4)
        self.x = _Axis(box[:, 0], box[:, 0] + box[:, 2], reach, centroid[:, 0], bbox[:, 0], bbox[:, 2])
        self.y = _Axis(box[:, 1], box[:, 1] + box[:, 3], reach, centroid[:, 1], bbox[:, 1], bbox[:, 3])
        # A part in no box costs about the least a part of its size costs against a box whose widened box does not
        # hold its centroid: the same under every drift, so it never pulls the reading, and more for a larger part, so
        # that leaving a large element that overflows its box out of every box never costs less than keeping it.
        self.loose = reach + CLEARANCE + np.minimum(bbox[:, 2] - bbox[:, 0], bbox[:, 3] - bbox[:, 1]) / 2
        # Widened boxes overlap only where rounding a sheet's boxes narrowed a gap; a part in both goes to the first.
        overlap = ((self.x.lo.T < self.x.hi) & (self.x.lo < self.x.hi.T)
                   & (self.y.lo.T < self.y.hi) & (self.y.lo < self.y.hi.T))
        self.earlier = np.triu(overlap, 1)  # [box, later box]

    def read(self, drift: tuple[float, float, float, float]) -> tuple[float, tuple[int, ...]]:
        """The reading at ``drift``: its cost, and each part's box (its index in layout order, -1 for none)."""
        sx, tx, sy, ty = drift
        held = self.x.held_at(sx, tx) & self.y.held_at(sy, ty)
        placed = held.any(axis=1)
        key = np.where(placed, held.argmax(axis=1), -1)
        short = self.x.short_at(sx, tx) + self.y.short_at(sy, ty)
        inside = np.take_along_axis(short, np.maximum(key, 0)[:, None], axis=1)[:, 0]
        return float(np.where(placed, inside, self.loose).sum()), tuple(key.tolist())

    def valid(self, key: tuple[int, ...]) -> bool:
        """Whether every required box holds a part."""
        return self.required <= set(key)

    def named(self, key: tuple[int, ...]) -> tuple[str | None, ...]:
        """Each part's box by name, None for none."""
        return tuple(self.names[box] if box >= 0 else None for box in key)

    def assigned(self, key: tuple[int, ...]) -> dict[str, list[dict[str, Any]]]:
        """The parts by box, in layout order."""
        assigned: dict[str, list[dict[str, Any]]] = {name: [] for name in self.names}
        for part, box in zip(self.parts, key):
            if box >= 0:
                assigned[self.names[box]].append(part)
        return assigned

    def bound(self, spans: list[tuple[float, ...]], open_: Any, fixed: Any) -> tuple[Any, dict[str, Any]]:
        """For each span of ``spans``, a lower bound of the cost of every reading at its drifts, and what the span
        decides of the parts still open (``open_``; ``fixed`` gives every other part's box, -1 for none and -2 for an
        open part), one row a span. For each open part, the least it can cost (``least``): in a box that may hold its
        centroid somewhere in the span, each of its sides at its own corner, or in no box when no box holds it
        everywhere (``free``). A box after one that holds it everywhere never holds it. Several spans are bounded in
        one pass over the arrays."""
        ends = np.asarray(spans, dtype=float)[:, :, None, None]  # [span, end, part, box]
        sx: Any
        sy: Any
        sx, sy =tuple(ends[:, k] for k in range(4)), tuple(ends[:, k] for k in range(4, 8))
        inx, outx = self.x.status(sx, open_)
        iny, outy = self.y.status(sy, open_)
        held = inx & iny
        may = ~(outx | outy) & ~((np.cumsum(held, axis=-1) - held) > 0)
        free = ~held.any(axis=-1)
        short = self.x.least_short(sx, open_) + self.y.least_short(sy, open_)
        least = np.where(may, short, np.inf).min(axis=-1, initial=np.inf)
        least = np.where(free, np.minimum(least, self.loose[open_]), least)
        placed = np.flatnonzero(fixed >= 0)
        settled = ((self.x.least_short(sx, placed, fixed[placed]) + self.y.least_short(sy, placed, fixed[placed]))
                   .sum(axis=(-2, -1)) + self.loose[fixed == -1].sum())
        return least.sum(axis=-1) + settled, {"inx": inx, "iny": iny, "may": may, "free": free, "short": short,
                                              "least": least}

    def count(self, span: tuple[float, ...], parts: Any, rows: Any, info: dict[str, Any]) -> float:
        """How many ways the span may read the undecided ``parts`` (``rows`` of the open parts `bound` gave ``info``
        of): the product of the lengths of their `outcomes`, counted without listing them."""
        may, free = info["may"][rows], info["free"][rows]
        out = np.zeros(may.shape)  # [part, box]: the ways its centroid lies out of the widened box
        for axis, side, inside in ((self.x, span[:4], info["inx"][rows]), (self.y, span[4:], info["iny"][rows])):
            s0, s1, t0, t1 = side
            c = axis.c[parts]
            out += ~inside * ((axis.lo * s1 + t1 >= c).astype(float) + (axis.hi * s0 + t0 <= c))
        loose = np.where(may, out, 1.0).prod(axis=1)
        if self.earlier.any():
            held = (np.where(may[:, :, None] & self.earlier[None], out[:, :, None], 1.0).prod(axis=1) * may).sum(axis=1)
        else:
            held = may.sum(axis=1)
        return float(np.prod(held + np.where(free, loose, 0.0)))

    def outcomes(self, span: tuple[float, ...], part: int, row: int,
                 info: dict[str, Any]) -> list[tuple[int, list[Row], list[Row]]]:
        """The ways the span may read an undecided part (``row`` of the open parts `bound` gave ``info`` of): its box
        (-1 for none) and the rows along x and along y that read it so. A part in a box is out of every earlier box
        whose widened box overlaps it; a part in no box is out of every box that may hold it, along x or along y."""
        sx, sy = cast(Span, span[:4]), cast(Span, span[4:])
        may = info["may"][row]

        def apart(others: Any) -> list[tuple[list[Row], list[Row]]]:
            """The ways the centroid lies out of each widened box of ``others``: along x or along y, at an edge each."""
            choices = [[("x", way) for way in (self.x.beside(part, box, sx) if not info["inx"][row, box] else [])]
                       + [("y", way) for way in (self.y.beside(part, box, sy) if not info["iny"][row, box] else [])]
                       for box in others]
            return [([way for axis, way in term if axis == "x"], [way for axis, way in term if axis == "y"])
                    for term in itertools.product(*choices)]

        ways = []
        for box in map(int, np.flatnonzero(may)):
            rx = [] if info["inx"][row, box] else self.x.within(part, box)
            ry = [] if info["iny"][row, box] else self.y.within(part, box)
            for ex, ey in apart(np.flatnonzero(self.earlier[:box, box] & may[:box])):
                ways.append((box, rx + ex, ry + ey))
        if info["free"][row]:
            ways += [(-1, ex, ey) for ex, ey in apart(np.flatnonzero(may))]
        return ways


def _split(span: tuple[float, ...], drawing: _Drawing) -> tuple[tuple[float, ...], tuple[float, ...], float]:
    """The two halves of ``span`` across its widest side, measured in how far it moves the boxes, and that width."""
    sx0, sx1, tx0, tx1, sy0, sy1, ty0, ty1 = span
    widths = [(sx1 - sx0) * drawing.x.extent, tx1 - tx0, (sy1 - sy0) * drawing.y.extent, ty1 - ty0]
    side = int(np.argmax(widths))
    middle = (span[2 * side] + span[2 * side + 1]) / 2
    low, high = list(span), list(span)
    low[2 * side + 1], high[2 * side] = middle, middle
    return tuple(low), tuple(high), max(widths)


class _Search:
    """The exact search for the least-cost readings of a drawing (`_judge`): best-first branch and bound over spans of
    drifts."""

    def __init__(self, drawing: _Drawing) -> None:
        self.drawing = drawing
        scale, offset = _drift_limits(drawing.unit)
        self.region = (1 - scale, 1 + scale, -offset, offset) * 2
        self.best = math.inf  # the least cost found
        self.exact = math.inf  # the least cost of a span solved whole
        # Every reading found within 2 TIE of the least cost, by its key (each part's box): its least cost, a drift
        # that reaches it, and how it was solved (the spans and rows along x and y; None for a reading at one drift).
        self.found: dict[tuple[int, ...], tuple[float, tuple[float, float, float, float], Any]] = {}
        self.close: set[tuple[int, ...]] = set()  # readings that hold every required box, at a cost of TIE or less
        self.nodes = 0

    def note(self, key: tuple[int, ...], cost: float, drift: tuple[float, float, float, float],
             solved: Any = None) -> None:
        self.best = min(self.best, cost)
        if cost <= self.best + 2 * TIE and cost < self.found.get(key, (math.inf,))[0]:
            self.found[key] = (cost, drift, solved)
            if cost <= TIE and self.drawing.valid(key):
                self.close.add(key)

    def read(self, drift: tuple[float, float, float, float]) -> None:
        cost, key = self.drawing.read(drift)
        self.note(key, cost, drift)

    def solve(self, span: tuple[float, ...], fixed: Any, undecided: Any,
              ways: list[list[tuple[int, list[Row], list[Row]]]], floors: Any, rest: float) -> None:
        """Every reading of a span solved whole: for each way of reading its undecided parts whose bound may still come
        within 2 TIE of the least cost, its least cost along each axis (`_least_on_axis`). A way whose rows leave no
        room (`_room`) is read at no drift and is not a reading."""
        drawing = self.drawing
        sx, sy = cast(Span, span[:4]), cast(Span, span[4:])
        least:dict[Any, tuple[float, Any]] = {}
        for choice in itertools.product(*ways):
            if rest + sum(floor[box] for floor, (box, _, _) in zip(floors, choice)) > self.best + 2 * TIE:
                continue
            key = fixed.copy()
            rows_x: list[Row] = []
            rows_y: list[Row] = []
            for part, (box, rx, ry) in zip(undecided, choice):
                key[part] = box
                rows_x += rx
                rows_y += ry
            placed = np.flatnonzero(key >= 0)
            places = key.tobytes()
            point = []
            cost = float(drawing.loose[key < 0].sum())
            for axis, side, rows in ((drawing.x, sx, rows_x), (drawing.y, sy, rows_y)):
                memo = (axis is drawing.x, places, tuple(rows))
                if memo not in least:
                    least[memo] = _least_on_axis(side, rows, axis.hinges(placed, key[placed]))
                value, at = least[memo]
                if at is None:
                    break
                cost += value
                point += at
            else:
                # a way that could change what the search keeps must be one some drift reads
                if cost <= self.best + 2 * TIE and not (_room(sx, rows_x) and _room(sy, rows_y)):
                    continue
                self.exact = min(self.exact, cost)
                self.note(tuple(key.tolist()), cost, tuple(point), (sx, rows_x, sy, rows_y))

    def run(self) -> Judgment:
        drawing = self.drawing
        count = len(drawing.parts)
        region = self.region
        for drift in ((1.0, 0.0, 1.0, 0.0), _centre(region)):
            self.read(drift)
        open_, fixed = np.arange(count), np.full(count, -2)
        tick = itertools.count()
        heap: list[tuple[float, int, int, tuple[float, ...], Any, Any]] = [(float(drawing.bound([region], open_, fixed)[0][0]), 0, next(tick), region, open_, fixed)]
        proven = None
        while heap and len(self.close) < 2:
            floor = min(heap[0][0], self.exact)
            if self.nodes >= SEARCH_NODES and floor > TIE:
                proven = floor  # every reading costs more than TIE: the drawing does not lie on the layout
                break
            bound, depth, _, span, open_, fixed = heapq.heappop(heap)
            if bound > self.best + 2 * TIE:
                break
            self.nodes += 1
            bounds, info = drawing.bound([span], open_, fixed)
            bound, info = float(bounds[0]), {name: value[0] for name, value in info.items()}
            may, free = info["may"], info["free"]
            number = may.sum(axis=1)
            decided = (number == 0) | ((number == 1) & ~free)
            fixed = fixed.copy()
            fixed[open_[decided]] = np.where(number[decided] == 0, -1, may[decided].argmax(axis=1))
            rows = np.flatnonzero(~decided)
            undecided = open_[rows]
            low, high, width = _split(span, drawing)
            if drawing.count(span, undecided, rows, info) <= LEAF_WAYS or width < LEAF_WIDTH:
                ways = [drawing.outcomes(span, int(part), int(row), info) for part, row in zip(undecided, rows)]
                # the bound of each way of reading a part: its least in each box, or in none
                floors = [{**dict(zip(map(int, np.flatnonzero(may[row])), info["short"][row, may[row]].tolist())),
                           -1: float(drawing.loose[part])} for part, row in zip(undecided, rows)]
                self.solve(span, fixed, undecided, ways, floors, bound - float(info["least"][rows].sum()))
                continue
            self.read(_centre(span))
            for half, half_bound in zip((low, high), drawing.bound([low, high], undecided, fixed)[0].tolist()):
                if half_bound <= self.best + 2 * TIE:
                    heapq.heappush(heap, (half_bound, depth - 1, next(tick), half, undecided, fixed))
        lower = min(heap[0][0] if heap else math.inf, self.exact, self.best)
        return self.judgment(proven, lower)

    def judgment(self, proven: float | None, lower: float) -> Judgment:
        """The judgment of what the search found (`Judgment`)."""
        drawing = self.drawing
        if proven is not None:
            return Judgment(drawing.parts, proven, proven, (), proven, self.nodes, drawing)
        found = {key: entry for key, entry in self.found.items() if entry[0] <= self.best + 2 * TIE}
        if len(self.close) > 1:
            # stopped early: two readings that hold every required box cost TIE or less, so both lie within TIE of
            # the least cost of such a reading, which lies within TIE of the least cost, and neither is drifted
            least = least_valid = lower
            picked = sorted(self.close)
        else:
            least = self.best
            least_valid = min((entry[0] for key, entry in found.items() if drawing.valid(key)), default=math.inf)
            whole = least_valid <= least + TIE  # the readings that hold every required box are the candidates
            tier = least_valid if whole else least
            picked = [key for key, entry in found.items()
                      if entry[0] <= tier + TIE and (drawing.valid(key) or not whole)]
        picked.sort(key=lambda key: (found[key][0], key))
        candidates = tuple(Reading(self.inside(key, *found[key][1:]), drawing.named(key), found[key][0])
                           for key in picked)
        return Judgment(drawing.parts, least, least_valid, candidates, None, self.nodes, drawing)

    def inside(self, key: tuple[int, ...], drift: tuple[float, float, float, float],
               solved: Any) -> tuple[float, float, float, float]:
        """A drift that reads the drawing as ``key`` says at its least cost, to within a millionth of a pixel. A span
        solved whole reaches its least cost at a corner, which may lie on the edge of a widened box, where the drawing
        reads otherwise: the corner is moved toward the middle of the drifts the reading's rows keep, by the smallest
        step that reads it so."""
        if solved is None or self.drawing.read(drift)[1] == key:
            return drift
        sx, rows_x, sy, rows_y = solved
        middle_x, middle_y = _middle(sx, rows_x), _middle(sy, rows_y)
        if middle_x is None or middle_y is None:
            return drift
        start = np.asarray(drift)
        towards = np.array((*middle_x, *middle_y)) - start
        for step in 10.0 ** np.arange(-12, -2):
            sx_, tx_, sy_, ty_ = (float(value) for value in start + step * towards)
            if self.drawing.read((sx_, tx_, sy_, ty_))[1] == key:
                return sx_, tx_, sy_, ty_
        return drift


def _centre(span: tuple[float, ...]) -> tuple[float, float, float, float]:
    sx0, sx1, tx0, tx1, sy0, sy1, ty0, ty1 = span
    return (sx0 + sx1) / 2, (tx0 + tx1) / 2, (sy0 + sy1) / 2, (ty0 + ty1) / 2


def _judge(parts: list[dict[str, Any]], size: tuple[int, int]) -> Judgment:
    """Search the drawing for its least-cost readings (docs/ui-kit.md "Reading the drawing"). A reading at a drift puts
    each part in the first box, in layout order, whose box moved by the drift and widened by half the gap holds its
    centroid, or in none. Its cost adds, for each part in a box, how far each of its sides falls short of lying
    CLEARANCE px inside the moved box, and for each part in no box `_Drawing.loose`. The search is exact: no grid, no
    sampling. It is best-first branch and bound over the drifts the cut considers: a span of drifts is bounded below by
    the least each part can cost in it, split across its widest side, and solved whole once its undecided parts can be
    read in at most LEAF_WAYS ways, each then a convex piecewise-linear cost along each axis, least at a corner. A span
    bounded above the least cost found plus 2 TIE is dropped: the readings the cut is decided among lie within it. The
    search stops early once it has found two readings that hold every required box at a cost of TIE or less (the cut
    then refuses the drawing as ambiguous, whatever else there is to find), and after SEARCH_NODES spans once every
    reading is proven to cost more than TIE."""
    return _Search(_Drawing(parts, size)).run()


def _drifted(drift: tuple[float, float, float, float], unit: float) -> str:
    sx, tx, sy, ty = drift
    return (f"the drawing drifted further from the layout than a cut accepts: fitted to it, the layout is scaled "
            f"{sx:.3f} along x and {sy:.3f} along y and offset {tx:.1f}, {ty:.1f} px, where a cut accepts a scale "
            f"from {1 - DRIFT_SCALE:g} to {1 + DRIFT_SCALE:g} and an offset of at most {DRIFT_OFFSET * unit:g} px "
            "along each axis; generate the sheet again")


def _centred(drawing: _Drawing, key: tuple[int, ...], cost: float) -> tuple[float, float, float, float] | None:
    """A drift whose cost is within TIE of ``cost``, that keeps the parts ``key`` places in their widened boxes and puts
    the centre of each box's parts' bounding box inside the moved box; None when there is none."""
    scale, offset = _drift_limits(drawing.unit)
    held = np.asarray(key, dtype=int)
    placed = np.flatnonzero(held >= 0)
    found = []
    for axis, index in ((drawing.x, 0), (drawing.y, 1)):
        rows = [row for part in placed for row in axis.within(int(part), int(held[part]))]
        for box in sorted(set(held[placed].tolist())):
            members = np.flatnonzero(held == box)
            middle = float(drawing.bbox[members, index].min() + drawing.bbox[members, index + 2].max()) / 2
            rows += [(float(axis.e0[0, box]), 1.0, middle), (-float(axis.e1[0, box]), -1.0, -middle)]
        found.append(_least_on_axis((1 - scale, 1 + scale, -offset, offset), rows, axis.hinges(placed, held[placed])))
    (vx, px), (vy, py) = found
    if px is None or py is None or vx + vy + float(drawing.loose[held < 0].sum()) > cost + TIE:
        return None
    return px[0], px[1], py[0], py[1]


def _decide(judgment: Judgment) -> Reading:
    """The cut: the one candidate, at the drift its report gives; or the refusal the judgment calls for, in the order
    docs/ui-kit.md "Reading the drawing" gives. The report's drift is the first of the layout's own boxes, the
    least-squares fit of the cut's assignment (`_fit`), the centring drift and the cut's least-cost drift that reads
    the cut at a cost within TIE of its own."""
    drawing = judgment.drawing
    if judgment.proven is not None:
        raise Refusal("the drawing does not lie on the layout under any drift a cut accepts: every reading leaves its "
                      f"parts at least {math.floor(judgment.proven)} px across their boxes' edges or in no box; "
                      "generate the sheet again", "off-layout", judgment)
    candidates = judgment.candidates
    keys = [tuple(drawing.names.index(name) if name is not None else -1 for name in reading.boxes)
            for reading in candidates]
    first = candidates[0]
    if first.cost > TIE:
        fit = _fit(drawing.assigned(keys[0]), dict(drawing.layout))
        if fit is not None and not _considered(fit, drawing.unit) and drawing.read(fit)[0] < first.cost - TIE:
            raise Refusal(_drifted(fit, drawing.unit), "drifted", judgment)
    if not drawing.valid(keys[0]):
        empty = [drawing.names[box] for box in sorted(drawing.required) if all(box not in key for key in keys)]
        if empty:
            holds = "its box holds" if len(empty) == 1 else "their boxes hold"
            raise Refusal(f"{', '.join(empty)}: {holds} no element; generate the sheet again", "required", judgment)
    if len(candidates) > 1:
        one, other = keys[0], keys[1]
        changed = [drawing.names[box] for box in sorted(
            {box for pair in zip(one, other) if pair[0] != pair[1] for box in pair} - {-1})]
        single = len(changed) == 1
        one_or_more = ("this element", "element it is") if single else ("these elements", "elements they are")
        raise Refusal(f"{', '.join(changed)}: under another drift the cut accepts, the drawing fits the layout at "
                      f"least as well (to within {TIE:g} px) with {one_or_more[0]} named differently, so it does not "
                      f"say which {one_or_more[1]}; generate the sheet again", "ambiguous", judgment,
                      (candidates[0], candidates[1]))
    cut, key = first, keys[0]
    assigned = drawing.assigned(key)
    centred = _centred(drawing, key, cut.cost)
    if centred is None:
        sx, tx, sy, ty = cut.drift
        moved = {name: (x * sx + tx, y * sy + ty, w * sx, h * sy) for name, (x, y, w, h) in drawing.layout.items()}
        off = _off_centre(assigned, moved)
        if off:
            holds = ("its box holds a part centred outside it (in the gap beside it or further, so the drawing does "
                     "not say which element it is)" if len(off) == 1 else
                     "their boxes hold parts centred outside them (in the gap beside them or further, so the drawing "
                     "does not say which elements they are)")
            raise Refusal(f"{', '.join(off)}: {holds}; generate the sheet again", "off-centre", judgment)
        centred = cut.drift
    for drift in ((1.0, 0.0, 1.0, 0.0), _fit(assigned, dict(drawing.layout)), centred, cut.drift):
        if drift is not None:
            cost, read = drawing.read(drift)
            if read == key and cost <= cut.cost + TIE:
                return Reading(drift, cut.boxes, cost)
    return cut


def _sheet_parts(image: Image.Image, alpha_floor: int, alpha_edge: int) -> tuple[Any, Any, list[dict[str, Any]], int]:
    """The sheet's pixels after the alpha cleanup, the cleanup's report, the parts of MIN_PART px or more, and how many
    smaller parts were dropped."""
    rgba = image.convert("RGBA")
    sheet_layout(rgba.size)
    cleanup = None
    if alpha_floor:
        rgba, cleanup = clear_haze(rgba, floor=alpha_floor, edge=alpha_edge)
    pixels = np.asarray(rgba)
    every = _parts(pixels[..., 3] > 0)
    kept = [part for part in every if part["pixels"] >= MIN_PART]
    return pixels, cleanup, kept, len(every) - len(kept)


def _read(image: Image.Image, *, alpha_floor: int = 64, alpha_edge: int = 2) -> Judgment:
    """The judgment `split_ui_kit` decides the sheet by: its least costs and the readings it is decided among."""
    _, _, kept, _ = _sheet_parts(image, alpha_floor, alpha_edge)
    return _judge(kept, image.size)


def split_ui_kit(image: Image.Image, *, alpha_floor: int = 64,
                 alpha_edge: int = 2) -> tuple[dict[str, Image.Image], dict[str, Any]]:
    """Cut ``image`` along the layout: element images by name, and the report as written to its default
    place, ``<out-dir>/ui-kit.json`` (each ``file`` is ``<element>.png``), without the sheet path. A `Refusal`
    for a sheet the cut refuses."""
    width, height = image.size
    _, slots = sheet_layout((width, height))
    pixels, cleanup, kept, dropped = _sheet_parts(image, alpha_floor, alpha_edge)
    cut = _decide(_judge(kept, (width, height)))
    sx, tx, sy, ty = cut.drift
    scaled = {name: (x * sx + tx, y * sy + ty, w * sx, h * sy) for name, (x, y, w, h) in boxes((width, height)).items()}
    assigned: dict[str, list[dict[str, Any]]] = {name: [] for name in scaled}
    loose: list[dict[str, Any]] = []
    for part, name in zip(kept, cut.boxes):
        (assigned[name] if name is not None else loose).append(part)
    unassigned, reasons = [], []
    for part in loose:
        x0, y0, x1, y1 = part["bbox"]
        unassigned.append({"rect": _rect(x0, y0, x1 - x0, y1 - y0), "pixels": part["pixels"]})
        reasons.append(f"a part of {part['pixels']} pixels at ({x0}, {y0}) lies in no box")
    missing = [slot.name for slot in slots if not assigned[slot.name]]
    elements: dict[str, Image.Image] = {}
    records: dict[str, Any] = {}
    for slot in slots:
        parts = assigned[slot.name]
        if not parts:
            continue
        x0, y0, x1, y1 = _bbox(parts)
        cut_pixels = np.zeros((y1 - y0, x1 - x0, 4), dtype=np.uint8)
        for part in parts:
            for y, a, b in part["runs"]:
                cut_pixels[y - y0, a - x0:b - x0] = pixels[y, a:b]
        element = Image.fromarray(cut_pixels)
        insets, inset_notes = measure_insets(element, slot.stretch)
        padding, padding_notes = measure_padding(element) if slot.content else (dict.fromkeys(SIDES, 0), [])
        round_notes = measure_round(element) if slot.round else []
        reasons += [f"{slot.name}: {note}" for note in inset_notes + padding_notes + round_notes]
        elements[slot.name] = element
        records[slot.name] = {
            "file": _element_png(slot.name),
            "box": _rect(*(round(value) for value in scaled[slot.name])),
            "rect": _rect(x0, y0, x1 - x0, y1 - y0),
            "size": {"w": x1 - x0, "h": y1 - y0},
            "stretch": slot.stretch,
            "insets": insets,
            "padding": padding,
            "parts": len(parts),
        }
    for slot in slots:
        if slot.inside is not None and slot.name in records:
            records[slot.name]["inside"], note = fit_inside(records[slot.name], records[slot.inside], slot.inside)
            reasons += [f"{slot.name}: {note}"] if note else []
    report: dict[str, Any] = {
        "kind": REPORT_KIND,
        "version": 1,
        "sheet_size": [width, height],
        "status": "needs-review" if reasons else "pass",
        "review_reasons": reasons,
        "alpha_cleanup": cleanup,
        "dropped_parts": dropped,
        "unassigned": unassigned,
    }
    # a layout with optional elements always says which are missing; a square sheet's elements are all required
    if any(slot.optional for slot in slots):
        report["missing"] = missing
    if cut.drift != (1.0, 0.0, 1.0, 0.0):
        # `+ 0.0`: an offset that rounds to zero is written 0.0, never -0.0
        report["layout_fit"] = {"scale": {"x": round(sx, 4), "y": round(sy, 4)},
                                "offset": {"x": round(tx, 1) + 0.0, "y": round(ty, 1) + 0.0}}
    report["elements"] = records
    return elements, report


def content_rect(record: dict[str, Any]) -> dict[str, int]:
    """The content rectangle of an element record (its size less its padding), from its top-left corner."""
    pad, size = record["padding"], record["size"]
    return _rect(pad["left"], pad["top"], size["w"] - pad["left"] - pad["right"],
                 size["h"] - pad["top"] - pad["bottom"])


def fit_inside(fill: dict[str, Any], frame: dict[str, Any], name: str) -> tuple[dict[str, Any], str | None]:
    """A fill's ``inside``: the frame it fills, that frame's content rectangle and the factor that makes the
    fill exactly as tall as it (docs/ui-kit.md "Fills"), and a review note when its fixed ends would not fit."""
    rect = content_rect(frame)
    scale = rect["h"] / fill["size"]["h"]
    note = None
    if (fill["insets"]["left"] + fill["insets"]["right"]) * scale >= rect["w"]:
        note = (f"scaled to the height of {name}'s content area, its fixed ends are wider than that area; "
                "a full bar would overflow it")
    return {"element": name, "rect": rect, "scale": round(scale, 6)}, note


def report_file(report: Path, png: Path) -> str:
    """The ``file`` a report at ``report`` gives ``png``: its path from the report's directory, ``/``-separated."""
    return Path(os.path.relpath(png, Path(report).parent)).as_posix()


def element_path(report: Path, file: str) -> Path:
    """The element PNG that the ``file`` of a report at ``report`` names."""
    return Path(report).parent / file


def _taken(out_dir: Path) -> str | None:
    """What keeps a kit out of ``out_dir``, said of it, or None when it is absent or an empty directory."""
    if not os.path.lexists(out_dir):
        return None
    if not out_dir.is_dir():
        return "is a file"
    with os.scandir(out_dir) as entries:
        held = sorted(entry.name for entry in entries)
    if not held:
        return None
    return f"holds {held[0]}" + (f" and {len(held) - 1} more" if len(held) > 1 else "")


def _make_new(path: Path, make: Callable[[Path], object], remove: Callable[[Path], object],
              made: list[tuple[Callable[[Path], object], Path]]) -> None:
    """``make(path)`` for a path that must be new. ``(remove, path)`` goes on ``made`` just before the call, so that a
    run interrupted right after it still removes what it made, and comes back off when the path existed: what another
    process made is not this run's to remove."""
    made.append((remove, path))
    try:
        make(path)
    except FileExistsError:
        made.pop()
        raise


def _make_dirs(directory: Path, made: list[tuple[Callable[[Path], object], Path]]) -> None:
    """Create ``directory`` and its missing parents, putting each one this run creates on ``made`` (`_make_new`)."""
    missing = []
    while not os.path.lexists(directory):
        missing.append(directory)
        directory = directory.parent
    for path in reversed(missing):
        try:
            _make_new(path, os.mkdir, os.rmdir, made)  # removed only while empty: never what another process put there
        except FileExistsError:
            continue  # another process made it


def _write_new(path: Path, data: bytes) -> None:
    # os.open makes the file and `open` wraps its descriptor at once, with no Python instruction between them: an
    # interrupt never finds a bare descriptor no file object will close (os.open, then os.fdopen, had one), under which
    # Windows cannot remove the file.
    with open(path, "xb", opener=functools.partial(os.open, mode=0o666)) as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _linked(report: Path, temp: Path) -> bool:
    """Whether ``report`` is this run's ``temp`` file under its own name. When that cannot be read and the report
    exists, it may be."""
    try:
        return os.path.samestat(os.lstat(report), os.lstat(temp))
    except OSError:
        return os.path.lexists(report)


def _rename_in(staging: Path, out_dir: Path, given_empty: bool) -> None:
    """Give ``staging`` the name ``out_dir``, removing an empty ``out_dir`` first where a rename cannot take its name.
    A ValueError when another run's kit took the name first. Its try statement is kept out of `_write_kit`'s, which
    holds none (see there)."""
    try:
        if given_empty and not RENAME_ONTO_EMPTY_DIR:
            os.rmdir(out_dir)
        os.rename(staging, out_dir)
    except OSError as exc:
        taken = os.path.lexists(staging) and _taken(out_dir)  # only when the rename did not happen
        if taken:
            raise ValueError(f"--out-dir {out_dir} was filled while this kit was cut: it {taken}; "
                             "nothing was written") from exc
        raise


def _link_report(temp: Path, report: Path) -> None:
    """Give the finished ``temp`` its own name, ``report``; a ValueError when another run's report took it first.
    Its try statement is kept out of `_write_kit`'s, as `_rename_in`'s is."""
    try:
        os.link(temp, report)
    except FileExistsError as exc:
        raise ValueError(f"--report {report} appeared while this kit was cut; nothing was written") from exc


def _write_kit(out_dir: Path, files: dict[str, bytes], report: Path | None, report_data: bytes) -> None:
    """Publish a kit whole into ``out_dir``, absent or empty. ``files`` (paths inside it) are written into a hidden
    staging directory beside it, which then takes its name with one rename. A ``report`` outside it is written to a
    hidden temp file beside it and linked to its own name after the rename; the link fails when that name exists, so
    no run replaces another's report.

    One step publishes the kit: the rename, or with a report outside, the link. Before it, a run that fails or is
    interrupted removes everything it made (the kit it renamed in, the directories it created, its staging directory
    and temp file) and puts back an empty out-dir it was given; from it on, it removes nothing but its temp file.

    Python can raise an interrupt at any instruction: a signal's handler, or a tracer's, runs between any two. So the
    one ``except`` below covers every instruction from the first name the run creates to the end of the publish, and
    no try statement is nested in its own: a nested ``try:`` line compiles to an instruction that no handler covers,
    so an exception raised there would leave without the clean-up. The rename's and the link's own try statements are
    in `_rename_in` and `_link_report`, whose exceptions reach this handler at their call. No step is recorded after
    its call: ``renaming`` and ``linking`` are set before theirs and the filesystem says whether it happened, and
    each name the run creates goes on ``made`` before the call that creates it.

    A killed run, or one interrupted again while it removes what it made, leaves no part of a kit under the out-dir's
    name: at most its hidden staging directory or temp file beside them and the directories it created, or, stopped
    between the rename and the link, the whole kit without its report."""
    given_empty = out_dir.is_dir()
    tag = secrets.token_hex(6)
    staging = out_dir.parent / f".{out_dir.name}.ui-kit-{tag}"
    temp = None if report is None else report.parent / f".{report.name}.{tag}.tmp"
    made: list[tuple[Callable[[Path], object], Path]] = []
    renaming = linking = False
    try:
        _make_dirs(out_dir.parent, made)
        _make_new(staging, os.mkdir, lambda path: shutil.rmtree(path, ignore_errors=True), made)
        for name, data in files.items():
            target = staging / name
            if target.parent != staging:  # a report in a directory of its own inside the out-dir
                target.parent.mkdir(parents=True, exist_ok=True)
            _write_new(target, data)
        if temp is not None:
            _make_dirs(temp.parent, made)
            _make_new(temp, lambda path: _write_new(path, report_data), os.unlink, made)
        renaming = True
        _rename_in(staging, out_dir, given_empty)
        if temp is not None:
            linking = True
            _link_report(temp, report)  # type: ignore[arg-type]  # a temp is made only for a report
            with contextlib.suppress(OSError):
                os.unlink(temp)  # published: the temp is a second name of the report
    except BaseException:
        # A file the run opened and an interrupted frame still holds (between `open` and its `with`, or as the `with`
        # closes it) is closed now, not when those frames go: Windows removes no file that is open.
        traceback.clear_frames(sys.exc_info()[2])
        renamed = renaming and not os.path.lexists(staging)  # the staging directory took the out-dir's name
        published = renamed if temp is None else linking and _linked(report, temp)  # type: ignore[arg-type]
        if published:
            if temp is not None:
                with contextlib.suppress(OSError):
                    os.unlink(temp)
            raise
        if renamed:
            with contextlib.suppress(OSError):
                os.rename(out_dir, staging)
        if given_empty and not os.path.lexists(out_dir):
            with contextlib.suppress(OSError):
                os.mkdir(out_dir)
        for remove, path in reversed(made):
            with contextlib.suppress(OSError):
                remove(path)
        raise


def _publish(*, sheet: Path, out_dir: Path, alpha_floor: int = 64, alpha_edge: int = 2,
             report: Path | None = None) -> int:
    sheet = Path(sheet).expanduser().resolve()
    out_dir = Path(out_dir).expanduser().resolve()
    report_path = Path(report).expanduser().resolve() if report is not None else out_dir / "ui-kit.json"
    if not sheet.is_file():
        raise ValueError(f"sheet is not a file: {sheet}")
    if inside_folder(sheet, out_dir) or same_folder(report_path, sheet):
        raise ValueError("the output directory must not hold the sheet; choose a new directory")
    if report_path.suffix.lower() != ".json":
        raise ValueError("--report must be a .json path")
    taken = _taken(out_dir)
    if taken:
        raise ValueError(f"--out-dir {out_dir} {taken}; a kit is written whole into a new or empty directory")
    inside = report_path.is_relative_to(out_dir)
    if not inside and os.path.lexists(report_path):
        raise ValueError(f"--report {report_path} already exists; a report outside --out-dir is written to a new path")
    with Image.open(sheet) as image:
        if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
            raise ValueError("--sheet must be a single-frame PNG")
        elements, measured = split_ui_kit(image, alpha_floor=alpha_floor, alpha_edge=alpha_edge)
    written = {"kind": measured["kind"], "version": measured["version"], "sheet": str(sheet)}
    written.update((key, value) for key, value in measured.items() if key not in written)
    # wherever --report puts the report, each `file` leads from it to the PNG written in out_dir
    written["elements"] = {name: {**record, "file": report_file(report_path, out_dir / record["file"])}
                           for name, record in measured["elements"].items()}
    files: dict[str, bytes] = {}
    for name, element in elements.items():
        png = io.BytesIO()
        element.save(png, format="PNG")
        files[measured["elements"][name]["file"]] = png.getvalue()
    report_data = (json.dumps(written, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if inside:
        files[report_path.relative_to(out_dir).as_posix()] = report_data
    _write_kit(out_dir, files, None if inside else report_path, report_data)
    missing = written.get("missing") or []
    left_out = f"; missing, left out of the kit: {', '.join(missing)}" if missing else ""
    print(f"ui-kit: {written['status']} — {len(elements)} elements in {out_dir} (report: {report_path}){left_out}")
    return 0


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sheet", required=True, type=Path,
                        help="transparent square or 2:3 PNG drawn with `gen --layout-guide ui-kit`")
    parser.add_argument("--out-dir", required=True, type=Path,
                        help="a new or empty directory for <element>.png and ui-kit.json; "
                             "one that holds anything is refused")
    parser.add_argument("--alpha-floor", type=int, default=64,
                        help="alpha haze cleanup floor, 1..255 (0 turns the cleanup off)")
    parser.add_argument("--alpha-edge", type=int, default=2,
                        help="faint pixels this close to a solid pixel are its edge and are kept")
    parser.add_argument("--report", type=Path,
                        help="JSON report (default: <out-dir>/ui-kit.json), a new path when outside --out-dir; "
                             "its element paths are relative to it")


def run(**kwargs: Any) -> int:
    try:
        return _publish(**kwargs)
    except (OSError, ValueError, TypeError) as exc:
        raise SystemExit(f"ui-kit: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
