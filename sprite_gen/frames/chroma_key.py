# SPDX-License-Identifier: Apache-2.0
"""Chroma-key matting on the RGB path: key detection, the hard key mask, despill and unmixing.

Moved out of `extract.py` unchanged; `extract` re-exports every name, so `extract.<name>` still works."""

from __future__ import annotations

import math
from typing import Any

from PIL import Image

from sprite_gen._deps import np
from sprite_gen.frames import decontam as decontam_module
from sprite_gen.frames.keyfield import (
    IN_BAND_UNMIX_KEY_DEPTH,
    KEY_CHANNEL_DARK,
    key_channel_split,
    key_distance_field,
    key_excess_field,
    key_tint_field,
)
from sprite_gen.frames.morph import grow_chebyshev, grow_into


def color_distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> float:
    return math.sqrt(sum((left[index] - right[index]) ** 2 for index in range(3)))


def alpha_nonzero_count(image: Image.Image) -> int:
    return sum(image.getchannel("A").histogram()[1:])


def edge_alpha_count(image: Image.Image, margin: int) -> int:
    alpha = image.getchannel("A")
    width, height = image.size
    total = 0
    for box in (
        (0, 0, width, margin),
        (0, height - margin, width, height),
        (0, 0, margin, height),
        (width - margin, 0, width, height),
    ):
        total += sum(alpha.crop(box).histogram()[1:])
    return total


# The sides a subject can leave its frame by — the bottom is the floor it stands on, contact there is expected.
FRAME_SIDES = ("top", "left", "right")
# The drawing's own border (`own_border`): a side the subject covers on its outermost row or column for this share of
# its length. A drawing cut there (a sleeve, hair off the top) covers far more; a strand's tip grazing the edge covers less.
BORDER_MIN_FRACTION = 0.02


def own_border(subject: np.ndarray) -> tuple[list[str], dict[str, int]]:
    """The drawing's own border — (sides, outer_px): the `FRAME_SIDES` whose outermost row or column `subject`, an
    (h, w) bool mask of the subject's pixels, covers for at least BORDER_MIN_FRACTION of the side's length, and the
    pixels it covers on each. `video-frames` accepts subject contact there in every frame of a clip; Breathe's pixel mode
    names it when it refuses a frame pushed off the cell. The rule: docs/video-pipeline.md section 3."""
    h, w = subject.shape
    outer = {"top": int(subject[0].sum()), "left": int(subject[:, 0].sum()), "right": int(subject[:, -1].sum())}
    length = {"top": w, "left": h, "right": h}
    return [s for s in FRAME_SIDES if outer[s] and outer[s] >= BORDER_MIN_FRACTION * length[s]], outer


def key_tint_score(color: tuple[int, int, int], chroma_key: tuple[int, int, int]) -> float:
    keyed_channels, unkeyed_channels = key_channel_split(chroma_key)
    if not keyed_channels:
        return 0.0
    keyed_average = sum(color[index] for index in keyed_channels) / len(keyed_channels)
    unkeyed_average = sum(color[index] for index in unkeyed_channels) / len(unkeyed_channels)
    return keyed_average - unkeyed_average



def despill_color(
    color: tuple[int, int, int],
    chroma_key: tuple[int, int, int],
    key_tint: float,
    tint: float,
    *,
    chroma_groups: tuple[list[int], list[int]] | None = None,
) -> tuple[float, tuple[int, int, int]]:
    """Estimate the key fraction of a blend pixel and remove it from the RGB.

    Blend model: observed = (1-k)*subject + k*key. key_tint_score is linear in
    the channels and scores the key itself at `key_tint`, so k = tint/key_tint
    recovers a subject estimate whose own tint score is ~0. Returns the
    subject coverage (1-k) and the despilled color. Opaque full-spill correction
    supplies the declared key's `chroma_groups` to limit chroma gain: recover
    mean brightness but do not magnify channel differences within either group.
    Small colour errors otherwise become strong secondary casts when coverage
    is small. Groups come from the declared key, even if the painted key is dim.
    """
    k = min(tint / key_tint, 1.0)
    coverage = 1.0 - k
    if coverage <= 0:
        return 0.0, (0, 0, 0)
    unmixed = [(color[index] - k * chroma_key[index]) / coverage for index in range(3)]
    if chroma_groups is not None:
        for channels in chroma_groups:
            if not channels:
                continue
            mean = sum(unmixed[index] for index in channels) / len(channels)
            observed_mean = sum(color[index] for index in channels) / len(channels)
            for index in channels:
                # Preserve observed differences, including with a slightly
                # impure painted key; subtracting its channel imbalance here
                # would manufacture another cast in otherwise neutral pixels.
                unmixed[index] = mean + color[index] - observed_mean
    red, green, blue = (min(255, max(0, round(value))) for value in unmixed)
    return coverage, (red, green, blue)


def unmix_key_blend(
    color: tuple[int, int, int],
    alpha: int,
    chroma_key: tuple[int, int, int],
    key_tint: float,
    tint: float,
) -> tuple[int, int, int, int]:
    """Separate a key/subject blend pixel into despilled RGB + partial alpha."""
    coverage, despilled = despill_color(color, chroma_key, key_tint, tint)
    out_alpha = round(alpha * coverage)
    if out_alpha <= 0:
        return (0, 0, 0, 0)
    return (*despilled, out_alpha)


