# SPDX-License-Identifier: Apache-2.0
"""`sprite-gen scale-check` — how big is the figure in every frame, against one reference frame?

An image model draws every pose of a row as a separate figure, so a row fitted at one scale still has poses a few to
twenty per cent bigger or smaller than the stance, and two rows fitted apart can differ more (a walk at 0.83, a run
at 1.2): the head swells and shrinks as a clip plays. A bounding box cannot tell: a crouch is shorter and a kick is
wider at the same scale. The head is the part whose size does not change with the pose, so it is the ruler.

The reference frame's head — the top 15 % of its opaque rows, as wide as the ink in that band — is the template.
Each frame is searched for it over scale x rotation by normalized cross-correlation on a grey composite (coarse on a
reduced copy, then fine at full size around the coarse hit). A match at least `--min-score` good is the frame's
size; a weaker one leaves the frame for an eye check. Heads found with a strong match then serve as more templates
for the frames still unsure — a shout or shut eyes match the stance's face badly and another frame's face well.

The report gives each frame its measured size and the correction that brings it to the reference (1 inside
`--deadband`), bound to the exact frame pixels; `game-pack` applies it (docs/game-pack.md "Scale check").
Only NumPy and Pillow: no feature matcher, so an unsure frame is reported, never guessed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

from PIL import Image, ImageDraw

from sprite_gen._deps import np
from sprite_gen.spec.assets import finite, load_asset, load_asset_states, sequence_identity
from sprite_gen.spec.runio import atomic_save_image, atomic_write_text
from sprite_gen.util.console import utf8_streams

REPORT_KIND = "sprite-gen-scale-check"
METHOD = "head-ncc-v1"
BACKGROUND = 128.0      # grey the cut-outs are composited on: neither the outline nor the highlight
HEAD_ALPHA = 64         # opaque enough to belong to the figure's silhouette
HEAD_BAND = 0.15        # the head template: this fraction of the figure's height from its top
COARSE_HEAD_PX = 40     # the template's height in the coarse search
COARSE_SCALE_STEP = 0.05     # half of it is inside the fine search's span
COARSE_ANGLE_STEP = 15
FINE_SCALE = (0.05, 0.01)       # +- span and step around the coarse scale
FINE_ANGLE = (7.5, 2.5)         # +- span and step around the coarse angle
EXTRA_SCORE = 0.88      # a head matched this well becomes a template for the frames still unsure
EXTRA_MAX = 8
FLAT_VARIANCE = 1.0     # grey levels squared: a window flatter than this is background
DEFAULTS = {"min_score": 0.8, "deadband": 0.02, "range": (0.7, 1.4), "angle": 45, "wide_angle": 90}


def frame_sha256(image: Image.Image) -> str:
    """The frame's decoded RGBA pixels: a correction applies to these pixels and no others."""
    rgba = image.convert("RGBA")
    return hashlib.sha256(f"{rgba.width}x{rgba.height}:".encode() + rgba.tobytes()).hexdigest()


def label(state, index=None) -> str:
    """`walk/3` names a frame, `walk` a state; a source without states is `-`."""
    name = state if state is not None else "-"
    return name if index is None else f"{name}/{index}"


def _grey(image: Image.Image) -> np.ndarray:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.float32)
    alpha = rgba[..., 3] / 255
    luma = rgba[..., 0] * 0.299 + rgba[..., 1] * 0.587 + rgba[..., 2] * 0.114
    return luma * alpha + BACKGROUND * (1 - alpha)


def _head_box(image: Image.Image) -> tuple[int, int, int, int]:
    mask = np.asarray(image.getchannel("A")) >= HEAD_ALPHA
    rows = np.flatnonzero(mask.any(axis=1))
    if not rows.size:
        raise ValueError(f"the reference frame has no pixel at alpha {HEAD_ALPHA}: nothing to measure a head on")
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    height = bottom - top
    band = top + max(4, int(HEAD_BAND * height))
    cols = np.flatnonzero(mask[top:band].any(axis=0))
    pad = max(2, round(0.01 * height))
    return (max(0, int(cols[0]) - pad), max(0, top - pad),
            min(image.width, int(cols[-1]) + 1 + pad), min(image.height, band))


