# SPDX-License-Identifier: Apache-2.0
# Modified for sprite-gen-agent (split into loop_params, loop_cycle, loop_pose, loop_output, loop_words and
# loop_cells; the gait witness and --view ported from upstream 3.13/3.14; see NOTICE).
"""`sprite-gen video-loop` — cut one seamless cycle out of keyed frames and emit the
loop as cycle frames, a horizontal strip (+ metadata), a transparent GIF and a WebP.

Period first, seam second. A single-start "most similar later frame" search lands
on the 1.5-cycle look-alike of a gait (legs swapped) and produces a loop that
hitches at the wrap (2026-09-08 실측: side walk picked 39 frames where the period
was 28, run picked 25 where it was 17). So the true period is read from the
GLOBAL profile `P[L] = mean_j |f[j] - f[j+L]|` — its deepest local minimum inside
the state's window — and only then is the start chosen as the best seam for that
period. Gaits also compare the motion around the two cycle boundaries so an
accidental single-frame seam cannot outrank a coherent repeat. Idle motion is
tiny and not strictly periodic: its window is opened to
most of the clip, where the seam is lowest.

The deepest repeat can hold two cycles (a clip drawn on twos whose cycle is an odd number of
frames repeats better two cycles on than one), but nothing here cuts shorter than it on the
pixels' word: a gait's half step with the legs drawn alike is the same picture as a true cycle
half a drawing late. The half and the third of the period are screened and recorded
(`cycle.fundamental`, sprite_gen/video/period.py); `video-cycle-align` stops a set on a loop the
screen suspects, and only a count given to it (`--cycles`) takes one cycle out of that loop.

Everything downstream is measured, never assumed: the seam ratio (wrap distance
over the mean adjacent-frame distance inside the cycle) gates the run, the GIF and
WebP are re-opened and verified (frame count, loop flag, transparent corners,
no stale RGB under alpha 0). The strip metadata carries `body_h` — the standing
body height (median per-frame bbox height) — so a consumer can scale a jump strip,
whose cells include air room, to the same on-screen body size as a walk strip.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cache, partial
from pathlib import Path
from typing import Any, Callable, cast

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.gen import handedness as handed_mod
from sprite_gen.spec.runio import atomic_write_text, new_stage, refuse_overlap
from sprite_gen.util.gif_utils import save_clean_gif
from sprite_gen.util.resample import resize_cell
from sprite_gen.video import motion_anchor, auto_motion, local_cycle, gait_fallback, nonloop, effect
from sprite_gen.video import part_pop as part_pop_mod
from sprite_gen.video import cadence as cadence_mod
from sprite_gen.video import crossings as crossings_mod
from sprite_gen.video import held as held_mod
from sprite_gen.video import legs as legs_mod
from sprite_gen.video import repair as repair_mod
from sprite_gen.video import rife as rife_mod
from sprite_gen.video.interpolation_quality import ghost_screen
from sprite_gen.video import evidence as evidence_mod
from sprite_gen.video.frames import drop_specks
from sprite_gen.util.console import utf8_streams
from sprite_gen.video import wrap_blend as wrap_blend_mod
from sprite_gen.video.loop_params import (  # moved out of this file; every name stays reachable here
    ANALYSIS_SIZE,
    ANCHOR_MODES,
    BODY_ANCHOR_BAND,
    BODY_ANCHOR_SEARCH,
    COVERAGE_MIN,
    CYCLE_MODES,
    CYCLE_REFUSED_DIR,
    CYCLE_SOURCE_DIR,
    FACINGS,
    FALLBACK_FRAMES,
    FOLLOW_SOURCE,
    FOOT_BAND,
    GAIT_DOUBLE_SEARCH,
    GAIT_DOUBLE_TOL,
    GAIT_NEAR_EXACT_STEP_FRACTION,
    GIF_FPS_DEFAULT,
    GIF_FRAMES_MIN,
    ONE_SHOT_MIN_ACTIVE,
    ONE_SHOT_MIN_COHERENCE,
    ONE_SHOT_MIN_CONTRAST,
    ONE_SHOT_MIN_LEN,
    ONE_SHOT_MIN_MOVED,
    ONE_SHOT_PAD,
    OVERLAP_WRAP_MODES,
    OVERLAP_WRAP_OFF,
    PERIODICITY_MIN,
    PIN_NOISE_MAX,
    REPAIR_MODES,
    SEAM_RATIO_MAX,
    SIZE_HOLD_MODES,
    STEPS,
    STEP_JITTER_PX,
    STEP_RATIO_MAX,
    STRIP_MAX_CELLS,
    STRIP_MAX_HEIGHT,
    STRIP_MAX_WIDTH,
    SWAY_RECORD,
    SWAY_RECORD_KEYS,
    UNFIXED_MODES,
)
from sprite_gen.video.loop_cycle import (  # moved out of this file; every name stays reachable here
    CycleSelectionError,
    _distances,
    _load_small,
    _repeat_context,
    _seam_on_cells,
    _small_features,
    detect_cycle,
    detect_one_shot,
    distance_matrix,
    fixed_cycle,
    frame_masses,
    leg_signals,
    once_return_cycle,
    periodicity_floor,
    pinned_cycle,
)
from sprite_gen.video.loop_pose import (  # moved out of this file; every name stays reachable here
    _scrub,
    body_centre,
    body_wrap_offset,
    drift_reference,
    foot_centre,
    ramp_frames,
    standing_height,
)
from sprite_gen.video.loop_output import (  # moved out of this file; every name stays reachable here
    handed_over,
    img2webp_supports_exact,
    refuse,
    verify_animation,
    write_loop_report,
    write_webp,
)
from sprite_gen.video.loop_words import (  # moved out of this file; every name stays reachable here
    RETAKE_REASON,
    STAND_REASON,
    _beside,
    _ghost_lines,
    _overlap_lines,
    _overlap_refused,
    _pop_lines,
    _pop_runs,
    _pop_said,
    _pop_settled,
    _stand_line,
    _step_reason,
    _unfixed_lines,
    part_retake,
    retakes,
    seam_reason,
    stand_retake,
)
from sprite_gen.video.loop_cells import (  # moved out of this file; every name stays reachable here
    CutOnCells,
    HeldEdge,
    SeamFlow,
    _discover_and_correct,
    _shifts,
    cell_features,
    cells_box,
    corrected_cut,
    cycle_apart,
    drawing_firsts,
    drawing_steps,
    fixed_count,
    moved_alpha,
    moved_frames,
    odd_count,
    read_steps,
    ring_steps,
    seam_drawings,
    seam_on_cells,
    step_jitter,
    wrap_on_cells,
    wrap_pop_on,
)


def sway_recorded(meta: dict) -> bool:
    """Whether a loop's strip sidecar carries a sway's record, under its new key or its old one."""
    return any(key in meta for key in SWAY_RECORD_KEYS)


# A gait's period is a fact about the body, not about how long the clip runs: the
# same walker takes the same ~1 s stride in a 3 s clip and a 6 s clip. A window cut
# from the clip length (the fractions below) shrank with the clip and, at 3 s, put a
# 1.0-1.3 s stride above the walk ceiling (measured 2026-09-18: one of four walks refused
# outright, another cut at a half step). Gait states therefore take their window in
# seconds — physical bounds on how fast and how slow a locomotion cycle can be — capped
# above by half the clip, the most a detector can *confirm* repeats (a cycle must be seen
# twice to be a cycle at all). The ceiling is a bound in seconds and not simply "half
# the clip" because the periodicity gate compares the period's dip with the profile's
# mean over the whole window: a ceiling that grows with the clip drags in long, sparsely
# sampled lags, inflates that mean, and let a clip with ONE hop and a jittering stand
# pass as a walk (measured 2026-09-20 on the one-shot fixture: 0.23 with a half-clip
# ceiling, 0.03 with 1.6 s). The half-period guard (`min_seconds`) is unchanged.


@dataclass(frozen=True)
class LoopProfile:
    min_frac: float  # window as a fraction of the clip's frame count (non-gait states)
    max_frac: float
    why: str
    periodic: bool = True  # gate: the profile must show a real period (idle is exempt)
    one_shot_ok: bool = False  # the state is an action that may legitimately happen once (jump, attack) -> one-shot detector may take over
    gait: bool = False  # locomotion: a period must hold two steps, so the half-period guard applies
    min_seconds: float = 0.0  # gait floor: a detected period shorter than this is treated as one step
    window_seconds: tuple[float, float] | None = None  # gait states: period bounds in seconds (see above)

    action_seconds: float | None = None  # partial repeats need observed context, not a clip fraction

    def window(self, n: int, fps: float) -> tuple[int, int]:
        """Period-length window [lo, hi] in frames for a clip of `n` keyed frames.

        Gait states: `window_seconds`, the ceiling capped at half the clip. Everything
        else uses clip-length fractions, except attack: its action-time ceiling retains
        actual comparison context and uses a coverage-dependent periodicity floor)."""
        if self.action_seconds is not None:
            lo = max(4, round(n * self.min_frac))
            context = max(8, math.ceil(fps * 0.5))
            return lo, min(round(self.action_seconds * fps), n - context)
        if self.window_seconds is not None:
            lo = max(4, round(self.window_seconds[0] * fps))
            return lo, max(lo + 2, min(n // 2, round(self.window_seconds[1] * fps)))
        lo = max(4, round(n * self.min_frac))
        return lo, max(lo + 2, round(n * self.max_frac))


# State -> detection window. Non-gait profiles are fractions of the clip length so 6 s and
# 10 s clips both work; gait profiles use `window_seconds` (see above). Walk: measured
# strides 0.58-1.5 s (2026-09-09..20, seven bodies); run: 0.67-0.8 s.
STATE_PROFILES: dict[str, LoopProfile] = {
    "idle": LoopProfile(0.60, 0.95, "breathing is slow and not strictly periodic; the lowest seam is a long window", periodic=False),
    "walk": LoopProfile(0.06, 0.31, "full gait = two steps; the floor admits a legless body's fast bounce (~13 frames at 24 fps) — the 15% depth rule rejects the one-step half period when the near/far leg shows, and the gait floor (min_seconds) catches it when it does not", gait=True, min_seconds=0.6, window_seconds=(0.5, 1.6)),
    "run": LoopProfile(0.07, 0.23, "faster gait", gait=True, min_seconds=0.35, window_seconds=(0.3, 1.2)),
    "jump": LoopProfile(0.11, 0.45, "crouch-spring-land-return", one_shot_ok=True),
    "attack": LoopProfile(0.11, 0.45, "swing and return to ready; retain at least half a second of repeat context", one_shot_ok=True, action_seconds=2.5),
    "default": LoopProfile(0.10, 0.45, "generic in-place action", one_shot_ok=True),
}


def profile_for(state: str | None) -> LoopProfile:
    return STATE_PROFILES.get((state or "").strip().lower(), STATE_PROFILES["default"])


@dataclass(frozen=True)
class CutWindow:
    """The cut's length window, worked out once (`of`) and read by every search that sets a length.

    `lo`/`hi` are the first search's window: the state's, save where the caller set a bound
    (`min_len`/`max_len`, --min-len/--max-len). A search that looks elsewhere asks this for its own
    window — the gait fallback and the one-shot failover past the state's ceiling (`past`), a count of
    steps two lengths on (`within`), a window two steps long (`max_len`, local_cycle's `length_cap`) —
    and gets it inside the caller's bounds: an explicit bound is never widened, and no search sets one
    of its own. `hold` is the last word on any cut, whatever chose it."""
    lo: int
    hi: int
    min_len: int | None = None
    max_len: int | None = None

    @classmethod
    def of(cls, prof: LoopProfile, n: int, fps: float, min_len: int | None, max_len: int | None) -> "CutWindow":
        lo_default, hi_default = prof.window(n, fps)
        lo = min_len if min_len is not None else lo_default
        return cls(lo, max_len if max_len is not None else max(lo + 2, hi_default), min_len, max_len)

    def within(self, lo: int, hi: int) -> tuple[int, int]:
        """A search's own window [lo, hi] inside the caller's bounds (empty when lo > hi comes back)."""
        return (lo if self.min_len is None else max(lo, self.min_len),
                hi if self.max_len is None else min(hi, self.max_len))

    def past(self, hi: int) -> int:
        """The ceiling of a search past the state's window: `hi`, or the caller's own where it set one."""
        return hi if self.max_len is None else self.max_len

    def said(self) -> str:
        """The bounds the caller set, as given on the command line."""
        return " ".join(f"--{flag} {v}" for flag, v in (("min-len", self.min_len), ("max-len", self.max_len)) if v is not None)

    def hold(self, cycle: dict[str, Any]) -> None:
        """Raise when a cut is outside the caller's bounds: two explicit instructions that disagree are
        a failure, never one of them followed without a word."""
        length = cycle["length"]
        if (self.min_len is not None and length < self.min_len) or (self.max_len is not None and length > self.max_len):
            exc = ValueError(f"the cut is {length} frames long ({cycle.get('kind', 'cut')} from frame {cycle['start']}), outside {self.said()}")
            exc.diagnostics = {**cycle, "window_refused": {"min_len": self.min_len, "max_len": self.max_len}}  # type: ignore[attr-defined]
            raise exc


def build_strip(frames: list[Image.Image], *, max_cells: int = STRIP_MAX_CELLS, max_height: int = STRIP_MAX_HEIGHT, max_width: int = STRIP_MAX_WIDTH, cycle_seconds: float, body_height: int | None = None, anchor: str = "none", kind: str = "periodic", standing_src: int | None = None, standing_frame: int = 0) -> tuple[Image.Image, dict[str, Any]]:
    """Union-crop (no bottom pad so feet meet the floor), scale, bottom-align, tile horizontally.

    The cell count is capped by the strip's PIXEL width (`max_width`) as well as by
    `max_cells`: the crop is measured over every cycle frame first, then as many evenly
    spaced frames as fit are kept. Subsampling is recorded in the meta, never silent."""
    L = len(frames)
    lifetime = kind == "lifetime"
    boxes: list[Any] = [(im.getchannel("A") if lifetime else im.getchannel("A").point(lambda v: 255 if v >= 8 else 0)).getbbox() for im in frames]
    boxes = [b for b in boxes if b]
    if not boxes:
        raise SystemExit("video-loop: every cycle frame is fully transparent")
    # The side margins may reach past the frame: a subject allowed to touch the left or right
    # edge (video-frames --allow-subject-edge-contact) still gets its 8 transparent columns,
    # so no cell corner is ever the subject. `crop` fills outside the frame with alpha 0.
    left = min(b[0] for b in boxes) - 8
    top = min(b[1] for b in boxes) - 8 if lifetime else max(0, min(b[1] for b in boxes) - 8)
    right = max(b[2] for b in boxes) + 8
    bottom = max(b[3] for b in boxes) + (8 if lifetime else 0)
    # body_h = the STANDING height: the tallest frame whose feet touch the floor. A median
    # over the whole cycle undercounts a jump (crouch + airborne frames dominate) and then
    # over-scales it — the 2026-09-09 hero read 22 % taller than the walk beside it.
    floor = max(b[3] for b in boxes)
    grounded = [b[3] - b[1] for b in boxes if b[3] >= floor - 4] or [b[3] - b[1] for b in boxes]
    # `standing_src` is the caller's own measurement of the standing pose (run_loop: the clip's
    # first frame, or the cut's first — clip frame `standing_frame` — where the cut is filmed at
    # another size than it). The tallest grounded frame counts whatever is raised overhead — an attack's
    # windup lifts the weapon above the head — and would shrink that state against the others.
    body_src = (max(b[3] for b in boxes) - min(b[1] for b in boxes) if lifetime else
                standing_src if standing_src is not None else max(grounded))
    # max_height is a ceiling on the CELL; an explicit body_height is a target for the BODY.
    # Without one, nothing is ever scaled up. With one, a state whose source body is SHORTER
    # than the request has to grow — clamping to 1.0 first made the option a downward clamp
    # only, so "the same value across states gives the same character size" was false for
    # exactly the states that needed it: a 200 px source and a 400 px source both asked for
    # 300 came out 200 and 300 (measured 2026-09-10). The cap still wins over the target.
    fit = max_height / (bottom - top)
    scale = min(1.0, fit) if body_height is None else min(fit, body_height / body_src)
    # --anchor feet: the model may have walked the subject across an "in-place" canvas;
    # a union crop keeps that drift inside every cell. Remove the drift (see
    # drift_reference) so every cell stands on the same mean foot line, and report it.
    feet, drift, sway = drift_reference(frames, boxes) if anchor == "feet" else (None, 0.0, 0.0)
    drift_px = round(drift)
    if feet:
        lead = max(fc - b[0] for fc, b in zip(feet, boxes)) + 8
        trail = max(b[2] - fc for fc, b in zip(feet, boxes)) + 8
        cell_src_w = lead + trail
    else:
        cell_src_w = right - left
    w = max(1, round(cell_src_w * scale))
    h = max(1, round((bottom - top) * scale))
    cap = max(1, min(max_cells, max_width // max(1, w)))
    idx = list(range(L))
    if L > cap:
        idx = nonloop.samples(L, cap) if kind in nonloop.KINDS else [round(k * L / cap) for k in range(cap)]
    chosen = [frames[i] for i in idx]
    if feet:
        cells = []
        for i in idx:
            im = frames[i]
            cell = Image.new("RGBA", (round(cell_src_w), bottom - top), (0, 0, 0, 0))
            cell.alpha_composite(im.crop((0, top, im.width, bottom)), (round(lead - feet[i]), 0))
            cells.append(resize_cell(cell, (w, h)))
    else:
        cells = [resize_cell(im.crop((left, top, right, bottom)), (w, h)) for im in chosen]
    strip = Image.new("RGBA", (w * len(cells), h), (0, 0, 0, 0))
    for k, im in enumerate(cells):
        strip.alpha_composite(im, (k * w, 0))
    meta = {
        "frames": len(cells),
        "sample_indices": idx,
        "source_rect": [left, top, right, bottom] if not feet else None,
        "w": w,
        "h": h,
        # how the cycle was cut, and whether a runtime should repeat it: a one-shot
        # (rest -> action -> rest) plays once on a trigger; every other cut is a loop.
        # The spec asset loader reads `loop` from this sidecar.
        "kind": kind,
        "loop": kind not in ("one-shot", *nonloop.KINDS),
        "body_h": round(body_src * scale),
        "body_src_h": body_src,  # the standing height as filmed; body_h / body_src_h > 1 means the cells were upscaled
        "body_ref": ("cut-first-frame" if standing_frame else "first-frame") if standing_src is not None else "tallest-grounded",
        "scale": round(scale, 4),
        "delay_ms": round(1000 * cycle_seconds / len(cells), 2),
        "cycle_frames": L,
        "cycle_seconds": round(cycle_seconds, 4),
        "subsampled": L > cap,
        "cell_cap": cap,
        "top_margin_px": top,
        "cell_height_cap": max_height,  # --strip-height: what a rebuild of these cells (video-cycle-align) scales against
        "body_height_target": body_height,
        "foot_anchor": anchor,  # "none" | "feet" — how the cells were aligned
        "drift_px": drift_px,  # source px of slow in-canvas drift removed across the cycle (0 when not measured)
        "foot_sway_px": round(sway),  # source px the planted foot moves within the gait, kept as is (0 when not measured)
        "foot_x": round(lead * scale) if feet else None,  # x of the foot line inside every cell
    }
    if standing_src is not None and standing_frame:
        meta["body_ref_frame"] = standing_frame  # the clip frame the standing height was read on
    if lifetime:
        meta.update(blend="add", body_ref="effect-extent",
                    anchor=[(frames[0].width / 2 - left) * w / (right - left),
                            (frames[0].height / 2 - top) * h / (bottom - top)])
    if feet:
        # the spec asset loader reads a sidecar `anchor` as the [x, y] pivot (default:
        # bottom-centre); with feet alignment the true pivot is the foot line at the
        # cell bottom, so declare it and scenes stand the sprite on its feet
        meta["anchor"] = [meta["foot_x"], h]
    return strip, meta


def resampled_cells(strip: Image.Image, meta: dict[str, Any], idx: list[int], length: int) -> list[Image.Image]:
    """The cells of `strip` (`build_strip`, its `meta`) at the frames `idx` of a cycle `length` frames long: the
    GIF and WebP frames, and what the seam gate reads on rendered cells. A strip subsampled past its cell cap
    holds fewer cells than the cycle has frames, and gives the nearest."""
    cells = [strip.crop((k * meta["w"], 0, (k + 1) * meta["w"], meta["h"])) for k in range(meta["frames"])]
    return [cells[min(len(cells) - 1, round(j * len(cells) / length))] for j in idx]


def shown_runs(cells: list[Image.Image]) -> int:
    """The frames a GIF or WebP of `cells` holds: a cell shown twice in a row (a filmed ghost given way
    to the frame beside it, `--between nearest` stretching a loop) is one frame of twice the delay in
    both, so one per run of identical cells."""
    return 1 + sum(1 for a, b in zip(cells, cells[1:]) if a.tobytes() != b.tobytes())


def _repair_jumps(frames: list[Image.Image], interpolate: rife_mod.Interpolate | None, *, facing: str,
                  required: bool, screen: dict[str, Any], wrap: dict[str, Any] | None = None
                  ) -> tuple[list[Image.Image], dict[str, Any], dict[str, Any] | None]:
    """`repair_mod.repair_jumps` on the cut as filmed and its ghost `screen`, with RIFE located only
    once a frame is to be made: a loop with no frame off its flow and no ghost between two clean frames
    needs no binary, nor one with no part that rushes (the eased retime, `repair.surges`). The report names the
    interpolator that made the frames.

    `wrap` (the clip's frames a cycle from the cut's beside its wrap, `cycle_apart`, and the gate's `seam_max`) asks
    first for the overlapped wrap (`repair_mod.overlap_wrap`) of a cut two steps long whose wrap the gate refuses;
    the frames it makes are then neither made from, nor beside, nor again (`overlapped`). Its record is returned
    third, None where it was not asked; a `wrap` with `off` (`--overlap-wrap off`) and no frames records it turned off,
    nothing read or made. The overlapped wrap and the eased retime after it read the body's drift on one torso, the
    cut's as filmed (`repair_mod.Torso`).

    A frame to make and no RIFE installed: `required` lets `RifeNotInstalled` through; otherwise
    each filmed ghost gives way to the clean frame beside it, a jump (a frame off the flow), a rush and a wrap that does
    not close stay as filmed, and the record says why where a jump or a rush was left (`rife`, `install`; `applied`
    false where nothing was replaced)."""
    made_by: list[dict[str, str]] = []
    calls = 0

    def located() -> rife_mod.Interpolate:
        nonlocal interpolate
        if interpolate is None:
            found = rife_mod.Rife()
            made_by.append(found.describe())
            interpolate = found
        return interpolate

    def lazy(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        nonlocal calls
        calls += 1
        return located()(a, b, t)

    def lazy_read(a: Image.Image, b: Image.Image, t: float) -> tuple[Image.Image, np.ndarray]:
        # the overlapped wrap reads each frame it makes with RIFE's single passes, for a part torn: an interpolator given
        # that cannot say how far its passes disagree is refused, never read as agreeing
        nonlocal calls
        read = getattr(located(), "read", None)
        if read is None:
            raise ValueError("the overlapped wrap reads how far RIFE's single passes disagree on each frame it makes, and "
                             "the interpolator given has no `read` (rife.Rife.read)")
        calls += 1
        return read(a, b, t)

    overlap: dict[str, Any] | None = None
    overlapped: list[int] = []
    torso = None
    if wrap is not None:
        torso = None if "off" in wrap else repair_mod.Torso(frames)
        asked = partial(repair_mod.overlap_wrap, others=wrap.get("others", {}), seam=wrap_on_cells,
                        steps=partial(ring_steps, cycles=wrap.get("cycles")),
                        seam_max=wrap["seam_max"], reads=bool(screen["reads"]), torso=torso, off=wrap.get("off"))
        try:
            frames, overlap = asked(frames, read=lazy_read)
        except rife_mod.RifeNotInstalled as exc:
            if required:
                raise
            frames, overlap = asked(frames, read=None)
            overlap.update(why="RIFE not installed — the wrap cut as filmed", rife=str(exc), install=rife_mod.install_command())
        overlapped = overlap["frames"]
        if overlap["calls"]:
            overlap["interpolator"] = {**made_by[0], **cast("rife_mod.Rife", interpolate).counts()} if made_by else {"kind": "injected"}
    wrap_calls = calls
    try:
        repaired, record = repair_mod.repair_jumps(frames, lazy, facing=facing, screen=screen, overlapped=overlapped,
                                                   torso=torso)
    except rife_mod.RifeNotInstalled as exc:
        if required:
            raise
        # RIFE is located before the first frame is made, so nothing was made yet: the stage runs again
        # with no interpolator, each ghost giving way to the clean frame beside it and a jump left as filmed.
        repaired, record = repair_mod.repair_jumps(frames, None, facing=facing, screen=screen, overlapped=overlapped,
                                                   torso=torso)
        record["applied"] = bool(record["replaced"])
        if "unmade" in record:
            record.update(why="RIFE not installed — cut as filmed" if not record["replaced"] else
                          "RIFE not installed — the jump cut as filmed, each filmed ghost given way to the clean frame beside it",
                          rife=str(exc), install=rife_mod.install_command())
        elif record.get("ease_skipped") == "no interpolator":
            # no jump, but a part that rushes: the frames either side of it were to be made again at eased times
            record.update(why="RIFE not installed — the rush cut as filmed" if not record["replaced"] else
                          "RIFE not installed — the rush cut as filmed, each filmed ghost given way to the clean frame beside it",
                          rife=str(exc), install=rife_mod.install_command())
        return repaired, record, overlap
    frames = repaired
    record["applied"] = bool(record["replaced"])
    if calls > wrap_calls:
        record["interpolator"] = dict(made_by[0]) if made_by else {"kind": "injected"}
    return frames, record, overlap


def _bridge_seam(frames: list[Image.Image], interpolate: rife_mod.Interpolate | None, *, facing: str, required: bool,
                 seam_max: float, hold: int | None, skip: str | None = None) -> tuple[list[Image.Image], dict[str, Any]]:
    """`repair_mod.seam_bridge` on a loop that is not a walk, with RIFE located only once a frame is to be made: a wrap
    that reads as a step of the loop needs no binary. The record names the interpolator that made the frames.

    A frame to make and no RIFE installed: `required` lets `RifeNotInstalled` through; otherwise the wrap is read again
    with no interpolator, cut as filmed, and the record says why (`rife`, `install`, the run it stopped at `unmade`)."""
    made_by: list[dict[str, str]] = []

    def lazy(a: Image.Image, b: Image.Image, t: float) -> Image.Image:
        nonlocal interpolate
        if interpolate is None:
            found = rife_mod.Rife()
            made_by.append(found.describe())
            interpolate = found
        return interpolate(a, b, t)

    asked = partial(repair_mod.seam_bridge, limit=seam_max, hold=hold, facing=facing, skip=skip)
    try:
        frames, record = asked(frames, lazy)
    except rife_mod.RifeNotInstalled as exc:
        if required:
            raise
        frames, record = asked(frames, None)
        record.update(why="RIFE not installed — the wrap cut as filmed", rife=str(exc), install=rife_mod.install_command())
    record["applied"] = bool(record["replaced"])
    if record["calls"]:
        record["interpolator"] = {**made_by[0], **cast("rife_mod.Rife", interpolate).counts()} if made_by else {"kind": "injected"}
    return frames, record


def _bridge_lines(bridge: dict[str, Any]) -> list[str]:
    """What became of a wrap the seam bridge read as a jump (`repair.seam_bridge`), in words (`video-loop: warning:`
    lines): the frames made and the ones to look at, or why none was made. A wrap that reads as a step says nothing."""
    if "skipped" in bridge:
        return []
    said = (f"the loop's wrap jumps (its step {bridge['seam']:.2f}x the mean of the {bridge['around']} steps either side, "
            f"over {bridge['limit']:g})")
    if bridge["replaced"]:
        taken = bridge["tries"][-1]
        a, b = taken["anchors"]
        return [f"{said}: frames {', '.join(map(str, taken['frames']))} were made again by RIFE between frames {a} and {b} "
                "(cycle/), so the loop plays across it — look at them in the loop, or cut it with --repair off "
                "(docs/loop-repair.md section 2)",
                *(f"frame {m['frame']} was made at the wrap with RIFE's frame between frames {a} and {b}, which may "
                  f"cross-fade two drawings inside its outline (crossfade {m['proposal']['crossfade']:.4f}); kept, a reading "
                  "to look at (docs/loop-repair.md section 4)" for m in taken["made"] if m["look"])]
    if "unmade" in bridge:
        return [f"{said}, and RIFE is not installed, so no frame was made round it — run `{rife_mod.install_command()}` "
                "and cut it again (docs/loop-repair.md section 2)"]
    reasons = {"line": "the straight line between its anchors does not close it", "fault": "a frame made has a fault",
               "seam": "the frames made do not close it"}
    refused = "; ".join(f"{t['width']}: {reasons[t['rejected_for']]}" + (f" ({', '.join(t['faults'])})" if t.get("faults") else "")
                        for t in bridge["tries"])
    return [f"{said}, and no frames made round it close it ({refused}): cut as filmed (docs/loop-repair.md section 2)"]


def _by_steps(cycle: dict[str, Any] | None, refused: ValueError | None, *, frames: list[Image.Image], analysis: dict[str, Any],
              D: np.ndarray, trajectory: np.ndarray, window: CutWindow, searched: dict[str, Any], detect: dict[str, Any],
              seam_max: float, cells: CutOnCells, signals: dict[str, list[float]] | None, view: str | None = None,
              facing: str = "right") -> dict[str, Any]:
    """The cut the clip's own steps choose (`local_cycle.by_steps`, the crossings of the frames the search read,
    sprite_gen/video/crossings.py, steps or cycles as the clip and its `view` say: `read_steps`), recorded
    `step_count`, from the search's cut or its refusal (`refused`).
    `cells` reads a cut of `frames` before it is cut as the seam gate and the seam pop gate read the loop; `signals`
    are `leg_signals(frames)`, read once for the search.

    Where the clip shows its steps but no cut of two, and the search's cut is one they refute (one step, three), the
    search runs again on the window it searched (`searched`) refusing such cuts (`detect`'s `judge`). A search that
    finds nothing names each refused window's count, those the crossings refute last, and raises."""
    reading = read_steps(frames, D, view=view, facing=facing, signals=signals)
    moved = moved_alpha(frames, analysis)
    picked, record = local_cycle.by_steps(
        D, trajectory, reading, found=cycle, refused=refused, min_len=window.lo, max_len=window.max_len or len(frames),
        seam_flow=SeamFlow(frames, analysis), wrap_pop=wrap_pop_on(moved), seam_gate=cells.seam, pop_gate=cells.gate,
        flow_limit=repair_mod.FLOW_LIMIT, seam_max=seam_max,
        gait_floor=detect["gait_floor"], periodicity_min=detect["periodicity_min"], signals=signals)
    if picked is not None and record.get("cut", {}).get("verdict") == "refuted":
        first = record["cut"]
        try:
            picked = local_cycle.detect(D, trajectory, signals=signals, wrap_pop=wrap_pop_on(moved), held_edge=HeldEdge(moved),
                                        length_cap=window.max_len, judge=reading.judge, pop_gate=cells.gate, **searched, **detect)
        except ValueError as exc:
            picked, refused = None, exc
        record["searched_again"] = {"refuted": first, "why": f"no cut of two steps, and the search's cut is {first['why']}: "
                                                              "searched again, refusing the cuts the crossings refute"}
        if picked is not None:
            record["cut"] = reading.judge(picked["start"], picked["length"])
        elif window.said() and record.get("two_cuts") == 0 and reading.twos(1, len(frames)):
            # The caller's window holds none of the walk's two steps: the window and the walk disagree.
            lengths = sorted({row["cut"][1] for row in reading.twos(1, len(frames))})
            failed = ValueError(f"no cut of two steps in {window.said()}: the clip's crossings show two steps take "
                                f"{lengths[0]}-{lengths[-1]} frames, and every cut the window holds that the search found is "
                                f"{first['why']} — widen the window, or count the steps (--steps)")
            failed.diagnostics = getattr(refused, "diagnostics", {})  # type: ignore[attr-defined]
            refused = failed
    if picked is None:
        diagnostics = getattr(refused, "diagnostics", None)
        if isinstance(diagnostics, dict):
            if reading.readable and diagnostics.get("candidates") and "step_count" not in diagnostics["candidates"][0]:
                local_cycle.judged(diagnostics["candidates"], reading.judge)
                diagnostics["candidates"].sort(key=lambda row: row["step_count"]["verdict"] == "refuted")
                diagnostics["order"] = f"{diagnostics.get('order', 'score')}, windows whose steps the crossings refute after the rest"
            diagnostics["step_count"] = record
        assert refused is not None
        raise refused
    picked["step_count"] = record
    return picked


def _witnessed(cycle: dict[str, Any], *, frames: list[Image.Image], view: str | None = None,
               signals: dict[str, list[float]] | None = None) -> dict[str, Any]:
    """The cut a walk's search and step count chose (`_by_steps`), held to one word before it is cut whichever way it
    was chosen — the search's, two steps by the clip's crossings, crossings read or not: the body's
    (`local_cycle.cut_word`: one of its lines saying so both on the screen and over its feet), or where it says
    no cycle the order the feet are drawn in (`legs.feet_order`, as the step count reads it; not from the side `view`),
    recorded `cut_witness` — not the order where the cut is the search's, kept because none of the clip's two-step cuts
    closes (`declined`). Legs drawn alike loop as well on one step as on two, and crossings read off feet that slide,
    or off a shadow under them on a picture that moves, count half steps. `signals` are `leg_signals(frames)`, read
    here where not given.

    - Its own rise and fall twice the cut, or four times: the cut is a cycle, or two, and stands. Cut two cycles, its
      strip still counts two steps where the crossings counted them, and `video-set` leaves it out of its state's cycle
      alignment (local_cycle.FOUR_STEPS, `local_cycle.four_steps`). Where its lowest row rises and falls twice the cut
      more than its top line on the screen, the record carries a mark (`mark`), and nothing is held on it.
    - Else nothing witnesses the cut handed out whole cycles, and it is held (`held: unwitnessed`): kept — two of the
      clip's crossing intervals where it counted them, else as the search chose it — and named
      (local_cycle.UNWITNESSED, `held_verdict`); `video-set` leaves it out of its state's cycle alignment
      (`local_cycle.unwitnessed`). So is a cut the body rises and falls once in (`says` one step): one step of a walk,
      or a whole cycle of a body that rises and falls once a cycle — a limp, a body with no legs that bounces as it
      goes — which nothing that reads no leg tells apart. It is not cut again two long on its word: that would loop
      two of such a body's cycles.

    A cut whose crossings the step count left on steps, nothing witnessing two steps an interval (`cycles.unwitnessed`),
    is held so already, and named on its step count (`held: step count`). Only a walk or a run comes here
    (motion-auto, its cycle searched, no count given)."""
    first = [cycle["start"], cycle["length"]]
    if ((cycle.get("step_count") or {}).get("cycles") or {}).get("unwitnessed"):
        cycle["cut_witness"] = {"cut": first, "held": "step count"}
        return cycle
    strike = leg_signals(frames) if signals is None else signals
    order = None if view == local_cycle.SIDE_VIEW or not strike else [legs_mod.feet_order(f) for f in frames]
    said = {"cut": first, **local_cycle.cut_word({**strike, **legs_mod.body_signals(frames)} if strike else None,
                                                 cycle["length"], order=order,
                                                 declined=(cycle.get("step_count") or {}).get("declined"))}
    cycle["cut_witness"] = said if said["says"] in ("two steps", "four steps") else {**said, "held": "unwitnessed"}
    return cycle


def _counted(cycle: dict[str, Any], steps: int, *, window: CutWindow, D: np.ndarray, trajectory: np.ndarray,
             frames: list[Image.Image], moved: list[np.ndarray], detect: dict[str, Any], cells: CutOnCells,
             signals: dict[str, list[float]] | None) -> dict[str, Any]:
    """The cut once its steps are counted (`--steps`): 2 keeps it; 1 cuts again in the window two of
    its lengths long, [2L-1, 2L+1], inside the caller's bounds (`window.within`) — not half the clip —
    with the same search (a repeat seen in the clip, the top band and the held side read again) save that
    the cut whose wrap the clip itself plays is taken among the equivalent ones (`local_cycle._closure`),
    and no window twice that. Where the caller's bounds leave none of [2L-1, 2L+1], the count and the
    bounds disagree: a failure, neither followed. Where they leave part of it, that part is read
    (`steps.window`, beside `steps.counted_window`). A clip too short to see such a window repeat is cut
    all the same, the count standing for the repeat (`local_cycle.counted`, `unseen` says why);
    `cycle.coverage` and the seam gate say the rest. Recorded `steps` (`first_cut`: the cut counted)."""
    first = {"start": cycle["start"], "length": cycle["length"]}
    if steps == 2:
        cycle["steps"] = {"count": 2, "by": "given", "given": 2}
        return cycle
    counted_window = [2 * first["length"] - 1, 2 * first["length"] + 1]
    lo, hi = window.within(*counted_window)
    record = {"count": 2, "by": "given", "given": 1, "first_cut": first, "window": [lo, hi]}
    if [lo, hi] != counted_window:
        record["counted_window"] = counted_window
    if lo > hi:
        exc = ValueError(f"--steps 1: the cut of {first['length']} frames from {first['start']} is one step, so the cycle is "
                         f"{counted_window[0]}-{counted_window[1]} frames long, and {window.said()} leaves none of that: the count "
                         f"and the window disagree — drop one, or give a window that holds the cycle")
        exc.diagnostics = {**cycle, "steps": {**record, "window": None, "refused": f"outside {window.said()}"}}  # type: ignore[attr-defined]
        raise exc
    try:
        found = local_cycle.detect(D, trajectory, min_len=lo, max_len=hi, length_cap=hi, signals=signals,
                                   wrap_pop=wrap_pop_on(moved), held_edge=HeldEdge(moved), double=False, closure=True,
                                   pop_gate=cells.gate, **detect)
    except ValueError as unseen:
        # Too short a clip to see the cycle repeat: the count stands for it (`cycle.coverage` says how short).
        try:
            found = local_cycle.counted(D, trajectory, min_len=lo, max_len=hi, periodicity_min=detect["periodicity_min"],
                                        signals=signals, pop_gate=cells.gate)
        except ValueError as exc:
            failed = ValueError(f"--steps 1: the cut of {first['length']} frames from {first['start']} is one step, and the clip "
                                f"({len(D)} frames) has no window {lo}-{hi} frames long to cut: {exc}")
            failed.diagnostics = {**getattr(unseen, "diagnostics", {}), "steps": record}  # type: ignore[attr-defined]
            raise failed from exc
        found["unseen"] = str(unseen)
    found["steps"] = record
    return found


def _from_lead_in(record: Any, lead: int) -> Any:
    """A search's record (a cut, or the windows a refusal measured) in the clip's own frame numbers: the
    search read the clip after its first `lead` frames, so every `start` and `context_pair_range` it wrote
    moves on by `lead`. A caller cuts again from these (`--cycle fixed --start`) on the whole clip."""
    if isinstance(record, dict):
        return {key: (value + lead if key == "start" and isinstance(value, int)
                      else [value[0] + lead, value[1] + lead] if key == "context_pair_range"
                      else _from_lead_in(value, lead)) for key, value in record.items()}
    if isinstance(record, list):
        return [_from_lead_in(value, lead) for value in record]
    return record


def _one_shot(D: np.ndarray, window: CutWindow, hi: int, masses: np.ndarray) -> dict[str, Any]:
    """`detect_one_shot` in its window: ONE_SHOT_MIN_LEN to `hi`, inside the caller's bounds. Where the
    caller set one and no one-shot fits, the refusal says the window it searched."""
    shortest, longest = window.within(ONE_SHOT_MIN_LEN, hi)
    try:
        return detect_one_shot(D, min_len=shortest, max_len=longest, frame_mass=masses)
    except CycleSelectionError as exc:
        if not window.said():
            raise
        raise CycleSelectionError(f"{exc} — in {shortest}-{longest} frames, inside {window.said()}", exc.diagnostics) from exc


def coverage_verdict(cycle: dict[str, Any]) -> list[str]:
    """A cut two steps long whose clip holds under COVERAGE_MIN of a cycle past it, in words; empty
    otherwise (and for a cut whose steps are not known)."""
    if "steps" not in cycle or cycle["coverage"] >= COVERAGE_MIN:
        return []
    return [f"the cut is two steps long ({cycle['length']} frames, one cycle) and the clip holds {cycle['coverage']:.2f} of a cycle "
            f"past it (under {COVERAGE_MIN:g}): its repeat is barely seen — the seam says whether it closes; a longer take shows it repeat"]


def _blend_wrap(frames: list[Image.Image], pin_file: Path, *, mode: str, anchor: str, cycle: dict[str, Any], fps: float,
                seam_max: float) -> tuple[list[Image.Image], dict[str, Any]]:
    """`wrap_blend.apply` on a pinned cut, the pinned last frame cleaned as the cut's frames are (`_scrub`,
    `drop_specks`). Not tried, and said why: `--wrap-blend off`; an anchor that moves the cells off the place the pin
    holds; a pin the gate refuses, which fails the clip whatever its wrap shows."""
    if mode == "off":
        return frames, {"applied": False, "why": "--wrap-blend off"}
    if anchor != "none":
        return frames, {"applied": False, "why": f"--anchor {anchor} moves the cells off the place the pinned frame holds"}
    if cycle["pin_error"] > cycle["pin_tolerance"]:
        return frames, {"applied": False, "why": "the pin does not close: the clip is refused on it"}
    pin = Image.open(pin_file).convert("RGBA")
    _scrub(pin)
    pin, _ = drop_specks(pin, alpha_over=16, diagonal=False, apart=0)
    return wrap_blend_mod.apply(frames, pin, fps=fps, seam_max=seam_max)


def _size_hold(frames: list[Image.Image], *, lo: int, hi: int,
               cut: tuple[int, int] | None = None) -> tuple[list[Image.Image] | None, dict[str, Any], Any]:
    """A walk or run held at its first frame's size, and its feet on the first frame's row, before its cut is chosen
    (`--size-hold auto`): the frames held (None: the clip changes size by less than SIZE_HOLD_MIN, read one cycle on,
    and is cut as filmed), the report's `size_hold`, and where a box of a frame as filmed lands in its held frame
    (`gait_fallback.held_box`, None when not held).

    A clip filmed from its first frame only grows or shrinks as it plays, so the frame one cycle on is not the size of
    the first and the loop pops at the wrap; a walk away from the camera climbs the frame as well, and held at its size
    with its feet where they were filmed it pops up instead. The change is read one cycle on, so a first pose that
    settles into the walk is not taken for a body that shrinks, nor for feet that climb (gait_fallback.cycle_drift,
    foot_drift). A cut the caller names (`cut`: start, length) is held only where it shows the change itself, a cycle
    of its length apart (`gait_fallback.at_cut`), and its feet are moved only where it shows them move: a clip whose
    frames that are not the walk read a change the cut does not make is cut as filmed (`size_hold.at_cut`, `why`).
    Held, its record carries the move each frame of the cut was held by (`size_hold.cut_frames`)."""
    measured = gait_fallback.cycle_drift(frames, min_lag=lo, max_lag=hi)
    hold: dict[str, Any] = {"applied": False, "min": gait_fallback.SIZE_HOLD_MIN,
                            **{k: measured[k] for k in ("height_first_px", "height_last_px", "drift", "method",
                                                        "lag", "pairs", "per_lag", "pose_match", "drift_fitted_line")}}
    if abs(measured["drift"]) < gait_fallback.SIZE_HOLD_MIN:
        return None, hold, None
    feet = gait_fallback.foot_drift(frames, measured)
    if cut is not None:
        seen = hold["at_cut"] = gait_fallback.at_cut(frames, measured, feet, start=cut[0], length=cut[1])
        if not seen["size"]:
            hold["why"] = ("the cut does not change size a cycle of its length apart as the clip reads it (frames that are "
                           "not the walk, or a clip too short to show its cycle twice): cut as filmed")
            return None, hold, None
        if feet["held"] and not seen["feet"]:
            feet = {**feet, "held": False, "lift": None,
                    "why": "the cut's feet do not move a cycle of its length apart as the clip reads them"}
    lift = feet.pop("lift")
    pad = gait_fallback.undo_padding(frames, measured, lift)
    held = gait_fallback.undo_scale(frames, measured, pad=pad, lift=lift)
    hold.update(applied=True, padding_ltrb=list(pad), feet=feet)
    if cut is not None:
        # The move each frame of the cut was held by, exact (`gait_fallback.frame_hold`): `video-loop-repair` holds the
        # cut's source frames by it again before it reads them (sprite_gen/video/source.py).
        hold["cut_frames"] = [gait_fallback.frame_hold(k, measured, lift) for k in range(cut[0], cut[0] + cut[1])]
    return held, hold, partial(gait_fallback.held_box, measured=measured, pad=pad, lift=lift)


class _Scratch:
    """The folders a run writes the frames a size hold or the gait fallback scaled back into: each a new one of the
    run's own in the out dir (`runio.new_stage`: `.gait-fallback-frames.<pid>-<random>`), so a folder already there —
    an input's, or another run's — is never written into or removed; `run_loop` removes them however the run ends."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.made: list[Path] = []

    def new(self) -> Path:
        out = self.out_dir.expanduser().resolve()
        out.mkdir(parents=True, exist_ok=True)
        self.made.append(new_stage(out / FALLBACK_FRAMES, ""))
        return self.made[-1]

    def remove(self) -> None:
        for folder in self.made:
            shutil.rmtree(folder, ignore_errors=True)


def _refuse_overlap(frames_dir: Path, files: list[Path], out_dir: Path, name: str, target: Path, report_given: bool) -> None:
    """A cut never writes over, moves or deletes the frames it reads: `--frames-dir`, every frame found in it and every
    link or folder their paths look up, against what the cut replaces in `--out-dir` (`handed_over`, `cycle.refused/`)
    and its report (`runio.refuse_overlap`). Refused before a frame is read or anything is made. `--out-dir` itself is
    not compared: a cut adds to it, and the clip's frames are often inside it (`video-frames`' `keyed/`)."""
    out = out_dir.expanduser().resolve()
    outputs = {f"{path.name} in --out-dir": path for path in (*handed_over(out, name), out / CYCLE_REFUSED_DIR)}
    outputs["--report" if report_given else f"{target.name} in --out-dir"] = target
    refuse_overlap({"--frames-dir": frames_dir, **{f"{f.name} in --frames-dir": f for f in files}}, outputs, verb="video-loop")


def run_loop(frames_dir: Path, out_dir: Path, **options: Any) -> dict[str, Any]:
    """Cut the loop (`_cut_loop`, which takes the options). The frames a size hold or the gait fallback scales back
    are written in folders of the run's own (`_Scratch`), removed however the run ends."""
    scratch = _Scratch(out_dir)
    try:
        return _cut_loop(frames_dir, out_dir, scratch=scratch, **options)
    finally:
        scratch.remove()


@dataclass
class _Run:
    """What one `video-loop` run carries from phase to phase of `_cut_loop`: where its report goes (`target`, in
    `out_dir`, under `name`) and the report as it grows (`report`). `refused` writes a refusal — the report so far, the
    cut, and what the refusing phase adds — and gives back the exit to raise (`raise run.refused(...) from exc`)."""

    target: Path
    out_dir: Path
    name: str
    report: dict[str, Any]

    def refused(self, error: str, cycle: Any, *, cut_written: bool = False, **extra: Any) -> SystemExit:
        refuse(self.target, self.out_dir, self.name, {**self.report, "status": "failed", "error": error, "cycle": cycle, **extra},
               cut_written=cut_written)
        return SystemExit(error)


def _settled_modes(state: str | None, cycle_mode: str, anchor: str | None, *, fps: float, gif_fps: float, n_out: int | None,
                   start: int | None, length: int | None, steps: int | None, size_hold: str, repair: str, overlap_wrap: str,
                   wrap_blend: str, unfixed: str, facing: str, seam_pop_max: float | None,
                   anchor_regions: list[motion_anchor.Box] | None, view: str | None = None) -> tuple[str | None, str, str]:
    """The run's state, cycle mode and anchor once the state's own (an effect lives once, a non-loop state plays once, a
    gait anchors on its body) are settled, every option checked against them; refused before any frame is read."""
    state = state.strip().lower() if state else state
    if state == effect.STATE:
        if cycle_mode == "auto":
            cycle_mode = "lifetime"
        elif cycle_mode != "lifetime":
            raise SystemExit("video-loop: an effect requires --cycle lifetime")
    if state in nonloop.STATES:
        if cycle_mode == "auto":
            cycle_mode = "once"
        elif cycle_mode != "once":
            raise SystemExit(f"video-loop: non-loop state {state} requires --cycle once")
    if cycle_mode in nonloop.KINDS:
        if state in nonloop.CYCLIC_STATES:
            raise SystemExit(f"video-loop: cyclic state {state} cannot use --cycle {cycle_mode}")
        if anchor not in (None, "none"):
            raise SystemExit("video-loop: a single-play cut requires --anchor none")
        if start is not None or length is not None:
            raise SystemExit("video-loop: a single-play cut selects its terminal bounds; drop --start/--length")
        if not math.isfinite(fps) or fps <= 0 or not math.isfinite(gif_fps) or gif_fps <= 0 or (n_out is not None and n_out < 2):
            raise SystemExit("video-loop: positive fps and at least two output frames are required")
    if steps is not None and steps not in STEPS:
        raise SystemExit(f"video-loop: --steps {steps}: the cut holds {' or '.join(map(str, STEPS))} steps here")
    if size_hold not in SIZE_HOLD_MODES:
        raise SystemExit(f"video-loop: unknown --size-hold {size_hold!r}; expected one of {', '.join(SIZE_HOLD_MODES)}")
    if repair not in REPAIR_MODES:
        raise SystemExit(f"video-loop: unknown --repair {repair!r}; expected one of {', '.join(REPAIR_MODES)}")
    if overlap_wrap not in OVERLAP_WRAP_MODES:
        raise SystemExit(f"video-loop: unknown --overlap-wrap {overlap_wrap!r}; expected one of {', '.join(OVERLAP_WRAP_MODES)}")
    if wrap_blend not in wrap_blend_mod.MODES:
        raise SystemExit(f"video-loop: unknown --wrap-blend {wrap_blend!r}; expected one of {', '.join(wrap_blend_mod.MODES)}")
    if unfixed not in UNFIXED_MODES:
        raise SystemExit(f"video-loop: unknown --unfixed {unfixed!r}; expected one of {', '.join(UNFIXED_MODES)}")
    if facing not in FACINGS:
        raise SystemExit(f"video-loop: unknown --facing {facing!r}; expected one of {', '.join(FACINGS)}")
    if view is not None and view not in handed_mod.VIEWS:
        raise SystemExit(f"video-loop: unknown --view {view!r}; expected one of {', '.join(handed_mod.VIEWS)}")
    if seam_pop_max is not None and not (math.isfinite(seam_pop_max) and seam_pop_max > 0):
        raise SystemExit(f"video-loop: --seam-pop-max {seam_pop_max}: a bound over 0, or off")
    if anchor is None:
        # gaits drift a few pixels over a cycle and the wrap shows it; in-place states do not
        anchor = "body" if profile_for(state).gait else "none"
    if anchor not in ANCHOR_MODES:
        raise SystemExit(f"video-loop: unknown --anchor {anchor!r}; expected one of {', '.join(ANCHOR_MODES)}")
    try:
        motion_anchor.validate_request(anchor, cycle_mode, length, anchor_regions)
    except ValueError as exc:
        raise SystemExit(f"video-loop: {exc}") from exc
    if anchor == "motion-auto" and (cycle_mode not in ("auto", "periodic") or not profile_for(state).gait
                                     or start is not None or length is not None):
        raise SystemExit("video-loop: --anchor motion-auto requires walk/run with automatic or periodic selection; no fixed cut")
    if steps is not None and anchor != "motion-auto":
        raise SystemExit("video-loop: --steps counts the steps in the cut --anchor motion-auto takes; pass --anchor motion-auto")
    if cycle_mode not in CYCLE_MODES:
        raise SystemExit(f"video-loop: unknown --cycle {cycle_mode!r}; expected one of {', '.join(CYCLE_MODES)}")
    return state, cycle_mode, anchor


class _Selection:
    """The cut a mode chooses (`cycle`) and what choosing it changed, set as it goes so a refusal reports the cut as it
    stood: the frames the search read (`files`, `D`, scaled where a hold or the gait fallback scaled them) and the clip
    they stand in (`clip_files`, `clip_D`), the walk's frames (`gait_frames`), the anchor regions, the motion analysis,
    the cuts read before the cut was taken (`cells`, motion-auto) and a periodic search failed over (`periodic_attempt`)."""

    def __init__(self, files: list[Path], D: np.ndarray, gait_frames: list[Image.Image] | None,
                 anchor_regions: list[motion_anchor.Box] | None) -> None:
        self.cycle: dict[str, Any] | None = None
        self.shifted = False
        self.files, self.D = files, D
        self.clip_files, self.clip_D = files, D
        self.gait_frames = gait_frames
        self.anchor_regions = anchor_regions
        self.analysis: dict[str, Any] | None = None
        self.cells: CutOnCells | None = None
        self.periodic_attempt: dict[str, Any] | None = None


def _select_motion_auto(sel: _Selection, report_base: dict[str, Any], *, scratch: _Scratch, prof: Any, fps: float,
                        window: CutWindow, skip: int, size_hold: str, seam_max: float, seam_pop_max: float | None,
                        steps: int | None, view: str | None = None, facing: str = "right") -> None:
    """`--anchor motion-auto`: the search on the frames' own motion (`local_cycle.detect`), once more with the gait fallback
    where it finds nothing, then the cut its steps say (`_counted`, `_by_steps`: steps or cycles as the clip and `view`,
    the view it is filmed from, say), and the cut so chosen without a count held to one word (`_witnessed`)."""
    lo, hi = window.lo, window.hi
    source_frames = sel.gait_frames[skip:]  # type: ignore[index]
    if size_hold == "auto":
        # Held before the search (`_size_hold`), the cut is chosen on frames of one size whose feet stand on one
        # row. The gait fallback below then finds nothing to undo.
        held, report_base["size_hold"], _ = _size_hold(source_frames, lo=lo, hi=hi)
        if held is not None:
            source_frames = held
            sel.files = gait_fallback.write_frames(held, [f.name for f in sel.files], scratch.new())
    else:
        report_base["size_hold"] = {"applied": False, "why": "--size-hold off"}
    sel.D, trajectory, sel.analysis = auto_motion.analyse(source_frames, fps=fps)
    report_base["automatic_motion_analysis"] = sel.analysis
    detect = dict(gait_floor=round(prof.min_seconds*fps), periodicity_min=PERIODICITY_MIN,
                  double_tolerance=GAIT_DOUBLE_TOL, double_search=GAIT_DOUBLE_SEARCH)
    searched: dict[str, Any] = {"min_len": lo, "max_len": hi}
    refused: ValueError | None = None
    # The cuts the search chooses among, read before one is cut as the seam gate and the seam pop gate read the loop.
    sel.cells = CutOnCells(source_frames, pop_max=seam_pop_max)
    # The legs' signals of the frames the searches read: read again only where the gait fallback scales them.
    signals = leg_signals(source_frames)
    try:
        moved = moved_alpha(source_frames, sel.analysis)
        sel.cycle = local_cycle.detect(sel.D, trajectory, min_len=lo, max_len=hi, signals=signals,
                                       wrap_pop=wrap_pop_on(moved), held_edge=HeldEdge(moved), length_cap=window.max_len,
                                       pop_gate=sel.cells.gate, **detect)
    except ValueError as first:
        # A front or back gait that walked toward the camera, or a slow one: one more
        # search, recorded (`gait_fallback`), and only after the first found nothing.
        drift = gait_fallback.cycle_drift(source_frames, min_lag=lo, max_lag=hi)
        fallback: dict[str, Any] = {"reason": str(first), "scale_drift": {k: drift[k] for k in ("height_first_px", "height_last_px", "drift", "method")},
                    "scale_drift_min": gait_fallback.SCALE_DRIFT_MIN, "scale_undone": False}
        if abs(drift["drift"]) >= gait_fallback.SCALE_DRIFT_MIN:
            fallback["padding_ltrb"] = list(gait_fallback.undo_padding(source_frames, drift))
            source_frames = gait_fallback.undo_scale(source_frames, drift, pad=tuple(fallback["padding_ltrb"]))
            sel.files = gait_fallback.write_frames(source_frames, [f.name for f in sel.files], scratch.new())
            sel.D, trajectory, sel.analysis = auto_motion.analyse(source_frames, fps=fps)
            report_base["automatic_motion_analysis"] = sel.analysis
            fallback["scale_undone"] = True
            sel.cells = CutOnCells(source_frames, pop_max=seam_pop_max)
            signals = leg_signals(source_frames)
        # An explicit --max-len is the caller's ceiling and stays one.
        hi_long = window.past(gait_fallback.long_window(lo, len(sel.files), fps))
        fallback["window"] = [lo, hi_long]
        report_base["gait_fallback"] = fallback
        searched = {"min_len": lo, "max_len": hi_long, "max_fraction": gait_fallback.LONG_CYCLE_FRACTION}
        try:
            moved = moved_alpha(source_frames, sel.analysis)
            sel.cycle = local_cycle.detect(sel.D, trajectory, min_len=lo, max_len=hi_long,
                                           max_fraction=gait_fallback.LONG_CYCLE_FRACTION,
                                           signals=signals, wrap_pop=wrap_pop_on(moved),
                                           held_edge=HeldEdge(moved), length_cap=window.max_len, pop_gate=sel.cells.gate, **detect)
        except ValueError as second:
            failed = ValueError(f"{second} (also with the gait fallback: window [{lo}, {hi_long}], "
                                f"scale drift {drift['drift']:+.1%}{', undone' if fallback['scale_undone'] else ''})")
            # The windows the wider search measured and refused go into the report's `cycle`.
            if hasattr(second, "diagnostics"):
                failed.diagnostics = second.diagnostics  # type: ignore[attr-defined]
            failed.__cause__ = first
            refused, sel.cycle = failed, None
    if steps is not None:
        # A count given is the word on the search's cut; the crossings are not asked.
        if refused is not None:
            raise refused
        sel.cycle = _counted(cast("dict[str, Any]", sel.cycle), steps, window=window, D=sel.D, trajectory=trajectory, frames=source_frames, moved=moved,
                             detect=detect, cells=sel.cells, signals=signals)
    else:
        sel.cycle = _by_steps(sel.cycle, refused, frames=source_frames, analysis=sel.analysis, D=sel.D, trajectory=trajectory,
                              window=window, searched=searched, detect=detect, seam_max=seam_max, cells=sel.cells,
                              signals=signals, view=view, facing=facing)
        # Whichever way it was chosen, the cut that will play is held to one word.
        sel.cycle = _witnessed(sel.cycle, frames=source_frames, view=view, signals=signals)


def _select_fixed(sel: _Selection, report_base: dict[str, Any], *, scratch: _Scratch, window: CutWindow, start: int | None,
                  length: int | None, size_hold: str, seam_max: float, view: str | None = None, facing: str = "right") -> None:
    """`--cycle fixed`: the cut the caller names; a walk's held as a search's is, and counted, an odd count refused."""
    lo, hi = window.lo, window.hi
    if start is None or length is None:
        raise SystemExit("video-loop: --cycle fixed needs --start and --length")
    sel.cycle = fixed_cycle(sel.D, start=start, length=length)
    if sel.gait_frames is not None and size_hold == "auto":
        # A walk or run cut the caller names is held as a search's cut is (`_size_hold`), read over the whole clip,
        # and cut on the frames held: the cut's distances, its count and the seam the gate reads are the frames
        # that play. The clip is then the clip held (a fixed cut leaves no lead-in out).
        held, report_base["size_hold"], box_of = _size_hold(sel.gait_frames, lo=lo, hi=hi, cut=(start, length))
        if held is not None:
            sel.gait_frames = held
            sel.files = gait_fallback.write_frames(held, [f.name for f in sel.files], scratch.new())
            sel.D = distance_matrix(sel.files)
            sel.clip_files, sel.clip_D = sel.files, sel.D
            sel.cycle = fixed_cycle(sel.D, start=start, length=length)
            if sel.anchor_regions is not None:
                # `--anchor-region` names the cut's first frame as filmed: the same parts of it, held.
                sel.anchor_regions = [box_of(tuple(box), start) for box in sel.anchor_regions]
                report_base["size_hold"]["anchor_regions"] = [list(box) for box in sel.anchor_regions]
    elif sel.gait_frames is not None:
        report_base["size_hold"] = {"applied": False, "why": "--size-hold off"}
    if sel.gait_frames is not None:
        # A walk or run loops on two steps: the clip's own count of the cut the caller named, as a search's cut is
        # counted. An odd count swaps the legs at the wrap, however close the frames there look.
        cycle = sel.cycle
        cycle["step_count"] = fixed_count(sel.gait_frames, sel.D, start, length, view=view, facing=facing)
        if odd_count(cycle):
            counted = cycle["step_count"]
            if seam_max <= STEP_RATIO_MAX:
                two = round(2 * counted["step_range"][1])
                report_base["seam_refusal"] = {"reading": "count", "cut": [start, length], "verdict": "refuted",
                                               "k": counted["cut"]["k"], "why": counted["cut"]["why"], "two_steps": two}
                raise SystemExit(
                    f"video-loop: loop step count refutes the fixed cut {start}/{length} — the clip's crossings count "
                    f"{counted['cut']['why']} where a walk loops on two, and an odd count swaps the legs at the wrap; "
                    f"cut it again two steps long (about {two} frames)")
            counted["lifted"] = {"seam_max": seam_max}
            print(f"video-loop: warning: the clip's crossings count the fixed cut {start}/{length} "
                  f"{counted['cut']['why']}: a walk loops on two steps, and an odd count swaps the legs at the wrap — "
                  f"kept, as --seam-max {seam_max:g} lifts the step gate", file=sys.stderr)


def _select_periodic(sel: _Selection, *, prof: Any, fps: float, window: CutWindow, skip: int, cycle_mode: str, n: int,
                     masses: Callable[[], np.ndarray]) -> None:
    """`--cycle auto` / `periodic`: the period of the clip (`detect_cycle`), a flat one failed over to a one-shot where the
    state may be one (the periodic attempt kept), else refused."""
    lo, hi = window.lo, window.hi
    gait_floor = round(prof.min_seconds * fps) if prof.gait and prof.min_seconds > 0 else None
    files = sel.files
    sel.cycle = cycle = detect_cycle(sel.D, min_len=lo, max_len=hi, gait_floor=gait_floor,
                                     signals=leg_signals(sel.gait_frames[skip:] if sel.gait_frames is not None
                                                         else [Image.open(f).convert("RGBA") for f in files]))
    cycle["kind"] = "periodic"
    floor = periodicity_floor(len(files), cycle["period_global"], partial_repeat=prof.action_seconds is not None)
    cycle["periodicity_min"] = floor
    if prof.periodic and cycle["periodicity"] < floor:
        flat = (
            f"the period profile is flat (periodicity {cycle['periodicity']:.2f} < {floor:.3f}) in window [{lo},{hi}]"
        )
        if cycle_mode == "auto" and prof.one_shot_ok:
            # explicit, recorded failover: the action happened once (allowed for this state),
            # so cut rest -> excursion -> rest instead. The periodic attempt stays in the report.
            sel.periodic_attempt = {**cycle, "window": [lo, hi], "why_rejected": flat}
            # A one-shot may run past the periodic window — not past the caller's ceiling.
            sel.cycle = _one_shot(sel.D, window, max(hi, round(n * 0.9)), masses())
        else:
            raise SystemExit(
                f"video-loop: no periodic cycle found — {flat}; the motion does not repeat, widen the window, "
                "regenerate the clip, or pass --cycle one-shot for a single performed action"
            )


def _select_cut(sel: _Selection, run: _Run, *, scratch: _Scratch, state: str | None, prof: Any, fps: float, window: CutWindow,
                skip: int, n: int, masses: Callable[[], np.ndarray], cycle_mode: str, anchor: str, start: int | None, length: int | None,
                size_hold: str, seam_max: float, seam_pop_max: float | None, steps: int | None, view: str | None = None,
                facing: str = "right") -> None:
    """The cut the mode chooses, read on the clip after its lead-in (`skip`) and said in the clip's own frame numbers,
    inside the caller's bounds (`window.hold`); refused, with the cut as it stood, where a mode finds none."""
    report_base = run.report
    sel.files, sel.D = sel.files[skip:], sel.D[skip:, skip:]
    try:
        if anchor == "motion-auto":
            _select_motion_auto(sel, report_base, scratch=scratch, prof=prof, fps=fps, window=window, skip=skip,
                                size_hold=size_hold, seam_max=seam_max, seam_pop_max=seam_pop_max, steps=steps,
                                view=view, facing=facing)
        elif cycle_mode == "once":
            source_frames = [Image.open(f).convert("RGBA") for f in sel.files]
            sel.cycle = nonloop.select_cut(sel.D, masses(), fps=fps, ending=partial(
                nonloop.hold_ending, pose=nonloop.end_pose(state, source_frames, _small_features)))
            nonloop.validate_body(source_frames[sel.cycle["start"]:sel.cycle["end_frame"] + 1])
        elif cycle_mode == "lifetime":
            sel.cycle = nonloop.select_cut(sel.D, masses(), fps=fps, kind=cycle_mode,
                                           ending=effect.lifetime_ending)
        elif cycle_mode == "fixed":
            _select_fixed(sel, report_base, scratch=scratch, window=window, start=start, length=length,
                          size_hold=size_hold, seam_max=seam_max, view=view, facing=facing)
        elif cycle_mode == "one-shot":
            sel.cycle = _one_shot(sel.D, window, window.hi, masses())
        elif cycle_mode == "pinned":
            sel.cycle = pinned_cycle(sel.D, seam_max=seam_max)
        elif cycle_mode == "once-return":
            sel.cycle = once_return_cycle(sel.D, seam_max=seam_max)
        else:
            _select_periodic(sel, prof=prof, fps=fps, window=window, skip=skip, cycle_mode=cycle_mode, n=n, masses=masses)
        if skip:
            sel.cycle, sel.shifted = _from_lead_in(sel.cycle, skip), True
            if "step_count" in sel.cycle:
                # Its frame numbers are the search's: the clip's after its lead-in.
                sel.cycle["step_count"]["read_from"] = skip
        # Whatever chose the cut — a search, a count, --cycle fixed or pinned — it is inside the caller's bounds.
        window.hold(cast("dict[str, Any]", sel.cycle))
    except (SystemExit, ValueError) as exc:
        refused = getattr(exc, "diagnostics", sel.cycle)
        run.refused(str(exc), _from_lead_in(refused, skip) if skip and not sel.shifted else refused,
                    periodic_attempt=sel.periodic_attempt)
        if isinstance(exc, ValueError):
            raise SystemExit(f"video-loop: {exc}") from exc
        raise
    # The cut is in the clip's own frame numbers; the frames it reads are the clip's, scaled where the search scaled them.
    sel.files, sel.D = sel.clip_files[:skip] + sel.files, sel.clip_D


def _cut_frames(files: list[Path], i: int, L: int, *, scrub: bool, sized: bool) -> tuple[list[Image.Image], list[Any], int, int]:
    """The cut's frames read (`files[i:i + L]`), their sizes as filmed where `sized` (a walk's `--body-height`), and, where
    `scrub`, each one's loose pieces under the speck size erased: (frames, sizes, scrubbed pixels, specks dropped)."""
    frames: list[Image.Image] = []
    scrubbed = specks = 0
    cut_sizes = []
    for f in files[i : i + L]:
        im = Image.open(f).convert("RGBA")
        if sized:
            cut_sizes.append(gait_fallback.body_size(im))
        # Motion review is pixel preserving: cleanup belongs to the keyed input (`video-frames` erases
        # the specks far from the body). The other cuts erase every loose piece of solid pixels under
        # the speck size, wherever it is: the outline's islands and a drawn shadow as well.
        if scrub:
            scrubbed += _scrub(im)
            im, d = drop_specks(im, alpha_over=16, diagonal=False, apart=0)
            specks += d
        frames.append(im)
    return frames, cut_sizes, scrubbed, specks


def _anchor_cut(frames: list[Image.Image], run: _Run, *, anchor: str, anchor_regions: list[motion_anchor.Box] | None,
                cycle: dict[str, Any], prof: Any, cells: CutOnCells | None, search_start: int, files: list[Path], i: int,
                L: int, wrap_blend: str, fps: float, seam_max: float) -> tuple[list[Image.Image], int, Any]:
    """The cut anchored as `--anchor` says — ramped on its body, or corrected on its motion (on the regions motion-auto
    found on the cut when the search read it, `CutOnCells.discovered`, else found now) — then, for a pinned clip, its
    last half second blended back to the first frame's drawing: (frames, wrap_dx, motion)."""
    report_base = run.report
    if prof.gait:
        report_base["gait"] = evidence_mod.gait_observation(frames, cycle)
    wrap_dx = 0
    if anchor == "body":
        wrap_dx = body_wrap_offset(frames)
        frames = ramp_frames(frames, wrap_dx)
    motion = None
    if anchor in ("motion", "motion-auto"):
        try:
            if anchor == "motion-auto":
                found = cells.discovered(search_start, L, frames) if cells is not None else None
                anchor_regions, region_report = found if found is not None else auto_motion.discover(frames, reference_index=0)
                report_base["automatic_motion_regions"] = region_report
            correct = auto_motion.correct_cycle if anchor == "motion-auto" else motion_anchor.correct_motion
            frames, motion = correct(
                frames, anchor_regions, coarse_dx=body_wrap_offset(frames, search=motion_anchor.COARSE_SEARCH),  # type: ignore[arg-type]
            )
        except ValueError as exc:
            raise run.refused(f"video-loop: {exc}", cycle) from exc
        report_base["motion_anchor"] = motion
    if anchor in ("none", "body") and cycle["kind"] != "lifetime":
        from sprite_gen.video.source import RECIPE
        report_base["source_projection"] = {
            "recipe": RECIPE, "wrap_dx_px": wrap_dx,
            "before_repair_pixels_sha256": [evidence_mod.digest(f.tobytes()) for f in frames],
        }
    if prof.gait:
        # The ghost screen of the cut as filmed (docs/loop-repair.md section 4), read before the jump repair: each
        # filmed ghost it names is a target of that repair, which gives it way to a clean frame where there is one.
        # A loop delivered as cut, never aligned, would show it otherwise.
        report_base["ghost_screen"] = ghost_screen(frames)
    if cycle["kind"] == "pinned":
        # The pinned last frame is the model's drawing of the first: where it redraws it, the cut's last half second takes
        # the first frame's drawing back (docs/loop-repair.md section 6). After the anchor, on the frames as filmed, before
        # any repair reads the wrap; `source_projection` above is the cut before it.
        frames, report_base["wrap_blend"] = _blend_wrap(frames, files[i + L], mode=wrap_blend, anchor=anchor, cycle=cycle,
                                                        fps=fps, seam_max=seam_max)
        for line in wrap_blend_mod.warnings(report_base["wrap_blend"], seam_max=seam_max):
            print(f"video-loop: warning: {line}", file=sys.stderr)
    return frames, wrap_dx, motion


def _repair_walk(frames: list[Image.Image], run: _Run, *, cycle: dict[str, Any], anchor: str, repair: str, files: list[Path],
                 i: int, L: int, skip: int, analysis: dict[str, Any] | None, motion: Any, seam_max: float, overlap_wrap: str,
                 interpolate: rife_mod.Interpolate | None, facing: str) -> tuple[list[Image.Image], dict[int, Image.Image]]:
    """A walk's or run's jump repair (`_repair_jumps`: its jumps, rushes, filmed ghosts and, for a motion-auto cut of two
    steps whose wrap a part on its own beat leaves open, the overlapped wrap), each outcome reported and warned:
    (frames, the cells given way to a frame as filmed, whose seam is read as filmed)."""
    report_base = run.report
    report_base["jump_repair"] = None
    filmed = frames
    wrap = None
    if anchor == "motion-auto" and (cycle.get("steps") or {}).get("by") == "crossings" and wrap_on_cells(frames) > seam_max:
        # Two steps by the clip's own crossings, and a wrap the gate refuses: a part on a beat of its own closes on no
        # cut of two steps. The clip's frames a cycle from the cut's beside the wrap make it again (the overlapped wrap),
        # unless it is turned off.
        wrap = ({"others": cycle_apart(files, start=i, length=L, skip=skip, analysis=cast("dict[str, Any]", analysis), motion=motion,
                                       size=frames[0].size), "seam_max": seam_max} if overlap_wrap == "on"
                else {"off": OVERLAP_WRAP_OFF, "seam_max": seam_max})
        wrap["cycles"] = cycle["step_count"].get("cycles")
    try:
        frames, report_base["jump_repair"], overlap = _repair_jumps(frames, interpolate, facing=facing, required=repair == "on",
                                                                    screen=report_base["ghost_screen"], wrap=wrap)
    except (ValueError, rife_mod.RifeUnavailable) as exc:
        fix = (f"install RIFE (`{rife_mod.install_command()}`), or pass --repair auto to cut it as filmed with a warning"
               if isinstance(exc, rife_mod.RifeNotInstalled) else "fix RIFE, or pass --repair off to cut it as filmed")
        error = (f"video-loop: {exc}; the loop has a frame to make (after a jump, for a part that rushes, at a wrap a part "
                 f"on its own beat does not close, or for a filmed ghost between two clean frames) — {fix} (docs/loop-repair.md)")
        raise run.refused(error, cycle) from exc
    if overlap is not None:
        report_base["wrap_overlap"] = overlap
        for line in _overlap_lines(overlap):
            print(f"video-loop: warning: {line}", file=sys.stderr)
    # What became of each filmed ghost: given way (the cell and what it shows now) or kept, no clean frame beside it.
    ghost_rounds = [r for r in report_base["jump_repair"]["rounds"] if r.get("why") == "ghost"]
    # A cell given way to the frame beside it is that frame shown twice in a row — a step of nothing and a step of two,
    # wherever the ghost was. The seam gate asks whether the cut closes, so it reads that cell as filmed (`seam_as_filmed`):
    # at the wrap the step of two would read as a cut that does not close, and the step of nothing as one that closes
    # whatever the cut. Only a frame made (RIFE's, for a jump or a ghost) has the gate read the rendered cells.
    as_filmed = {r["target"]: filmed[r["target"]] for r in ghost_rounds if isinstance(r["taken"], dict)}
    if as_filmed:
        report_base["seam_as_filmed"] = sorted(as_filmed)
    if set(report_base["jump_repair"]["replaced"]) - set(as_filmed):
        report_base["seam_measurement"] = "rendered-cells"
    report_base["ghost_given_way"] = [{"frame": r["target"], "ghost": r["original"]["ghost"], "taken": r["taken"]}
                                      for r in ghost_rounds if r["taken"] is not None]
    report_base["ghost_kept"] = [{"frame": r["target"], "ghost": r["original"]["ghost"]} for r in ghost_rounds if r["taken"] is None]
    for line in _ghost_lines(report_base["jump_repair"]):
        print(f"video-loop: warning: {line}", file=sys.stderr)
    for r in report_base["jump_repair"].get("rounds", []):
        if r["outcome"] != "accepted" or not r["look"]:
            continue
        # A ghost round made one frame; a flow round a frame a drawing it took, each read on its own.
        looked = ([(f"frame {r['target']} was given way for a filmed ghost", r["proposal"])] if r["why"] == "ghost" else
                  [(f"frame{'s' if len(m['frames']) > 1 else ''} {', '.join(map(str, m['frames']))} "
                    f"{'were' if len(m['frames']) > 1 else 'was'} repaired", m["proposal"]) for m in r["made"] if m["look"]])
        for what, proposal in looked:
            print(f"video-loop: warning: {what} with RIFE's frame between its neighbours, which may "
                  f"cross-fade two drawings inside its outline (crossfade {proposal['crossfade']:.4f}); kept, a reading to "
                  "look at — see it in the loop, or cut it with --repair off (docs/loop-repair.md section 4)", file=sys.stderr)
    if "unmade" in report_base["jump_repair"]:
        unmade = report_base["jump_repair"]["unmade"]
        print(f"video-loop: warning: a jump frame was not repaired (frames {unmade['frames']}, {unmade['off']:.2f}x its "
              "region's usual step off the line between its neighbours) — RIFE is not installed, so the loop is cut as filmed; "
              f"run `{rife_mod.install_command()}` and cut it again (docs/loop-repair.md)", file=sys.stderr)
    elif "rife" in report_base["jump_repair"]:
        rush = report_base["jump_repair"]["surge"]
        print(f"video-loop: warning: a part of the loop rushes (frames {rush['frames']}, {rush['ratio']:.1f}x its steps "
              "either side) and was not eased — RIFE is not installed, so the loop is cut as filmed; "
              f"run `{rife_mod.install_command()}` and cut it again (docs/loop-repair.md section 2)", file=sys.stderr)
    return frames, as_filmed


def _bridge_wrap(frames: list[Image.Image], run: _Run, *, cycle: dict[str, Any], repair: str, seam_max: float,
                 interpolate: rife_mod.Interpolate | None, facing: str) -> list[Image.Image]:
    """A loop that is not a walk: its flow is not read (an idle's blink would leave it), its wrap alone is, and the
    frames round a wrap that jumps are made again (the seam bridge, docs/loop-repair.md section 2). A pinned clip
    the pin refuses is refused whatever its wrap shows."""
    report_base = run.report
    pin_refused = cycle["kind"] == "pinned" and cycle["pin_error"] > cycle["pin_tolerance"]
    try:
        frames, report_base["jump_repair"] = _bridge_seam(
            frames, interpolate, facing=facing, required=repair == "on", seam_max=seam_max,
            hold=report_base["cycle_drawings"]["hold"],
            skip="the pin does not close: the clip is refused on it" if pin_refused else None)
    except (ValueError, rife_mod.RifeUnavailable) as exc:
        fix = (f"install RIFE (`{rife_mod.install_command()}`), or pass --repair auto to cut it as filmed with a warning"
               if isinstance(exc, rife_mod.RifeNotInstalled) else "fix RIFE, or pass --repair off to cut it as filmed")
        error = f"video-loop: {exc}; the loop's wrap jumps and the frames round it are to be made — {fix} (docs/loop-repair.md section 2)"
        raise run.refused(error, cycle) from exc
    if report_base["jump_repair"]["replaced"]:
        report_base["seam_measurement"] = "rendered-cells"
    for line in _bridge_lines(report_base["jump_repair"]):
        print(f"video-loop: warning: {line}", file=sys.stderr)
    return frames


def _walk_gates(frames: list[Image.Image], cut_frames: list[Image.Image], run: _Run, *, cycle: dict[str, Any], i: int, L: int,
                repair: str, facing: str, seam_pop_max: float | None, jolt_max: float | None, head_step_max: float | None,
                unfixed: str, cycle_mode: str) -> None:
    """A walk's or run's loop as it will play, after the repair: its jolt (a warning past the reference bounds, a refusal
    past a bound the caller passed), the seam pop gate's reading, frames left off the flow and parts that pop at the wrap
    (refused with `--unfixed refuse`), and the warnings the loop is kept with."""
    report_base = run.report
    # The jolt is measured on the loop as it will play — after the repair — and reported on
    # every walk and run (docs/loop-repair.md section 3). Beyond the reference bounds it is a
    # warning line; it fails the loop only against a bound the caller passed (--jolt-max /
    # --head-step-max), and never with --repair off, which cuts and reports as filmed. A loop
    # whose repair could not run (no RIFE) is gated as filmed, and the failure says why.
    jolt = repair_mod.measure_jolt(frames, facing=facing)
    reference = {"jolt_max": repair_mod.JOLT_REFERENCE, "head_step_max": repair_mod.HEAD_STEP_REFERENCE}
    # The seam pop is a gate where the seam ratio gates the cut — not a pinned clip, whose gate is its pin — read on
    # the loop as it plays, whatever cut it and whether the repair ran. Its word is given after the seam and step
    # gates (`_loop_gates`), so a cut they refuse is refused in their line.
    report_base["seam_pop_gate"] = (
        repair_mod.seam_pop_gate(jolt["seam_pop"], pop_max=seam_pop_max) if cycle["kind"] not in ("pinned", *nonloop.KINDS)
        else {"verdict": "not-applicable", "why": f"a {cycle['kind']} cut is gated by its pin"})
    warnings = repair_mod.jolt_verdict(jolt, **reference)
    if report_base["seam_pop_gate"]["verdict"] != "refused":
        # A pop the gate refuses is said in its refusal, not in a line that keeps the loop.
        warnings += repair_mod.seam_pop_verdict(jolt, head_step_max=repair_mod.HEAD_STEP_REFERENCE)
    # The held side below the top, read on the source frames when the cut was chosen again (local_cycle).
    warnings += repair_mod.held_edge_verdict(cycle.get("seam_pop"))
    warnings += coverage_verdict(cycle)
    warnings += local_cycle.step_verdict(cycle)
    gated = repair != "off" and (jolt_max is not None or head_step_max is not None)
    over = repair_mod.jolt_verdict(jolt, jolt_max=jolt_max, head_step_max=head_step_max) if gated else []
    report_base["jolt"] = {**jolt, "reference": reference, "warnings": warnings,
                           "gate": {"jolt_max": jolt_max, "head_step_max": head_step_max} if gated else None,
                           "gated": gated, "over": over, "passed": not over,
                           "measured": "after the jump repair" if report_base["jump_repair"].get("replaced") else "as filmed"}
    if over:
        error = (f"video-loop: loop jolts — {'; '.join(over)} ({cycle.get('kind', 'periodic')} length {L} frames from {i}"
                 f"{', after repairing frames ' + str(report_base['jump_repair']['replaced']) if report_base['jump_repair'].get('replaced') else ''}); "
                 "the motion does not play smoothly — regenerate the clip"
                 f"{' (the jump repair did not run: RIFE is not installed — ' + rife_mod.install_command() + ')' if 'rife' in report_base['jump_repair'] else ''}")
        raise run.refused(error, cycle)
    left = _unfixed_lines(report_base["jump_repair"])
    if left and unfixed == "refuse":
        error = (f"video-loop: loop frames left off the flow — {'; '.join(left)} ({cycle.get('kind', 'periodic')} length {L} "
                 f"frames from {i}); the motion does not play smoothly — regenerate the clip")
        raise run.refused(error, cycle)
    # A part that pops at the wrap of the cut taken: closed where the jump repair changed it away, else a take to film
    # again (`retake`), named.
    _pop_settled(report_base["part_pop"], frames, cut_frames)
    given = cycle_mode in ("fixed", "pinned")
    stand = stand_retake(cycle)
    report_base["retake"] = retakes(part_retake(report_base["part_pop"], (i, L), given=given), stand)
    if stand is not None:
        print(f"video-loop: warning: {_stand_line(stand)}", file=sys.stderr)
    pops_left = _pop_lines(report_base["part_pop"], given=given)
    if pops_left and unfixed == "refuse":
        error = (f"video-loop: a part pops at the loop's wrap — {'; '.join(pops_left)} ({cycle.get('kind', 'periodic')} "
                 f"length {L} frames from {i}); the motion does not play smoothly")
        raise run.refused(error, cycle)
    for line in left:
        print(f"video-loop: warning: {line} — a jump played as filmed; --unfixed refuse refuses such a loop "
              "(docs/loop-repair.md section 2)", file=sys.stderr)
    for line in pops_left:
        print(f"video-loop: warning: {line}; --unfixed refuse refuses such a loop (docs/loop-repair.md section 2, "
              '"The part pop")', file=sys.stderr)
    for line in warnings:
        print(f"video-loop: warning: {line} (reference bound; the loop is kept — docs/loop-repair.md)", file=sys.stderr)
    if "ghost_given_way" not in report_base:
        # The jump repair did not run (--repair off): a filmed ghost is delivered as cut, named here;
        # `video-cycle-align` screens its source again and takes a clean frame beside it.
        for g in report_base["ghost_screen"]["ghosts"]:
            print(f"video-loop: warning: frame {g['frame']} of the cut (cycle/frame-{g['frame']:03d}.png) carries a part-covered band "
                  f"over {100 * g['ghost']:.2f} % of its body, at least {rife_mod.GHOST_THICK} px thick — a filmed ghost; delivered as cut "
                  "it shows (--repair off), and video-cycle-align takes a clean frame beside it (docs/loop-repair.md)", file=sys.stderr)
    if not report_base["ghost_screen"]["reads"]:
        print(f"video-loop: warning: the ghost screen does not read this cut — {report_base['ghost_screen']['why']}; no frame "
              "of it is named a ghost, here or in video-cycle-align, so look at cycle/ (docs/loop-repair.md)", file=sys.stderr)


def _write_cut(frames: list[Image.Image], out_dir: Path) -> None:
    """The cut's frames as `cycle/frame-NNN.png`, in place of an earlier cut's and of what was made from it."""
    cycle_dir = out_dir / "cycle"
    cycle_dir.mkdir(parents=True, exist_ok=True)
    for old in cycle_dir.glob("frame-*.png"):
        old.unlink()
    shutil.rmtree(out_dir / CYCLE_SOURCE_DIR, ignore_errors=True)
    # A new cut makes a follow-through over the old strip stale (`video-follow` reads this source).
    (out_dir / FOLLOW_SOURCE).unlink(missing_ok=True)
    shutil.rmtree(out_dir / CYCLE_REFUSED_DIR, ignore_errors=True)
    for k, im in enumerate(frames):
        im.save(cycle_dir / f"frame-{k:03d}.png")


def _strip_record(strip_meta: dict[str, Any], report_base: dict[str, Any], *, cycle: dict[str, Any], state: str | None, i: int,
                  L: int, motion: Any, anchor: str, wrap_dx: int, frames: list[Image.Image],
                  seam_frames: list[Image.Image]) -> None:
    """What the strip's meta says of the cut beside its cells, for `video-cycle-align` and the engines that read it:
    the cut it came from, its steps and drawings, the cells no longer as filmed, and the anchor and the wrap it plays."""
    strip_meta["source_cut"] = {"source": report_base["source"], "start": i, "length": L,
                                "samples": [i + k for k in strip_meta["sample_indices"]]}
    if cycle["kind"] in nonloop.KINDS:
        strip_meta.update(nonloop.metadata(cycle, strip_meta["sample_indices"]))
    if state:
        # `video-cycle-align` reads the gait floor of this state when it screens a loop for two cycles.
        strip_meta["state"] = str(state).strip().lower()
    if "steps" in cycle:
        # A cut whose steps are known — counted (`--steps`) or two candidate lengths taken so a held part
        # closes (local_cycle._held_rank): `video-cycle-align` reads a return half way as its step.
        strip_meta["steps"] = cycle["steps"]["count"]
    # `video-cycle-align` reads the hold here, over the cut (`cycle_drawings`) and over the clip
    # (`drawings`, where the cut is too short to read): read on the keyed steps, since `--anchor
    # motion-auto` moves each frame of a pair apart in the cut it writes (sprite_gen/video/held.py).
    strip_meta["drawings"] = {k: report_base["drawings"][k] for k in ("hold", "drawings_per_second", "contrast", "frames")}
    strip_meta["cycle_drawings"] = {k: report_base["cycle_drawings"][k] for k in ("start", "length", "steps", "hold", "drawings_per_second", "contrast")
                                    } | ({"why": report_base["cycle_drawings"]["why"]} if "why" in report_base["cycle_drawings"] else {})
    if "clip_feet" in report_base:
        strip_meta["clip_feet"] = report_base["clip_feet"]
    # A cut frame that is no longer the frame as filmed for a filmed ghost, and one kept with no clean frame beside
    # it: `cycle/` is the alignment's material (`cycle.source/`), whose screen no longer sees a ghost given way, so the
    # strip says it, and an alignment carries it over (frames of the cut).
    for key in ("ghost_given_way", "ghost_kept"):
        if report_base.get(key):
            strip_meta[key] = report_base[key]
    if report_base.get("wrap_blend", {}).get("applied"):
        # The cells the wrap blend changed: they are no longer the cut's frames as filmed.
        strip_meta["wrap_blend"] = {k: report_base["wrap_blend"][k] for k in ("cells", "K")}
    if motion is not None:
        strip_meta["foot_anchor"] = anchor
        strip_meta["motion_anchor"] = motion
    if anchor == "body":
        strip_meta["foot_anchor"] = "body"
        strip_meta["wrap_dx_px"] = wrap_dx
        # the wrap as it now plays: last ramped frame -> first, against an ordinary step (a cell given way read as filmed)
        Dr = _distances(np.stack([_small_features(f) for f in seam_frames]))
        inner = float(np.mean([Dr[k, k + 1] for k in range(len(frames) - 1)])) if len(frames) > 1 else 0.0
        strip_meta["seam_ratio_after_anchor"] = round(float(Dr[len(frames) - 1, 0]) / inner, 4) if inner > 0 else None


@dataclass
class _Seam:
    """The cells the GIF/WebP play (`pick`, at `idx` of the cut; `preview_indices` of the strip for a cut played once) and
    the loop's wrap against its steps as the seam gate reads them (`_read_seam`)."""

    pick: list[Image.Image]
    idx: list[int]
    preview_indices: list[int] | None
    adjacent: float
    seam: float
    ratio: float


def _read_seam(strip: Image.Image, strip_meta: dict[str, Any], strip_of: Any, report_base: dict[str, Any], *,
               cycle: dict[str, Any], prof: Any, i: int, L: int, n_out: int, D: np.ndarray, anchor: str, wrap_dx: int,
               seam_max: float, gait_frames: list[Image.Image] | None, as_filmed: dict[int, Image.Image],
               seam_frames: list[Image.Image]) -> _Seam:
    """The cells the GIF/WebP play, `n_out` of them across the cut, and the loop's wrap read against its steps: on the
    source frames, or on the cells that play where a correction, a repair or the body anchor's ramp made them other than
    the source frames (`seam_measurement`); a walk's between its drawings (`seam_drawings`)."""
    single_play = cycle["kind"] in nonloop.KINDS
    preview_indices = None
    if single_play:
        preview_indices = nonloop.samples(strip_meta["frames"], n_out)
        idx = [strip_meta["sample_indices"][k] for k in preview_indices]
        pick = [strip.crop((k * strip_meta["w"], 0, (k + 1) * strip_meta["w"], strip_meta["h"])) for k in preview_indices]
    else:
        idx = [min(L - 1, i2) for i2 in (round(k * L / n_out) for k in range(n_out))]
        pick = resampled_cells(strip, strip_meta, idx, L)
    seam_idx = [i + j for j in idx]
    on_cells = report_base["seam_measurement"] == "rendered-cells"
    # A walk's or run's seam is read between its drawings, as its steps are (`seam_drawings`), where the seam ratio gates
    # the cut: not a pin or a cut played once. Any other loop's, and a walk drawn a frame each, on every frame.
    by_drawing = prof.gait and cycle["kind"] not in ("pinned", *nonloop.KINDS)
    seam_read = None
    read_idx = seam_idx
    if by_drawing and not on_cells:
        # The source frames the distances were read on, each drawing's first.
        firsts, seam_read = seam_drawings([gait_frames[j] for j in seam_idx])  # type: ignore[index]
        read_idx = [seam_idx[k] for k in firsts]
    resampled_adjacent = float(np.mean([D[read_idx[k], read_idx[k + 1]] for k in range(len(read_idx) - 1)]))
    resampled_seam = float(D[read_idx[-1], read_idx[0]])
    source_ratio = resampled_seam / resampled_adjacent if resampled_adjacent > 0 else math.inf
    # The body anchor moved the cells to close a wrap the source frames refuse: the cells that play are read again. Where
    # the seam ratio gates the cut, not a pin or a cut played once.
    ramped = (not on_cells and anchor == "body" and wrap_dx != 0 and cycle["kind"] not in ("pinned", *nonloop.KINDS)
              and source_ratio > seam_max)
    if on_cells or ramped:
        # The actual corrected/repaired/resampled strip cells, with the same limit — a cell given way to the frame
        # beside it as filmed (`seam_as_filmed`), on the strip the cut makes with those frames; a walk's between its drawings.
        cells = resampled_cells(*strip_of(seam_frames), idx, L) if as_filmed else pick  # type: ignore[call-arg]
        on, cells_read = seam_drawings(cells) if by_drawing else (list(range(len(cells))), None)
        adjacent, wrap, steps = _seam_on_cells([cells[k] for k in on])
    if ramped:
        # The ramp spreads whatever offset the wrap shows over the loop: a jump of a few frames comes out moved into the
        # loop, not closed. So the cells overrule the source frames only where their wrap and every step of the loop are
        # within the same limit; otherwise the refusal is the source frames'.
        largest = int(np.argmax(steps))
        report_base["seam_after_anchor"] = {
            "source_seam_ratio": round(source_ratio, 4) if math.isfinite(source_ratio) else None,
            "seam_ratio": round(wrap / adjacent, 4) if adjacent > 0 else None,
            "largest_step_ratio": round(float(steps[largest]) / adjacent, 4) if adjacent > 0 else None,
            "largest_step_into": idx[on[largest + 1]],
            **(cells_read or {}),
        }
        if adjacent > 0 and wrap / adjacent <= seam_max and float(steps[largest]) / adjacent <= seam_max:
            report_base["seam_measurement"] = "rendered-cells"
            on_cells = True
    if on_cells:
        resampled_adjacent, resampled_seam = adjacent, wrap
        seam_read = cells_read
    if seam_read is not None:
        # What the seam ratio below was read on: the drawings, as many as there are, and the hold.
        report_base["seam_read"] = seam_read
    seam_ratio = resampled_seam / resampled_adjacent if resampled_adjacent > 0 else math.inf
    return _Seam(pick, idx, preview_indices, resampled_adjacent, resampled_seam, seam_ratio)


def _loop_gates(seam: _Seam, strip: Image.Image, strip_meta: dict[str, Any], run: _Run, *, cycle: dict[str, Any],
                periodic_attempt: dict[str, Any] | None, prof: Any, i: int, L: int, seam_max: float) -> None:
    """The gates the cut, once written, is refused by: a pin that does not close; a wrap that does not close (the seam
    ratio); a walk's step that jumps inside the loop (the step gate, read between its drawings); and a top of the
    silhouette that jumps at the wrap (the seam pop gate)."""
    report_base = run.report
    resampled = {"resampled_seam": seam.seam, "resampled_inner_mean_adjacent": seam.adjacent, "resampled_seam_ratio": seam.ratio}
    single_play = cycle["kind"] in nonloop.KINDS
    if cycle["kind"] in ("pinned", "once-return"):
        if cycle["pin_error"] > cycle["pin_tolerance"]:
            what = "the loop does not close" if cycle["kind"] == "pinned" else "it does not return to its first pose"
            error = (
                f"video-loop: the {cycle['kind']} clip does not end on its first frame (pin error {cycle['pin_error']:.4f} "
                f"exceeds {cycle['pin_tolerance']:.4f}); {what} — regenerate the clip with "
                "--last-frame set to its first frame"
            )
            raise run.refused(error, cycle, cut_written=True, **resampled)
    elif not single_play and seam.ratio > seam_max:
        known = seam_reason(report_base, cycle)
        error = (
            f"video-loop: loop seam ratio {seam.ratio:.2f} exceeds {seam_max} "
            f"({cycle['kind']} length {L} frames from {i}); the cycle does not close — "
            + (f"{known['words']} — regenerate with that part moving on the walk's beat" if known["reason"] else
               "regenerate with a complete return to the starting pose")
        )
        raise run.refused(error, cycle, cut_written=True, periodic_attempt=periodic_attempt, **resampled,
                          seam_refusal={"reading": "wrap", "ratio": seam.ratio, "max": seam_max,
                                        **{k: v for k, v in known.items() if k != "words"}})
    if prof.gait and not single_play:
        # Every step of the loop as it plays, read between its drawings: a frame that jumps inside the loop, which the
        # seam ratio at the wrap does not see. The bound is the step gate's, or the seam gate's where --seam-max lifts it;
        # a step its drawing moving a pixel of the frames as filmed makes is a pixel's jitter of a loop that barely moves,
        # and is not a jump (`step_jitter`, the cells' pixels of one of them the strip's scale).
        step_max = max(STEP_RATIO_MAX, seam_max)
        read = drawing_steps([strip.crop((k * strip_meta["w"], 0, (k + 1) * strip_meta["w"], strip_meta["h"]))
                              for k in range(strip_meta["frames"])], pixel=strip_meta["scale"])
        if read is None:
            report_base["step_gate"] = {"read": "drawings", "skipped": "fewer than three drawings", "max": step_max}
        else:
            of = strip_meta["sample_indices"]
            read.update(largest_into=of[read["largest_into"]], largest_from=of[read["largest_from"]], max=step_max,
                        lifted=seam_max > STEP_RATIO_MAX)
            for step in read.get("jitter", []):
                step.update({"from": of[step["from"]], "into": of[step["into"]]})
            report_base["step_gate"] = read
            ratio = read["largest_ratio"]
            if ratio is not None and ratio > step_max:
                known = _step_reason(report_base["jump_repair"], {read["largest_from"], read["largest_into"]})
                error = (f"video-loop: loop step ratio {ratio:.2f} exceeds {step_max:g} ({cycle['kind']} length {L} frames "
                         f"from {i}, into frame {read['largest_into']}, read between its {read['drawings']} drawings); the loop "
                         f"jumps inside{known.pop('words', '')} — regenerate the clip")
                raise run.refused(error, cycle, cut_written=True, periodic_attempt=periodic_attempt, **resampled,
                                  seam_refusal={"reading": "steps", "ratio": ratio, "max": step_max,
                                                "into": read["largest_into"], **known})
            if ratio is not None and ratio > STEP_RATIO_MAX:
                read["kept"] = f"--seam-max {seam_max:g} lifts the step gate"
                print(f"video-loop: warning: the loop's largest step between drawings is {ratio:.2f}x their mean (into frame "
                      f"{read['largest_into']}), over {STEP_RATIO_MAX:g}: it jumps inside — kept, as --seam-max {seam_max:g} "
                      "lifts the step gate", file=sys.stderr)
    popped = report_base.get("seam_pop_gate")
    if popped is not None and popped["verdict"] == "refused":
        # The top of the loop — the head, or a part held above it — jumps at the wrap: the loop does not close there,
        # whatever its seam ratio, an area measure, says. Not lifted by --seam-max: --seam-pop-max off turns it off.
        error = (f"video-loop: loop seam pop {popped['pop']:.2f} exceeds {popped['max']:g} ({cycle['kind']} length {L} frames "
                 f"from {i}): the top of the silhouette jumps into the loop's first frame "
                 f"{repair_mod.seam_pop_words(report_base['jolt']['seam_pop'], popped['max'])} — the loop does not close at "
                 "its top; cut it where it does, or regenerate the clip")
        raise run.refused(error, cycle, cut_written=True, periodic_attempt=periodic_attempt, **resampled)


def _write_previews(pick: list[Image.Image], run: _Run, *, cycle: dict[str, Any], gif_path: Path, webp_path: Path,
                    delay_ms: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """The GIF and WebP of the cells that play, each verified as written: (gif report, webp report). An effect's GIF is
    baked onto its declared black background, since a GIF's transparency is binary."""
    single_play = cycle["kind"] in nonloop.KINDS
    # GIF has binary transparency. Bake the effect onto its declared black preview
    # background instead of turning its faint partial-alpha glow into opaque dark pixels.
    gif_background = "#000000" if cycle["kind"] == "lifetime" else None
    gif_pick = [effect.on_black(im) for im in pick] if gif_background is not None else pick
    try:
        # img2webp runs while the GIF is made, into a folder of its own beside the WebP (`runio.new_stage`), and the WebP
        # is moved into place only once the GIF is written: a GIF that fails leaves no WebP, as when one ran after the other.
        stage = new_stage(webp_path, ".webp")
        try:
            with ThreadPoolExecutor(1) as pool:
                webp = pool.submit(write_webp, pick, stage / webp_path.name, delay_ms=delay_ms, **({"loop": 1} if single_play else {}))
                save_clean_gif(gif_pick, gif_path, duration_ms=delay_ms, loop=None if single_play else 0,
                               alpha_threshold=128, opaque=gif_background is not None)
                webp.result()
            os.replace(stage / webp_path.name, webp_path)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        # A filmed ghost given way to the frame beside it is that frame shown twice in a row: one frame of the GIF or WebP
        shown = shown_runs(pick)
        gif_report = verify_animation(gif_path, expect_frames=shown_runs(gif_pick), check_stale=False,
                                      **({"expected_loop": None} if single_play else {}),
                                      **({"background": gif_background} if gif_background is not None else {}))  # type: ignore[arg-type]
        webp_report = verify_animation(webp_path, expect_frames=shown, check_stale=True, **({"expected_loop": 1} if single_play else {}))  # type: ignore[arg-type]
    except SystemExit as exc:
        # The GIF or WebP could not be written (no img2webp) or failed its verification: nothing of the cut is handed over.
        run.refused(str(exc), cycle, cut_written=True)
        raise
    return gif_report, webp_report


def _cut_loop(
    frames_dir: Path,
    out_dir: Path,
    *,
    scratch: _Scratch,
    fps: float,
    state: str | None,
    min_len: int | None,
    max_len: int | None,
    n_out: int | None,
    seam_max: float,
    name: str,
    report_path: Path | None,
    cycle_mode: str = "auto",
    gif_fps: float = GIF_FPS_DEFAULT,
    start: int | None = None,
    length: int | None = None,
    strip_height: int = STRIP_MAX_HEIGHT,
    body_height: int | None = None,
    anchor: str | None = None,
    anchor_regions: list[motion_anchor.Box] | None = None,
    repair: str = "auto",
    facing: str = "right",
    interpolate: rife_mod.Interpolate | None = None,
    jolt_max: float | None = None,
    head_step_max: float | None = None,
    size_hold: str = "auto",
    steps: int | None = None,
    overlap_wrap: str = "on",
    wrap_blend: str = "auto",
    unfixed: str = "warn",
    seam_pop_max: float | None = repair_mod.SEAM_POP_REFERENCE,
    view: str | None = None,
) -> dict[str, Any]:
    """One `video-loop` run, phase by phase: the options settled (`_settled_modes`); the clip read and measured; the cut
    chosen (`_select_cut`); its frames read (`_cut_frames`), anchored (`_anchor_cut`) and repaired (`_repair_walk`,
    `_bridge_wrap`); a walk's gates (`_walk_gates`); the cut and its strip written (`_write_cut`, `_strip_record`); the
    wrap read (`_read_seam`) and gated (`_loop_gates`); the previews (`_write_previews`) and the report. A refusal at any
    phase writes the report so far (`_Run.refused`)."""
    state, cycle_mode, anchor = _settled_modes(
        state, cycle_mode, anchor, fps=fps, gif_fps=gif_fps, n_out=n_out, start=start, length=length, steps=steps,
        size_hold=size_hold, repair=repair, overlap_wrap=overlap_wrap, wrap_blend=wrap_blend, unfixed=unfixed, facing=facing,
        seam_pop_max=seam_pop_max, anchor_regions=anchor_regions, view=view)
    given = frames_dir
    frames_dir = frames_dir.expanduser().resolve()
    files = sorted(frames_dir.glob("*.png"))
    if len(files) < 6:
        raise SystemExit(f"video-loop: need at least 6 keyed frames in {frames_dir}, found {len(files)}")
    target = (report_path or (out_dir / f"{name}.loop.report.json")).expanduser().resolve()
    _refuse_overlap(given, files, out_dir, name, target, report_path is not None)
    prof = profile_for(state)
    n = len(files)
    # A walk or run may open on a lead-in (gait_fallback.lead_in): the video model reframing a small
    # subject before it walks at the clip's size. An automatic search reads the clip after it as a clip
    # of its own, and what it finds is said in the clip's own frame numbers (`_from_lead_in`); a cut the
    # caller names (fixed, pinned) is the caller's frames, lead-in or not.
    gait_frames = [Image.open(f).convert("RGBA") for f in files] if prof.gait else None
    lead: dict[str, Any] | None = None
    if gait_frames is not None:
        whole = CutWindow.of(prof, n, fps, min_len, max_len)
        lead = gait_fallback.lead_in(gait_frames, min_lag=whole.lo, max_lag=whole.hi)
    skip = lead["frames"] if lead is not None and cycle_mode in ("auto", "periodic") else 0
    # Every search below reads its window off this one (`CutWindow`): none sets a bound of its own.
    window = CutWindow.of(prof, n - skip, fps, min_len, max_len)
    lo, hi = window.lo, window.hi
    D = distance_matrix(files)
    # The clip's steps as keyed, before `--anchor motion-auto` reads its own distance or moves a frame:
    # the hold is read on them, over the clip and over the cut (sprite_gen/video/held.py).
    clip_steps = [float(D[k, k + 1]) for k in range(n - 1)]
    # Each frame's mass, read only where a mode reads it (a cut played once, an effect's lifetime, a one-shot, a periodic
    # search failed over to one): a walk's search never does. An effect's is read at full size (`effect.frame_masses`).
    masses = cache(partial(effect.frame_masses if cycle_mode == "lifetime" else frame_masses, files))
    report_base: dict[str, Any] = {
        "kind": "sprite-gen-video-loop-report", "frames_dir": str(frames_dir),
        "source": evidence_mod.source_identity(files, fps),
        "producer": evidence_mod.engine_identity(),
        "out_dir": str(out_dir), "state": state, "fps": fps, "frames_total": n,
        "window": [lo, hi], "profile": prof.why, "cycle_mode": cycle_mode,
        "seam_max": seam_max,
        "anchor": anchor,
        "seam_measurement": "rendered-cells" if anchor in ("motion", "motion-auto") else "source-frames",
        # How many drawings the clip shows a second, and whether it shows each for two or three frames
        # (sprite_gen/video/held.py) — over the whole clip as keyed, before any cut. A record for
        # `video-cycle-align`, which says when a held clip stretched to the set's length must be filmed again.
        "drawings": held_mod.measure(clip_steps, fps=fps),
    }
    if lead is not None:
        # `search_from`: the first clip frame the search read (0: the whole clip, or a cut the caller named).
        report_base["lead_in"] = {**lead, "search_from": skip}
    run = _Run(target, out_dir, name, report_base)
    sel = _Selection(files, D, gait_frames, anchor_regions)
    _select_cut(sel, run, scratch=scratch, state=state, prof=prof, fps=fps, window=window, skip=skip, n=n, masses=masses,
                cycle_mode=cycle_mode, anchor=anchor, start=start, length=length, size_hold=size_hold, seam_max=seam_max,
                seam_pop_max=seam_pop_max, steps=steps, view=view, facing=facing)
    cycle, files, D, gait_frames, periodic_attempt = cast("dict[str, Any]", sel.cycle), sel.files, sel.D, sel.gait_frames, sel.periodic_attempt
    i, L = cycle["start"], cycle["length"]
    if "steps" in cycle:
        # A cut two steps long is one cycle: how much of a second the clip holds past it (after its lead-in).
        cycle["coverage"] = round((n - skip - L) / L, 4)
    # The same, over the cut alone: a clip held for part of its length is held where the cut is.
    report_base["cycle_drawings"] = held_mod.measure_cycle(clip_steps, start=i, length=L, fps=fps)
    if gait_frames is not None:
        # Every cut of the cut's length the clip holds, read for the same foot stepping twice in a row (sprite_gen/video/
        # cadence.py): `video-cycle-align` says from it whether cutting a loop that does so again could help.
        report_base["clip_feet"] = cadence_mod.clip_feet(gait_frames[skip:], L, first=skip)
    # playback density, not a fixed count: a long cycle gets more frames so every state plays at
    # ~gif_fps (a fixed 12 made a 2.5 s jump hold each frame 210 ms while a 1.1 s walk held 90 ms)
    if n_out is None:
        n_out = max(GIF_FRAMES_MIN, round(L / fps * gif_fps))
    n_out = min(n_out, L)  # never ask for more distinct frames than the cycle holds

    out_dir = out_dir.expanduser().resolve()
    # A walk or run cut for a `--body-height` has its frames' sizes read as filmed, for the standing height below.
    sized = body_height is not None and prof.gait
    frames, cut_sizes, scrubbed, specks = _cut_frames(
        files, i, L, scrub=anchor not in ("motion", "motion-auto") and cycle["kind"] != "lifetime", sized=sized)
    cycle_seconds = L / fps
    frames, wrap_dx, motion = _anchor_cut(frames, run, anchor=anchor, anchor_regions=sel.anchor_regions, cycle=cycle, prof=prof,
                                          cells=sel.cells, search_start=i - skip, files=files, i=i, L=L,
                                          wrap_blend=wrap_blend, fps=fps, seam_max=seam_max)
    # Each cut frame given way to the frame beside it, as filmed: the seam is read on it there (`seam_as_filmed`).
    as_filmed: dict[int, Image.Image] = {}
    if prof.gait:
        # The part reading (docs/loop-repair.md section 2, "The part pop") of every walk and run, on the cut as filmed —
        # anchored, before any repair — whatever --repair asks: a hand or a foot that pops at the wrap.
        report_base["part_pop"] = part_pop_mod.public(part_pop_mod.read(frames))
        cut_frames = frames
    if repair != "off" and prof.gait:
        frames, as_filmed = _repair_walk(frames, run, cycle=cycle, anchor=anchor, repair=repair, files=files, i=i, L=L,
                                         skip=skip, analysis=sel.analysis, motion=motion, seam_max=seam_max,
                                         overlap_wrap=overlap_wrap, interpolate=interpolate, facing=facing)
    elif repair != "off" and cycle["kind"] not in nonloop.KINDS:
        frames = _bridge_wrap(frames, run, cycle=cycle, repair=repair, seam_max=seam_max, interpolate=interpolate, facing=facing)
    else:
        report_base["jump_repair"] = {"applied": False, "why": "--repair off" if repair == "off" else f"not a gait state ({state})"}
    if prof.gait:
        _walk_gates(frames, cut_frames, run, cycle=cycle, i=i, L=L, repair=repair, facing=facing, seam_pop_max=seam_pop_max,
                    jolt_max=jolt_max, head_step_max=head_step_max, unfixed=unfixed, cycle_mode=cycle_mode)
    _write_cut(frames, out_dir)
    # The standing height is the base still's pose as filmed, the clip's first frame — but a video model that
    # reframes the subject after it films that pose at another size than the walk. Where the cut is filmed
    # SIZE_HOLD_MIN or more larger or smaller than the first frame (gait_fallback.size_change: what its height,
    # mass and breadth all agree on, so not a pose), the standing height is read on the cut's own first frame.
    # A lead-in is such a reframing, and so is one too small to open a lead-in.
    standing_frame = 0
    if sized:
        report_base["cut_size"] = gait_fallback.size_change(gait_fallback.body_size(Image.open(files[0]).convert("RGBA")), cut_sizes)
        if abs(report_base["cut_size"]["change"]) >= gait_fallback.SIZE_HOLD_MIN:
            standing_frame = i
    try:
        strip_of = partial(build_strip, max_height=strip_height, cycle_seconds=cycle_seconds, body_height=body_height, anchor="feet" if anchor == "feet" else "none", kind=str(cycle.get("kind") or "periodic"),
                           standing_src=standing_height(files[standing_frame], standing_frame) if body_height is not None and cycle["kind"] != "lifetime" else None,
                           standing_frame=standing_frame)
        strip, strip_meta = strip_of(frames)
    except SystemExit as exc:
        # No body to measure or to crop the strip to: the cut is refused like any other.
        run.refused(str(exc), cycle, cut_written=True)
        raise
    # The cut as its seam is read: each cell given way to the frame beside it as filmed (`seam_as_filmed`).
    seam_frames = [as_filmed.get(k, f) for k, f in enumerate(frames)]
    _strip_record(strip_meta, report_base, cycle=cycle, state=state, i=i, L=L, motion=motion, anchor=anchor, wrap_dx=wrap_dx,
                  frames=frames, seam_frames=seam_frames)
    # The GIF/WebP are cut from the strip's cells. A cycle longer than the cell cap was
    # subsampled there, so asking for more frames than cells repeats cells back to back
    # and the GIF writer merges the identical neighbours — the file then holds fewer
    # frames than requested and verification refused it (2026-09-20: a 68-frame jump cycle
    # capped at 64 cells came back as a 64-frame GIF "expected 68").
    n_out = min(n_out, int(strip_meta["frames"]))
    strip_path = out_dir / f"{name}.strip.png"
    strip.save(strip_path)
    atomic_write_text(out_dir / f"{name}.strip.json", json.dumps(strip_meta, indent=2) + "\n")

    # resampled GIF/WebP frames: same crop as the strip, n_out evenly across the cycle
    single_play = cycle["kind"] in nonloop.KINDS
    seam = _read_seam(strip, strip_meta, strip_of, report_base, cycle=cycle, prof=prof, i=i, L=L, n_out=n_out, D=D,
                      anchor=anchor, wrap_dx=wrap_dx, seam_max=seam_max, gait_frames=gait_frames, as_filmed=as_filmed,
                      seam_frames=seam_frames)
    delay_ms = max(20, round(1000 * cycle_seconds / n_out))
    _loop_gates(seam, strip, strip_meta, run, cycle=cycle, periodic_attempt=periodic_attempt, prof=prof, i=i, L=L,
                seam_max=seam_max)
    gif_report, webp_report = _write_previews(seam.pick, run, cycle=cycle, gif_path=out_dir / f"{name}.gif",
                                              webp_path=out_dir / f"{name}.webp", delay_ms=delay_ms)

    payload = {
        **report_base,
        "kind": "sprite-gen-video-loop-report",
        "frames_dir": str(frames_dir),
        "out_dir": str(out_dir),
        "state": state,
        "fps": fps,
        "frames_total": n,
        "window": [lo, hi],
        "profile": prof.why,
        "cycle_mode": cycle_mode,
        "cycle": cycle,
        "periodic_attempt": periodic_attempt,
        "cycle_seconds": round(cycle_seconds, 4),
        "n_out": n_out,
        "gif_fps": gif_fps,
        "delay_ms": delay_ms,
        "status": "passed",
        "resampled_seam": seam.seam,
        "resampled_inner_mean_adjacent": seam.adjacent,
        "resampled_seam_ratio": None if single_play else round(seam.ratio, 4),
        **({"seam_measurement": "not-applicable", "playback_indices": seam.preview_indices} if single_play else {}),
        "seam_max": seam_max,
        "scrubbed_rgb_pixels": scrubbed,
        "specks_dropped": specks,
        "strip": {"path": str(strip_path), **strip_meta},
        "gif": gif_report,
        "webp": webp_report,
    }
    write_loop_report(target, payload)
    payload["report"] = str(target)
    return payload


def _seam_pop_max(value: str) -> float | None:
    """`--seam-pop-max`: a bound over 0, or `off` (None)."""
    if value.strip().lower() == "off":
        return None
    try:
        bound = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r}: a bound over 0, or off") from None
    if not (math.isfinite(bound) and bound > 0):
        raise argparse.ArgumentTypeError(f"{value!r}: a bound over 0, or off")
    return bound


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--frames-dir", required=True, type=Path, help="keyed RGBA frames from `sprite-gen video-frames` (…/keyed)")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fps", type=float, default=24.0, help="frame rate of the keyed frames (from the frames report)")
    parser.add_argument("--state", help="motion state (idle/walk/run/jump/attack) — selects the detection window")
    parser.add_argument("--min-len", type=int, help="override: minimum cycle length in frames, on every path that sets one (a cut shorter fails)")
    parser.add_argument("--max-len", type=int, help="override: maximum cycle length in frames, on every path that sets one (a cut longer fails)")
    parser.add_argument("--n-out", type=int, help="frames in the GIF/WebP (default: cycle seconds x --gif-fps)")
    parser.add_argument("--gif-fps", type=float, default=GIF_FPS_DEFAULT, help=f"GIF/WebP playback density (default {GIF_FPS_DEFAULT:g}); every state plays at this rate regardless of cycle length")
    parser.add_argument("--seam-max", type=float, default=SEAM_RATIO_MAX, help=f"loop seam gate (default {SEAM_RATIO_MAX}): the wrap over the mean step. A walk or run is also held to the step gate — its largest step between drawings over their mean step at most {STEP_RATIO_MAX:g} (a step its drawing moving at most {STEP_JITTER_PX:g} pixels of the frames as filmed makes is a pixel's jitter, not held to it), and a fixed cut not one the clip's crossings count an odd number of steps; a --seam-max past {STEP_RATIO_MAX:g} lifts the step gate with it (every step's bound is then --seam-max, and an odd count a warning line)")
    parser.add_argument("--cycle", choices=CYCLE_MODES, default="auto", help="once: a performed action ending in a stable held pose, with no return or seam gate; auto: non-loop states use once, otherwise periodic first, one-shot failover for action states (recorded in the report); periodic / one-shot force one detector; fixed cuts exactly --start/--length (no detection, reported as kind=fixed); pinned: the whole clip but its last frame, for a clip pinned to end on its first frame (the gate is that pin); once-return: the whole clip with its last frame, played once from its first pose back to it (loop false, returns true), for a clip pinned to end on its first frame (the gate is that pin)")
    parser.add_argument("--start", type=int, help="fixed cut: first keyed frame of the cycle (with --cycle fixed)")
    parser.add_argument("--length", type=int, help="fixed cut: cycle length in frames (with --cycle fixed)")
    parser.add_argument("--strip-height", type=int, default=STRIP_MAX_HEIGHT, help=f"cell/strip/GIF height cap in px (default {STRIP_MAX_HEIGHT}); the cycle is scaled down to fit, and never up unless --body-height asks for it")
    parser.add_argument("--body-height", type=int, help="scale so the STANDING height (the subject in the clip's first frame, i.e. the base still's pose) is this many px — the same value across states gives the same character size; --strip-height stays the cap")
    parser.add_argument("--anchor", choices=ANCHOR_MODES, default=None, help="motion-auto: automatic stable regions, local repeat selection and XY ramp (walk/run); motion: fixed cut with explicit regions; body (default for walk/run): measure the last-to-first head-and-torso offset once and spread it as a ramp over the cycle; feet: remove in-canvas drift (a straight-line trend) so every cell stands on the mean foot line; none: leave placement as filmed (default for other states)")
    parser.add_argument("--anchor-region", type=motion_anchor.parse_region, action="append", help="motion anchor only: repeat twice with head then torso x0,y0,x1,y1 in the first selected frame; requires --cycle fixed and at least 6 frames")
    parser.add_argument("--repair", choices=REPAIR_MODES, default="auto", help="auto (default): take out the frames off the walk's flow (one or two drawings that lie 3x their region's usual step off the line between the frames either side) and make them again with RIFE between those frames, each at its own time, so the loop keeps its length; adopt only without smear/outline faults, at most 3 calls and never made from or beside a frame made (docs/loop-repair.md section 2). A rejected group keeps its frames. A loop that is not a walk or run is not read for its flow: where its wrap steps over --seam-max times the mean of the three steps either side, one, two or four frames round it are made again by RIFE between the frames either side, the fewest first, taken only without faults and where every step from one to the other then reads within that limit (docs/loop-repair.md section 2, \"The seam bridge\"). Missing RIFE keeps the filmed loop with a warning (`sprite-gen rife install`); on: the same repair, but no RIFE fails the loop; off: cut as filmed")
    parser.add_argument("--overlap-wrap", choices=OVERLAP_WRAP_MODES, default="on", help="--anchor motion-auto, under --repair auto or on: on (default): a cut two steps long whose wrap a part on its own beat leaves open (the seam gate refuses it) has four to twelve frames beside its wrap made again between the clip's frames a cycle apart and its own, each read with RIFE's single passes for a part torn — 34 calls of the binary a frame, up to forty frames (docs/loop-repair.md section 2, \"The overlapped wrap\"); off: not tried, recorded `wrap_overlap.skipped` with a warning line, and the gate refuses the cut as filmed — for a host whose RIFE runs on a CPU, where one frame of a 960 px clip takes minutes")
    parser.add_argument("--wrap-blend", choices=wrap_blend_mod.MODES, default="auto", help="--cycle pinned with --anchor none: auto (default): where the pinned last frame draws the first again (the first frame and it differ by more than --seam-max times the cut's median step, at full size), the last half second of the cut takes the first frame's drawing back in a ramp, only where the two ends differ by a redraw and the cell stands where the pin stands; a ramp that steps more than the cut already does, or does not shrink the wrap, is not used and a warning line says so (report `wrap_blend`, docs/loop-repair.md section 6); off: the cut as filmed")
    parser.add_argument("--unfixed", choices=UNFIXED_MODES, default="warn", help="walk/run: a frame the jump repair leaves off the walk's flow (under --repair auto or on) — no group of up to two drawings brings it back, or the group that would was refused (its RIFE frame had a fault) — is recorded in jump_repair.unfixed; a part the size of a hand or a foot that pops at the wrap of the cut taken — the cut kept as cut — in part_pop.left, a take to film again (retake, reason part-pop; docs/loop-repair.md section 2, \"The part pop\"); warn (default): a warning line, the loop kept; refuse: the loop fails")
    parser.add_argument("--jolt-max", type=float, default=None, help=f"walk/run: fail the repaired loop when its jolt index (how far each step strays from its neighbours' mean, over the median step) exceeds this. Default: no gate — the index is reported and a value over {repair_mod.JOLT_REFERENCE} is a warning line")
    parser.add_argument("--head-step-max", type=float, default=None, help=f"walk/run: fail the repaired loop when the head's largest sideways move in one frame exceeds this %% of the body height. Default: no gate — reported, and over {repair_mod.HEAD_STEP_REFERENCE} is a warning line")
    parser.add_argument("--seam-pop-max", type=_seam_pop_max, default=repair_mod.SEAM_POP_REFERENCE, metavar="BOUND|off",
                        help=f"walk/run: fail the loop when the top of the silhouette (the head, or a part held above it) jumps into "
                             f"its first frame more than this many times its median step (`jolt.seam_pop`, read on the loop as it "
                             f"plays; default {repair_mod.SEAM_POP_REFERENCE:g}), a cut fixed by hand as one --anchor motion-auto "
                             "chooses — which reads the cuts it chooses among the same way and takes the next where the gate would "
                             "refuse the first. A top that cannot be read is not refused (`seam_pop_gate: unreadable`); a pinned "
                             f"cut is gated by its pin. --seam-max does not lift it. off: no gate — over {repair_mod.SEAM_POP_REFERENCE:g} "
                             "is a warning line (docs/loop-repair.md section 3, \"The seam pop\")")
    parser.add_argument("--facing", choices=FACINGS, default="right", help="which way the body faces in the frames (default right): the jolt index's hair, and the size reading video-loop-compare's jump guard reads, are behind it; the jump repair reads the flow either way")
    parser.add_argument("--size-hold", choices=SIZE_HOLD_MODES, default="auto", help="walk/run, --anchor motion-auto or --cycle fixed: auto (default) scales a clip whose height, read one cycle on, changes by 1 %% or more over the clip back to its first frame's size about its feet, and moves its feet to the first frame's row where they climb or sink by 1 %% of the height or more, applied before the cycle search or over the whole clip before a fixed cut is read (a fixed cut is held only where the cut itself shows the change a cycle of its length apart; recorded as size_hold, with each frame's move on a held fixed cut (size_hold.cut_frames), which video-loop-repair holds the source frames by again); off: the frames as filmed, except motion-auto's gait fallback, which after a first search that finds nothing still scales back a change of 3 %% or more by the same reading, its feet where they were filmed (recorded as gait_fallback)")
    parser.add_argument("--steps", type=int, choices=STEPS, default=None,
                        help="--anchor motion-auto: how many steps the cut it takes without this holds, as counted by whoever looked "
                             "at the loop (how often each foot lands) — the report's `cycle.step_screen` names a cut that may be one "
                             "step. 1: the cut is one step, so the walk is cut again two of its lengths long (the window [2L-1, 2L+1], "
                             "not bounded by half the clip but held inside --min-len/--max-len: a window that leaves none of it "
                             "fails), recorded `cycle.steps`; 2: the cut is kept, recorded. Either way the strip "
                             "metadata says `steps: 2`, which `video-cycle-align` reads")
    parser.add_argument("--view", choices=handed_mod.VIEWS, default=None,
                        help="walk/run: the view the clip is filmed from. The step count asks whether the feet, which need not "
                             "meet on the screen at every pass, meet once a cycle (`cycle.step_count.cycles`): where a pass "
                             "can hide, as without it, and from the front, the back or a diagonal also where passes are brief. "
                             "Wherever it is asked, the crossings mark cycles only where the body's top line rises and falls "
                             "twice an interval; the view's foot cue only refuses, where it says the other leg leads one "
                             "interval on. With no witness they stay on steps, as from the side, with a warning "
                             "(docs/loop-repair.md section 3, \"The step count\"). Default: not given, asked only where a pass "
                             "can hide")
    parser.add_argument("--name", default="loop", help="basename for strip/gif/webp outputs")
    parser.add_argument("--report", type=Path)