# --- background key detection (RGB path) -------------------------------------
# Image models asked for #00FF00 / #FF00FF hand back a slightly different flat
# colour every run (measured 2026-09-11: Grok painted (8, 162, 24) for green).
# Its absolute distance to the pure key was 96.38 — a hair over the 96 hard-cut
# radius — so the whole background survived, while (7, 163, 24) at 95.34 keyed
# out. The rule below reads the *actual* background from the frame borders and,
# when that colour is the declared key's hue family, measures the hard cut from
# it as well. Raising the radius instead would only move the cliff to the next
# darker green; this removes the cliff for any brightness the model picks.
_KEY_FAMILY_MIN_KEYED = KEY_CHANNEL_DARK  # a keyed channel must at least read as lit (the key split's dark bar)
_KEY_FAMILY_MAX_UNKEYED_RATIO = 0.35  # unkeyed channels stay below this fraction of the dimmest keyed one
_KEY_FAMILY_KEYED_BALANCE = 0.8  # for two-channel keys (magenta) the dimmer keyed channel keeps this share of the brighter
# Border candidate (2026-09-12): Grok paints #FF00FF as (216, 46, 147) / (225, 52, 155) /
# (236, 59, 161) — blue/red ≈ 0.68, under the 0.8 balance — so `is_key_family` said "not
# the key" for a colour that filled the whole border, the detector fell back to the
# declared key, and at ~120 from pure magenta the entire background survived. A flat
# border is already evidence of *background*; the balance rule exists to keep hot pink
# (250, 77, 150) and purple (213, 112, 246) inside the subject alive, and those two fail
# the border rule on a different axis: their unkeyed channel is lit (77, 112 >= 64), the
# painted keys' never is (46..59). So the border rule keeps the hue signature — keyed
# channels lit, unkeyed channels dark and saturated against the *brighter* keyed
# channel — and drops the balance between the keyed channels.
# Leaning green (2026-10-21): the dark bar was set between magentas, whose one unkeyed
# channel is what tells a painted key from hot pink. Green has two, and a green screen
# leans on one of them — the studio chroma-key green #00B140 is (0, 177, 64), blue on the
# bar — so holding every unkeyed channel under the bar refused a whole painted background
# that leans a little further. For a key with two unkeyed channels the darker one keeps
# the bar, and the one the colour leans on only has to stay under half the brightest
# keyed channel: the hue then sits nearer the key than the neighbouring secondary (green
# 120 degrees, cyan 180 and yellow 60, the midpoints 150 and 90). A key with one unkeyed
# channel is unchanged — its darker and leaning channel are the same one, under the bar —
# so hot pink's lit green still refuses it. The lean is evidence about the background that
# leans, so inside the frame it widens only the ball of a painted colour that leans itself
# (`painted_key_leans`): on a background the rule took before, a leaning-green patch in
# the subject is not the painted colour's and stays. On the border it is read only where
# the rule before it finds no background and the declared key's own cut would leave one
# (`detect_background_key_rgb`): a border that rule took keeps the colour it found there,
# which does not lean, and keys as it did; so does a leaning border the key clears itself.
_BORDER_KEY_MAX_UNKEYED_RATIO = _KEY_FAMILY_MAX_UNKEYED_RATIO  # against the brightest keyed channel, not the dimmest
_BORDER_KEY_MAX_LEAN_RATIO = 0.5  # the unkeyed channel a key leans on stays under half the brightest keyed channel
_KEY_DETECT_CORNER_DIV = 5  # corner patch = width/5 x height/5 (same sampling as the YCbCr path)
_KEY_DETECT_MIN_FRACTION = 0.12  # family share of the samples needed to trust the detection
_KEY_DETECT_BIN_SHIFT = 3  # RGB histogram bin width 8 — the mode bin, never a mean, picks the colour
_KEY_DETECT_LEAN_LEFT_MIN_FRACTION = 0.02  # share of the samples the key's cut must leave by the mode bin to read the lean
_KEY_DETECT_NEIGHBOUR_BINS = 1  # bins either way on each channel that are still the mode bin's background
DEFAULT_KEY_THRESHOLD = 96.0  # `--key-threshold` default; the YCbCr path's decontam geometry reuses it


def is_key_family(color: tuple[int, int, int], chroma_key: tuple[int, int, int]) -> bool:
    """True when `color` is the key's hue at any brightness — a *variant of the key*.

    The *interior* rule: what a pixel with no border evidence has to be before
    it counts as key (the border rule is `is_border_key_candidate`).
    Brightness is free; saturation and hue are not. Every channel the key
    saturates must read as lit (>= 64), the channels the key leaves dark must
    stay under 35% of the dimmest keyed channel, and a two-channel key keeps
    its keyed channels within 20% of each other. (8, 162, 24) and (170, 8, 180)
    are family; hot pink (250, 77, 150), purple (213, 112, 246) and cyan are
    subject colours, not keys. Degenerate keys have no family.
    """
    keyed_channels, unkeyed_channels = key_channel_split(chroma_key)
    if not keyed_channels:
        return False
    keyed_min = min(color[index] for index in keyed_channels)
    keyed_max = max(color[index] for index in keyed_channels)
    unkeyed_max = max(color[index] for index in unkeyed_channels)
    return (
        keyed_min >= _KEY_FAMILY_MIN_KEYED
        and unkeyed_max <= keyed_min * _KEY_FAMILY_MAX_UNKEYED_RATIO
        and keyed_min >= keyed_max * _KEY_FAMILY_KEYED_BALANCE
    )


