# SPDX-License-Identifier: Apache-2.0
"""Measuring a cut on its cells: shifts, held edges, seam flow, ring and drawing steps.

Moved out of `loop.py`; `loop` re-exports every name, so `loop.<name>` still works. `read_steps` and the `view`/`cycles`
readings of `ring_steps` and `fixed_count` are ported from upstream sprite-gen 3.13/3.14 (the gait witness)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.gen import handedness as handed_mod
from sprite_gen.video import auto_motion, local_cycle, motion_anchor
from sprite_gen.video import crossings as crossings_mod
from sprite_gen.video import legs as legs_mod
from sprite_gen.video import repair as repair_mod
from sprite_gen.video.loop_cycle import _small_features, leg_signals
from sprite_gen.video.loop_params import STEP_JITTER_PX, STEP_RATIO_MAX
from sprite_gen.video.loop_pose import body_wrap_offset


def _shifts(analysis: dict[str, Any]) -> tuple[np.ndarray, int]:
    """The analysis translation `auto_motion.analyse` measured, whole pixels a frame, and the room it needs."""
    shifts = np.rint(np.asarray(analysis["analysis_only_translation_xy"], dtype=np.float64)).astype(int)
    return shifts, int(np.abs(shifts).max()) + 1


def moved_frames(frames: list[Image.Image], analysis: dict[str, Any]) -> list[Image.Image]:
    """Every frame whole, moved as `moved_alpha` moves its alpha, on one canvas: what `SeamFlow` reads a cut's wrap
    on, so a body that walks across the frame or toward it is read at the wrap as the cut will be corrected."""
    shifts, pad = _shifts(analysis)
    out = []
    for f, (dx, dy) in zip(frames, shifts):
        canvas = Image.new("RGBA", (f.width + 2 * pad, f.height + 2 * pad))
        canvas.paste(f, (pad + int(dx), pad + int(dy)))
        out.append(canvas)
    return out


def moved_alpha(frames: list[Image.Image], analysis: dict[str, Any]) -> list[np.ndarray]:
    """The alpha of every frame, each moved by the analysis translation `auto_motion.analyse` measured
    (its drift taken out, the way the cut will be corrected), cropped once to the clip's subject:
    what `wrap_pop_on` and `HeldEdge` read a cut on."""
    shifts, pad = _shifts(analysis)
    boxes = [b for b in (f.getchannel("A").point(lambda v: 255 if v >= repair_mod.ALPHA_SOLID else 0).getbbox() for f in frames) if b]
    box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    w, h = box[2] - box[0] + 2 * pad, box[3] - box[1] + 2 * pad
    moved = []
    for f, (dx, dy) in zip(frames, shifts):
        a = np.zeros((h, w), dtype=np.uint8)
        crop = np.asarray(f.getchannel("A").crop(box))
        a[pad + dy:pad + dy + crop.shape[0], pad + dx:pad + dx + crop.shape[1]] = crop
        moved.append(a)
    return moved


def wrap_pop_on(moved: list[np.ndarray]) -> Any:
    """`repair.seam_pop` of a cut (start, length) of the moved alpha (`moved_alpha`): the top band's
    jump at the wrap, for `local_cycle.detect` to rank its candidates by. A cut's value is kept once
    measured."""
    measured: dict[tuple[int, int], dict[str, Any]] = {}

    def pop(start: int, length: int) -> dict[str, Any]:
        if (start, length) not in measured:
            try:
                measured[start, length] = repair_mod.seam_pop([a.astype(np.float32) / 255.0 for a in moved[start:start + length]])
            except ValueError as exc:
                # A frame with nothing in the top band: no jump read, and no pop to rank it by —
                # `local_cycle` neither takes it as closing nor as popping (`skipped`, why).
                measured[start, length] = {"skipped": str(exc), "reference": repair_mod.SEAM_POP_REFERENCE}
        return measured[start, length]
    return pop


class HeldEdge:
    """The held side of the moved alpha (`moved_alpha`), read by `local_cycle` only once a cut's top
    band pops: `side` of a cut (`repair.held_side`, or `skipped`) and `seam` of a cut on a side
    (`repair.held_edge`, or `skipped` with why). A side's edges are read once, on first use."""

    reference = repair_mod.HELD_EDGE_REFERENCE

    def __init__(self, moved: list[np.ndarray]):
        self.moved = moved
        self.frames = len(moved)
        self._edges: dict[str, np.ndarray] = {}

    def _masks(self, start: int, stop: int) -> list[np.ndarray]:
        return [a >= repair_mod.HELD_EDGE_ALPHA for a in self.moved[start:stop]]

    def side(self, start: int, length: int) -> dict[str, Any]:
        try:
            return {"side": repair_mod.held_side(self._masks(start, start + length))}
        except ValueError as exc:
            return {"skipped": str(exc)}

    def seam(self, start: int, length: int, side: str) -> dict[str, Any]:
        if side not in self._edges:
            self._edges[side] = repair_mod.side_edges(self._masks(0, self.frames), side)
        try:
            return repair_mod.held_edge(self._edges[side], start, length)
        except ValueError as exc:
            return {"skipped": str(exc), "reference": repair_mod.HELD_EDGE_REFERENCE}


