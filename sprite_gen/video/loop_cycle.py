# SPDX-License-Identifier: Apache-2.0
"""Finding the cycle in a clip: frame features, the distance matrix, and the periodic, one-shot, fixed and pinned cycle pickers.

Moved out of `loop.py` unchanged; `loop` re-exports every name, so `loop.<name>` still works."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.video import legs as legs_mod
from sprite_gen.video import period as period_mod
from sprite_gen.video.loop_params import (
    ANALYSIS_SIZE,
    GAIT_DOUBLE_SEARCH,
    GAIT_DOUBLE_TOL,
    GAIT_NEAR_EXACT_STEP_FRACTION,
    ONE_SHOT_MIN_ACTIVE,
    ONE_SHOT_MIN_COHERENCE,
    ONE_SHOT_MIN_CONTRAST,
    ONE_SHOT_MIN_LEN,
    ONE_SHOT_MIN_MOVED,
    ONE_SHOT_PAD,
    PERIODICITY_MIN,
    PIN_NOISE_MAX,
)


def _load_small(path: Path) -> np.ndarray:
    return _small_features(Image.open(path).convert("RGBA"))


def _small_features(image: Image.Image) -> np.ndarray:
    im = image.copy()
    im.thumbnail((ANALYSIS_SIZE, ANALYSIS_SIZE))
    a = np.asarray(im, dtype=np.float32) / 255.0
    rgb = a[..., :3] * a[..., 3:4]  # premultiplied: transparent pixels contribute 0
    return np.concatenate([rgb, a[..., 3:4]], axis=-1).reshape(-1)


def frame_masses(files: list[Path]) -> np.ndarray:
    """Per-frame pixel mass of the analysis thumbnail (mean |premultiplied rgba|): how much
    subject there is, on the same scale as the distance matrix."""
    return np.array([float(np.abs(_load_small(f)).mean()) for f in files], dtype=np.float32)


def leg_signals(frames: list[Image.Image]) -> dict[str, list[float]] | None:
    """The legs' per-frame signals for the two-cycle screen's record (`legs.strike_signals`), or
    None where a frame has no solid body to read them from."""
    try:
        return legs_mod.strike_signals(frames)
    except ValueError:
        return None


def distance_matrix(files: list[Path]) -> np.ndarray:
    return _distances(np.stack([_load_small(f) for f in files]))


def _seam_on_cells(cells: list[Image.Image]) -> tuple[float, float, np.ndarray]:
    """Cells as they play (a walk's each drawing's first, `seam_drawings`), on their analysis features: the mean step, the
    wrap (last -> first) and each step."""
    flat = np.stack([_small_features(im) for im in cells])
    steps = np.abs(flat[1:] - flat[:-1])
    return float(steps.mean()), float(np.abs(flat[-1] - flat[0]).mean()), steps.mean(axis=1)


def _distances(flat: np.ndarray) -> np.ndarray:
    """The distance between every two frames of their analysis features (`_small_features`)."""
    n = len(flat)
    D = np.zeros((n, n), dtype=np.float32)
    # Symmetric to the bit (|a - b| is |b - a|, each row's mean the same sum): measure the upper triangle, mirror it.
    for i in range(n):
        D[i, i:] = np.abs(flat[i:] - flat[i]).mean(axis=1)
        D[i:, i] = D[i, i:]
    return D


def _repeat_context(D: np.ndarray, start: int, length: int, step: float) -> dict[str, Any]:
    """Compare a neighbourhood at the cut with the same poses one cycle later.

    A quarter-cycle on either side samples half a cycle of motion instead of one
    coincident pose. At a clip edge only real pairs participate; no padding or
    synthetic wrap is evidence of repetition. Normalise by the candidate's
    playback step so the cost is independent of character size and contrast.
    This measures temporal consistency, not limb identity.
    """
    radius = max(1, length // 4)
    first = max(0, start - radius)
    stop = min(len(D) - length, start + radius + 1)
    error = float(np.mean([D[j, j + length] for j in range(first, stop)]))
    return {
        "context_pair_range": [first, stop],  # half-open source indices
        "context_repeat_error": error,
        "context_repeat_over_step": error / step if step > 0 else math.inf,
    }


def detect_cycle(D: np.ndarray, *, min_len: int, max_len: int, gait_floor: int | None = None,
                 signals: dict[str, list[float]] | None = None) -> dict[str, Any]:
    """Global period, then a wrap-compatible start with coherent gait context.

    `gait_floor` (frames) turns on the half-period guard: a period below it is one step
    of a two-step gait, so the doubled period is taken when it repeats about as well
    (see GAIT_DOUBLE_TOL). Above the floor, ambiguous non-exact harmonics may also
    retain two phase occurrences; the report flags that decision for visual review.
    Gait starts balance the wrap step with observed repetition around the cut;
    other states keep their wrap-only ranking. The period taken is then screened for
    two or three cycles (`fundamental`, `period.screen`; `signals` are the frames'
    `legs.strike_signals`, for the record): what is found is recorded, nothing is
    cut shorter."""
    n = D.shape[0]
    max_len = min(max_len, n - 2)
    if min_len < 2 or max_len < min_len:
        raise SystemExit(f"video-loop: window [{min_len}, {max_len}] is empty for {n} frames")
    adjacent = np.array([D[i, i + 1] for i in range(n - 1)])
    prof: dict[int, float] = {}
    for L in range(max(2, min_len // 2), max_len + 1):
        prof[L] = float(np.mean([D[j, j + L] for j in range(0, n - L, 2)]))
    cands = [L for L in range(min_len, max_len + 1) if L - 1 in prof and L + 1 in prof and prof[L] <= prof[L - 1] and prof[L] <= prof[L + 1]]
    if not cands:
        cands = list(range(min_len, max_len + 1))
    # The true period is the SMALLEST minimum that is about as deep as the deepest one:
    # exact repeats also dip at 2x and 3x the period, and a half-period look-alike dips
    # noticeably less (near/far limb difference) — so a 15% depth tolerance separates both.
    deepest = min(prof[L] for L in cands)
    period = min(L for L in cands if prof[L] <= deepest * 1.15 + 1e-4)  # abs floor: exact repeats sit at ~0
    guard: dict[str, Any] = {"applied": False}
    if gait_floor is not None and period < gait_floor:
        # A period under the gait floor is one step, not a gait. The full gait is the
        # repeat at about twice that; the profile's own minimum near 2x (within
        # GAIT_DOUBLE_SEARCH) is the candidate, since a real cycle rarely lands on
        # exactly 2p. If no such repeat exists, the clip holds one step only — a
        # quadruped whose near and far legs read alike does this — and keeping the
        # step would loop half a stride without a word. Refuse instead, under the
        # same words as a flat profile, so a caller's regenerate-on-no-period rule
        # covers both.
        doubles = [L for L in cands if abs(L - 2 * period) <= GAIT_DOUBLE_SEARCH and L <= max_len]
        if not doubles:
            doubles = [L for L in (2 * period - 1, 2 * period, 2 * period + 1) if L in prof and L <= max_len]
        L2 = min(doubles, key=lambda L: prof[L]) if doubles else None
        if L2 is not None and prof[L2] <= prof[period] * (1 + GAIT_DOUBLE_TOL) + 1e-4:
            guard = {"applied": True, "from": period, "to": L2, "gait_floor": gait_floor, "depth_ratio": round(prof[L2] / prof[period], 3) if prof[period] > 0 else None}
            period = L2
        else:
            guard = {"applied": False, "below_floor": period, "gait_floor": gait_floor,
                     "double_candidate": L2, "double_depth_ratio": round(prof[L2] / prof[period], 3) if L2 is not None and prof[period] > 0 else None,
                     "why": "the only repeat is one step: nothing near twice the period repeats about as well"}
            exc: Any = SystemExit(
                f"video-loop: no periodic cycle found — the only repeat is one step ({period} frames, under the gait floor of "
                f"{gait_floor}) and nothing near twice that ({2 * period - GAIT_DOUBLE_SEARCH}..{2 * period + GAIT_DOUBLE_SEARCH}) "
                f"repeats within {round(GAIT_DOUBLE_TOL * 100)} % of it"
                + (f" (best {L2}: {round(prof[L2] / prof[period], 2)}x worse)" if L2 is not None and prof[period] > 0 else "")
                + "; the clip shows a half stride, regenerate it"
            )
            exc.diagnostics = {"kind": "periodic", "period_global": period, "half_period_guard": guard,
                               "profile_minima": [[L, round(prof[L], 5)] for L in sorted(cands, key=lambda L: prof[L])[:6]]}
            raise exc
    # A plausible duration does not prove that a gait contains both phases. If a
    # second local minimum at twice the period is similarly good, retain both
    # occurrences at the original fps. This is a conservative ambiguity policy,
    # not an anatomical inference: a true short cycle may be shown twice.
    # Do it only once, inside the requested window, and leave near-exact repeats
    # alone. The duration-floor guard above still owns implausibly short beats.
    profile_mean = float(np.mean([prof[L] for L in prof]))
    review_recommended = False
    if gait_floor is not None and period >= gait_floor and not guard["applied"]:
        ordinary_step = float(adjacent.mean())
        repeat_fraction = prof[period] / ordinary_step if ordinary_step > 0 else math.inf
        doubles = [L for L in cands if abs(L - 2 * period) <= 1]
        if doubles and repeat_fraction > GAIT_NEAR_EXACT_STEP_FRACTION:
            L2 = min(doubles, key=lambda L: prof[L])
            if (prof[L2] <= prof[period] * (1 + GAIT_DOUBLE_TOL) + 1e-4
                    and profile_mean > 0
                    and (profile_mean - prof[L2]) / profile_mean >= PERIODICITY_MIN):
                guard = {
                    "applied": True, "from": period, "to": L2,
                    "gait_floor": gait_floor, "reason": "ambiguous-harmonic",
                    "depth_ratio": round(prof[L2] / prof[period], 3),
                    "repeat_error_over_step": round(repeat_fraction, 3),
                    "why": "both periods are plausible; retain two phase occurrences at source speed",
                }
                period = L2
                review_recommended = True
    periodicity = (profile_mean - prof[period]) / profile_mean if profile_mean > 0 else 0.0
    # A half or a third of the period that returns to a pose on the motion's path may be the
    # cycle, or one step of it with the legs alike: recorded, never taken (sprite_gen/video/period.py).
    fundamental = period_mod.screen(
        period, D=D, prof=prof, minima=period_mod.local_minima(prof), mean=profile_mean,
        lowest=max(min_len, gait_floor or 0), periodicity_min=PERIODICITY_MIN, signals=signals, gait=gait_floor is not None,
        floor_why="the gait floor" if gait_floor is not None and gait_floor >= min_len else "the window")
    # Score the last displayed frame -> first frame transition against an ordinary
    # playback step. Minimising distance alone rewards a repeated pose (a stall).
    # Log distance penalises steps that are too short or too long symmetrically.
    best: dict[str, Any] | None = None
    best_score = math.inf
    candidates = []
    for L in (period - 1, period, period + 1):
        if L < min_len or L > max_len:
            continue
        for i in range(0, n - L):
            seam = float(D[i + L - 1, i])
            inner = float(adjacent[i : i + L - 1].mean())
            ratio = seam / inner if inner > 0 else math.inf
            score = abs(math.log(ratio)) if ratio > 0 else math.inf
            candidates.append({"start": i, "length": L, "seam": seam, "inner_mean_adjacent": inner, "ratio": ratio})
            selection = None
            if gait_floor is not None:
                selection = _repeat_context(D, i, L, inner)
                selection["method"] = "repeat-context-and-wrap"
                selection["wrap_log_error"] = score
                score += selection["context_repeat_over_step"]
                selection["score"] = score
            if best is None or score < best_score:
                best_score = score
                best = {"start": i, "length": L, "seam": seam, "inner_mean_adjacent": inner,
                        "ratio": ratio,
                        "next_frame_distance": float(D[i, i + L]) if i + L < n else None}
                if selection is not None:
                    best["selection"] = selection
    assert best is not None
    best["candidates"] = candidates
    best["repeat_pairs"] = n - period
    best["profile_sample_pairs"] = len(range(0, n - period, 2))
    best["period_global"] = period
    best["half_period_guard"] = guard
    best["fundamental"] = fundamental
    best["review_recommended"] = review_recommended
    best["periodicity"] = round(periodicity, 4)  # how far below the profile mean the period dips (0 = flat = no period)
    best["profile_minima"] = [[L, round(prof[L], 5)] for L in sorted(cands, key=lambda L: prof[L])[:6]]
    return best


class CycleSelectionError(SystemExit):
    """A selection refusal with measurements that survive into the failure report."""

    def __init__(self, message: str, diagnostics: dict[str, Any]):
        super().__init__(message)
        self.diagnostics = diagnostics


def periodicity_floor(n: int, period: int, *, partial_repeat: bool) -> float:
    """Require stronger evidence when fewer than one full period can be compared.

    Full-cycle coverage keeps the existing 15% floor. Missing coverage increases
    the required dip linearly toward 100%; one or two lucky pairs cannot prove
    a repeat. This is a conservative coverage policy, not a confidence interval.
    """
    coverage = min(1.0, max(0, n - period) / period)
    return PERIODICITY_MIN + (1 - PERIODICITY_MIN) * (1 - coverage) if partial_repeat else PERIODICITY_MIN


def detect_one_shot(D: np.ndarray, *, min_len: int, max_len: int, frame_mass: np.ndarray | None = None) -> dict[str, Any]:
    """Find an observed return pair enclosing a complete excursion.

    A global medoid can be the held strike instead of ready. Enumerate close
    endpoint poses, measure departure from both, and require the peak's entire
    active component to have observed rest on BOTH sides. A clipped component
    at frame zero or n-1 is never repaired by padding. The existing contrast /
    moved-mass floors still reject stand jitter; return distance must additionally
    be at most a quarter of the departure. No seam threshold is relaxed.
    No cut is shorter than `min_len` (nor ONE_SHOT_MIN_LEN) or longer than `max_len`; `run_loop`
    passes ONE_SHOT_MIN_LEN unless the caller set --min-len (`CutWindow`).
    """
    n = len(D)
    adjacent = np.diag(D, 1)
    candidates: list[dict[str, Any]] = []
    max_moved = 0.0
    shortest = max(ONE_SHOT_MIN_LEN, min_len)
    for start in range(n - ONE_SHOT_MIN_LEN + 1):
        for end in range(start + shortest - 1, min(n, start + max_len)):
            seam = float(D[start, end])
            e = (D[start] + D[end]) / 2
            peak_at = start + int(np.argmax(e[start:end + 1]))
            peak = float(e[peak_at])
            departure = peak - seam / 2
            if departure <= 0 or seam > departure * 0.25:
                continue
            med = float(np.median(e))
            mad = float(np.median(np.abs(e - med))) or 1e-6
            contrast = (peak - med) / mad
            mass = float((frame_mass[start] + frame_mass[end]) / 2) if frame_mass is not None else 0
            moved = departure / mass if mass > 0 else None
            max_moved = max(max_moved, moved or 0)
            if contrast >= ONE_SHOT_MIN_CONTRAST:
                rule = "contrast"
                active = e > med + ONE_SHOT_MIN_CONTRAST * mad
            elif moved is not None and moved >= ONE_SHOT_MIN_MOVED:
                rule = "moved"
                active = e > seam / 2 + 0.5 * departure
            else:
                continue
            if not active[peak_at]:
                continue
            a = b = peak_at
            while a > 0 and active[a - 1]:
                a -= 1
            while b + 1 < n and active[b + 1]:
                b += 1
            if b - a + 1 < ONE_SHOT_MIN_ACTIVE:
                continue
            if not (start <= a - ONE_SHOT_PAD and end >= b + ONE_SHOT_PAD):
                continue
            inner = float(adjacent[start:end].mean())
            if inner <= 0 or departure < ONE_SHOT_MIN_COHERENCE * inner:
                continue
            candidates.append({
                "excursion_over_step": departure / inner,
                "kind": "one-shot", "start": start, "length": end - start + 1,
                "seam": seam, "inner_mean_adjacent": inner,
                "ratio": seam / inner if inner > 0 else math.inf,
                "period_global": None, "periodicity": None,
                "rest_frame": start, "return_pair": [start, end],
                "return_distance_over_departure": seam / departure,
                "excursion": [a, b], "excursion_peak": peak_at,
                "excursion_contrast": round(contrast, 2),
                "excursion_moved": round(moved, 3) if moved is not None else None,
                "excursion_rule": rule, "departure": departure,
            })
    if not candidates:
        raise CycleSelectionError(
            f"video-loop: no complete one-shot return — the clip never leaves its rest pose "
            f"and returns with observed endpoints (moved {max_moved:.2f}, "
            f"need {ONE_SHOT_MIN_MOVED})",
            {"kind": "one-shot", "candidates": []},
        )
    # Keep the full strongest action, then minimise excess rest. Do not choose a
    # tiny low-seam twitch just because its endpoints happen to match exactly.
    strongest = max(c["departure"] for c in candidates)
    eligible = [c for c in candidates if c["departure"] >= strongest * 0.95]
    best = min(eligible, key=lambda c: (c["length"], c["ratio"], c["start"]))
    return {**best, "candidates": candidates}


def fixed_cycle(D: np.ndarray, *, start: int, length: int) -> dict[str, Any]:
    """An explicitly requested cut (`--cycle fixed --start N --length L`): no detection, no
    periodicity gate — the caller says which frames are the cycle and the report says so
    (kind = "fixed"). The seam is still measured and the seam gate still applies."""
    n = D.shape[0]
    if start < 0 or length < 2 or start + length > n:
        raise SystemExit(f"video-loop: --start {start} --length {length} does not fit {n} frames")
    end = start + length - 1
    adjacent = np.array([D[i, i + 1] for i in range(start, end)])
    inner = float(adjacent.mean())
    seam = float(D[start, end])
    return {"kind": "fixed", "start": start, "length": length, "seam": seam, "inner_mean_adjacent": inner,
            "ratio": seam / inner if inner > 0 else math.inf, "period_global": None, "periodicity": None}


def pinned_cycle(D: np.ndarray, *, seam_max: float) -> dict[str, Any]:
    """The whole clip as one cycle, for a clip pinned to end on its first frame.

    Every frame but the last is kept: the last re-renders the first, so playing it too would show
    that pose twice at the wrap. The wrap (second-to-last -> first) then plays like the step into
    that last frame, and closes when the last frame really is the first again — so the gate is
    the pin error, allowed the same `seam_max` steps as any seam or the re-render noise, whichever
    is larger. A near-still clip moves so little per frame that the seam ratio alone reads its
    re-render noise as a jump.
    """
    n = D.shape[0]
    length = n - 1
    adjacent = np.array([D[i, i + 1] for i in range(length - 1)])
    inner = float(adjacent.mean())
    seam = float(D[length - 1, 0])
    pin_error = float(D[n - 1, 0])
    return {"kind": "pinned", "start": 0, "length": length, "seam": seam, "inner_mean_adjacent": inner,
            "ratio": seam / inner if inner > 0 else math.inf, "pin_error": pin_error,
            "pin_tolerance": max(seam_max * inner, PIN_NOISE_MAX), "period_global": None, "periodicity": None}


def once_return_cycle(D: np.ndarray, *, seam_max: float) -> dict[str, Any]:
    """The whole clip played once, for a clip pinned to end on its first frame (`--cycle once-return`).

    Every frame is kept, the last too: the strip starts on the first pose and ends on it again, so a game plays it
    once and goes on from the pose it started in. The gate is the pin, as `pinned_cycle`'s: the last frame is the
    first again within `seam_max` ordinary steps or the re-render noise. The motion's peak is its largest step,
    as a `once` cut's (`nonloop.select_cut`).
    """
    n = D.shape[0]
    steps = np.diag(D, 1)
    inner = float(steps.mean())
    pin_error = float(D[n - 1, 0])
    return {"kind": "once-return", "start": 0, "length": n, "end_frame": n - 1, "event_frame": int(np.argmax(steps)) + 1,
            "seam": pin_error, "inner_mean_adjacent": inner, "pin_error": pin_error,
            "pin_tolerance": max(seam_max * inner, PIN_NOISE_MAX), "period_global": None, "periodicity": None,
            "ratio": 0.0, "review_recommended": False}