def is_border_key_candidate(color: tuple[int, int, int], chroma_key: tuple[int, int, int]) -> bool:
    """True when a colour *found on a flat border* can be the key as the model painted it.

    The border rule — one function for every entry point that classifies the
    border: `detect_background_key_rgb`, `cutout --key auto`, `video-canvas`
    and the `video-frames` edge-contact split. A superset of `is_key_family`:
    everything the interior rule admits, plus colours that carry the key's hue
    *signature* without the keyed channels balancing — every keyed channel lit
    (>= 64), the darkest unkeyed channel dark (< 64, the key split's own bar)
    and under 35% of the *brightest* keyed channel, and the brightest unkeyed
    channel — the one a green screen leans on — under half of it. So Grok's
    (216, 46, 147) magenta is a candidate, and so are greens leaning toward cyan
    or yellow such as (0, 177, 64) and (0, 200, 90); hot pink (250, 77, 150) and
    purple (213, 112, 246) are not (their green channel, magenta's only unkeyed
    one, is lit), nor are ivory and cyan. The interior rule stays
    `is_key_family` — a pixel *inside* the frame has no border evidence.
    """
    keyed_channels, unkeyed_channels = key_channel_split(chroma_key)
    if not keyed_channels:
        return False
    keyed_min = min(color[index] for index in keyed_channels)
    keyed_max = max(color[index] for index in keyed_channels)
    unkeyed_min = min(color[index] for index in unkeyed_channels)
    unkeyed_max = max(color[index] for index in unkeyed_channels)
    return is_key_family(color, chroma_key) or (
        keyed_min >= _KEY_FAMILY_MIN_KEYED
        and unkeyed_min < KEY_CHANNEL_DARK
        and unkeyed_min <= keyed_max * _BORDER_KEY_MAX_UNKEYED_RATIO
        and unkeyed_max < keyed_max * _BORDER_KEY_MAX_LEAN_RATIO
    )


def _key_family_field(rgb: np.ndarray, keyed_channels: list[int], unkeyed_channels: list[int]) -> np.ndarray:
    """Vector form of `is_key_family` — same three inequalities, same constants."""
    keyed_min = rgb[..., keyed_channels].min(axis=-1)
    keyed_max = rgb[..., keyed_channels].max(axis=-1)
    unkeyed_max = rgb[..., unkeyed_channels].max(axis=-1)
    return (
        (keyed_min >= _KEY_FAMILY_MIN_KEYED)
        & (unkeyed_max <= keyed_min * _KEY_FAMILY_MAX_UNKEYED_RATIO)
        & (keyed_min >= keyed_max * _KEY_FAMILY_KEYED_BALANCE)
    )


def border_key_candidate_field(rgb: np.ndarray, keyed_channels: list[int], unkeyed_channels: list[int], *,
                                lean: bool = True) -> np.ndarray:
    """Vector form of `is_border_key_candidate` — same inequalities, same constants.

    `lean=False` holds the brightest unkeyed channel to the darkest one's bars as well (every
    unkeyed channel dark): the signature as a painted colour that does not lean carries it
    (`painted_key_leans`), which is the border rule as it was before the lean.
    """
    keyed_min = rgb[..., keyed_channels].min(axis=-1)
    keyed_max = rgb[..., keyed_channels].max(axis=-1)
    unkeyed_min = rgb[..., unkeyed_channels].min(axis=-1)
    unkeyed_max = rgb[..., unkeyed_channels].max(axis=-1)
    signature = (
        (keyed_min >= _KEY_FAMILY_MIN_KEYED)
        & (unkeyed_min < KEY_CHANNEL_DARK)
        & (unkeyed_min <= keyed_max * _BORDER_KEY_MAX_UNKEYED_RATIO)
        & (unkeyed_max < keyed_max * _BORDER_KEY_MAX_LEAN_RATIO)
    )
    if not lean:
        signature &= (unkeyed_max < KEY_CHANNEL_DARK) & (unkeyed_max <= keyed_max * _BORDER_KEY_MAX_UNKEYED_RATIO)
    return _key_family_field(rgb, keyed_channels, unkeyed_channels) | signature


def painted_key_leans(painted_key: tuple[int, int, int], keyed_channels: list[int], unkeyed_channels: list[int]) -> bool:
    """Whether the painted colour is the key only through the lean: a green screen leaning toward cyan or yellow.

    The lean is border evidence about *that* background. What its ball claims inside the frame
    (`hard_key_mask`, `subject_window`) and which edge pixels read as leftover background
    (`video-frames`' edge-contact split) lean as far as the painted colour gives them leave to:
    on a background that leans, the colours that lean with it; on one that does not, only the
    signature with every unkeyed channel dark — so a leaning-green patch buried in the subject on
    a background the rule took before the lean stays as it was.

    A colour `detect_background_key_rgb` finds from the candidates before the lean never leans: a
    key with one unkeyed channel has no lean, and with two (green) the floor of a mean of one
    8-wide bin of them is one of them (asserted over every bin of green in
    tests/frames/test_chroma_key_relative.py). So on a border the rule took before the lean, every
    reader of the painted colour reads it as it did.
    """
    rgb = np.asarray([painted_key], dtype=np.int32)
    return bool(border_key_candidate_field(rgb, keyed_channels, unkeyed_channels)[0]
                and not border_key_candidate_field(rgb, keyed_channels, unkeyed_channels, lean=False)[0])