class SeamFlow:
    """The flow at a cut's wrap, for `local_cycle.by_steps` to rank two-step cuts by: the flow off of the cut's last and
    first frames (`off`), their worst windows (`window`, fractions of the clip's union box) and turns there (`turn`) —
    `repair.ring_scores` on the clip's path, its frames moved as the cut will be corrected (`moved_frames`), read once
    on first use (`repair.clip_flow_states`)."""

    def __init__(self, frames: list[Image.Image], analysis: dict[str, Any]):
        self.frames, self.analysis = frames, analysis
        self._states: np.ndarray | None = None

    def __call__(self, start: int, length: int) -> dict[str, Any]:
        if self._states is None:
            self._states = repair_mod.clip_flow_states(moved_frames(self.frames, self.analysis))
        scores = repair_mod.ring_scores(self._states[start:start + length])
        at = (length - 1, 0)
        return {"off": [round(float(scores["off"][k]), 3) for k in at], "turn": [round(float(scores["turn"][k]), 3) for k in at],
                "window": [[round(v, 3) for v in scores["windows"][int(scores["window"][k])]] for k in at]}


def _discover_and_correct(frames: list[Image.Image]) -> tuple[tuple[Any, dict[str, Any]], list[Image.Image]]:
    """`auto_motion.discover`'s (regions, report) of a motion-auto cut, and its frames corrected on them
    (`auto_motion.correct_cycle`), as `run_loop` anchors the cut it takes. Raises ValueError where they cannot be."""
    found = auto_motion.discover(frames, reference_index=0)
    frames, _ = auto_motion.correct_cycle(frames, found[0], coarse_dx=body_wrap_offset(frames, search=motion_anchor.COARSE_SEARCH))
    return found, frames


def corrected_cut(frames: list[Image.Image]) -> list[Image.Image]:
    """A motion-auto cut's frames corrected as the cut will be (`auto_motion.discover`, `auto_motion.correct_cycle`,
    as `run_loop` corrects the cut it takes). Raises ValueError where they cannot be."""
    return _discover_and_correct(frames)[1]


def seam_on_cells(frames: list[Image.Image]) -> float:
    """The seam gate's reading of a motion-auto cut before it is cut: its frames corrected as the cut will be
    (`corrected_cut`), then read as `wrap_on_cells` reads them. Infinite where the frames cannot be corrected."""
    try:
        frames = corrected_cut(frames)
    except ValueError:
        return math.inf
    return wrap_on_cells(frames)