def run(**kwargs: object) -> int:
    payload = run_loop(
        Path(str(kwargs["frames_dir"])), Path(str(kwargs["out_dir"])),
        fps=float(kwargs.get("fps") or 24.0), state=kwargs.get("state"),  # type: ignore[arg-type]
        min_len=kwargs.get("min_len"), max_len=kwargs.get("max_len"), n_out=kwargs.get("n_out"),  # type: ignore[arg-type]
        seam_max=float(kwargs.get("seam_max") or SEAM_RATIO_MAX), name=str(kwargs.get("name") or "loop"), report_path=kwargs.get("report"),  # type: ignore[arg-type]
        cycle_mode=str(kwargs.get("cycle") or "auto"), gif_fps=float(kwargs.get("gif_fps") or GIF_FPS_DEFAULT),  # type: ignore[arg-type]
        start=kwargs.get("start"), length=kwargs.get("length"), strip_height=int(kwargs.get("strip_height") or STRIP_MAX_HEIGHT),  # type: ignore[arg-type, call-overload]
        body_height=kwargs.get("body_height"),  # type: ignore[arg-type]
        anchor=kwargs.get("anchor"),  # None resolves by state inside run_loop
        anchor_regions=kwargs.get("anchor_region"),
        repair=str(kwargs.get("repair") or "auto"), facing=str(kwargs.get("facing") or "right"),
        jolt_max=kwargs.get("jolt_max"), head_step_max=kwargs.get("head_step_max"),  # type: ignore[arg-type]
        size_hold=str(kwargs.get("size_hold") or "auto"), steps=kwargs.get("steps"),  # type: ignore[arg-type]
        overlap_wrap=str(kwargs.get("overlap_wrap") or "on"), wrap_blend=str(kwargs.get("wrap_blend") or "auto"),
        unfixed=str(kwargs.get("unfixed") or "warn"),
        seam_pop_max=kwargs.get("seam_pop_max", repair_mod.SEAM_POP_REFERENCE),  # type: ignore[arg-type]
        view=kwargs.get("view"),  # type: ignore[arg-type]
    )
    summary = {k: payload[k] for k in ("state", "frames_total", "window", "cycle_seconds", "n_out", "delay_ms", "resampled_seam_ratio", "specks_dropped", "report")}
    summary["cycle"] = {k: payload["cycle"].get(k) for k in ("kind", "start", "length", "period_global", "ratio", "review_recommended", "half_period_guard", "fundamental")}
    summary["drawings"] = {k: payload["drawings"][k] for k in ("hold", "drawings_per_second")}
    if payload.get("wrap_blend"):
        summary["wrap_blend"] = {k: payload["wrap_blend"][k] for k in ("applied", "why", "residual_ratio", "wrap_ratio_before",
                                                                      "wrap_ratio_after") if k in payload["wrap_blend"]}
    if payload["periodic_attempt"]:
        summary["periodic_attempt"] = payload["periodic_attempt"]["why_rejected"]
    if payload.get("motion_anchor", {}).get("applied") is False:
        summary["motion_anchor"] = payload["motion_anchor"]
    if payload.get("jolt"):
        summary["jolt"] = {"index": payload["jolt"]["index"], "warnings": payload["jolt"]["warnings"],
                           "head_x_step_max_pct": payload["jolt"]["head"].get("x", {}).get("step_max_pct"),
                           "seam_pop": payload["jolt"]["seam_pop"].get("pop")}
    if payload["cycle"].get("seam_pop", {}).get("applied"):
        summary["cycle"]["seam_pop"] = payload["cycle"]["seam_pop"]
    if payload["cycle"].get("seam_pop_gate", {}).get("applied"):
        summary["cycle"]["seam_pop_gate"] = payload["cycle"]["seam_pop_gate"]
    if payload.get("seam_pop_gate"):
        summary["seam_pop_gate"] = payload["seam_pop_gate"]
    if "steps" in payload["cycle"]:
        summary["cycle"]["steps"] = payload["cycle"]["steps"]
        summary["cycle"]["coverage"] = payload["cycle"]["coverage"]
    if "step_screen" in payload["cycle"]:
        summary["cycle"]["step_screen"] = payload["cycle"]["step_screen"]
    if "step_count" in payload["cycle"]:
        counted = payload["cycle"]["step_count"]
        summary["cycle"]["step_count"] = {k: counted[k] for k in ("mode", "readable", "chosen_by", "cut", "why") if k in counted}
    if "cut_witness" in payload["cycle"]:
        summary["cycle"]["cut_witness"] = {k: payload["cycle"]["cut_witness"][k] for k in ("cut", "says", "by", "held", "mark")
                                           if k in payload["cycle"]["cut_witness"]}
    if payload.get("ghost_screen", {}).get("ghosts") or payload.get("ghost_screen", {}).get("reads") is False:
        summary["ghost_screen"] = payload["ghost_screen"]
    for key in ("ghost_given_way", "ghost_kept"):
        if payload.get(key):
            summary[key] = payload[key]
    if payload.get("wrap_overlap"):
        overlap = payload["wrap_overlap"]
        summary["wrap_overlap"] = {k: overlap[k] for k in ("seam", "frames", "side", "width", "seam_after", "skipped") if k in overlap}
    if payload.get("step_gate"):
        summary["step_gate"] = {k: payload["step_gate"][k] for k in ("drawings", "hold", "largest_ratio", "largest_into", "max",
                                                                     "skipped")
                                if k in payload["step_gate"]}
        if payload["step_gate"].get("jitter"):
            summary["step_gate"]["jitter"] = [step["into"] for step in payload["step_gate"]["jitter"]]
    if payload.get("jump_repair", {}).get("reading") == "seam":
        bridge = payload["jump_repair"]
        if "skipped" not in bridge:
            summary["jump_repair"] = {k: bridge[k] for k in ("reading", "seam", "replaced", "seam_after", "why") if k in bridge}
    elif payload.get("jump_repair", {}).get("replaced"):
        summary["jump_repair"] = {k: payload["jump_repair"][k] for k in ("replaced", "off_max_before", "off_max_after")}
        eased = [r for r in payload["jump_repair"]["rounds"] if r.get("why") == "ease" and r["outcome"] == "accepted"]
        if eased:
            summary["jump_repair"]["eased"] = eased[0]["replaced"]
    if payload.get("jump_repair", {}).get("unfixed"):
        summary.setdefault("jump_repair", {})["unfixed"] = [u["frames"] for u in payload["jump_repair"]["unfixed"]]
    if payload.get("part_pop", {}).get("pops"):
        popped = payload["part_pop"]
        summary["part_pop"] = {"pops": [{k: p[k] for k in ("from", "to", "at", "body_pct", "kind", "margin")} for p in popped["pops"]],
                               "outcome": popped["outcome"], "left": [[p["from"], p["to"]] for p in popped["left"]]}
    if payload.get("retake"):
        summary["retake"] = {k: payload["retake"][k] for k in ("reason", "remedy")}
    summary["strip"] = {k: payload["strip"][k] for k in ("path", "frames", "w", "h", "body_h", "delay_ms")}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(prog="sprite-gen video-loop", description=__doc__)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