def _key_detect_sample_mask(height: int, width: int, top: int = 0, bottom: int | None = None) -> np.ndarray:
    """Corner patches (w/5 x h/5) plus the 1-px border — where the background lives.

    Rows `top` to `bottom` of the (height, width) mask, so an image can be sampled a band
    of rows at a time.
    """
    bottom = height if bottom is None else bottom
    mask = np.zeros((bottom - top, width), dtype=bool)
    if height == 0 or width == 0:
        return mask
    corner_w = width // _KEY_DETECT_CORNER_DIV
    corner_h = height // _KEY_DETECT_CORNER_DIV
    if corner_w < 2:
        corner_w = width
    if corner_h < 2:
        corner_h = height
    rows = np.arange(top, bottom)
    corner_rows = (rows < corner_h) | (rows >= height - corner_h)
    mask[corner_rows, :corner_w] = True
    mask[corner_rows, width - corner_w:] = True
    mask[(rows == 0) | (rows == height - 1), :] = True
    mask[:, 0] = mask[:, -1] = True
    return mask


_KEY_DETECT_BAND_ROWS = 64  # rows sampled at a time: a large still is never one int32 array


def _rgba_rows(image: Image.Image, top: int, bottom: int) -> np.ndarray:
    """Rows `top` to `bottom` of `image` as an RGBA uint8 array, converting only those rows."""
    rows = image.crop((0, top, image.width, bottom))
    return np.asarray(rows if rows.mode == "RGBA" else rows.convert("RGBA"))