class CutOnCells:
    """A motion-auto cut (`start`, `length` of `frames`) read before it is cut as the gates read the loop: its frames
    corrected as the cut will be (`corrected_cut`), then its seam (`seam`, as `seam_on_cells` reads it) and the top band's
    jump into its first frame (`pop`, `repair.seam_pop_of`: the seam pop gate's reading, `jolt.seam_pop`), short of the
    jump repair, which runs on the cut once it is taken. A cut is corrected once, whichever is read first, and none of
    its frames is kept; the regions `auto_motion.discover` found on it are (`discovered`), small, so the cut taken is
    not searched for them again. `gate` is `pop` for `local_cycle` (`pop_gate`), or None where the seam pop gate is off."""

    def __init__(self, frames: list[Image.Image], *, pop_max: float | None):
        self.frames, self.pop_max = frames, pop_max
        self._read: dict[tuple[int, int], tuple[float, dict[str, Any]]] = {}
        self._found: dict[tuple[int, int], tuple[Any, dict[str, Any]]] = {}

    def _of(self, start: int, length: int) -> tuple[float, dict[str, Any]]:
        if (start, length) not in self._read:
            try:
                self._found[start, length], cut = _discover_and_correct(self.frames[start:start + length])
            except ValueError as exc:
                self._read[start, length] = (math.inf, {"skipped": f"the cut's frames could not be corrected ({exc})"})
            else:
                self._read[start, length] = (wrap_on_cells(cut), repair_mod.seam_pop_of(cut))
        return self._read[start, length]

    def discovered(self, start: int, length: int, frames: list[Image.Image]) -> tuple[Any, dict[str, Any]] | None:
        """The (regions, report) `auto_motion.discover` found on the cut (`start`, `length`) when it was read, if it was
        and `frames` are its frames to the byte (the cut taken, read back from the frames written for it); else None."""
        found = self._found.get((start, length))
        if found is None or len(frames) != length:
            return None
        if any(a.mode != b.mode or a.size != b.size or a.tobytes() != b.tobytes()
               for a, b in zip(frames, self.frames[start:start + length])):
            return None
        return found

    def seam(self, start: int, length: int) -> float:
        return self._of(start, length)[0]

    def pop(self, start: int, length: int) -> dict[str, Any]:
        measured = self._of(start, length)[1]
        if "skipped" in measured:
            return {"skipped": measured["skipped"], "max": self.pop_max}
        return {"pop": measured["pop"], "over": self.pop_max is not None and measured["pop"] > self.pop_max, "max": self.pop_max}

    @property
    def gate(self) -> Any:
        return self.pop if self.pop_max is not None else None


def cells_box(frames: list[Image.Image]) -> tuple[int, int, int, int]:
    """The union box of a loop's frames, every pixel not fully transparent: the crop its rendered cells are read on."""
    boxes = [b for b in (f.getbbox() for f in frames) if b]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def cell_features(frames: list[Image.Image], box: tuple[int, int, int, int] | None = None) -> np.ndarray:
    """A loop's frames on the analysis thumbnail, each cropped to `box` (their union box, `cells_box`, where none is
    given): the rendered cells, short of the strip's scale, as the seam gate (`wrap_on_cells`) and the seam bridge
    (`repair.seam_bridge`) read them."""
    box = box if box is not None else cells_box(frames)
    return np.stack([_small_features(f.crop(box)) for f in frames])


def wrap_on_cells(frames: list[Image.Image]) -> float:
    """The seam gate's reading of a walk's frames as corrected: cropped to their union box, the wrap over the mean step
    on the analysis thumbnail, between the loop's drawings (`seam_drawings`) — the rendered cells the gate reads, short of
    the strip's scale."""
    flat = cell_features(frames)[seam_drawings(frames)[0]]
    step = float(np.abs(flat[1:] - flat[:-1]).mean())
    return float(np.abs(flat[-1] - flat[0]).mean()) / step if step > 0 else math.inf


def read_steps(frames: list[Image.Image], D: np.ndarray, *, view: str | None = None, facing: str = "right",
               signals: dict[str, list[float]] | None = None) -> crossings_mod.Reading:
    """The clip's crossings (`crossings.read`), and whether they mark its cycles (`local_cycle.read_cycles`, on `D`, the
    clip's distances, the body's bob read off `signals` — the frames' leg signals, read here where not given, with the
    body's own (`legs.body_signals`) — the order its feet are drawn in (`legs.feet_order`), and the `view`'s foot cue:
    `legs.shade_cue` from the side or a diagonal, drawn in tones of `legs.TONE`, `legs.depth_cue` from the front or
    back, in pixel rows of the body's median height). Every
    count the clip's steps make is read on it: the search's cut (`loop._by_steps`), a fixed cut (`fixed_count`) and,
    carried to the loop, the loop as it plays (`ring_steps`)."""
    signals = leg_signals(frames) if signals is None else signals
    try:
        reading = crossings_mod.read(crossings_mod.foot_signals(frames, gap=signals["gap"] if signals else None))
    except ValueError:
        return crossings_mod.Reading(len(frames), "gap", [], why="a frame has no solid body to read the feet from")
    cue = step = None
    if view is not None:
        lateral = view in handed_mod.LATERAL_VIEWS
        cue = [legs_mod.shade_cue(f, facing) if lateral else legs_mod.depth_cue(f) for f in frames]
        heights = signals["height"] if signals else legs_mod.strike_signals(frames)["height"]
        step = legs_mod.TONE if lateral else 1.0 / float(np.median(heights))
    body = {**signals, **legs_mod.body_signals(frames)} if signals else None
    local_cycle.read_cycles(reading, D, view=view, cue=cue, cue_step=step, signals=body,
                            order=[legs_mod.feet_order(f) for f in frames])
    return reading