def _resize(array: np.ndarray, scale: float) -> np.ndarray:
    h, w = array.shape
    size = (max(4, round(w * scale)), max(4, round(h * scale)))
    resample = Image.Resampling.BOX if scale < 1 else Image.Resampling.BICUBIC
    return np.asarray(Image.fromarray(array, "F").resize(size, resample), dtype=np.float32)


def _rotation(shape: tuple[int, int], angle: float):
    """Size of `shape` turned `angle` degrees and the affine (output -> input, Pillow AFFINE data) that turns it."""
    h, w = shape
    theta = math.radians(angle)
    c, s = math.cos(theta), math.sin(theta)
    out_w = math.ceil(abs(w * c) + abs(h * s))
    out_h = math.ceil(abs(w * s) + abs(h * c))
    # input = centre_in + R(-angle) (output - centre_out)
    a, b, d, e = c, -s, s, c
    cx, cy, ox, oy = w / 2, h / 2, out_w / 2, out_h / 2
    return (out_h, out_w), (a, b, cx - a * ox - b * oy, d, e, cy - d * ox - e * oy)


def _rotate(array: np.ndarray, angle: float) -> tuple[np.ndarray, tuple[float, ...]]:
    if not angle:
        return array, (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    (out_h, out_w), data = _rotation(array.shape, angle)
    turned = Image.fromarray(array, "F").transform((out_w, out_h), Image.Transform.AFFINE, data,
                                                     resample=Image.Resampling.BICUBIC, fillcolor=BACKGROUND)
    return np.asarray(turned, dtype=np.float32), data


def _to_input(data, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = data
    return a * x + b * y + c, d * x + e * y + f


def _to_output(data, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = data
    det = a * e - b * d
    x, y = x - c, y - f
    return (e * x - b * y) / det, (-d * x + a * y) / det


def _good(n: int) -> int:
    """The next size whose prime factors are 2, 3 and 5: the FFT of an awkward prime size is slow."""
    while True:
        m = n
        for p in (2, 3, 5):
            while m % p == 0:
                m //= p
        if m == 1:
            return n
        n += 1


class _Image:
    """One searched picture: its spectrum and window sums, computed once for every template size."""

    def __init__(self, array: np.ndarray):
        # centred, so the window sums of squares do not cancel into a variance that is only rounding
        self.array = array.astype(np.float64) - float(array.mean())
        self.shape = (_good(array.shape[0]), _good(array.shape[1]))
        self.spectrum = np.fft.rfft2(self.array, s=self.shape)
        padded = np.pad(self.array, ((1, 0), (1, 0)))
        self.sum = padded.cumsum(0).cumsum(1)
        self.sq = (padded ** 2).cumsum(0).cumsum(1)
        self.spreads = {}

    def ncc(self, template: np.ndarray) -> np.ndarray | None:
        """Normalized cross-correlation of every placement of `template` (OpenCV's TM_CCOEFF_NORMED)."""
        H, W = self.array.shape
        h, w = template.shape
        if h > H or w > W:
            return None
        t = template.astype(np.float64) - template.mean()
        norm = math.sqrt(float((t * t).sum()))
        if norm < 1e-6:
            return None
        corr = np.fft.irfft2(self.spectrum * np.conj(np.fft.rfft2(t, s=self.shape)), s=self.shape)[:H - h + 1, :W - w + 1]

        def window(table):
            return table[h:, w:] - table[:-h, w:] - table[h:, :-w] + table[:-h, :-w]

        n = h * w
        if (h, w) not in self.spreads:
            s1, s2 = window(self.sum), window(self.sq)
            self.spreads[h, w] = np.sqrt(np.maximum(s2 - s1 * s1 / n, 0))
        spread = self.spreads[h, w] * norm
        # a window of plain background (under one grey level of spread) holds no head, only noise to divide by
        return np.where(spread > norm * math.sqrt(n * FLAT_VARIANCE), corr / np.maximum(spread, 1e-12), 0.0)


def _search(picture: np.ndarray, template: np.ndarray, scales, angles, *, near=None):
    """Best (score, scale, angle, centre in `picture`) of `template` over `scales` x `angles`.
    `near` = (centre, reach) keeps the search to a window around a centre already found."""
    best = (-1.0, 1.0, 0.0, (0.0, 0.0))
    for angle in angles:
        turned, data = _rotate(picture, float(angle))
        origin = (0, 0)
        if near is not None:
            (x, y), reach = near
            x, y = _to_output(data, x, y)
            x0, y0 = max(0, int(x - reach)), max(0, int(y - reach))
            x1, y1 = min(turned.shape[1], int(math.ceil(x + reach))), min(turned.shape[0], int(math.ceil(y + reach)))
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue
            turned, origin = turned[y0:y1, x0:x1], (x0, y0)
        searched = _Image(turned)
        for scale in scales:
            t = _resize(template, float(scale))
            scores = searched.ncc(t)
            if scores is None:
                continue
            index = int(np.argmax(scores))
            score = float(scores.flat[index])
            if score > best[0]:
                y, x = divmod(index, scores.shape[1])
                centre = _to_input(data, origin[0] + x + t.shape[1] / 2, origin[1] + y + t.shape[0] / 2)
                # the size the template was actually drawn at: whole pixels, not the scale asked for
                drawn = (t.shape[0] / template.shape[0] + t.shape[1] / template.shape[1]) / 2
                best = (score, drawn, float(angle), centre)
    return best


def _measure(frame: Image.Image, template: np.ndarray, *, size_range, max_angle):
    """(score, size, angle, centre in frame pixels) of the best head match, or None for a frame with no ink."""
    alpha = np.asarray(frame.getchannel("A"))
    rows, cols = np.flatnonzero((alpha >= HEAD_ALPHA).any(axis=1)), np.flatnonzero((alpha >= HEAD_ALPHA).any(axis=0))
    if not rows.size:
        return None
    # the figure and a template's reach around it: the rest of the canvas is background
    reach = int(max(template.shape) * size_range[1]) + 2
    x0, y0 = max(0, int(cols[0]) - reach), max(0, int(rows[0]) - reach)
    x1, y1 = min(frame.width, int(cols[-1]) + 1 + reach), min(frame.height, int(rows[-1]) + 1 + reach)
    picture = _grey(frame.crop((x0, y0, x1, y1)))
    coarse = min(0.5, max(0.1, COARSE_HEAD_PX / template.shape[0]))
    lo, hi = size_range
    scales = np.arange(lo, hi + 1e-9, COARSE_SCALE_STEP)
    angles = range(-max_angle, max_angle + 1, COARSE_ANGLE_STEP)
    _score, scale, angle, (cx, cy) = _search(_resize(picture, coarse), _resize(template, coarse), scales, angles)
    span, step = FINE_SCALE
    fine_scales = [s for s in np.arange(scale - span, scale + span + 1e-9, step) if lo - 1e-9 <= s <= hi + 1e-9]
    fine_angles = np.arange(angle - FINE_ANGLE[0], angle + FINE_ANGLE[0] + 1e-9, FINE_ANGLE[1])
    # the fine search turns only the part of the picture around the coarse hit
    cx, cy = cx / coarse, cy / coarse
    reach = max(template.shape) * (scale + span) / 2 + 2 / coarse + 4
    r = int(math.ceil(reach * 1.5 + 2))
    px, py = max(0, int(cx) - r), max(0, int(cy) - r)
    part = picture[py:int(cy) + r, px:int(cx) + r]
    score, scale, angle, (cx, cy) = _search(part, template, fine_scales, fine_angles, near=((cx - px, cy - py), reach))
    return score, scale, angle, (cx + px + x0, cy + py + y0)


def _decide(record: dict, *, min_score: float, deadband: float) -> dict:
    size = record.get("size")
    if record["how"] in ("reference", "empty"):
        record["correction"] = 1.0
    elif record["score"] is not None and record["score"] >= min_score:
        record["how"] = "head"
        record["correction"] = 1.0 if abs(size - 1) <= deadband else round(1 / size, 4)
    else:
        record.update(how="check", correction=None)
    return record


def check(reference, reference_frame: int, assets, *, wide_states=(), min_score=DEFAULTS["min_score"],
          deadband=DEFAULTS["deadband"], size_range=DEFAULTS["range"], angle=DEFAULTS["angle"],
          wide_angle=DEFAULTS["wide_angle"]) -> tuple[dict, list, np.ndarray]:
    """The report for `assets` (a list of {state: FrameSequence}) against frame `reference_frame` of `reference`,
    the matched head crops for a board and the reference head template."""
    lo, hi = (finite(v, "scale range", positive=True) for v in size_range)
    if not lo < 1 < hi:
        raise ValueError("scale range must enclose 1 (LOW,HIGH with LOW < 1 < HIGH)")
    if not 0 <= deadband < 0.5 or not 0 < min_score <= 1:
        raise ValueError("deadband must be in [0, 0.5) and min score in (0, 1]")
    if not 0 <= reference_frame < len(reference.frames):
        raise ValueError(f"reference frame {reference_frame} is outside its {len(reference.frames)} frames")
    ref_image = reference.frames[reference_frame].convert("RGBA")
    box = _head_box(ref_image)
    template = _grey(ref_image.crop(box))
    ref_sha = frame_sha256(ref_image)
    records, crops = [], {}
    for states in assets:
        for state, sequence in states.items():
            frames = []
            wide = state in wide_states
            for index, frame in enumerate(sequence.frames):
                frame = frame.convert("RGBA")
                record = {"frame": label(state, index), "sha256": frame_sha256(frame), "score": None, "size": None,
                          "angle": None, "template": "reference"}
                if record["sha256"] == ref_sha:
                    record.update(how="reference", size=1.0, score=1.0, angle=0.0)
                else:
                    found = _measure(frame, template, size_range=(lo, hi), max_angle=wide_angle if wide else angle)
                    if found is None:
                        record["how"] = "empty"
                    else:
                        score, size, turn, centre = found
                        record.update(how="measured", score=round(score, 4), size=round(size, 4), angle=round(turn, 1))
                        crops[record["frame"]] = (frame, size, turn, centre, wide)
                frames.append(record)
            records.append({"identity": sequence_identity(sequence), "frames": frames})
    # second pass: strongly matched heads become templates for the frames still unsure
    extra = []
    ranked = sorted((f for r in records for f in r["frames"] if f["how"] == "measured" and f["score"] >= EXTRA_SCORE),
                    key=lambda f: -f["score"])
    for f in ranked[:EXTRA_MAX]:
        crop = _head_crop(*crops[f["frame"]][:4], template.shape)
        if crop is not None:
            extra.append((f["frame"], _resize(crop, template.shape[0] / crop.shape[0])))
    for r in records:
        for f in r["frames"]:
            if f["how"] != "measured" or f["score"] >= min_score:
                continue
            frame, *_rest, wide = crops[f["frame"]]
            for name, t in extra:
                found = _measure(frame, t, size_range=(lo, hi), max_angle=wide_angle if wide else angle)
                if found is not None and found[0] > f["score"]:
                    score, size, turn, centre = found
                    f.update(score=round(score, 4), size=round(size, 4), angle=round(turn, 1), template=name)
                    crops[f["frame"]] = (frame, size, turn, centre, wide)
    for r in records:
        for f in r["frames"]:
            _decide(f, min_score=min_score, deadband=deadband)
        resolved = [f["correction"] for f in r["frames"] if f["how"] == "head"]
        open_frames = [f for f in r["frames"] if f["how"] == "check"]
        r["state_correction"] = None if open_frames or not resolved else round(float(np.median(resolved)), 4)
        if not resolved and not open_frames:
            r["state_correction"] = 1.0
    unresolved = [f["frame"] for r in records for f in r["frames"] if f["how"] == "check"]
    report = {"kind": REPORT_KIND, "version": 1, "method": METHOD,
              "reference": {"identity": sequence_identity(reference), "frame": reference_frame, "sha256": ref_sha,
                            "head_box": list(box)},
              "settings": {"min_score": min_score, "deadband": deadband, "range": [lo, hi], "angle": angle,
                           "wide_angle": wide_angle, "wide_states": sorted(wide_states)},
              "assets": records, "unresolved": unresolved, "ok": not unresolved}
    return report, [(f["frame"], f, crops.get(f["frame"])) for r in records for f in r["frames"]], template


def _head_crop(frame, size, angle, centre, shape):
    """The matched head at its own size, turned upright."""
    h, w = shape[0] * size, shape[1] * size
    grey = _grey(frame)
    turned, data = _rotate(grey, angle)
    x, y = _to_output(data, *centre)
    x0, y0 = int(round(x - w / 2)), int(round(y - h / 2))
    x1, y1 = x0 + int(round(w)), y0 + int(round(h))
    if x0 < 0 or y0 < 0 or x1 > turned.shape[1] or y1 > turned.shape[0] or x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return turned[y0:y1, x0:x1]


def board(matches, template: np.ndarray, out: Path) -> Path:
    """The reference head beside every frame's matched head at its own size; the yellow line is the reference
    head's height, so a frame drawn too big or too small shows as a head past or short of it."""
    th, tw = template.shape
    tile_w, tile_h = int(tw * 1.5) + 8, int(th * 1.5) + 28
    tiles = []
    for name, record, crop in [("reference", {"how": "reference", "size": 1.0}, None), *matches]:
        tile = Image.new("RGB", (tile_w, tile_h), (60, 64, 76))
        head = template if name == "reference" else (_head_crop(crop[0], crop[1], crop[2], crop[3], template.shape)
                                                    if crop else None)
        if head is not None:
            tile.paste(Image.fromarray(np.clip(head, 0, 255).astype(np.uint8), "L").convert("RGB"), (4, 24))
        draw = ImageDraw.Draw(tile)
        colour = {"check": (255, 96, 96), "head": (255, 255, 255)}.get(record["how"], (170, 200, 255))
        draw.text((4, 4), f"{name} {record.get('size') or '?'} {record['how']}", fill=colour)
        draw.line([(0, 24 + th), (tile_w, 24 + th)], fill=(255, 200, 0), width=1)
        tiles.append(tile)
    columns = 8
    sheet = Image.new("RGB", (tile_w * columns, tile_h * math.ceil(len(tiles) / columns)), (40, 40, 48))
    for index, tile in enumerate(tiles):
        sheet.paste(tile, (index % columns * tile_w, index // columns * tile_h))
    atomic_save_image(sheet, out)
    return out


# ---- applying a report (game-pack) ------------------------------------------------------------------------------

def load_report(path: Path) -> dict:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("kind") != REPORT_KIND or report.get("version") != 1:
        raise ValueError(f"scale_check.report must be a version 1 {REPORT_KIND} report: {Path(path).name}")
    return report


def factors(report: dict, sequence, state, *, per: str, fix: dict) -> list[float]:
    """The correction of every frame of `sequence`: a looked-at `fix` wins, else the report's measurement. Refuses a
    report that measured other pixels and a frame nobody has resolved."""
    # source and state find the record and the frame pixels bind it; a playback override (fps, anchor) in the
    # request changes neither what was measured nor what it means
    identity = sequence_identity(sequence)
    record = next((r for r in report.get("assets", []) if isinstance(r.get("identity"), dict)
                   and (r["identity"].get("source"), r["identity"].get("state")) == (identity["source"], identity["state"])), None)
    if record is None:
        raise ValueError(f"the scale-check report does not cover {label(state)} of this source; run scale-check on it")
    frames = record["frames"]
    if len(frames) != len(sequence.frames) or any(f["sha256"] != frame_sha256(image)
                                                 for f, image in zip(frames, sequence.frames)):
        raise ValueError(f"the scale-check report measured other pixels than {label(state)}; run scale-check again")
    if per == "state":
        value = fix.get(label(state), record["state_correction"])
        if value is None:
            raise ValueError(f"{label(state)}: frames await an eye check ({', '.join(f['frame'] for f in frames if f['how'] == 'check')}); "
                             f"look at the board and give scale_check.fix[\"{label(state)}\"]")
        return [_factor(value, label(state))] * len(frames)
    result = []
    for f in frames:
        value = fix.get(f["frame"], f["correction"])
        if value is None:
            raise ValueError(f"{f['frame']} awaits an eye check (head match {f['score']}); look at the board and "
                             f"give scale_check.fix[\"{f['frame']}\"]")
        result.append(_factor(value, f["frame"]))
    return result


def _factor(value, name) -> float:
    number = finite(value, f"scale_check correction for {name}", positive=True)
    if not 0.5 <= number <= 2:
        raise ValueError(f"scale_check correction for {name} must be between 0.5 and 2")
    return float(number)


# ---- command line -----------------------------------------------------------------------------------------------

def _range(value: str) -> tuple[float, float]:
    parts = value.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("LOW,HIGH")
    return float(parts[0]), float(parts[1])


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--reference", type=Path, required=True, help="the asset holding the reference frame (a strip, manifest or game asset)")
    parser.add_argument("--reference-state", help="the reference frame's state in a multi-state source (e.g. idle)")
    parser.add_argument("--reference-frame", type=int, default=0, help="zero-based reference frame (default 0)")
    parser.add_argument("--asset", action="append", type=Path, required=True, help="an asset to measure, every state of it (repeatable)")
    parser.add_argument("--state", action="append", default=[], help="measure only these states (repeatable; default all)")
    parser.add_argument("--wide-state", action="append", default=[], help="a state whose head turns past 45 degrees (a fall, a flip): searched to --wide-angle (repeatable)")
    parser.add_argument("--min-score", type=float, default=DEFAULTS["min_score"], help="head match a size is taken from (default %(default)s); below it the frame awaits an eye check")
    parser.add_argument("--deadband", type=float, default=DEFAULTS["deadband"], help="a size within this of 1 is left alone (default %(default)s)")
    parser.add_argument("--range", type=_range, default=DEFAULTS["range"], help="LOW,HIGH sizes searched against the reference (default 0.7,1.4)")
    parser.add_argument("--angle", type=int, default=DEFAULTS["angle"], help="head tilt searched either way, degrees (default %(default)s)")
    parser.add_argument("--wide-angle", type=int, default=DEFAULTS["wide_angle"], help="the same for --wide-state (default %(default)s)")
    parser.add_argument("--out", type=Path, required=True, help="the report JSON")
    parser.add_argument("--board", type=Path, help="a review PNG: the reference head beside every matched head")


def run(**kwargs) -> int:
    try:
        reference = load_asset(Path(kwargs["reference"]), state=kwargs.get("reference_state"))
        wanted = set(kwargs.get("state") or [])
        assets = []
        for source in kwargs["asset"]:
            states = load_asset_states(Path(source))
            if wanted:
                states = {s: q for s, q in states.items() if s in wanted}
            assets.append(states)
        if not any(assets):
            raise ValueError("no asset state to measure: check --asset and --state")
        report, matches, template = check(
            reference, int(kwargs.get("reference_frame") or 0), assets, wide_states=set(kwargs.get("wide_state") or []),
            min_score=float(kwargs.get("min_score", DEFAULTS["min_score"])),
            deadband=float(kwargs.get("deadband", DEFAULTS["deadband"])), size_range=kwargs.get("range") or DEFAULTS["range"],
            angle=int(kwargs.get("angle", DEFAULTS["angle"])), wide_angle=int(kwargs.get("wide_angle", DEFAULTS["wide_angle"])))
        if kwargs.get("board"):
            report["board"] = str(board(matches, template, Path(kwargs["board"])))
        atomic_write_text(Path(kwargs["out"]), json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    except (ValueError, OSError) as exc:
        raise SystemExit(f"scale-check: {exc}") from exc
    frames = [f for r in report["assets"] for f in r["frames"]]
    print(json.dumps({"ok": report["ok"], "report": str(kwargs["out"]), "frames": len(frames),
                      "corrected": sum(1 for f in frames if f["correction"] not in (None, 1.0)),
                      "unresolved": report["unresolved"]}, ensure_ascii=False))
    if report["unresolved"]:
        print(f"scale-check: {len(report['unresolved'])} frame(s) await an eye check: {', '.join(report['unresolved'][:12])}",
              file=sys.stderr)
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    utf8_streams()
    parser = argparse.ArgumentParser(prog="sprite-gen scale-check", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_arguments(parser)
    return run(**vars(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