def detect_background_key_rgb(
    image: Image.Image, chroma_key: tuple[int, int, int], threshold: float = DEFAULT_KEY_THRESHOLD
) -> tuple[int, int, int]:
    """The flat background colour as the model actually painted it, or the declared key.

    Samples the corner patches and the border, keeps the opaque pixels that
    carry the declared key's hue signature (`is_border_key_candidate` — the
    border rule, which unlike the interior `is_key_family` does not demand the
    keyed channels balance), and — when they make up at
    least 12% of the samples — returns the mean of the most populated 8-wide
    RGB histogram bin among them. The mode, never the mean of everything: a
    handful of antialiased fringe pixels on the border must not drag an
    exact-key background off the key, and a two-cluster border must resolve to
    the dominant cluster, not a midpoint that mattes neither. Otherwise — no
    such background, the subject crowds every border, a degenerate key — the
    declared key comes back unchanged, which keeps `remove_chroma_background`
    byte-identical to its pre-detection behaviour on exact-key backgrounds.

    The lean (`_BORDER_KEY_MAX_LEAN_RATIO`) is read second, and only where it is the
    difference. The candidates the rule admitted before it (`border_key_candidate_field(...,
    lean=False)`) are counted on their own: when they make up 12% of the samples, the answer
    is their mode bin's mean, byte for byte what the detector returned before the lean (a
    leaning sample in the bin of a background found before would move its mean, and the edge
    of the painted ball with it, past a subject on the radius). Otherwise the samples only the
    lean admits join them, and their mode bin's mean is the answer only where the declared
    key's own cut (`threshold`, the radius the caller keys with) leaves that background: the
    leaning samples farther than `threshold` from the key, in the mode bin or a bin beside it
    (`_KEY_DETECT_NEIGHBOUR_BINS`), make up 2% of the samples
    (`_KEY_DETECT_LEAN_LEFT_MIN_FRACTION`), and the candidates outside those bins stay under
    12%. A leaning border the key clears by itself keeps the key: the leaning colour's ball
    would reach no more of the background, only further into a subject near that colour. So
    does a border with a second key-coloured colour, which may be the subject's (a leaf on the
    edge, another green). The 2% keeps the soft rim where a subject meets an edge of the frame,
    its blend with the background crossing the cut there, from being read as background the
    cut left; a background whose tail past the cut is under 2% of the border keys as before the
    lean, the few pixels past the cut included. Every answer is the one the rule before the lean
    gives or the one it gives with the lean's samples read together.

    The image is read a band of rows at a time (any mode, each band converted to RGBA
    on its own) into a count and a channel sum per bin, so the corners of a large
    still cost a histogram, not an array of samples.
    """
    keyed_channels, unkeyed_channels = key_channel_split(chroma_key)
    if not keyed_channels:
        return tuple(chroma_key)  # type: ignore[return-value]
    width, height = image.size
    bits = 8 - _KEY_DETECT_BIN_SHIFT
    # one slot per bin, ordered by (R, G, B) bin: the lowest slot is the lowest bin; row 0 holds
    # the candidates the rule admitted before the lean, row 1 the ones only the lean admits, row 2
    # those of row 1 the declared key's cut leaves
    counts = np.zeros((3, 1 << (3 * bits)), dtype=np.int64)
    sums = np.zeros((2, 3, 1 << (3 * bits)), dtype=np.float64)  # integers far below 2**53: exact
    sampled = 0
    for top in range(0, height, _KEY_DETECT_BAND_ROWS):
        bottom = min(height, top + _KEY_DETECT_BAND_ROWS)
        band = _rgba_rows(image, top, bottom)
        sample = _key_detect_sample_mask(height, width, top, bottom) & (band[..., 3] != 0)
        rgb = band[sample][:, :3].astype(np.int32)
        sampled += len(rgb)
        before = border_key_candidate_field(rgb, keyed_channels, unkeyed_channels, lean=False)
        lean = border_key_candidate_field(rgb, keyed_channels, unkeyed_channels) & ~before
        left = lean & (key_distance_field(rgb, chroma_key) > threshold)
        for row, family in enumerate((before, lean, left)):
            colors = rgb[family]
            bins = colors >> _KEY_DETECT_BIN_SHIFT
            slots = (bins[:, 0] << (2 * bits)) | (bins[:, 1] << bits) | bins[:, 2]
            counts[row] += np.bincount(slots, minlength=counts.shape[1])
            if row < 2:  # the samples the cut leaves are only counted
                for channel in range(3):
                    sums[row, channel] += np.bincount(slots, weights=colors[:, channel], minlength=counts.shape[1])
    bar = sampled * _KEY_DETECT_MIN_FRACTION
    if not sampled or counts[:2].sum() < bar:
        return tuple(chroma_key)  # type: ignore[return-value]
    rows = 1 if counts[0].sum() >= bar else 2  # the candidates from before the lean, else with the lean's
    family_counts = counts[:rows].sum(axis=0)
    mode = int(np.argmax(family_counts))  # first max = lowest slot on a tie: deterministic
    if rows == 2:
        # the mode bin and the bins beside it on every channel: the one background the lean may name
        mask = (1 << bits) - 1
        slots = np.arange(counts.shape[1])
        near = np.ones(counts.shape[1], dtype=bool)
        for shift in (2 * bits, bits, 0):
            near &= np.abs(((slots >> shift) & mask) - ((mode >> shift) & mask)) <= _KEY_DETECT_NEIGHBOUR_BINS
        if counts[2][near].sum() < sampled * _KEY_DETECT_LEAN_LEFT_MIN_FRACTION or family_counts[~near].sum() >= bar:
            return tuple(chroma_key)  # type: ignore[return-value]
    members = int(family_counts[mode])
    return tuple(int(sums[:rows, channel, mode].sum()) // members for channel in range(3))  # type: ignore[return-value]


# remove_chroma_background pixel classes, decided once on the source colors.
_KEYED = 0  # erased: transparent input or hard key cut
_SUBJECT = 1  # not key-tinted — never touched
_BLEND_IN_BAND = 2  # key-tinted, within fringe_threshold of the key
_BLEND_OUT_OF_BAND = 3  # key-tinted, farther than fringe_threshold

# A trapped-spill cluster must contain at least one strongly key-tinted pixel
# to be treated. This is the plan's visible-residue detector (every keyed
# channel clears every unkeyed channel by >40): warm subject colors (skin)
# score a marginal tint just above fringe_delta and must not be "corrected".
_SPILL_MIN_TINT = 40.0
DEFAULT_UNMIX_REACH = 4


def hard_key_mask(source_rgb: np.ndarray, alpha: np.ndarray, chroma_key: tuple[int, int, int],
                  painted_key: tuple[int, int, int], threshold: float) -> tuple[np.ndarray, np.ndarray]:
    """The hard cut: (keyed_mask, key_distance) for an (H, W, 3) int32 source.

    `key_distance` is the distance to the declared key, lowered to the distance to the
    painted colour for the pixels that colour may claim; `keyed_mask` is transparent
    input plus everything within `threshold` of it. One definition for the RGB matte and
    for the decontam pass that runs after either matte.
    """
    keyed_channels, unkeyed_channels = key_channel_split(chroma_key)
    key_distance = key_distance_field(source_rgb, chroma_key)
    if painted_key != tuple(chroma_key):
        # The painted colour's authority is border evidence, and a pixel deep
        # inside the frame has none. So its ball erases the *background*: the
        # pixels that carry the key's hue signature themselves, plus whatever
        # else inside the ball touches that keyed region (the antialiased rim
        # of the subject, whose blend with a lit subject colour lifts the
        # unkeyed channel over the signature bar). An isolated patch inside
        # the subject that merely resembles the painted colour is left alone:
        # hot pink (250, 77, 150) sits 46 from Grok's (216, 46, 147) magenta
        # and would otherwise be cut out of the subject. The signature is the
        # one the painted colour carries: the leaning greens only when it leans
        # itself (`painted_key_leans`). The declared key's ball stays what it
        # always was — a colour ball, position-blind.
        painted_distance = key_distance_field(source_rgb, painted_key)
        in_ball = painted_distance <= threshold
        lean = painted_key_leans(painted_key, keyed_channels, unkeyed_channels)
        painted_keyed = in_ball & border_key_candidate_field(source_rgb, keyed_channels, unkeyed_channels, lean=lean)
        painted_keyed |= in_ball & (key_distance <= threshold)
        painted_keyed = grow_into(painted_keyed, in_ball)
        key_distance = np.where(painted_keyed, np.minimum(key_distance, painted_distance), key_distance)
    return (alpha == 0) | (key_distance <= threshold), key_distance


_WINDOW_BAND_ROWS = 64  # rows `subject_window` widens to int32 at a time


def subject_window(image: Image.Image, chroma_key: tuple[int, int, int], threshold: float, margin: int,
                   painted_key: tuple[int, int, int] | None = None) -> tuple[int, int, int, int] | None:
    """The box outside which `remove_chroma_background` erases every pixel, grown by `margin`.

    `hard_key_mask` keys, whatever their alpha, every pixel within `threshold` of the
    declared key and, when the painted colour differs, every pixel within `threshold` of
    the painted colour that carries the key's border signature (`is_border_key_candidate`,
    leaning only when the painted colour leans: `painted_key_leans`).
    Only the other pixels can stay opaque. Returns their bounding box grown by `margin`
    and clipped to the image, as (left, top, right, bottom), or None when there are none
    (the whole image keys out). Pass the painted colour the engine will use.

    Keying the crop instead of the image changes no pixel inside it, provided `margin` is
    at least 1 and the same painted colour, detected on the whole image, is passed as
    `background_key`. Every pixel of the margin is keyed and would seed the painted ball,
    so from any pixel inside, a keyed pixel beyond the crop is never nearer than the
    margin pixel on the way to it: the unmix depth, a fringe band counted on the result,
    and a painted-ball path all come out the same. Spill clusters and the subject count
    lie inside the box. A still that is mostly key, such as a canvas padded for motion
    room, is then keyed at the size of its subject.

    Scanned in bands of rows, each converted to RGBA on its own, so a large image is never
    converted or widened to int32 as a whole.
    """
    keyed_channels, unkeyed_channels = key_channel_split(chroma_key)
    ball = painted_key is not None and tuple(painted_key) != tuple(chroma_key) and bool(keyed_channels)
    lean = ball and painted_key_leans(painted_key, keyed_channels, unkeyed_channels)  # type: ignore[arg-type]
    width, height = image.size
    rows = np.zeros(height, dtype=bool)
    cols = np.zeros(width, dtype=bool)
    for top in range(0, height, _WINDOW_BAND_ROWS):
        rgb = _rgba_rows(image, top, min(height, top + _WINDOW_BAND_ROWS))[..., :3].astype(np.int32)
        erased = key_distance_field(rgb, chroma_key) <= threshold
        if ball:
            erased |= ((key_distance_field(rgb, painted_key) <= threshold)  # type: ignore[arg-type]
                       & border_key_candidate_field(rgb, keyed_channels, unkeyed_channels, lean=lean))
        rows[top:top + len(rgb)] = ~erased.all(axis=1)
        cols |= ~erased.all(axis=0)
    if not rows.any():
        return None
    ys = np.flatnonzero(rows)
    xs = np.flatnonzero(cols)
    return (max(0, int(xs[0]) - margin), max(0, int(ys[0]) - margin),
            min(width, int(xs[-1]) + 1 + margin), min(height, int(ys[-1]) + 1 + margin))


def key_material_pixels(image: Image.Image, chroma_key: tuple[int, int, int],
                        min_tint: float = _SPILL_MIN_TINT, *, require_hue: bool = False,
                        ignore_fringe: int = 0) -> tuple[int, int]:
    """Opaque pixels that are key-tinted past `min_tint` (the trapped-spill bar), and all
    opaque pixels, of an already keyed RGBA image. A still that has almost none carries no
    key-coloured material of its own.

    The bar is a parameter because the decision and the treatment have to read the same
    one: judging a reference at 40 and then despilling it down to 8 would call a subject
    with a mild green of its own "no key material" and then scrub that green away.
    `require_hue` uses channel excess rather than the mean-channel tint axis;
    `ignore_fringe` discounts dark pixels in that band beside transparency;
    bright key-coloured accents remain material evidence. The denominator stays
    total subject area."""
    keyed_channels, unkeyed_channels = key_channel_split(chroma_key)
    data = np.asarray(image.convert("RGBA")).astype(np.int32)
    opaque = data[..., 3] > 0
    if not keyed_channels:
        return 0, int(opaque.sum())
    field = key_excess_field if require_hue else key_tint_field
    tint = field(data[..., :3], keyed_channels, unkeyed_channels)
    # Discount only dark outline contamination in the matte's unmix band.
    # A bright green/magenta accent is material even when it is thin or on an edge.
    fringe = ~opaque
    for _ in range(ignore_fringe):
        fringe = grow_chebyshev(fringe)
    dark_fringe = fringe & (data[..., :3].max(axis=-1) < KEY_CHANNEL_DARK)
    return int((opaque & ~dark_fringe & (tint > min_tint)).sum()), int(opaque.sum())


def remove_chroma_background(
    image: Image.Image,
    chroma_key: tuple[int, int, int],
    threshold: float,
    fringe_threshold: float,
    fringe_delta: float,
    *,
    unmix_reach: int = DEFAULT_UNMIX_REACH,
    spill_max_fraction: float = 0.005,
    spill_min_tint: float = _SPILL_MIN_TINT,
    spill_require_hue: bool = False,
    background_key: tuple[int, int, int] | None = None,
    decontam: str = "off",
    decontam_fit: str = "still",
    decontam_palette: dict[str, Any] | None = None,
    decontam_stats: dict[str, Any] | None = None,
    decontam_edge_band: int = 0,
    decontam_history: dict[str, Any] | None = None,
    matte: dict[str, Any] | None = None,
) -> Image.Image:
    """Key `chroma_key` out of `image` (hard cut + soft-alpha fringe unmix + trapped-spill despill).

    `background_key`: the background colour as actually painted. None (the
    default) detects it from the borders with `detect_background_key_rgb`, at this
    call's `threshold`;
    passing `chroma_key` itself pins the single-key behaviour (the frozen
    byte-identity gate does that).

    `decontam="palette"` re-explains the edge afterwards with the subject's own
    colours (`sprite_gen.frames.decontam`; "auto" does so where it applies and reports
    why not elsewhere); "off" (the default) returns exactly what the passes above produce. `decontam_stats`, when given, receives what
    that pass did; `decontam_palette` reuses a palette learned elsewhere,
    `decontam_edge_band` keeps the frame edge as the matte left it (`decontaminate`'s `edge_band`),
    and `decontam_history` is a clip's record of its last frame for the video fit (`decontaminate`'s `history`).
    `matte` is `chroma_matte`'s result for this same image and these same arguments, computed
    elsewhere (a worker process): the passes before decontamination are then not run again.
    """
    if decontam != "off":
        decontam_module.validate(decontam, decontam_fit)
    rgba = image.convert("RGBA")
    if matte is None:
        matte = chroma_matte(rgba, chroma_key, threshold, fringe_threshold, fringe_delta, unmix_reach=unmix_reach,
                             spill_max_fraction=spill_max_fraction, spill_min_tint=spill_min_tint,
                             spill_require_hue=spill_require_hue, background_key=background_key)
    data = matte["data"]
    if decontam != "off":
        # a matte from a worker leaves the source out: it is this image's, and cheaper to read again than to send
        source_rgb = matte["source_rgb"] if "source_rgb" in matte else np.asarray(rgba)[..., :3].astype(np.int32)
        data, stats = decontam_module.decontaminate(source_rgb, data, matte["keyed_mask"], chroma_key,
                                                    fit=decontam_fit, alpha_depth=unmix_reach,
                                                    palette=decontam_palette, mode=decontam,
                                                    source_alpha=matte["source_alpha"], edge_band=decontam_edge_band,
                                                    history=decontam_history, prepared=matte.get("decontam"))
        if decontam_stats is not None:
            decontam_stats.update(stats)
    # Back into the converted copy rather than a fresh Image.fromarray, so the
    # returned image keeps the mode, size and `info` (icc profile, dpi) that
    # `convert` carried over from the caller's image.
    rgba.frombytes(data.tobytes())
    return rgba


def chroma_matte(
    rgba: Image.Image,
    chroma_key: tuple[int, int, int],
    threshold: float,
    fringe_threshold: float,
    fringe_delta: float,
    *,
    unmix_reach: int = DEFAULT_UNMIX_REACH,
    spill_max_fraction: float = 0.005,
    spill_min_tint: float = _SPILL_MIN_TINT,
    spill_require_hue: bool = False,
    background_key: tuple[int, int, int] | None = None,
) -> dict[str, Any]:
    """`remove_chroma_background`'s passes before decontamination, on an RGBA image: `data` (H, W, 4)
    the keyed frame, `source_rgb` and `source_alpha` the frame as given, `keyed_mask` the hard cut.
    They read nothing but the image and the arguments, so a clip's frames can be matted in any order."""
    width, height = rgba.size
    data = np.array(rgba, dtype=np.uint8)  # (H, W, 4)
    source_rgb = data[..., :3].astype(np.int32)
    source_alpha = data[..., 3].copy()  # coverage decontam may give back
    keyed_channels, unkeyed_channels = key_channel_split(chroma_key)
    unseen = 255

    # The background as painted. Distances are taken to the declared key *and*
    # to the detected family colour and the smaller one wins, so an exact-key
    # background reproduces the single-key result bit for bit while a darker or
    # paler key of the same hue is cut from where it actually sits. The blend
    # model (despill / unmix) also mixes against the painted colour — that is
    # the colour the antialiased fringe was actually blended with. Which
    # channels count as "keyed" stays a property of the declared key.
    painted_key = (
        detect_background_key_rgb(rgba, chroma_key, threshold)
        if background_key is None
        else (int(background_key[0]), int(background_key[1]), int(background_key[2]))
    )

    # Classification, decided on the source colors before anything is erased.
    # np.select takes the first condition that holds, so the condlist order below
    # *is* the if/elif order it replaces — swapping the subject and in-band rows
    # changes the output wherever both hold at once (a wide --fringe-key-threshold
    # makes that reachable, and the gate has a case for it).
    keyed_mask, key_distance = hard_key_mask(source_rgb, data[..., 3], chroma_key, painted_key, threshold)
    source_tint = key_tint_field(source_rgb, keyed_channels, unkeyed_channels)
    classes = np.select(
        [keyed_mask, source_tint < fringe_delta, key_distance <= fringe_threshold],
        [_KEYED, _SUBJECT, _BLEND_IN_BAND],
        default=_BLEND_OUT_OF_BAND,
    ).astype(np.uint8)
    data[keyed_mask] = 0

    depths = np.full((height, width), unseen, dtype=np.uint8)  # chebyshev distance to keyed region
    depths[keyed_mask] = 0

    key_tint = key_tint_score(painted_key, chroma_key)
    max_reach = min(unseen - 1, unmix_reach if key_tint > 0 else 0)

    # Geometric distance to the nearest keyed-out pixel — outer background
    # *and* interior holes (hair gaps) alike. This walk is not blocked by
    # subject pixels, so an isolated key blend locked inside subject material
    # still gets a depth. Growing the keyed set one ring at a time numbers the
    # rings in the order a breadth-first walk would reach them.
    frontier = keyed_mask
    reached = keyed_mask
    depth = 0
    while frontier.any() and depth < max_reach:
        depth += 1
        frontier = grow_chebyshev(frontier) & ~reached
        depths[frontier] = depth
        reached = reached | frontier

    # Soft-alpha unmix — binary erase cannot represent antialiased coverage.
    # Any key-tinted pixel within unmix_reach of the keyed region is separated
    # into despilled RGB + partial alpha instead:
    #   - out-of-band blends always (they are too subject-heavy to erase);
    #   - in-band blends only within the AA band nearest the key. Deeper
    #     key-tinted material stays byte-identical (v1.10.1 guardrail).
    # Only the selection is vectorized. The pixels that survive it are the AA
    # fringe — a thin minority — so the blend itself stays on the scalar
    # primitives that despill_color and unmix_key_blend already own.
    if key_tint > 0 and unmix_reach > 0:
        in_reach = (depths > 0) & (depths <= min(unmix_reach, unseen))
        unmixable = in_reach & (
            ((classes == _BLEND_IN_BAND) & (depths <= IN_BAND_UNMIX_KEY_DEPTH))
            | (classes == _BLEND_OUT_OF_BAND)
        )
        rows, cols = np.nonzero(unmixable)
        for y, x in zip(rows.tolist(), cols.tolist()):
            red, green, blue, alpha = (int(value) for value in data[y, x])
            color = (red, green, blue)
            out_red, out_green, out_blue, out_alpha = unmix_key_blend(
                color, alpha, painted_key, key_tint, key_tint_score(color, chroma_key)
            )
            # The scalar path wrote this through `pixels[x, y] = (...)`, and PIL
            # clamped every channel to 0..255 on the way in. A uint8 array does
            # not — it raises — so the clamp has to be restored here or the
            # rewrite changes behavior instead of just its speed.
            #
            # Exactly one channel needs it. The three color channels leave
            # `despill_color` already squeezed by its own `min(255, max(0, …))`,
            # and re-clamping them here would put that bound in two places; the
            # alpha is the one value nothing bounds, because `coverage` exceeds
            # 1 whenever `tint` is negative (reachable with `--fringe-delta < 0`)
            # and `round(alpha * coverage)` then lands above `alpha`. It cannot
            # go the other way: `unmix_key_blend` returns a fully transparent
            # pixel for `out_alpha <= 0`, so the only unbounded direction is up.
            # Both premises are asserted in tests/test_chroma_rcb_byte_identity.py.
            data[y, x] = (out_red, out_green, out_blue, min(255, out_alpha))

    # Trapped-spill despill — generators paint key-colored spill *inside* the
    # subject (a green streak buried in crimson hair, key reflections between
    # strands) too far from any keyed pixel for depth-based treatment to
    # reach. Among the still-tinted pixels left after the passes above, a
    # small connected cluster is spill; a large one is intentional key-tinted
    # material (the hot-pink seed packet) and stays untouched. Spill keeps its
    # alpha — it sits inside opaque subject, so this is color correction, not
    # coverage: partial alpha here would punch pinholes through the sprite.
    if key_tint > 0 and keyed_mask.any() and spill_max_fraction > 0:
        subject_count = int(np.count_nonzero(~keyed_mask))
        spill_limit = max(32, round(subject_count * spill_max_fraction))
        # Re-scored on the *current* colors: the unmix pass above rewrote part of
        # the image, and a pixel it despilled is no longer a spill candidate.
        spill_field = key_excess_field if spill_require_hue else key_tint_field
        current_tint = spill_field(data[..., :3].astype(np.int32), keyed_channels, unkeyed_channels)
        # Candidacy and acceptance read the same bar: a cluster can never be accepted
        # below `spill_min_tint`, so admitting only pixels at or above `fringe_delta`
        # would silently keep the lowered bar from reaching anything when it is the
        # smaller of the two.
        candidate_tint = min(fringe_delta, spill_min_tint)
        candidates = np.flatnonzero(
            ((data[..., 3] != 0) & (current_tint >= candidate_tint)).reshape(-1)
        )
        tints_left: dict[int, float] = dict(
            zip(candidates.tolist(), current_tint.reshape(-1)[candidates].tolist())
        )
        visited: set[int] = set()
        for start in tints_left:
            if start in visited:
                continue
            stack = [start]
            visited.add(start)
            cluster = []
            while stack:
                index = stack.pop()
                cluster.append(index)
                x = index % width
                y = index // width
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if 0 <= x + dx < width and 0 <= y + dy < height:
                            neighbor = (y + dy) * width + (x + dx)
                            if neighbor in tints_left and neighbor not in visited:
                                visited.add(neighbor)
                                stack.append(neighbor)
            if len(cluster) > spill_limit:
                continue
            if max(tints_left[index] for index in cluster) <= spill_min_tint:
                continue
            for index in cluster:
                x = index % width
                y = index // width
                red, green, blue, alpha = (int(value) for value in data[y, x])
                color = (red, green, blue)
                # Hue excess decides *whether* this is spill. It is nonlinear
                # and cannot estimate a blend fraction: G-max(R,B) drives G
                # to the larger channel, turning a slight blue bias into cyan.
                # Use the linear tint axis for unmixing and bound the gain on
                # the remaining chroma when treating opaque full-spill pixels.
                coverage, despilled = despill_color(
                    color, painted_key, key_tint,
                    key_tint_score(color, chroma_key) if spill_require_hue else tints_left[index],
                    chroma_groups=(keyed_channels, unkeyed_channels) if spill_require_hue else None,
                )
                if coverage > 0:
                    data[y, x] = (*despilled, alpha)
    return {"data": data, "source_rgb": source_rgb, "source_alpha": source_alpha, "keyed_mask": keyed_mask}