def ring_steps(frames: list[Image.Image], *, cycles: dict[str, Any] | None = None) -> dict[str, Any]:
    """The steps a loop shows as it plays round: the crossings read on its frames played three times over
    (`crossings.read`), the turn in the middle judged (`Reading.judge`) — a two where the loop still walks two steps,
    joined where the walk goes on. `cycles` is what the clip the loop was cut from said of its crossings
    (`read_steps`): a loop is too short to be read two intervals on, and its crossings mark what the clip's mark."""
    ring = [*frames, *frames, *frames]
    signals = leg_signals(ring)
    try:
        reading = crossings_mod.read(crossings_mod.foot_signals(ring, gap=signals["gap"] if signals else None))
    except ValueError as exc:
        return {"verdict": "uncertain", "code": "unread", "why": str(exc)}
    if cycles is not None and reading.readable:
        reading.mark_cycles(cycles)
    return reading.judge(len(frames), len(frames))


def step_jitter(before: Image.Image, after: Image.Image, *, pixel: float, mean: float) -> dict[str, Any] | None:
    """A step between two drawings that is a pixel's jitter (STEP_JITTER_PX): the move that lays `after` on `before` with
    the least coverage difference (`stop.register`, on alpha), at most STEP_JITTER_PX pixels of the frames as filmed, each
    `pixel` cell pixels (the strip's scale; never under one cell pixel), and the step with that move taken back, on the
    analysis features, within STEP_RATIO_MAX times the loop's `mean` step. The move, in cell pixels, and the ratio of the
    step taken back, or None."""
    from sprite_gen.video import stop as stop_mod  # stop imports this module
    allowed = max(1.0, STEP_JITTER_PX * pixel)
    (dx, dy), _ = stop_mod.register(before, after, max(4, math.ceil(2 * allowed)))
    if max(abs(dx), abs(dy)) > allowed:
        return None
    back = Image.new("RGBA", after.size, (0, 0, 0, 0))
    back.paste(after, (dx, dy))
    ratio = float(np.abs(_small_features(back) - _small_features(before)).mean()) / mean
    return {"move": [dx, dy], "ratio_moved_back": round(ratio, 4)} if ratio <= STEP_RATIO_MAX else None


def drawing_firsts(cells: list[Image.Image]) -> tuple[list[int], int]:
    """A loop's cells as they play, by drawing (`repair.flow_drawings`: a cell held on twos or threes shows its drawing
    again): each drawing's first cell, in play order round the loop, and the hold. A loop drawn a frame each is every
    cell, in order."""
    runs, hold = repair_mod.flow_drawings(repair_mod.flow_states(cells))
    return [run[0] for run in runs], hold


def seam_drawings(cells: list[Image.Image]) -> tuple[list[int], dict[str, Any]]:
    """The cells a walk's or run's seam is read on, in play order round the loop, and the reading (`seam_read`): each
    drawing's first (`drawing_firsts`), as the step gate reads its steps. Read cell by cell, a loop held on twos or threes
    steps nothing and then two or three steps at each drawing: a wrap between two drawings reads two or three times the
    mean step where the loop closes, and a step into a drawing as much where nothing jumps. Every cell of a loop that
    shows fewer than three drawings (`read: cells`), as the step gate skips it; a loop drawn a frame each is every cell
    either way, and reads as it did."""
    firsts, hold = drawing_firsts(cells)
    if len(firsts) < 3:
        return list(range(len(cells))), {"read": "cells", "why": "fewer than three drawings", "drawings": len(firsts), "hold": hold}
    return firsts, {"read": "drawings", "drawings": len(firsts), "hold": hold}


