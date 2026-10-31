# SPDX-License-Identifier: Apache-2.0
"""Chroma-key matting in YCbCr chrominance (the opt-in `--chroma-mode ycbcr` path).

Moved out of `extract.py` unchanged; `extract` re-exports every name, so `extract.<name>` still works."""

from __future__ import annotations

import math
import sys
from typing import Any, cast

from PIL import Image, ImageChops

from sprite_gen._deps import np
from sprite_gen.frames import decontam as decontam_module
from sprite_gen.frames.chroma_key import (
    DEFAULT_KEY_THRESHOLD,
    DEFAULT_UNMIX_REACH,
    detect_background_key_rgb,
    hard_key_mask,
)

# --- YCbCr chrominance matting (opt-in) --------------------------------------
# Port of the chrominance-plane matting from perfectpixel-studio
# (https://github.com/gykim80/perfectpixel-studio, internal/sprite/chroma.go),
# Copyright Andrew Kim (gykim80), MIT License. See NOTICE.
#
# The default path above classifies pixels by RGB distance to the key, so
# background shading and JPEG 4:2:0 chroma subsampling can push background
# pixels past the erase radius and leave fringe. This path drops luma entirely
# and separates on the CbCr plane: shading moves Y, not CbCr, so the key stays
# one tight chroma cluster. Selected with request `chroma.mode: "ycbcr"` or
# `--chroma-mode ycbcr`; the default "rgb" path stays byte-identical.

_YCC_CHROMA_IN = 24.0  # CbCr distance at/below → fully transparent (key)
_YCC_CHROMA_OUT = 72.0  # CbCr distance at/above → fully opaque (subject)
_YCC_DESPILL_BAND = 100.0  # despill pixels whose CbCr distance is inside this
_YCC_DESPILL_SCALE = 0.92  # despill strength (key-direction chroma suppression)
_YCC_FLOOD_TOL = 88.0  # border flood fill background tolerance (CbCr, lenient)
_YCC_ALPHA_EMPTY = 10  # alpha at/below counts as an empty pixel
_YCC_KEY_RESIDUE_DIST = 55.0  # residue metric radius around the declared key
_YCC_KEY_BIAS_FRACTION = 0.12  # declared-key border share that overrides the mode
_YCC_REMATTE_OPAQUE_FRAC = 0.60  # opaque-ratio spike → key likely mis-detected
_YCC_REMATTE_RESIDUE_FRAC = 0.025  # declared-key residue spike → matting incomplete


def rgb_to_ycc(red: int, green: int, blue: int) -> tuple[float, float, float]:
    """BT.601 YCbCr, 8-bit range, chroma centered on 128."""
    luma = 0.299 * red + 0.587 * green + 0.114 * blue
    return luma, (blue - luma) * 0.564 + 128.0, (red - luma) * 0.713 + 128.0


def _u8(value: float) -> int:
    if value <= 0:
        return 0
    if value >= 255:
        return 255
    return int(value + 0.5)


def ycc_to_rgb(luma: float, cb: float, cr: float) -> tuple[int, int, int]:
    return (
        _u8(luma + 1.402 * (cr - 128.0)),
        _u8(luma - 0.344136 * (cb - 128.0) - 0.714136 * (cr - 128.0)),
        _u8(luma + 1.772 * (cb - 128.0)),
    )


def smoothstep(edge0: float, edge1: float, x: float) -> float:
    """Hermite 0→1 transition (soft matte edge feathering)."""
    if edge1 <= edge0:
        return 0.0
    t = (x - edge0) / (edge1 - edge0)
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return t * t * (3.0 - 2.0 * t)