def drawing_steps(cells: list[Image.Image], pixel: float = 1.0) -> dict[str, Any] | None:
    """A loop's cells as they play, read between their drawings (`repair.flow_drawings`: a cell held on twos or threes
    shows its drawing again): each drawing's first cell on the analysis features (`_small_features`) and the step into the
    next drawing's, the last drawing's into the first's across the wrap. The largest of them, the wrap's included, that is
    not a pixel's jitter (`step_jitter`, a drawing's pixel `pixel` cell pixels) over the mean of the others
    (`largest_ratio`, from the cell `largest_from` into the cell `largest_into`, each a drawing's first), the steps over
    STEP_RATIO_MAX that are one (`jitter`, larger first, where there are any), and the wrap's (`wrap_ratio`). None for a
    loop of fewer than three drawings."""
    firsts, hold = drawing_firsts(cells)
    if len(firsts) < 3:
        return None
    flat = np.stack([_small_features(cells[k]) for k in firsts])
    steps = np.abs(np.roll(flat, -1, axis=0) - flat).mean(axis=1)
    mean = float(steps[:-1].mean())
    jitter = []
    # Larger first: a step the ratio holds to the bound only where it is no pixel's jitter. One of the steps the mean is
    # taken over is at most the mean, so the walk stops by it.
    for largest in (int(k) for k in np.argsort(-steps, kind="stable")):
        into = firsts[(largest + 1) % len(firsts)]
        if mean <= 0 or float(steps[largest]) / mean <= STEP_RATIO_MAX:
            break
        moved = step_jitter(cells[firsts[largest]], cells[into], pixel=pixel, mean=mean)
        if moved is None:
            break
        jitter.append({"from": firsts[largest], "into": into, "ratio": round(float(steps[largest]) / mean, 4), **moved})
    return {"read": "drawings", "drawings": len(firsts), "hold": hold, "mean_step": round(mean, 6),
            "largest_ratio": round(float(steps[largest]) / mean, 4) if mean > 0 else None,
            "largest_from": firsts[largest], "largest_into": into,
            "wrap_ratio": round(float(steps[-1]) / mean, 4) if mean > 0 else None,
            **({"jitter": jitter} if jitter else {})}


def fixed_count(frames: list[Image.Image], D: np.ndarray, start: int, length: int, *, view: str | None = None,
                facing: str = "right") -> dict[str, Any]:
    """The clip's own count of the steps a fixed cut covers: the crossings of its frames, steps or cycles as the clip
    says (`read_steps`, on `D`, the clip's distances, as `--anchor motion-auto` reads them), the cut judged
    (`Reading.judge`). Recorded `chosen_by: caller`: the caller named the cut."""
    reading = read_steps(frames, D, view=view, facing=facing)
    return {**reading.record(), "chosen_by": "caller", "cut": reading.judge(start, length)}


def odd_count(cycle: dict[str, Any]) -> bool:
    """A fixed cut the clip's crossings count an odd number of steps (`fixed_count`): its legs swap at the wrap."""
    counted = (cycle.get("step_count") or {}).get("cut") or {}
    return cycle.get("kind") == "fixed" and counted.get("verdict") == "refuted" and bool(counted["k"][0] % 2)


def cycle_apart(files: list[Path], *, start: int, length: int, skip: int, analysis: dict[str, Any],
                motion: dict[str, Any], size: tuple[int, int]) -> dict[str, dict[int, Image.Image]]:
    """The clip's frames a cycle from a motion-auto cut's frames beside its wrap, for `repair.overlap_wrap`: a cycle after
    its first frames ("after") and before its last ("before"), one past the widest run, as far as the clip after its
    lead-in holds them. Each is placed where its cut frame is: moved by the analysis translation between the two
    (`_shifts`, the drift the clip walks over a cycle), then corrected as the cut frame was (`motion`'s shift and padding)."""
    shifts, _ = _shifts(analysis)
    left, top = motion["padding_ltrb"][:2]
    widest = max(repair_mod.OVERLAP_WIDTHS)
    out: dict[str, dict[int, Image.Image]] = {}
    for side, sign in zip(repair_mod.OVERLAP_SIDES, (1, -1)):
        out[side] = {}
        for q in repair_mod.overlap_run(side, widest + 1, length):
            cut, other = start + q, start + q + sign * length
            if not skip <= other < len(files):
                continue
            dx, dy = (int(v) for v in shifts[other - skip] - shifts[cut - skip] + np.asarray(motion["shifts_xy"][q]))
            canvas = Image.new("RGBA", size)
            canvas.paste(Image.open(files[other]).convert("RGBA"), (left + dx, top + dy))
            out[side][q] = canvas
    return out