def detect_background_key_ycc(
    image: Image.Image, declared_key: tuple[int, int, int]
) -> tuple[int, int, int]:
    """Estimate the background key from corner patches + thin borders.

    Mode of a quantized CbCr histogram — never a mean: a mean drifts on
    gradient/compression noise, the mode locks onto the dominant chroma
    cluster. Wide poses (walk cycles) touch the borders and can crowd the
    histogram with subject colors, so when a sufficient share of the samples
    sits in the declared key's chroma family, that cluster wins outright
    (the original biases toward its always-magenta key the same way).
    """
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    width, height = rgba.size
    if width == 0 or height == 0:
        return declared_key
    pixels: Any = rgba.load()
    _, declared_cb, declared_cr = rgb_to_ycc(*declared_key)
    bins: dict[int, list[int]] = {}
    total = 0
    key_family = [0, 0, 0, 0]  # count, sum r, sum g, sum b

    def visit(x: int, y: int) -> None:
        nonlocal total
        red, green, blue = pixels[x, y][:3]
        total += 1
        _, cb, cr = rgb_to_ycc(red, green, blue)
        if math.hypot(cb - declared_cb, cr - declared_cr) < _YCC_KEY_RESIDUE_DIST:
            key_family[0] += 1
            key_family[1] += red
            key_family[2] += green
            key_family[3] += blue
        slot = (int(cb) >> 3 << 6) | (int(cr) >> 3)
        acc = bins.get(slot)
        if acc is None:
            acc = [0, 0, 0, 0]
            bins[slot] = acc
        acc[0] += 1
        acc[1] += red
        acc[2] += green
        acc[3] += blue

    corner_w, corner_h = width // 5, height // 5
    if corner_w < 2:
        corner_w = width
    if corner_h < 2:
        corner_h = height
    for x0, y0, x1, y1 in (
        (0, 0, corner_w, corner_h),
        (width - corner_w, 0, width, corner_h),
        (0, height - corner_h, corner_w, height),
        (width - corner_w, height - corner_h, width, height),
    ):
        for y in range(y0, y1):
            for x in range(x0, x1):
                visit(x, y)
    for x in range(width):
        visit(x, 0)
        visit(x, height - 1)
    for y in range(height):
        visit(0, y)
        visit(width - 1, y)

    if total > 0 and key_family[0] * 100 >= total * int(_YCC_KEY_BIAS_FRACTION * 100):
        count = key_family[0]
        return (key_family[1] // count, key_family[2] // count, key_family[3] // count)
    best = max(bins.values(), key=lambda acc: acc[0], default=None)
    if best is None or best[0] == 0:
        return declared_key
    return (best[1] // best[0], best[2] // best[0], best[3] // best[0])


def _flood_clear_background_ycc(out: Image.Image, source: Image.Image, key_cb: float, key_cr: float) -> None:
    """4-connected flood fill from the borders clearing key-chroma pixels.

    Connectivity is what preserves the subject: an interior pixel whose chroma
    happens to sit near the key (isolated highlight, gem) never connects to
    the border and survives; a gradient/noisy background the soft matte could
    not fully erase is border-connected and gets cleared.
    """
    width, height = source.size
    if width < 3 or height < 3:
        return
    src_pixels: Any = source.load()
    visited = bytearray(width * height)
    stack: list[int] = []
    # floodable(rgb) 는 픽셀값의 순수 함수 (키·톨은 고정) — 색값으로 메모이즈하면
    # 지배적인 단색 배경의 float 판정(rgb→ycc, hypot)을 색당 한 번만 돈다. 판정
    # 결과가 동일하므로 방문 집합·클리어 결과가 **바이트 동일**하다.
    flood_cache: dict[tuple[int, int, int], bool] = {}

    def push(x: int, y: int) -> None:
        position = y * width + x
        if visited[position]:
            return
        rgb = src_pixels[x, y][:3]
        ok = flood_cache.get(rgb)
        if ok is None:
            _, cb, cr = rgb_to_ycc(rgb[0], rgb[1], rgb[2])
            ok = math.hypot(cb - key_cb, cr - key_cr) <= _YCC_FLOOD_TOL
            flood_cache[rgb] = ok
        if ok:
            visited[position] = 1
            stack.append(position)

    for x in range(width):
        push(x, 0)
        push(x, height - 1)
    for y in range(height):
        push(0, y)
        push(width - 1, y)
    # 클리어 대상 = 방문 집합과 정확히 동일 (모든 방문 픽셀이 정확히 한 번 pop 되어
    # 알파 0 이 된다). 그래서 루프에서 픽셀별로 지우지 않고 방문 집합만 확정한 뒤
    # 마지막에 PIL 마스크로 한 번에 지운다 — RGB 불변, ~90만 PixelAccess 왕복 제거.
    while stack:
        position = stack.pop()
        x, y = position % width, position // width
        if x > 0:
            push(x - 1, y)
        if x < width - 1:
            push(x + 1, y)
        if y > 0:
            push(x, y - 1)
        if y < height - 1:
            push(x, y + 1)
    # 방문 집합을 마스크로 알파만 0 으로 (RGB 불변). 원본의 픽셀별 클리어와 동일 결과.
    # visited 는 0/1 → 하드 마스크로 쓰려면 0/255 로 (paste 마스크는 알파 블렌드).
    clear_mask = Image.frombuffer("L", (width, height), bytes(visited), "raw", "L", 0, 1)
    clear_mask = clear_mask.point(lambda v: 255 if v else 0)
    alpha = out.getchannel("A")
    alpha.paste(0, mask=clear_mask)
    out.putalpha(alpha)


def _matte_ycc(source: Image.Image, key: tuple[int, int, int]) -> tuple[Image.Image, float]:
    """Soft-matte `source` against `key` on the CbCr plane.

    Returns the matted RGBA image and its opaque-pixel fraction (the
    self-diagnostic input: a mis-detected key erases the subject instead of
    the background and the fraction spikes).
    """
    width, height = source.size
    out = Image.new("RGBA", source.size, (0, 0, 0, 0))
    _, key_cb, key_cr = rgb_to_ycc(*key)
    key_vb, key_vr = key_cb - 128.0, key_cr - 128.0
    key_len = math.hypot(key_vb, key_vr)
    # 핫스팟 (상태당 추출의 ~1/3, 1280×720 전 픽셀). 매트 출력은 픽셀값 (r,g,b) 의
    # 순수 함수(키는 고정) — 크로마 배경 스프라이트는 고유색이 적어 색값으로
    # 메모이즈하면 같은 float 연산을 색당 한 번만 돈다. 산술은 **바이트 동일**.
    # 캐시값: (despill 후 r, g, b, coverage). coverage<=0 이면 원본과 같이 건너뛴다.
    cache: dict[tuple[int, int, int], tuple[int, int, int, float]] = {}
    out_data = [(0, 0, 0, 0)] * (width * height)
    src = cast(tuple[tuple[int, ...], ...], source.get_flattened_data())
    for i, px in enumerate(src):
        alpha = px[3]
        if alpha == 0:
            continue
        rgb = (px[0], px[1], px[2])
        hit = cache.get(rgb)
        if hit is None:
            red, green, blue = rgb
            luma, cb, cr = rgb_to_ycc(red, green, blue)
            dist = math.hypot(cb - key_cb, cr - key_cr)
            coverage = smoothstep(_YCC_CHROMA_IN, _YCC_CHROMA_OUT, dist)
            if coverage <= 0:
                hit = (0, 0, 0, coverage)
            else:
                if key_len > 1 and dist < _YCC_DESPILL_BAND:
                    # Despill: subtract only the key-direction chroma component —
                    # colors orthogonal to the key keep their saturation.
                    pixel_vb, pixel_vr = cb - 128.0, cr - 128.0
                    proj = (pixel_vb * key_vb + pixel_vr * key_vr) / key_len
                    if proj > 0:
                        weight = smoothstep(0.0, 1.0, (_YCC_DESPILL_BAND - dist) / _YCC_DESPILL_BAND) * _YCC_DESPILL_SCALE
                        cb = 128.0 + (pixel_vb - key_vb / key_len * proj * weight)
                        cr = 128.0 + (pixel_vr - key_vr / key_len * proj * weight)
                        red, green, blue = ycc_to_rgb(luma, cb, cr)
                hit = (red, green, blue, coverage)
            cache[rgb] = hit
        cov = hit[3]
        if cov <= 0:
            continue
        out_data[i] = (hit[0], hit[1], hit[2], int(alpha * cov))
    out.putdata(out_data)
    _flood_clear_background_ycc(out, source, key_cb, key_cr)
    opaque = sum(out.getchannel("A").histogram()[_YCC_ALPHA_EMPTY + 1 :])
    frac = opaque / (width * height) if width and height else 0.0
    return out, frac


def key_residue_fraction_ycc(image: Image.Image, key: tuple[int, int, int]) -> float:
    """Fraction of pixels still opaque within the key's chroma family.

    Symptom metric for "the matte did not finish removing the background".
    """
    width, height = image.size
    if not width or not height:
        return 0.0
    _, key_cb, key_cr = rgb_to_ycc(*key)
    pixels: Any = image.load()
    count = 0
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            if alpha <= _YCC_ALPHA_EMPTY:
                continue
            _, cb, cr = rgb_to_ycc(red, green, blue)
            if math.hypot(cb - key_cb, cr - key_cr) < _YCC_KEY_RESIDUE_DIST:
                count += 1
    return count / (width * height)


def _neighbour_code(op: Image.Image) -> Image.Image:
    """`op` (1 where opaque, 0 elsewhere) as one code per pixel: op*16 plus how many of its eight
    neighbours are opaque (outside the image counts as clear)."""
    width, height = op.size
    # PIL 네이티브 스텐실 — 8이웃 불투명 카운트를 시프트-합으로 벡터화한다 (핫스팟:
    # 상태당 추출의 ~17%, 1280×720 전체 순회). 정수 논리(임계 비교·이웃 카운트)라
    # 픽셀 루프와 **바이트 동일**하며, numpy 없이 pillow 만으로 된다.
    # 1px zero-pad 후 8방향 크롭으로 이웃을 꺼내 합산 (경계 밖 = 0, 정수 덧셈은 순서 무관).
    padded = Image.new("L", (width + 2, height + 2), 0)
    padded.paste(op, (1, 1))
    neighbors: Any = None
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1),
                   (-1, -1), (1, -1), (-1, 1), (1, 1)):
        shifted = padded.crop((1 + dx, 1 + dy, 1 + dx + width, 1 + dy + height))
        neighbors = shifted if neighbors is None else ImageChops.add(neighbors, shifted)
    # combined = op*16 + neighbors (둘 다 작아 24 이하) → 단일 LUT 로 마스크를 분리한다.
    return ImageChops.add(op.point(lambda v: v * 16), neighbors)


def _dots(code: Image.Image) -> Image.Image:
    return code.point(lambda v: 255 if (v >> 4) == 1 and (v & 15) == 0 else 0)


def isolated_dots(op: Image.Image) -> Image.Image:
    """The lone pixels of `op` (an L mask, 1 where opaque): 255 where a pixel is opaque and none of
    its eight neighbours is — one touching another only at a corner is joined to it. One judgment:
    the YCbCr matte's cleanup erases a matte's dots by it (`_cleanup_alpha_ycc`), and `pixel-draw`'s
    G5 counts a grid's stray pixels by it."""
    return _dots(_neighbour_code(op))


def _cleanup_alpha_ycc(image: Image.Image) -> None:
    """Remove fully isolated opaque dots and fill nearly-enclosed pinholes.

    Decisions are made on an alpha snapshot so the pass cannot cascade; soft
    matte edges are untouched (only 0-neighbor dots — `isolated_dots` — and
    ≥7-neighbor holes).
    """
    width, height = image.size
    if width < 3 or height < 3:
        return
    alpha = image.getchannel("A")
    # op: 불투명 마스크 (0/1) — before 스냅샷과 같은 기준 (> EMPTY).
    op = alpha.point(lambda a: 1 if a > _YCC_ALPHA_EMPTY else 0)
    combined = _neighbour_code(op)
    clear_mask = _dots(combined)
    fill_mask = combined.point(lambda v: 255 if (v >> 4) == 0 and (v & 15) >= 7 else 0)
    # 원본은 알파만 바꾼다 (RGB 불변) — 스냅샷에서 시작해 두 disjoint 마스크만 덮어쓴다.
    new_alpha = alpha.copy()
    new_alpha.paste(0, mask=clear_mask)
    new_alpha.paste(255, mask=fill_mask)
    image.putalpha(new_alpha)


def remove_chroma_background_ycbcr(
    image: Image.Image,
    chroma_key: tuple[int, int, int],
    warnings: list[str] | None = None,
    *,
    decontam: str = "off",
    decontam_fit: str = "still",
    decontam_palette: dict[str, Any] | None = None,
    decontam_stats: dict[str, Any] | None = None,
) -> Image.Image:
    """Chrominance-plane matting with self-diagnostic pure-key rematte.

    Pipeline: detect the background key from the borders (CbCr histogram
    mode), soft-matte + despill + border flood fill, then check the two
    mis-detection symptoms — opaque-fraction spike (subject erased instead of
    background) and declared-key residue spike (background survived). Either
    symptom triggers a rematte with the declared pure key; the better result
    wins and the fallback is reported through `warnings` (observable, never
    silent).

    `decontam="palette"` then re-explains the edge exactly as the RGB path does,
    over this matte's output; its geometry is the RGB path's hard cut at the
    default `--key-threshold`, so both mattes share one definition of the edge.
    """
    if decontam != "off":
        decontam_module.validate(decontam, decontam_fit)

    def note(message: str) -> None:
        if warnings is not None:
            warnings.append(message)
        print(f"[chroma-ycbcr] {message}", file=sys.stderr)

    rgba = image.convert("RGBA")
    detected = detect_background_key_ycc(rgba, chroma_key)
    out, opaque_frac = _matte_ycc(rgba, detected)
    residue = key_residue_fraction_ycc(out, chroma_key)
    used_declared = detected == tuple(chroma_key)

    if not used_declared and (
        opaque_frac > _YCC_REMATTE_OPAQUE_FRAC or residue > _YCC_REMATTE_RESIDUE_FRAC
    ):
        retry, retry_frac = _matte_ycc(rgba, chroma_key)
        retry_residue = key_residue_fraction_ycc(retry, chroma_key)
        better_frac = retry_frac < opaque_frac - 0.03 and retry_frac > 0.02
        less_residue = retry_residue < residue
        if (better_frac or less_residue) and retry_frac > 0.02:
            note(
                f"detected key {detected} rejected (opaque {opaque_frac:.3f}, "
                f"residue {residue:.4f}) — rematted with declared key {tuple(chroma_key)}"
            )
            out, opaque_frac, residue = retry, retry_frac, retry_residue
            used_declared = True

    if not used_declared:
        # Detected key outside the declared key's chroma family (dark/neutral
        # border mis-read): also try the declared pure key, keep the cleaner.
        _, detected_cb, detected_cr = rgb_to_ycc(*detected)
        _, declared_cb, declared_cr = rgb_to_ycc(*chroma_key)
        if math.hypot(detected_cb - declared_cb, detected_cr - declared_cr) > _YCC_KEY_RESIDUE_DIST:
            retry, retry_frac = _matte_ycc(rgba, chroma_key)
            if retry_frac > 0.02 and key_residue_fraction_ycc(retry, chroma_key) < residue:
                note(
                    f"detected key {detected} outside declared key family — "
                    f"rematted with declared key {tuple(chroma_key)}"
                )
                out = retry

    _cleanup_alpha_ycc(out)
    if decontam != "off":
        source = np.array(rgba, dtype=np.uint8)
        source_rgb = source[..., :3].astype(np.int32)
        painted = detect_background_key_rgb(rgba, chroma_key, DEFAULT_KEY_THRESHOLD)
        keyed_mask, _ = hard_key_mask(source_rgb, source[..., 3], chroma_key, painted, DEFAULT_KEY_THRESHOLD)
        data, stats = decontam_module.decontaminate(source_rgb, np.array(out, dtype=np.uint8), keyed_mask, chroma_key,
                                                    fit=decontam_fit, alpha_depth=DEFAULT_UNMIX_REACH,
                                                    palette=decontam_palette, mode=decontam,
                                                    source_alpha=source[..., 3])
        if decontam_stats is not None:
            decontam_stats.update(stats)
        out.frombytes(data.tobytes())
    return out
