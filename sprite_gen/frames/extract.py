# SPDX-License-Identifier: Apache-2.0
# Modified for sprite-gen-agent (pose-plan GPT motion; split into chroma_key, chroma_ycc, pixel_grid and
# frames_manifest; see NOTICE).
"""Extract component-row sprite strips into clean RGBA frames."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
from pathlib import Path
from statistics import median
from typing import Any

from PIL import Image

from sprite_gen.util.cli_kwargs import namespace_from_kwargs
from sprite_gen._deps import np
from sprite_gen.curate.curation import effective_logical_height, pixel_snap_scale
from sprite_gen.spec.layout import frames_dir_rel, raw_rel, take_raw_rel
from sprite_gen.spec.runio import (REQUEST_FILENAME, acquire_run_dir_lock, atomic_save_image,
                              atomic_write_text, load_request, publish_guard, release_run_dir_lock)
from sprite_gen.frames import decontam as decontam_module
from sprite_gen.frames.keyfield import key_channel_split, key_distance_field  # key_channel_split: tests import it from here
from sprite_gen.frames.segment import separate_fused_poses
from sprite_gen.spec.subject import (SUBJECT_DEFAULT, cell_number, default_min_used_pixels, sparse_frame_error,
                                     subject_kind)
from sprite_gen.util.resample import resize_cell
from sprite_gen.util.console import utf8_streams
from sprite_gen import local_tool
from sprite_gen.frames.chroma_key import (  # moved out of this file; every name stays reachable here
    BORDER_MIN_FRACTION,
    DEFAULT_KEY_THRESHOLD,
    DEFAULT_UNMIX_REACH,
    FRAME_SIDES,
    _BLEND_IN_BAND,
    _BLEND_OUT_OF_BAND,
    _BORDER_KEY_MAX_LEAN_RATIO,
    _BORDER_KEY_MAX_UNKEYED_RATIO,
    _KEYED,
    _KEY_DETECT_BAND_ROWS,
    _KEY_DETECT_BIN_SHIFT,
    _KEY_DETECT_CORNER_DIV,
    _KEY_DETECT_LEAN_LEFT_MIN_FRACTION,
    _KEY_DETECT_MIN_FRACTION,
    _KEY_DETECT_NEIGHBOUR_BINS,
    _KEY_FAMILY_KEYED_BALANCE,
    _KEY_FAMILY_MAX_UNKEYED_RATIO,
    _KEY_FAMILY_MIN_KEYED,
    _SPILL_MIN_TINT,
    _SUBJECT,
    _WINDOW_BAND_ROWS,
    _key_detect_sample_mask,
    _key_family_field,
    _rgba_rows,
    alpha_nonzero_count,
    border_key_candidate_field,
    chroma_matte,
    color_distance,
    despill_color,
    detect_background_key_rgb,
    edge_alpha_count,
    hard_key_mask,
    is_border_key_candidate,
    is_key_family,
    key_material_pixels,
    key_tint_score,
    own_border,
    painted_key_leans,
    remove_chroma_background,
    subject_window,
    unmix_key_blend,
)
from sprite_gen.frames.chroma_ycc import (  # moved out of this file; every name stays reachable here
    _YCC_ALPHA_EMPTY,
    _YCC_CHROMA_IN,
    _YCC_CHROMA_OUT,
    _YCC_DESPILL_BAND,
    _YCC_DESPILL_SCALE,
    _YCC_FLOOD_TOL,
    _YCC_KEY_BIAS_FRACTION,
    _YCC_KEY_RESIDUE_DIST,
    _YCC_REMATTE_OPAQUE_FRAC,
    _YCC_REMATTE_RESIDUE_FRAC,
    _cleanup_alpha_ycc,
    _dots,
    _flood_clear_background_ycc,
    _matte_ycc,
    _neighbour_code,
    _u8,
    detect_background_key_ycc,
    isolated_dots,
    key_residue_fraction_ycc,
    remove_chroma_background_ycbcr,
    rgb_to_ycc,
    smoothstep,
    ycc_to_rgb,
)
from sprite_gen.frames.pixel_grid import (  # moved out of this file; every name stays reachable here
    PITCH_FAMILY_RATIO,
    RUNLEN_ALPHA_THRESHOLD,
    RUNLEN_MIN_RUNS,
    RUNLEN_MODE_SHARE,
    RUNLEN_RGB_TOLERANCE,
    _LE_KEEP,
    _LE_ONES,
    _axis_int_score,
    _axis_int_seed,
    _axis_refine,
    _best_phase,
    _dominant_block_color,
    _edge_histograms,
    _grid_phase,
    _grid_row_splits,
    _grid_rows,
    _grid_score_edges,
    _grid_uniformity,
    _pitch_from_edges,
    _pitch_pair,
    _runlen_axis_estimate,
    arbitrate_pitch,
    crosscheck_pitch_runlen,
    detect_pixel_grid,
    detect_pixel_pitch,
    estimate_pixel_grid_runlen,
    grid_edges,
    resolve_frame_pitch,
)
from sprite_gen.frames.frames_manifest import (  # moved out of this file; every name stays reachable here
    _load_validated,
    _require_complete_manifest,
    _require_failure_evidence,
    _require_generation_consistency,
    load_consistent_frames_manifest,
    load_failure_evidence,
    load_frames_manifest,
    require_frames_manifest,
)


def connected_components(image: Image.Image) -> list[dict[str, Any]]:
    alpha = image.getchannel("A")
    width, height = image.size
    data = alpha.tobytes()
    size = len(data)
    last_col = width - 1
    visited = bytearray(size)
    components: list[dict[str, Any]] = []
    for start, alpha_value in enumerate(data):
        if alpha_value <= 16 or visited[start]:
            continue
        stack = [start]
        visited[start] = 1
        pixels: list[int] = []
        append, push, pop = pixels.append, stack.append, stack.pop
        while stack:
            current = pop()
            append(current)
            x = current % width
            if x > 0:
                n = current - 1
                if not visited[n] and data[n] > 16:
                    visited[n] = 1
                    push(n)
            if x < last_col:
                n = current + 1
                if not visited[n] and data[n] > 16:
                    visited[n] = 1
                    push(n)
            n = current - width
            if n >= 0 and not visited[n] and data[n] > 16:
                visited[n] = 1
                push(n)
            n = current + width
            if n < size and not visited[n] and data[n] > 16:
                visited[n] = 1
                push(n)
        ys, xs = np.divmod(np.fromiter(pixels, dtype=np.int64, count=len(pixels)), width)
        min_x, max_x, min_y, max_y = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
        components.append({"pixels": pixels, "area": len(pixels), "bbox": (min_x, min_y, max_x + 1, max_y + 1),
                           "center_x": (min_x + max_x + 1) / 2})
    return components


def component_group_image(source: Image.Image, components: list[dict[str, Any]], padding: int = 4, *, preserve_y: bool = False) -> Image.Image:
    width, height = source.size
    min_x = max(0, min(component["bbox"][0] for component in components) - padding)
    min_y = max(0, min(component["bbox"][1] for component in components) - padding)
    max_x = min(width, max(component["bbox"][2] for component in components) + padding)
    max_y = min(height, max(component["bbox"][3] for component in components) + padding)
    if preserve_y:
        min_y, max_y = 0, height
    src = np.asarray(source.convert("RGBA"))
    mask = np.zeros(width * height, dtype=bool)
    for component in components:
        mask[np.asarray(component["pixels"], dtype=np.int64)] = True
    window = mask.reshape(height, width)[min_y:max_y, min_x:max_x]
    out = np.where(window[..., None], src[min_y:max_y, min_x:max_x], 0).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def cell_geometry(cell: dict[str, Any]) -> tuple[int, int, int, int]:
    width = cell_number(cell, "width", "size")
    height = cell_number(cell, "height", "size")
    safe_margin_x = cell_number(cell, "safe_margin_x", "safe_margin")
    safe_margin_y = cell_number(cell, "safe_margin_y", "safe_margin")
    if width <= 0 or height <= 0:
        raise SystemExit("cell width/height must be positive in sprite-request.json")
    return width, height, safe_margin_x, safe_margin_y


def safe_area_fit(sizes: list[tuple[int, int]], cell_width: int, cell_height: int, safe_margin_x: int,
                  safe_margin_y: int, scale: int = 1) -> dict[str, Any]:
    """셀에 들어갈 프레임 크기(논리 픽셀 `(w, h)`, `scale` = 논리 1칸의 셀 픽셀 수)의 안전영역 판정.

    물리캡 = 가로는 칸 너비, 세로는 칸 높이에서 바닥 여백 하나를 뺀 것(바닥 줄에 선 프레임이 가진
    줄들). 넘으면 `over` — 경고, 리롤 후보. 안전영역(사방 여백 안)은 넘었지만 물리캡 이내면 `soft` —
    여백 침범, 리롤 대상 아님, 정보성 알림만 (maintainer 확정 2026-07-14). 실패는 없다.
    `over`/`soft` 는 `sizes` 의 인덱스. 판정은 여기 하나다 — 픽셀 스냅(아래 `_snap_strip`)과
    `pixel-draw` 의 G8(`sprite_gen/gen/pixel_gates.py`)이 같이 부른다."""
    cap_w = max(1, cell_width // scale)
    cap_h = max(1, (cell_height - safe_margin_y) // scale)
    safe_w = max(1, (cell_width - safe_margin_x * 2) // scale)
    safe_h = max(1, (cell_height - safe_margin_y * 2) // scale)
    over = [i for i, (w, h) in enumerate(sizes) if w > cap_w or h > cap_h]
    soft = [i for i, (w, h) in enumerate(sizes) if (w > safe_w or h > safe_h) and w <= cap_w and h <= cap_h]
    return {"cap": [cap_w, cap_h], "safe": [safe_w, safe_h], "over": over, "soft": soft}


# align_x "alpha-centroid" 는 perfectpixel-studio internal/sprite/extract.go 이식
# (github.com/gykim80/perfectpixel-studio, MIT). bbox 중심 정렬은 팔/무기가 뻗은
# 프레임에서 면적이 큰 몸통을 반대로 밀어 재생 시 좌우 지터를 만들고, 알파 가중
# 무게중심 cx=Σ(x·α)/Σα 는 몸통이 지배해 축이 안정된다. α ≤ 10 은 소프트 매팅
# 프린지로 보고 무게에서 제외한다 (원본 alphaThreshold 와 동일).
ALPHA_CENTROID_MIN_ALPHA = 10


def _alpha_centroid_x(sprite: Image.Image, bottom_fraction: float = 1.0, min_alpha: int = 0) -> float:
    alpha = sprite.getchannel("A")
    width, height = sprite.size
    pixels = alpha.load()
    y_start = max(0, height - max(2, round(height * bottom_fraction)))
    total = 0
    weighted = 0.0
    for y in range(y_start, height):
        for x in range(width):
            value = pixels[x, y]
            if value > min_alpha:
                total += value
                weighted += value * (x + 0.5)
    if total == 0 and bottom_fraction < 1.0:
        return _alpha_centroid_x(sprite, 1.0, min_alpha)
    return (weighted / total) if total else width / 2.0


def _anchor_x(sprite: Image.Image, align_x: str) -> float:
    """The x of `sprite` (cropped to its bbox) that `fit_to_cell` puts on the cell's centre line."""
    if align_x == "foot-centroid":
        return _alpha_centroid_x(sprite, 0.2)
    if align_x == "centroid":
        return _alpha_centroid_x(sprite)
    if align_x == "alpha-centroid":
        return _alpha_centroid_x(sprite, 1.0, ALPHA_CENTROID_MIN_ALPHA)
    return sprite.width / 2.0


def fit_scale(sprites: list[Image.Image], cell_width: int, cell_height: int, safe_margin_x: int,
              safe_margin_y: int, align_x: str) -> float:
    """The one scale (at most 1) at which every sprite, cropped to its bbox and placed with its anchor
    (`_anchor_x`) on the cell's centre line, stays inside the safe area: the reach on either side of the
    anchor within half the safe width, the height within the safe height. A frame alone is a row of one.
    With `bbox-center` the reach is half the width, so this is the safe width over the widest frame."""
    half = max(0.5, cell_width / 2.0 - safe_margin_x)
    max_height = max(1, cell_height - safe_margin_y * 2)
    scale = 1.0
    for sprite in sprites:
        anchor = _anchor_x(sprite, align_x)
        scale = min(scale, half / max(anchor, sprite.width - anchor, 0.5), max_height / sprite.height)
    return scale


def _alpha_centroid_row_left(frame: Image.Image, cell_width: int, scale: int) -> int:
    # 픽셀 언페이크 행 경로의 프레임별 가로 배치 (align_x: alpha-centroid 전용).
    # 행 union 공동 left 는 register_row_frames 의 정합 잔차가 그대로 지터로
    # 남는다 — perfectpixel 방식대로 프레임마다 무게중심을 셀 중앙에 앉힌다.
    # NEAREST xN 업스케일은 논리 픽셀 중심 (x+0.5) 을 scale·(x+0.5) 로 보내므로
    # 논리 해상도에서 잰 무게중심에 scale 을 곱하면 리샘플 없이 정확하다.
    left = round(cell_width / 2.0 - scale * _alpha_centroid_x(frame, 1.0, ALPHA_CENTROID_MIN_ALPHA))
    left = max(0, min(cell_width - frame.width * scale, left))
    return left - left % scale  # 논리 픽셀 격자 스냅 (flip 대칭 보존)


def _kcentroid_downscale(sprite: Image.Image, target_width: int, target_height: int, detail_bias: bool = False) -> Image.Image:
    # Astropulse kCentroid-style pixel-art downscale: each output pixel takes the
    # centroid of the dominant 2-means color cluster of its source block, so dark
    # outlines survive instead of being averaged away (LANCZOS) or arbitrarily
    # sampled (NEAREST when the target grid does not match the art's pixel grid).
    source = sprite.convert("RGBA")
    source_width, source_height = source.size
    src = source.load()
    output = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
    out = output.load()
    for oy in range(target_height):
        y0 = oy * source_height // target_height
        y1 = max(y0 + 1, (oy + 1) * source_height // target_height)
        for ox in range(target_width):
            x0 = ox * source_width // target_width
            x1 = max(x0 + 1, (ox + 1) * source_width // target_width)
            block = [src[x, y] for y in range(y0, y1) for x in range(x0, x1)]
            opaque = [p for p in block if p[3] >= 128]
            if len(opaque) * 2 < len(block):
                continue
            if len(opaque) == 1:
                out[ox, oy] = opaque[0]
                continue
            color = _dominant_block_color(opaque, detail_bias)
            alpha_value = sum(p[3] for p in opaque) // len(opaque)
            out[ox, oy] = (color[0], color[1], color[2], alpha_value)
    return output


def fit_to_cell(
    image: Image.Image,
    cell_width: int,
    cell_height: int,
    safe_margin_x: int,
    safe_margin_y: int,
    fit: dict[str, Any] | None = None,
    *,
    scale: float | None = None,
) -> Image.Image:
    # `fit` comes from sprite-request.json ("fit" object):
    #   resample: "lanczos" (default) | "nearest" | "kcentroid" — kcentroid is the
    #             pixel-art downscale that keeps 1px dark outlines readable
    #   align_x:  "foot-centroid" (default) | "centroid" | "alpha-centroid" |
    #             "bbox-center" —
    #             foot-centroid anchors on the bottom 20% alpha (the legs), so
    #             trailing hair/capes do not pull the body off the cell axis
    #             (critical for runtime horizontal flip); alpha-centroid is the
    #             perfectpixel-studio port — full alpha-weighted centroid that
    #             ignores soft-matte fringe (α ≤ 10), and in the pixel unfake
    #             row path it is applied per frame instead of the row union
    #   align_y:  "bottom" (default) | "center" — bottom pins feet to a shared baseline
    # 2026-07-04 (maintainer): 기본값을 foot-centroid/bottom 으로 승격 — 프레임 간
    # "무게감"(발밑 기준선)이 기본으로 잡혀야 한다. pixel_unfake 경로와 동일 기본.
    fit = fit or {}
    resample_name = str(fit.get("resample", "lanczos")).lower()
    align_x = str(fit.get("align_x", "foot-centroid")).lower()
    align_y = str(fit.get("align_y", "bottom")).lower()
    bbox = image.getbbox()
    target = Image.new("RGBA", (cell_width, cell_height), (0, 0, 0, 0))
    if bbox is None:
        return target
    sprite = image.crop(bbox)
    if scale is None:
        scale = fit_scale([sprite], cell_width, cell_height, safe_margin_x, safe_margin_y, align_x)
    if scale != 1.0:
        new_size = (max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale)))
        if resample_name == "kcentroid":
            sprite = _kcentroid_downscale(sprite, new_size[0], new_size[1])
        elif resample_name == "nearest":
            sprite = sprite.resize(new_size, Image.Resampling.NEAREST)
        else:
            # "lanczos" names the smooth shrink: LANCZOS coverage, colour mixed apart from it
            # (resize_cell). LANCZOS over the keyed RGBA itself drew a light rim and key tint
            # at the edge that the frame did not have.
            sprite = resize_cell(sprite, new_size)
        cropped = sprite.getbbox()
        if cropped is not None:
            sprite = sprite.crop(cropped)
    if align_x in ("foot-centroid", "centroid", "alpha-centroid"):
        left = round(cell_width / 2.0 - _anchor_x(sprite, align_x))
        left = max(0, min(cell_width - sprite.width, left))
    else:
        left = (cell_width - sprite.width) // 2
    if align_y == "bottom":
        top = max(0, cell_height - safe_margin_y - sprite.height)
    else:
        top = (cell_height - sprite.height) // 2
    target.alpha_composite(sprite, (left, top))
    return target


def solid_alpha_bbox(image: Image.Image, threshold: int = 128) -> tuple[int, int, int, int] | None:
    """α>=threshold 픽셀만의 bbox — AA 프린지(반투명 가장자리)를 제외한 실 콘텐츠 범위.

    픽셀 언페이크 경로의 불투명 판정(α>=128: `grid_snap_downscale`/`binarize_alpha`/
    `apply_palette`)과 같은 기준이어야 한다. any-alpha `getbbox()` 로 격자를 치면
    프린지가 bbox 를 부풀려 `grid_edges` 의 셀 개수 반올림이 한 칸 늘고, 내용이
    프린지뿐인 가장자리 셀이 50% 규칙을 통과해 실루엣 밖 부스러기 픽셀로 굳는다
    (회귀 2026-07-17: synthetic_fixture_a 150 프레임 중 141 프레임 폭 +1~4px, maintainer 발견 —
    회귀 테스트 test_pixel_snap.py::test_fringe_does_not_inflate_grid)."""
    return image.convert("RGBA").split()[3].point(lambda a: 255 if a >= threshold else 0).getbbox()


def tighten_components(images: list[Image.Image]) -> list[Image.Image]:
    """픽셀 언페이크 스냅 전에 컴포넌트를 실 콘텐츠(solid alpha) bbox 로 타이트하게 조인다.

    `grid_edges` 의 lead-스냅(위상 < 피치의 1/4 이면 0 으로)은 컴포넌트가 bbox 로
    잘려 블록 경계에서 시작한다는 전제다. `component_group_image` 는 사방 4px
    패딩을 두르므로, 패딩째 격자를 치면 위상 추정 노이즈가 lead-스냅 문턱 아래로
    떨어지는 순간 격자 전체가 패딩만큼 위로 밀린다 — 꼬리에 자투리 셀이 생기고,
    경계에서 쪼개진 바닥 블록 + 문턱 근처 알파(~134) 프린지가 유령 픽셀 한 줄로
    태어난다 (회귀 2026-07-14: down_idle blink 발밑 1px 돌출, maintainer 발견 —
    회귀 테스트 test_pixel_snap.py::test_padded_component_no_ghost_bottom_row).
    크롭 기준은 `solid_alpha_bbox` — any-alpha bbox 는 프린지째 격자를 쳐서 같은
    유령 픽셀을 사방 가장자리에 만든다 (해당 docstring 참조). 전부 프린지인
    퇴화 컴포넌트만 any-alpha bbox 로 폴백한다 (어차피 binarize 에서 빈 프레임)."""
    return [
        component.crop(box)
        if (box := (solid_alpha_bbox(component) or component.getbbox())) else component
        for component in images
    ]


def _boundary_mass(image: Image.Image) -> tuple[list[int], list[int]]:
    """축별 색/알파 경계 질량 프로파일 — 절단선 스냅의 증거."""
    px = image.load()
    w, h = image.size
    col = [0] * max(1, w)
    row = [0] * max(1, h)
    for y in range(h):
        for x in range(w - 1):
            a, b = px[x, y], px[x + 1, y]
            if (a[3] >= 128) != (b[3] >= 128) or (
                a[3] >= 128 and b[3] >= 128
                and abs(a[0]-b[0]) + abs(a[1]-b[1]) + abs(a[2]-b[2]) > 48
            ):
                col[x + 1] += 1
    for x in range(w):
        for y in range(h - 1):
            a, b = px[x, y], px[x, y + 1]
            if (a[3] >= 128) != (b[3] >= 128) or (
                a[3] >= 128 and b[3] >= 128
                and abs(a[0]-b[0]) + abs(a[1]-b[1]) + abs(a[2]-b[2]) > 48
            ):
                row[y + 1] += 1
    return col, row


def refine_edges_to_boundaries(component: Image.Image, x_edges: list[int], y_edges: list[int],
                               pitch: tuple[float, float]) -> tuple[list[int], list[int]]:
    """등간격 절단선을 실제 블록 경계로 스냅 (maintainer 확정 2026-07-18).

    AI 블록은 국소적으로 흔들려서 등간격 자름은 경계 조각을 옆 칸으로 흘린다
    (회귀: 눈 아래 유령 픽셀, 발밑 1픽셀 — 같은 뿌리). 각 내부 절단선을
    ±pitch/3 창 안의 색경계 질량 최대 위치로 옮긴다. 질량이 무의미하면(평탄 영역)
    등간격 위치 유지. 단조 증가·최소 칸폭 불변식 강제 — 결정론.

    **슬리버 가드 (maintainer 2026-07-24, plan backbone-sliver-guard)**: 최소 칸폭은
    절대 2px 가 아니라 **피치 비례(≥0.6×pitch)** 다. 절대 2px 는 피치 ~13px 소재에서
    0.15셀짜리 슬리버 쌍(0.4/1.6)을 허용했고, ~5px 간격의 경계 쌍이 인접 절단선
    둘을 서로 향해 당기면 출력 두 행이 소스의 거의 같은 밴드를 이중 샘플링했다 —
    v8 down_jump 회귀 두 건이 이 한 기전이다: f2 턱 늘어남(턱 라인 세로 복제),
    f0 눈물점(50/50 걸침 셀의 지배색 플립). 스냅은 창(±pitch/3) 안에서 계속 경계를
    따르되, 이웃과 0.6×pitch 미만으로는 절대 붙지 못한다 — 전역 격자(머리끈
    일관성)는 그대로다."""
    col, row = _boundary_mass(component)

    def snap(edges: list[int], mass: list[int], pitch_axis: float, limit: int) -> list[int]:
        if len(edges) < 3:
            return edges
        out = list(edges)
        window = max(1, int(pitch_axis / 3))
        min_gap = max(2, int(round(pitch_axis * 0.6)))
        for i in range(1, len(out) - 1):
            e = edges[i]
            lo = max(out[i - 1] + min_gap, e - window)
            hi = min(edges[i + 1] - min_gap, e + window, limit - 1)
            if hi < lo:
                continue
            best = max(range(lo, hi + 1), key=lambda pos: (mass[pos] if pos < len(mass) else 0))
            if (mass[best] if best < len(mass) else 0) > 0:
                out[i] = best
        # 단조/최소폭 재확인 (스냅 순서상 위반 불가지만 방어적 단정)
        for i in range(1, len(out)):
            if out[i] <= out[i - 1]:
                return list(edges)  # 위반 시 통째로 등간격 유지 (관측 불필요 — 발생 불가 경로)
        return out

    return (snap(x_edges, col, pitch[0], component.width),
            snap(y_edges, row, pitch[1], component.height))


def snap_by_edges(image: Image.Image, x_edges: list[int], y_edges: list[int],
                  detail_bias: bool = False) -> Image.Image:
    """명시 절단선으로 블록 샘플링 (grid_snap_downscale 의 코어 — 비등간격 지원)."""
    source = image.convert("RGBA")
    pixels = source.load()
    output = Image.new("RGBA", (len(x_edges) - 1, len(y_edges) - 1), (0, 0, 0, 0))
    out = output.load()
    for oy in range(len(y_edges) - 1):
        for ox in range(len(x_edges) - 1):
            block = [
                pixels[x, y]
                for y in range(y_edges[oy], y_edges[oy + 1])
                for x in range(x_edges[ox], x_edges[ox + 1])
            ]
            opaque = [p for p in block if p[3] >= 128]
            if len(opaque) * 2 < len(block):
                continue
            color = _dominant_block_color(opaque, detail_bias)
            out[ox, oy] = (color[0], color[1], color[2], 255)
    return output


def grid_snap_downscale(
    image: Image.Image,
    pitch: float | tuple[float, float],
    detail_bias: bool = False,
    phase: tuple[float, float] | None = None,
) -> Image.Image:
    """pitch/phase 는 소수를 받는다 — 격자선만 정수 픽셀로 확정한다 (`grid_edges`).

    pitch 는 스칼라 또는 (가로, 세로) 쌍. 축마다 블록 크기가 다른 생성물이 있어서
    `detect_pixel_grid` 가 축별 피치를 낸다. 정수 pitch + 정수 phase 를 주면 예전과 같은
    경계가 나온다 (골든 추출 회귀 없음).
    """
    source = image.convert("RGBA")
    width, height = source.size
    pitch_x, pitch_y = _pitch_pair(pitch)
    if phase is None:
        offset_x, offset_y = _grid_phase(source, max(2, int(round(pitch_x))))
    else:
        offset_x, offset_y = phase
    x_edges = grid_edges(width, pitch_x, offset_x)
    y_edges = grid_edges(height, pitch_y, offset_y)
    pixels = source.load()
    output = Image.new("RGBA", (len(x_edges) - 1, len(y_edges) - 1), (0, 0, 0, 0))
    out = output.load()
    for oy in range(len(y_edges) - 1):
        for ox in range(len(x_edges) - 1):
            block = [
                pixels[x, y]
                for y in range(y_edges[oy], y_edges[oy + 1])
                for x in range(x_edges[ox], x_edges[ox + 1])
            ]
            opaque = [p for p in block if p[3] >= 128]
            if len(opaque) * 2 < len(block):
                continue
            color = _dominant_block_color(opaque, detail_bias)
            out[ox, oy] = (color[0], color[1], color[2], 255)
    return output


def binarize_alpha(image: Image.Image) -> Image.Image:
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            p = pixels[x, y]
            if p[3] < 128:
                pixels[x, y] = (0, 0, 0, 0)
            elif p[3] != 255:
                pixels[x, y] = (p[0], p[1], p[2], 255)
    return image


def conform_row_logical(images: list, logical_width: int, logical_height: int, detail_bias: bool = True) -> list:
    # 행(row) 단위 크기 통일: 축소 배율을 행에서 가장 큰 프레임 기준 하나로 계산해
    # 전 프레임에 동일 적용한다(프레임 간 크기 호흡 제거). 입력은 이미 격자 스냅된
    # 논리 해상도 프레임들이다.
    snapped = []
    for image in images:
        bbox = image.getbbox()
        snapped.append(image.crop(bbox) if bbox else image)
    max_width = max(s.width for s in snapped)
    max_height = max(s.height for s in snapped)
    if max_width > logical_width or max_height > logical_height:
        scale = min(logical_width / max_width, logical_height / max_height)
        conformed = []
        for sprite in snapped:
            resized = _kcentroid_downscale(
                sprite,
                max(1, round(sprite.width * scale)),
                max(1, round(sprite.height * scale)),
                detail_bias,
            )
            bbox = resized.getbbox()
            conformed.append(resized.crop(bbox) if bbox else resized)
        snapped = conformed
    return [binarize_alpha(s) for s in snapped]


def register_row_frames(frames: list, slack_x: int = 8, slack_y: int = 3) -> list:
    # 프레임 간 정합: 로코모션에서 다리는 원래 움직이므로, 안정 부위(상체 65%)의
    # 알파 겹침을 최대화하는 정수 시프트를 프레임마다 찾아 공통 캔버스에 앉힌다.
    # 이후 배치는 행 공통(union) 기준 1회 계산 → 프레임 간 몸통 흔들림 제거.
    cropped = []
    for frame in frames:
        bbox = frame.getbbox()
        cropped.append(frame.crop(bbox) if bbox else frame)
    canvas_width = max(f.width for f in cropped) + slack_x * 2
    canvas_height = max(f.height for f in cropped) + slack_y * 2

    def base_pos(f):
        return ((canvas_width - f.width) // 2, canvas_height - slack_y - f.height)

    reference = cropped[0]
    ref_x, ref_y = base_pos(reference)
    upper_limit = ref_y + int(reference.height * 0.65)
    ref_pixels = reference.load()
    ref_mask = set()
    for y in range(reference.height):
        if ref_y + y >= upper_limit:
            break
        for x in range(reference.width):
            if ref_pixels[x, y][3] >= 128:
                ref_mask.add((ref_x + x, ref_y + y))

    registered = []
    for index, frame in enumerate(cropped):
        base_x, base_y = base_pos(frame)
        best_dx, best_dy = 0, 0
        if index > 0 and ref_mask:
            pixels = frame.load()
            points = [
                (x, y)
                for y in range(frame.height)
                for x in range(frame.width)
                if pixels[x, y][3] >= 128 and base_y + y < upper_limit
            ]
            best_score = -1
            for dy in range(-slack_y, slack_y + 1):
                for dx in range(-slack_x, slack_x + 1):
                    score = sum(1 for (x, y) in points if (base_x + x + dx, base_y + y + dy) in ref_mask)
                    if score > best_score:
                        best_score = score
                        best_dx, best_dy = dx, dy
        canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
        canvas.alpha_composite(frame, (min(max(0, base_x + best_dx), canvas_width - frame.width), min(max(0, base_y + best_dy), canvas_height - frame.height)))
        registered.append(canvas)
    # 공통 union bbox 로 크롭 — 슬랙 여백이 셀보다 커져 배치 시 하단(발)이
    # 잘리는 것을 방지. 동일 박스 크롭이라 프레임 간 정합은 유지된다.
    union = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
    for canvas in registered:
        union.alpha_composite(canvas)
    bbox = union.getbbox()
    if bbox is not None:
        registered = [canvas.crop(bbox) for canvas in registered]
    return registered


def _content_center_top(sprite: Image.Image, cell_height: int) -> int:
    bbox = sprite.getbbox()
    content_top, content_bottom = bbox[1::2] if bbox else (0, sprite.height)
    return round((cell_height - (content_bottom - content_top)) / 2) - content_top


def row_placement(frames: list, cell_width: int, cell_height: int, safe_margin_y: int, scale: int, fit: dict[str, Any]) -> tuple[int, int]:
    # 가로 배치 오프셋은 행 union 기준으로 1회 계산해 전 프레임에 동일 적용한다
    # (플립 대칭·수평 안정). 세로는 place_row_frame 이 프레임별로 접지한다.
    union = Image.new("RGBA", frames[0].size, (0, 0, 0, 0))
    for frame in frames:
        union.alpha_composite(frame)
    sprite = union.resize((union.width * scale, union.height * scale), Image.Resampling.NEAREST)
    align_x = str(fit.get("align_x", "foot-centroid")).lower()
    if align_x == "foot-centroid":
        left = round(cell_width / 2.0 - _alpha_centroid_x(sprite, 0.2))
    elif align_x == "centroid":
        left = round(cell_width / 2.0 - _alpha_centroid_x(sprite))
    elif align_x == "alpha-centroid":
        # union 기준 값 — 실제 배치는 _run 이 프레임별로 _alpha_centroid_row_left
        # 를 쓴다 (per-frame 이 이 모드의 핵심).
        left = round(cell_width / 2.0 - _alpha_centroid_x(sprite, 1.0, ALPHA_CENTROID_MIN_ALPHA))
    else:
        left = (cell_width - sprite.width) // 2
    left = max(0, min(cell_width - sprite.width, left))
    left -= left % scale
    if str(fit.get("align_y", "bottom")).lower() == "center":
        return left, _content_center_top(sprite, cell_height)
    bbox = sprite.getbbox()
    content_bottom = bbox[3] if bbox else sprite.height
    top = max(0, cell_height - safe_margin_y - content_bottom)
    return left, top


def place_row_frame(frame: Image.Image, cell_width: int, cell_height: int, scale: int, left: int, top: int, safe_margin_y: int | None = None, ground: bool = True, align_y: str = "bottom") -> Image.Image:
    # 2026-07-04 (maintainer): 세로는 프레임마다 콘텐츠 바닥을 공유 기준선에 접지한다 —
    # 행 union 공동 top 만 쓰면 소스 스트립의 상하 요동이 이동으로 남아 프레임 간
    # "무게감"(발밑 높이)이 들쭉해진다. perfectpixel-studio 의 프레임별 알파 가중
    # 정렬과 같은 원리의 세로축 버전. 점프 같은 의도적 오프셋은 fit.ground_frames
    # = false 로 끌 수 있다 (row_placement 의 공동 top 사용).
    target = Image.new("RGBA", (cell_width, cell_height), (0, 0, 0, 0))
    if frame.getbbox() is None:
        return target
    sprite = frame.resize((frame.width * scale, frame.height * scale), Image.Resampling.NEAREST)
    frame_top = top
    if ground and str(align_y).lower() == "center":
        frame_top = _content_center_top(sprite, cell_height)
    elif ground and safe_margin_y is not None:
        bbox = sprite.getbbox()
        content_bottom = bbox[3] if bbox else sprite.height
        frame_top = max(0, cell_height - safe_margin_y - content_bottom)
    target.alpha_composite(sprite, (left, frame_top))
    return target


PALETTE_LOCK = "palette.lock.json"


def load_pinned_palette(run_dir: Path) -> list | None:
    """런에 고정된 공유 팔레트 (없으면 None). 팔레트 피닝 (maintainer 확정 2026-07-18):
    공유 팔레트가 추출 배치 구성에 따라 재계산되며 확정 행 픽셀을 흔들던 드리프트
    (회귀 2026-07-17 down_idle, 3회)를 원천 차단한다 — 배치가 어떻게 바뀌어도
    팔레트는 이 파일 하나가 진실이다. 재산출은 `--repalette` 명시 시에만."""
    path = run_dir / PALETTE_LOCK
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        colors = [tuple(int(v) for v in c) for c in data["colors"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"corrupt {PALETTE_LOCK}: {exc} — fix or delete it explicitly")
    if not colors:
        raise SystemExit(f"{PALETTE_LOCK} has no colors — fix or delete it explicitly")
    return colors


def write_pinned_palette(run_dir: Path, palette: list, source: str) -> None:
    atomic_write_text(run_dir / PALETTE_LOCK, json.dumps({
        "kind": "sprite-gen-palette-lock",
        "source": source,
        "colors": [list(c) for c in palette],
    }, ensure_ascii=False, indent=2) + "\n")


def build_shared_palette(frames: list, size: int) -> list:
    colors: list = []
    for frame in frames:
        pixels = frame.load()
        for y in range(frame.height):
            for x in range(frame.width):
                p = pixels[x, y]
                if p[3] >= 128:
                    colors.append((p[0], p[1], p[2]))
    if not colors:
        return []

    def box_widest(box):
        best_range = -1
        best_channel = 0
        for channel in range(3):
            lo = min(c[channel] for c in box)
            hi = max(c[channel] for c in box)
            if hi - lo > best_range:
                best_range = hi - lo
                best_channel = channel
        return best_range, best_channel

    boxes = [colors]
    while len(boxes) < size:
        best = None
        for index, box in enumerate(boxes):
            if len(box) < 2:
                continue
            spread, channel = box_widest(box)
            if spread > 0 and (best is None or spread > best[0]):
                best = (spread, channel, index)
        if best is None:
            break
        _, channel, index = best
        box = boxes.pop(index)
        box.sort(key=lambda c: c[channel])
        mid = len(box) // 2
        boxes.append(box[:mid])
        boxes.append(box[mid:])
    return [
        tuple(sum(c[channel] for c in box) // len(box) for channel in range(3))
        for box in boxes
        if box
    ]


def apply_palette(image: Image.Image, palette: list) -> Image.Image:
    if not palette:
        return image
    pixels = image.load()
    cache: dict = {}
    for y in range(image.height):
        for x in range(image.width):
            p = pixels[x, y]
            if p[3] < 128:
                pixels[x, y] = (0, 0, 0, 0)
                continue
            key = (p[0], p[1], p[2])
            if key not in cache:
                cache[key] = min(
                    palette,
                    key=lambda c: (c[0] - key[0]) ** 2 + (c[1] - key[1]) ** 2 + (c[2] - key[2]) ** 2,
                )
            color = cache[key]
            pixels[x, y] = (color[0], color[1], color[2], 255)
    return image


def enforce_outline(image: Image.Image, strength: float = 0.62) -> Image.Image:
    # 균일 오토 아웃라인: 실루엣 경계(투명 인접) 픽셀을 자기 색 기준으로 어둡게.
    # 다운스케일에서 얇은 원본 외곽선이 패치워크로 살아남는 문제를 결정론으로 보정 —
    # 모든 프레임/행에서 1 논리픽셀 외곽선이 보장돼 프레임 간 플리커도 줄인다.
    pixels = image.load()
    width, height = image.size
    boundary = []
    for y in range(height):
        for x in range(width):
            if pixels[x, y][3] < 128:
                continue
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if nx < 0 or ny < 0 or nx >= width or ny >= height or pixels[nx, ny][3] < 128:
                    boundary.append((x, y))
                    break
    keep = 1.0 - strength
    for x, y in boundary:
        r, g, b, _a = pixels[x, y]
        pixels[x, y] = (int(r * keep), int(g * keep), int(b * keep), 255)
    return image


def fit_component_to_bbox(component: Image.Image, cell_width: int, cell_height: int,
                          bbox: tuple[int, int, int, int], scale: int = 1,
                          ) -> tuple[Image.Image, dict[str, float] | None]:
    """원본 컴포넌트를 픽셀 언페이크 프레임의 콘텐츠 bbox(×scale) 풋프린트에 앉힌다.

    plain/orig 쌍둥이용: 픽셀 언페이크 결과와 같은 크기·같은 자리에 원본 화질 스프라이트를
    두어, 큐레이터의 픽셀 언페이크 토글이 크기 변화 없이 픽셀 처리 품질만 비교하게 한다
    (contain 맞춤 + 하단 정렬 + 가로 중앙 — bbox 종횡비와의 오차는 격자 반올림 수준).

    두 번째 반환값은 컴포넌트 좌표 → 쌍둥이 좌표 매핑 {crop_x, crop_y, ratio, left, top}
    (twin = (raw - crop) * ratio + offset) — 검출 격자선을 쌍둥이 위에 겹칠 때 쓴다."""
    target = Image.new("RGBA", (cell_width * scale, cell_height * scale), (0, 0, 0, 0))
    src_bbox = component.getbbox()
    if src_bbox is None:
        return target, None
    src = component.crop(src_bbox)
    x0, y0, x1, y1 = (v * scale for v in bbox)
    box_w, box_h = max(1, x1 - x0), max(1, y1 - y0)
    ratio = min(box_w / src.width, box_h / src.height)
    tw, th = max(1, round(src.width * ratio)), max(1, round(src.height * ratio))
    resized = resize_cell(src, (tw, th))
    left = x0 + (box_w - tw) // 2
    top = y1 - th
    target.alpha_composite(resized, (left, top))
    mapping = {"crop_x": float(src_bbox[0]), "crop_y": float(src_bbox[1]),
               "ratio": ratio, "left": float(left), "top": float(top)}
    return target, mapping


def extract_component_images(strip: Image.Image, frame_count: int, *, preserve_y: bool = False) -> list[Image.Image] | None:
    components = connected_components(strip)
    if not components:
        return None
    largest_area = max(component["area"] for component in components)
    seed_threshold = max(120, largest_area * 0.20)
    seeds = [component for component in components if component["area"] >= seed_threshold]
    if len(seeds) < frame_count:
        seeds = sorted(components, key=lambda component: component["area"], reverse=True)[:frame_count]
    if len(seeds) < frame_count:
        return None

    seeds = sorted(
        sorted(seeds, key=lambda component: component["area"], reverse=True)[:frame_count],
        key=lambda component: component["center_x"],
    )
    seed_ids = {id(seed) for seed in seeds}
    groups: list[list[dict[str, Any]]] = [[seed] for seed in seeds]
    noise_threshold = max(12, largest_area * 0.002)

    dropped = 0
    for component in components:
        if id(component) in seed_ids or component["area"] < noise_threshold:
            continue
        nearest_index = min(
            range(len(seeds)),
            key=lambda index: abs(seeds[index]["center_x"] - component["center_x"]),
        )
        # x 거리로만 붙이면 멀리 떨어진 파편(크로마 잔여물·분리된 이펙트)까지
        # 병합돼 bbox 가 늘어나고 프레임 바닥선/크롭이 흔들린다 (maintainer 2026-07-04
        # "이상한 거 딸려나오게 하지 마"). 시드 bbox 를 살짝 넓힌 근접 영역과
        # 겹치는 위성만 병합하고, 나머지는 관측 가능하게 버린다.
        sx0, sy0, sx1, sy1 = seeds[nearest_index]["bbox"]
        pad_x = max(6, round((sx1 - sx0) * 0.15))
        pad_y = max(6, round((sy1 - sy0) * 0.15))
        cx0, cy0, cx1, cy1 = component["bbox"]
        if cx0 < sx1 + pad_x and cx1 > sx0 - pad_x and cy0 < sy1 + pad_y and cy1 > sy0 - pad_y:
            groups[nearest_index].append(component)
        else:
            dropped += 1
    if dropped:
        print(f"[extract] dropped {dropped} stray satellite component(s) outside seed proximity", file=sys.stderr)

    return [component_group_image(strip, group, preserve_y=preserve_y) for group in groups]


def fit_row_preserving_y(images: list[Image.Image], cell_width: int, cell_height: int,
                         safe_margin_x: int, safe_margin_y: int, fit: dict[str, Any]) -> list[Image.Image]:
    """One scale and vertical translation for a painted row, before source Y is lost.

    Only placement is preserved: an image's bottom is not proof of foot contact.
    source_ground_y, when supplied, is measured in the raw strip's coordinates.
    Otherwise the deepest opaque pixel supplies a shared placement baseline.
    """
    if len({image.height for image in images}) > 1:
        raise SystemExit("ground_frames=false requires a common source Y canvas")
    boxes = [image.getbbox() for image in images]
    occupied = [box for box in boxes if box]
    if not occupied:
        return [Image.new("RGBA", (cell_width, cell_height)) for _ in images]
    ground = fit.get("source_ground_y", max(box[3] for box in occupied))
    if (isinstance(ground, bool) or not isinstance(ground, (int, float)) or
            not math.isfinite(ground) or ground < max(box[3] for box in occupied) or ground > images[0].height):
        raise SystemExit("source_ground_y must enclose the opaque row and lie inside its source canvas")
    align_x = str(fit.get("align_x", "foot-centroid")).lower()
    sprites = [image.crop(box) for image, box in zip(images, boxes) if box]
    scale = min(fit_scale(sprites, cell_width, cell_height, safe_margin_x, safe_margin_y, align_x),
                (cell_height - 2 * safe_margin_y) / (ground - min(box[1] for box in occupied)))
    if scale <= 0:
        raise SystemExit("No safe vertical area for common row placement")
    result = []
    for image, box in zip(images, boxes):
        target = Image.new("RGBA", (cell_width, cell_height))
        if box:
            sprite = image.crop(box)
            size = (max(1, round(sprite.width * scale)), max(1, round(sprite.height * scale)))
            if fit.get("resample") == "nearest":
                sprite = sprite.resize(size, Image.Resampling.NEAREST)
            elif fit.get("resample") == "kcentroid":
                sprite = _kcentroid_downscale(sprite, *size)
            else:
                sprite = resize_cell(sprite, size)
            anchor = _anchor_x(sprite, align_x) if align_x in ("centroid", "foot-centroid", "alpha-centroid") else sprite.width / 2
            left = round(cell_width / 2 - anchor)
            top = round(cell_height - safe_margin_y - (ground - box[1]) * scale)
            if left < 0 or top < 0 or left + sprite.width > cell_width or top + sprite.height > cell_height:
                raise SystemExit("Common row placement would clip a frame")
            target.alpha_composite(sprite, (left, top))
        result.append(target)
    return result


def fit_row_to_cells(images: list[Image.Image], cell_width: int, cell_height: int,
                     safe_margin_x: int, safe_margin_y: int,
                     fit: dict[str, Any] | None = None) -> list[Image.Image]:
    # The prop's reach changes a pose's bounds, not the character's physical size. One scale for the
    # complete row (fit_scale) keeps every pose inside the safe area where its anchor puts it, so a
    # weapon held far out neither shrinks its pose alone nor pushes that pose off its anchor.
    if (fit or {}).get("ground_frames") is False:
        return fit_row_preserving_y(images, cell_width, cell_height, safe_margin_x, safe_margin_y, fit)
    align_x = str((fit or {}).get("align_x", "foot-centroid")).lower()
    sprites = [image.crop(bbox) for image in images if (bbox := image.getbbox()) is not None]
    scale = fit_scale(sprites, cell_width, cell_height, safe_margin_x, safe_margin_y, align_x) if sprites else 1.0
    return [fit_to_cell(image, cell_width, cell_height, safe_margin_x, safe_margin_y,
                        fit, scale=scale) for image in images]


def extract_component_frames(strip: Image.Image, frame_count: int, cell_width: int, cell_height: int, safe_margin_x: int, safe_margin_y: int, fit: dict[str, Any] | None = None) -> list[Image.Image] | None:
    images = extract_component_images(strip, frame_count, preserve_y=(fit or {}).get("ground_frames") is False)
    if images is None:
        return None
    return fit_row_to_cells(images, cell_width, cell_height, safe_margin_x, safe_margin_y, fit)


def extract_slot_frames(strip: Image.Image, frame_count: int, cell_width: int, cell_height: int, safe_margin_x: int, safe_margin_y: int, fit: dict[str, Any] | None = None) -> list[Image.Image]:
    slot_width = strip.width / frame_count
    images = []
    for index in range(frame_count):
        left = round(index * slot_width)
        right = round((index + 1) * slot_width)
        images.append(strip.crop((left, 0, right, strip.height)))
    return fit_row_to_cells(images, cell_width, cell_height, safe_margin_x, safe_margin_y, fit)


def chroma_adjacent_count(image: Image.Image, chroma_key: tuple[int, int, int], threshold: float) -> int:
    data = np.asarray(image.convert("RGBA"))
    distance = key_distance_field(data[..., :3].astype(np.int32), chroma_key)
    return int(np.count_nonzero((data[..., 3] > 16) & (distance <= threshold)))


def inspect_frames(frames: list[Image.Image], chroma_key: tuple[int, int, int], args: argparse.Namespace) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    warnings: list[str] = []
    records: list[dict[str, Any]] = []
    areas = [alpha_nonzero_count(frame) for frame in frames]
    frame_median = median(areas) if areas else 0
    for index, frame in enumerate(frames):
        nontransparent = areas[index]
        edge = edge_alpha_count(frame, args.edge_margin)
        adjacent = chroma_adjacent_count(frame, chroma_key, args.chroma_adjacent_threshold)
        bbox = frame.getbbox()
        records.append(
            {
                "index": index,
                "nontransparent_pixels": nontransparent,
                "bbox": list(bbox) if bbox else None,
                "edge_pixels": edge,
                "chroma_adjacent_pixels": adjacent,
            }
        )
        if nontransparent < args.min_used_pixels:
            errors.append(sparse_frame_error(index, nontransparent, args.min_used_pixels,
                                             getattr(args, "_subject_kind", SUBJECT_DEFAULT)))
        if edge > args.edge_pixel_threshold:
            warnings.append(f"frame {index:02d} has {edge} non-transparent edge pixels")
        if adjacent > args.chroma_adjacent_pixel_threshold:
            errors.append(f"frame {index:02d} has {adjacent} chroma-adjacent pixels")
        if frame_median and nontransparent < frame_median * args.small_outlier_ratio:
            warnings.append(f"frame {index:02d} is much smaller than median ({nontransparent} vs {frame_median:.0f})")
        if frame_median and nontransparent > frame_median * args.large_outlier_ratio:
            warnings.append(f"frame {index:02d} is much larger than median ({nontransparent} vs {frame_median:.0f})")
    return errors, warnings, records


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--states", default="all")
    parser.add_argument("--key-threshold", type=float, default=96.0)
    parser.add_argument("--fringe-key-threshold", type=float, default=180.0)
    parser.add_argument("--fringe-delta", type=float, default=18.0)
    parser.add_argument(
        "--fringe-unmix-reach",
        type=int,
        default=None,
        help="peel depth for soft-alpha unmix of out-of-band key blends; "
        "default from request chroma.unmix_reach, else 4; 0 disables",
    )
    parser.add_argument(
        "--spill-max-fraction",
        type=float,
        default=None,
        help="max size of a trapped key-spill cluster to despill, as a fraction "
        "of subject pixels; default from request chroma.spill_max_fraction, "
        "else 0.005; 0 disables",
    )
    parser.add_argument(
        "--chroma-mode",
        choices=("rgb", "ycbcr"),
        default=None,
        help="background matting path; overrides request chroma.mode "
        "(ycbcr = chrominance-plane matting with border-mode key detection, "
        "despill and flood fill — perfectpixel-studio port; default rgb)",
    )
    parser.add_argument(
        "--decontam",
        choices=("off", "auto", "palette"),
        default=None,
        help="edge decontamination after the matte; overrides request chroma.decontam "
        "(palette = re-explain key-tinted edges with the subject's own colours, auto = where it "
        "applies; default off)",
    )
    parser.add_argument(
        "--segmentation",
        choices=("components", "projection"),
        default=None,
        help="frame separation mode; overrides request fit.segmentation "
        "(projection = projection-profile + DP optimal cut for fused poses, "
        "default components)",
    )
    parser.add_argument("--allow-slot-fallback", action="store_true")
    parser.add_argument("--repalette", action="store_true",
                        help="고정 팔레트(palette.lock.json)를 무시하고 재산출·재고정")
    # Default is None on purpose: the floor's SSoT is the request's subject
    # resolution-aware profile (sprite_gen.spec.subject); the CLI is an
    # explicit override only, same rule as the chroma tunables below.
    parser.add_argument("--min-used-pixels", type=int, default=None)
    parser.add_argument("--edge-margin", type=int, default=2)
    parser.add_argument("--edge-pixel-threshold", type=int, default=24)
    parser.add_argument("--chroma-adjacent-threshold", type=float, default=150.0)
    parser.add_argument("--chroma-adjacent-pixel-threshold", type=int, default=120)
    parser.add_argument("--small-outlier-ratio", type=float, default=0.35)
    parser.add_argument("--large-outlier-ratio", type=float, default=2.75)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    return parser


def _merged_failure_lists(prior: dict, result: dict, target_states: set, all_states: set):
    """Compute the union of the run's CURRENTLY-UNRESOLVED per-state failures. A subset
    `--states` extract only determines the outcome of the states it targets, so carry forward
    prior failures for untouched states and replace the target states with this attempt's
    outcome (a target state that succeeded drops out). Warnings are scoped to states that still
    have an error, so a resolved state never leaves a stray warning in the failure evidence.
    Returns (errors, warnings); empty errors means every recorded failure is resolved."""
    def _state_of(message: object):
        head = str(message).split(":", 1)[0]
        return head if head in all_states else None

    kept_errors = [e for e in prior.get("errors", []) if _state_of(e) is not None and _state_of(e) not in target_states]
    new_errors = [e for e in result.get("errors", []) if _state_of(e) is not None]
    merged_errors = kept_errors + new_errors
    unresolved = {str(e).split(":", 1)[0] for e in merged_errors}
    kept_warnings = [w for w in prior.get("warnings", []) if _state_of(w) not in target_states and _state_of(w) in unresolved]
    new_warnings = [w for w in result.get("warnings", []) if _state_of(w) in unresolved]
    return merged_errors, kept_warnings + new_warnings


def _commit_generation(run_dir: Path, staging: Path | None, result: dict, target_states: set, all_states: set) -> None:
    """Advance the run's two canonical surfaces — the frames/ generation and the per-state
    failure evidence (extract-failure.json) — as ONE transaction under a single publish_guard.

    `staging` is the new complete frames dir to swap into frames/ on success, or None to leave
    frames/ untouched on failure. The evidence is (re)written from the merged unresolved
    failures, or removed when none remain. Either BOTH surfaces advance or, on any I/O failure,
    both roll back to their pre-commit state (Atomicity/Consistency). Because the whole commit
    holds publish_guard, a reader under read_guard never observes the new frames alongside a
    stale failure record (Isolation) — the old defect where a swapped-in generation and a not-
    yet-merged prior failure were briefly visible together."""
    frames_final = run_dir / "frames"
    evidence = run_dir / "extract-failure.json"
    frames_backup = run_dir / ".frames.sg-backup"
    evidence_backup = run_dir / ".extract-failure.sg-backup"

    def _discard(p: Path) -> None:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()

    with publish_guard(run_dir):
        prior = load_failure_evidence(evidence)  # fail-loud on a corrupt/broken-schema record
        merged_errors, merged_warnings = _merged_failure_lists(prior, result, target_states, all_states)

        _discard(frames_backup)
        _discard(evidence_backup)
        frames_backed_up = False
        frames_swapped = False
        evidence_backed_up = False
        evidence_written = False
        try:
            if staging is not None:
                if frames_final.exists():
                    frames_final.rename(frames_backup)
                    frames_backed_up = True
                staging.rename(frames_final)
                frames_swapped = True
            if evidence.exists():
                evidence.rename(evidence_backup)
                evidence_backed_up = True
            if merged_errors:
                payload = dict(result)
                payload["ok"] = False
                payload["errors"] = merged_errors
                payload["warnings"] = merged_warnings
                atomic_write_text(evidence, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                evidence_written = True
            # else: leave evidence absent — every recorded per-state failure is resolved
        except BaseException:
            # roll BOTH surfaces back to their pre-commit state, in reverse order
            if evidence_written:
                _discard(evidence)
            if evidence_backed_up and not evidence.exists():
                evidence_backup.rename(evidence)
            if frames_swapped:
                _discard(frames_final)
                if frames_backed_up:
                    frames_backup.rename(frames_final)
            elif frames_backed_up and not frames_final.exists():
                frames_backup.rename(frames_final)
            raise
        _discard(frames_backup)
        _discard(evidence_backup)


def _run(args: argparse.Namespace):
    if args.fringe_key_threshold < args.key_threshold:
        raise SystemExit("--fringe-key-threshold must be greater than or equal to --key-threshold")
    if args.fringe_unmix_reach is not None and args.fringe_unmix_reach < 0:
        raise SystemExit("--fringe-unmix-reach must be zero or positive")
    if args.spill_max_fraction is not None and args.spill_max_fraction < 0:
        raise SystemExit("--spill-max-fraction must be zero or positive")

    run_dir = args.run_dir.expanduser().resolve()
    acquire_run_dir_lock(run_dir, "extract_sprite_row_frames")
    try:
        return _run_locked(args, run_dir)
    finally:
        # 작업 단위 상호배제 — atexit 까지 쥐고 있으면 장수 프로세스(테스트 러너 등)가
        # in-process 추출 뒤 heal_run 서브프로세스를 자기 락으로 막는다.
        release_run_dir_lock(run_dir)


def _run_locked(args: argparse.Namespace, run_dir: Path):
    request = load_request(run_dir)
    # Sparse-floor SSoT is the request's subject profile; the CLI flag is an
    # explicit override only. Profile-derived values are NOT stamped into
    # extract_args (frames are a derived cache of raw + request + engine, so a
    # later subject change must re-resolve on heal); explicit flags are.
    args._subject_kind = subject_kind(request)
    args._min_used_pixels_explicit = args.min_used_pixels is not None
    if args.min_used_pixels is None:
        args.min_used_pixels = default_min_used_pixels(request)
    # 크로마 튜너블의 SSoT 는 request JSON `chroma` — CLI 는 명시 override 만.
    # 실제 적용값은 request 에 되써서 런이 스스로 재현 가능하게 남긴다.
    chroma_config = dict(request.get("chroma") or {})
    unmix_reach = (
        args.fringe_unmix_reach
        if args.fringe_unmix_reach is not None
        else int(chroma_config.get("unmix_reach", 4))
    )
    if unmix_reach < 0:
        raise SystemExit("chroma.unmix_reach must be zero or positive")
    spill_max_fraction = (
        args.spill_max_fraction
        if args.spill_max_fraction is not None
        else float(chroma_config.get("spill_max_fraction", 0.005))
    )
    if spill_max_fraction < 0:
        raise SystemExit("chroma.spill_max_fraction must be zero or positive")
    chroma_mode = (
        args.chroma_mode
        if args.chroma_mode is not None
        else str(chroma_config.get("mode", "rgb"))
    )
    if chroma_mode not in ("rgb", "ycbcr"):
        raise SystemExit("chroma.mode must be 'rgb' or 'ycbcr'")
    decontam_mode = (
        args.decontam
        if args.decontam is not None
        else str(chroma_config.get("decontam", "off"))
    )
    if decontam_mode not in ("off", "auto", "palette"):
        raise SystemExit("chroma.decontam must be 'off', 'auto' or 'palette'")
    effective_chroma = {
        **chroma_config,
        "mode": chroma_mode,
        "unmix_reach": unmix_reach,
        "spill_max_fraction": spill_max_fraction,
    }
    # Recorded once it is in play, so a run keyed before decontam existed keeps its request bytes.
    if decontam_mode != "off" or "decontam" in chroma_config:
        effective_chroma["decontam"] = decontam_mode
    decontam_kwargs = {} if decontam_mode == "off" else {"decontam": decontam_mode}
    if effective_chroma != chroma_config:
        request["chroma"] = effective_chroma
        atomic_write_text(
            run_dir / "sprite-request.json",
            json.dumps(request, ensure_ascii=False, indent=2) + "\n",
        )
    states = list(request["states"]) if args.states == "all" else [state.strip() for state in args.states.split(",") if state.strip()]
    cell_width, cell_height, safe_margin_x, safe_margin_y = cell_geometry(request["cell"])
    fit_config = request.get("fit") or {}
    chroma_key = tuple(int(value) for value in request["chroma_key"]["rgb"])
    # Build the new frames in a staging dir, then swap it into place as one generation under
    # publish_guard (below). Extract rewrites many frame files; without this, a live curation
    # reader (serve_curation read_guard) would observe a mix of old/new frame generations
    # mid-extract. The manifest records the FINAL `frames/<state>/...` paths regardless of the
    # staging write location. Writer-writer exclusion stays the separate `.sprite-gen.lock`.
    frames_final = run_dir / "frames"
    frames_root = run_dir / ".frames.sg-staging"
    target = set(states)
    # A subset `--states` re-extract (auto-correction / single-row regeneration) must preserve
    # the states it is NOT rebuilding — the staging generation, which is swapped in whole, is
    # seeded with those states' current frames and carries their prior manifest rows, so the
    # swap replaces only the rebuilt states and never deletes the others (SSoT/Idempotency).
    # Read+validate the prior manifest FIRST, before staging is even created: a corrupt prior
    # generation fails loud here (No Silent Fallback), so the prior generation stays byte-intact
    # and we never publish an incomplete manifest that disagrees with the carried frame tree.
    rows = []
    if frames_final.is_dir():
        prior_manifest = load_frames_manifest(frames_final / "frames-manifest.json")
        rows = [row for row in prior_manifest.get("rows", []) if row.get("state") not in target]
    if frames_root.exists():
        shutil.rmtree(frames_root)
    frames_root.mkdir(parents=True)
    if frames_final.is_dir():
        for state_name in request["states"]:
            if state_name not in target:
                rel = frames_dir_rel(request, state_name).removeprefix("frames/")
                src = frames_final / rel
                if src.is_dir():
                    (frames_root / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(src, frames_root / rel)
    all_errors: list[str] = []
    all_warnings: list[str] = []

    pixel_unfake = bool(fit_config.get("pixel_unfake"))
    # 기본 = 셀과 동일(1:1) — 생성 프롬프트가 "TRUE 셀xN pixel grid" 를 명시하는
    # 현행 레시피에서 원본 그리드를 그대로 따라간다. 청키 2x 룩을 원할 때만
    # 절반 값(예: 셀 64 + 로지컬 32)을 명시한다. (2026-07-05, 이전 기본은 usable//2)
    # 배율·유효 논리 높이의 SSoT 는 `curation.pixel_snap_scale` / `effective_logical_height`
    # 다 — 여기서 식을 손으로 복제하면 소비자(웹뷰·compose)와 조용히 갈린다 (원칙 1).
    pp_scale = pixel_snap_scale(request) or 1
    logical_height = effective_logical_height(request) or cell_height
    logical_width = max(1, cell_width // pp_scale)
    # 정수 격자가 선언을 반올림해 무효로 만들면 관측시킨다 — 아무 일도 안 하는 값이
    # 요청 파일에 남아 라벨과 지문만 오염시키던 조용한 실패다 (원칙 6, maintainer 2026-07-25).
    declared_logical_height = fit_config.get("logical_height")
    if (pixel_unfake and declared_logical_height is not None
            and int(declared_logical_height) != logical_height):
        all_warnings.append(
            f"fit.logical_height={declared_logical_height} is not applied as declared: the "
            f"integer logical grid rounds it to {logical_height} (scale {pp_scale}x). Declare a "
            f"divisor of cell height {cell_height} (e.g. {cell_height}, {max(1, cell_height // 2)}) "
            f"for a chunkier look, or drop the key — as declared it changes nothing.")
    pp_detail_bias = bool(fit_config.get("detail_bias", True))
    # 기본 48 (24 에서 상향, 2026-07-17): run-wide 팔레트는 배치의 모든 행이 나눠 쓴다.
    # 24 는 다상태 배치(hero 36상태×150프레임)에서 희소 포인트 컬러를 굶겼다 —
    # population median-cut 이 금색(머리끈·목걸이) 클러스터를 갈색 박스에 흡수해
    # 디테일이 통째로 사라졌다 (실측: 24색 배치 팔레트에서 금색 최근접 ΔRGB 59,
    # 48색이면 5.5). 레트로 감축이 필요한 런은 request `fit.palette_size` 로 명시한다.
    palette_size = int(fit_config.get("palette_size", 48))
    # 원본 화질 표시 쌍둥이(orig/)의 배율 상한: 배율 자체는 행별 네이티브 비율로
    # 정한다 (_orig_display_scale). 고정 ×4 캡은 raw 피치가 4px 를 넘는 런에서
    # 다운스케일 열화를 만들어 "원본" 뷰가 원본이 아니게 됐다 (synthetic_fixture_b down
    # 피치 ~19px → 4.8× 축소 뭉갬, maintainer 발견 2026-07-23). 상한은 파일/메모리
    # 바운드일 뿐 화질 정책이 아니다.
    plain_display_cap = max(1, 2048 // max(1, cell_width, cell_height))

    def _orig_display_scale(components: list, frames: list) -> int:
        """행별 orig 트윈 배율 = 네이티브 비율의 ceil (다운스케일 금지, cap 바운드).

        ratio = 컴포넌트 crop 크기 / 최종 콘텐츠 bbox 크기 (축별 max). S ≥ ratio 면
        fit_component_to_bbox 의 리샘플이 경미한 업스케일이 돼 raw 블록이 뭉개지지
        않는다. 트윈 = 셀×S 정사각이라는 셀 좌표 계약은 그대로다."""
        ratio = 0.0
        for component, frame in zip(components, frames):
            frame_bbox = frame.getbbox()
            comp_bbox = component.getbbox()
            if frame_bbox is None or comp_bbox is None:
                continue
            ratio = max(
                ratio,
                (comp_bbox[2] - comp_bbox[0]) / max(1, frame_bbox[2] - frame_bbox[0]),
                (comp_bbox[3] - comp_bbox[1]) / max(1, frame_bbox[3] - frame_bbox[1]),
            )
        return min(plain_display_cap, max(1, math.ceil(ratio)))

    pending: list = []

    def finalize_state(state: str, frames: list, frame_count: int, method: str,
                       plain_frames: list | None = None, orig_frames: list | None = None,
                       input_grids: list | None = None, labels: list | None = None,
                       takes: list | None = None) -> None:
        rel_dir = frames_dir_rel(request, state)  # e.g. frames/down/idle (taxonomy) | frames/down_idle (legacy)
        state_dir = frames_root / rel_dir.removeprefix("frames/")
        state_dir.mkdir(parents=True, exist_ok=True)
        output_paths = []
        for index, frame in enumerate(frames):
            output = state_dir / f"frame-{index}.png"
            atomic_save_image(frame, output)
            output_paths.append(f"{rel_dir}/frame-{index}.png")  # final location (staging is swapped in)
        # 픽셀 언페이크 전 원본 변형(.plain.png) — curation.json `pixel_unfake: false`
        # 굽기(compose)가 이 셀 크기 쌍둥이를 읽는다. 아틀라스 슬롯 = 셀 크기라 여긴
        # 셀 해상도로 유지한다 (compose_atlas 가 정확히 셀 크기를 요구, 기하 불변).
        plain_paths = []
        if plain_frames is not None:
            for index, frame in enumerate(plain_frames):
                output = state_dir / f"frame-{index}.plain.png"
                atomic_save_image(frame, output)
                plain_paths.append(f"{rel_dir}/frame-{index}.plain.png")
        # 원본 화질 표시용 고해상 쌍둥이 — 큐레이션뷰 pp 해제 토글이 읽는다. 셀 크기
        # .plain.png 는 확대 시 흐리므로 별도 서브폴더(orig/)에 S×셀로 굽는다. 서브폴더라
        # frame-*.png glob 소비자(inspect/measure/compose)와 충돌하지 않고 순수 표시용이다.
        orig_paths = []
        if orig_frames is not None:
            orig_dir = state_dir / "orig"
            orig_dir.mkdir(parents=True, exist_ok=True)
            for index, frame in enumerate(orig_frames):
                output = orig_dir / f"frame-{index}.png"
                atomic_save_image(frame, output)
                orig_paths.append(f"{rel_dir}/orig/frame-{index}.png")

        errors, warnings, frame_records = inspect_frames(frames, chroma_key, args)
        all_errors.extend(f"{state}: {error}" for error in errors)
        all_warnings.extend(f"{state}: {warning}" for warning in warnings)
        row = {
            "state": state,
            "frames": frame_count,
            "method": method,
            "files": output_paths,
            "frame_records": frame_records,
            "ok": not errors,
            # 파생 캐시 키 — 이 행을 구운 엔진 리비전. heal_run 이 현재 엔진과
            # 비교해 stale 행을 raw 에서 자동 재유도한다 (self-heal).
            "engine_revision": engine_revision(),
        }
        if labels and any(labels):
            row["labels"] = labels
        if takes:
            row["takes"] = takes
        if plain_paths:
            row["plain_files"] = plain_paths
        if orig_paths:
            row["orig_files"] = orig_paths
        if input_grids is not None and any(g is not None for g in input_grids):
            # 프레임별 검출 입력 격자(셀 좌표 절단선) — 큐레이터 원본 뷰 오버레이용
            row["input_grids"] = input_grids
        rows.append(row)

    def _load_strip(state: str, tag: str, rel: str, frame_count: int) -> Image.Image | None:
        raw_path = run_dir / rel
        if not raw_path.is_file():
            all_errors.append(f"{tag}: missing raw strip {raw_path}")
            return None
        with Image.open(raw_path) as opened:
            if chroma_mode == "ycbcr":
                ycc_notes: list[str] = []
                strip = remove_chroma_background_ycbcr(opened, chroma_key, ycc_notes, **decontam_kwargs)
                all_warnings.extend(f"{tag}: {note}" for note in ycc_notes)
            else:
                strip = remove_chroma_background(
                    opened,
                    chroma_key,
                    args.key_threshold,
                    args.fringe_key_threshold,
                    args.fringe_delta,
                    unmix_reach=unmix_reach,
                    spill_max_fraction=spill_max_fraction,
                    **decontam_kwargs,
                )
        return separate_fused_poses(strip, frame_count, fit_config, args.segmentation, state)

    def _snap_strip(tag: str, strip: Image.Image, frame_count: int) -> dict[str, Any] | None:
        """한 생성 스트립(primary 또는 take)을 컴포넌트 분리→합의 스냅→캡까지.

        프레임별 픽셀 언페이크 (2026-07-05 재설계, 2026-07-20 per-frame 확정): 포즈
        컴포넌트를 먼저 분리한 뒤 각 프레임마다 피치·위상을 독립 검출해 **자기
        검출값으로** 스냅한다. 스트립 전역 단일 격자는 프레임 간 위상 드리프트로
        미끄러졌고(maintainer 관찰: "격자가 픽셀에 안 맞음"), 합의 피치 강제는 측정차를
        폭 전체에 누적시켰다(maintainer 관찰: "눈이 반쪽", plan per-frame-pixel-grid).
        스트립(=한 번의 생성) 단위 합의는 검출 실패 프레임의 fallback 전용이다
        — 테이크마다 생성이 달라 블록 크기가 다를 수 있어 합의도 스트립 단위다.
        """
        images = extract_component_images(strip, frame_count)
        method = "components"
        if images is None:
            if not args.allow_slot_fallback:
                all_errors.append(f"{tag}: could not extract {frame_count} sprite components")
                return None
            slot_width = strip.width / frame_count
            images = [
                strip.crop((round(i * slot_width), 0, round((i + 1) * slot_width), strip.height))
                for i in range(frame_count)
            ]
            method = "slots-explicit"
        images = tighten_components(images)
        # 프레임 자체 검출 (피치·위상) 이 1순위 진실이다. 프레임별 검출값의
        # 중앙값(합의)은 검출 실패 프레임의 fallback 전용으로만 계산한다 —
        # 합의를 프레임에 강제하면 측정차가 폭 전체에 누적된다 (아래 스냅 루프 주석).
        # 피치는 소수이고 축마다 다를 수 있다 — AI 가 그린 블록은 정수 픽셀로 떨어지지
        # 않고(예: 17.24), 비균등 리스케일된 생성물은 가로/세로 블록 크기가 어긋난다.
        # 정수로 반올림하면 그 오차가 폭 전체에 누적돼 셀 경계가 블록 한가운데를 지난다.
        # 측정은 소수·축별로 하고, 격자선은 `grid_edges` 가 정수로 확정한다.
        hint = int(fit_config.get("pitch_hint", 0))
        grids = [detect_pixel_grid(component) for component in images]

        def _consensus(axis: int) -> float:
            confident = sorted(g[0][axis] for g in grids if g[0][axis] >= 2.0)
            if confident:
                # 붕괴한 프레임(참 피치의 약수로 떨어진 값)이 중앙값을 오염시킨다 —
                # down_carry_run 은 6 프레임 중 절반이 3.00 으로 무너져 합의가 5.00 이 됐다.
                # 스트립 안에서 참 피치는 거의 같으므로, 최대값의 60% 미만은 붕괴로 보고 버린다.
                ceiling = confident[-1]
                trusted = [p for p in confident if p >= ceiling * 0.6]
                dropped = len(confident) - len(trusted)
                if dropped:
                    all_warnings.append(
                        f"{tag}: dropped {dropped} collapsed per-frame pitch(es) below {ceiling * 0.6:.2f}"
                    )
                return trusted[len(trusted) // 2]
            if hint >= 2:
                all_warnings.append(
                    f"{tag}: pitch from fit.pitch_hint={hint} (all per-frame detections inconclusive)"
                )
                return float(hint)
            strip_pitch, _ = detect_pixel_grid(strip)
            if strip_pitch[axis] >= 2.0:
                all_warnings.append(
                    f"{tag}: pitch from whole-strip detection={strip_pitch[axis]:.2f}"
                )
            return strip_pitch[axis]

        consensus_x, consensus_y = _consensus(0), _consensus(1)
        # 세컨드 오피니언 (perfectpixel unfake 이식): 동일색 런 최빈값으로 축별
        # 피치를 따로 추정해 합의 피치와 교차검증한다. 경고 전용 — 스냅은 아래에서
        # 계속 detect_pixel_grid 합의만 쓴다 (자동 교체 금지, No Silent Fallback).
        runlen_axes = [estimate_pixel_grid_runlen(component) for component in images]

        def _runlen_consensus(axis: int) -> float:
            confident = sorted(e[axis] for e in runlen_axes if e[axis] >= 2.0)
            return confident[len(confident) // 2] if confident else 1.0

        for note in crosscheck_pitch_runlen(
            (consensus_x, consensus_y), (_runlen_consensus(0), _runlen_consensus(1))
        ):
            all_warnings.append(f"{tag}: {note}")
            print(f"[pitch-crosscheck] {tag}: {note}", file=sys.stderr)
        # 피치 중재 (하모닉 오검출 방어): 세컨드 오피니언과 크게 어긋나면 셀 균일도
        # 실측으로 승자 채택 — 채택 결과는 경고로 관측된다 (arbitrate_pitch docstring).
        # 위상은 주고받지 않는다 — 채점도 스냅도 각자 `_best_phase` 로 실측한다
        # (plan `frame-pitch-consensus-eats-a-row`). 프레임 검출 위상을 넘겨 그대로
        # 돌려받던 인자/반환은 읽는 곳이 없어 걷어냈다.
        (consensus_x, consensus_y), _pitch_src = arbitrate_pitch(
            images, (consensus_x, consensus_y),
            (_runlen_consensus(0), _runlen_consensus(1)), tag, all_warnings)
        # 프레임 자체 검출 격자가 1순위 진실이다 (maintainer 2026-07-20, plan
        # sprite-gen/per-frame-pixel-grid): 합의 피치를 프레임에 강제하면 측정차
        # (0.5px/셀 수준)가 폭 전체에 누적돼 경계가 블록 중앙을 지난다 (회귀
        # synthetic_fixture_a down_jump frame-0: 합의 13.00 vs 자체 12.50 → 눈이 반쪽).
        # 단 own 채택은 합의 '피치 패밀리'(비율 1.1) 이내에서만이다 — 하모닉/붕괴
        # 오검출(×2/×3·÷2/÷3)까지 믿으면 한 프레임의 거대 native 가
        # conform_row_logical 의 행 일관 배율을 끌어내려 행 전체가 붕괴한다
        # (회귀 synthetic_fixture_a up_run frame-2, 2026-07-22 — 판정 근거는
        # resolve_frame_pitch docstring). 패밀리 밖 = 합의로 스냅하고 프레임별
        # 경고로 관측한다 (No Silent Fallback). 합의는 자체 검출이 실패한
        # 프레임의 fallback 이기도 하다. 기록(cut_edges)은 실제 샘플링한 그
        # 선이라 표시 격자 = 샘플링 진실 (maintainer 확정 2026-07-18) 유지.
        snapped = []
        cut_edges: list[tuple[list[int], list[int]] | None] = []
        used_pitches: list[tuple[float, float]] = []
        for index, (component, ((own_x, own_y), _own_phase)) in enumerate(zip(images, grids)):
            if min(own_x, own_y) >= 2.0:
                (use_x, use_y), outlier = resolve_frame_pitch(
                    (own_x, own_y), (consensus_x, consensus_y))
                if outlier:
                    all_warnings.append(
                        f"{tag}: frame {index} own pitch {own_x:.2f}x{own_y:.2f} is outside the "
                        f"consensus pitch family {consensus_x:.2f}x{consensus_y:.2f} — "
                        f"harmonic/collapsed misdetection; snapped at consensus")
            elif min(consensus_x, consensus_y) >= 2.0:
                use_x, use_y = consensus_x, consensus_y
                all_warnings.append(
                    f"{tag}: frame {index} pitch detection inconclusive — consensus fallback "
                    f"{consensus_x:.2f}x{consensus_y:.2f}")
            else:
                snapped.append(component)
                cut_edges.append(None)
                used_pitches.append((1.0, 1.0))
                continue
            # 위상은 엣지 히스토그램의 무게중심(`_axis_refine` 반환값)이 아니라 실제
            # 셀 균일도로 다시 고른다 (maintainer 관측 2026-07-25 "눈이 8칸이어야 하는데 7칸",
            # synthetic_fixture_b down_jump frame-0). 히스토그램 위상은 1차원 신호의 근사라
            # 참 위상에서 반 칸까지 밀린다 — 실측: 피치 13.00 에서 검출 위상 y=2.02 vs
            # 실측 최적 8.12 (차이 6.1 ≈ pitch/2). `refine_edges_to_boundaries` 는
            # ±pitch/3 창 안에서만 절단선을 당기므로 이 크기의 위상 오차는 구조적으로
            # 복구되지 않고, 눈 4행이 3행으로 병합됐다. 피치가 고정된 상태에서 위상만
            # 비교하므로 균일도 지표의 '거친 격자 편애' 편향은 개입하지 않는다.
            frame_phase = _best_phase(component, (use_x, use_y))
            xs = grid_edges(component.width, use_x, frame_phase[0])
            ys = grid_edges(component.height, use_y, frame_phase[1])
            xs, ys = refine_edges_to_boundaries(component, xs, ys, (use_x, use_y))
            snapped.append(snap_by_edges(component, xs, ys, pp_detail_bias))
            cut_edges.append((xs, ys))
            used_pitches.append((use_x, use_y))
        rounded = [(round(px_, 2), round(py_, 2)) for (px_, py_) in used_pitches]
        def _pitch_repr(pair: tuple[float, float]):
            return pair[0] if abs(pair[0] - pair[1]) < 0.05 else pair
        pitch = (_pitch_repr(rounded[0]) if len(set(rounded)) == 1
                 else [_pitch_repr(pair) for pair in rounded])
        # 눌림 없음 — 스냅된 네이티브 논리 크기를 유지한다 (maintainer 확정 2026-07-14,
        # 옵트인 잔재까지 완전 제거 2026-07-17): 계약(logical_height)으로의 conform
        # 축소는 칸을 병합해 디테일을 갈라먹는다. 과거의 `fit.conform: true` 옵트인은
        # 제거된 결정이 플래그 하나로 되살아나는 회귀 경로였다 (회귀 2026-07-17:
        # 높이 통일 시도가 이 플래그로 재발) — 선언되어 있으면 요란하게 거부한다.
        # 물리 한계(셀에서 바닥 마진만 지킴)만 캡으로 강제하고, 캡에 걸리면 관측
        # 가능하게 경고한다. 캡과 안전영역의 판정은 `safe_area_fit` 하나다.
        if fit_config.get("conform") is not None:
            raise SystemExit(
                "fit.conform was removed (maintainer 2026-07-14/17): native logical size is "
                "the only behavior — squashing to logical_height merges cells and eats "
                "detail. Remove the key from sprite-request.json.")
        fit = safe_area_fit([(s.width, s.height) for s in snapped], cell_width, cell_height,
                            safe_margin_x, safe_margin_y, pp_scale)
        (cap_w, cap_h), (safe_w, safe_h) = fit["cap"], fit["safe"]
        over = [f"{i}:{snapped[i].width}x{snapped[i].height}" for i in fit["over"]]
        if over:
            all_warnings.append(
                f"{tag}: native logical exceeds the physical cap "
                f"{cap_w}x{cap_h} — capped frames (reroll candidates): {', '.join(over)}")
        # 안전영역(사방 여백 준수 상한)은 넘었지만 물리캡 이내 = 여백 침범.
        # 리롤 대상 아님 — 정보성 알림만 (maintainer 확정 2026-07-14).
        soft = [f"{i}:{snapped[i].width}x{snapped[i].height}" for i in fit["soft"]]
        if soft:
            all_warnings.append(
                f"{tag}: frames exceed the safe area {safe_w}x{safe_h} but fit "
                f"within the margin zone (informational, no action): {', '.join(soft)}")
        logical_frames = conform_row_logical(snapped, cap_w, cap_h, pp_detail_bias)
        return {"method": method, "pitch": pitch, "logical": logical_frames,
                "components": images, "cut_edges": cut_edges}

    total_steps = len(states) * 2  # 1패스(컴포넌트/스냅) + 2패스(팔레트/배치)
    progress_step = 0
    for state in states:
        _write_progress(run_dir, "extract", progress_step, total_steps, state, "components")
        progress_step += 1
        if state not in request["states"]:
            raise SystemExit(f"unknown state in request: {state}")
        state_cfg = request["states"][state]
        frame_count = int(state_cfg["frames"])
        takes_cfg = state_cfg.get("takes") or []
        if takes_cfg and not pixel_unfake:
            all_errors.append(f"{state}: takes require fit.pixel_unfake")
            continue
        if not pixel_unfake:
            strip = _load_strip(state, state, raw_rel(request, state), frame_count)
            if strip is None:
                continue
            frames = extract_component_frames(strip, frame_count, cell_width, cell_height, safe_margin_x, safe_margin_y, fit_config)
            method = "components"
            if frames is None:
                if not args.allow_slot_fallback:
                    all_errors.append(f"{state}: could not extract {frame_count} sprite components")
                    continue
                frames = extract_slot_frames(strip, frame_count, cell_width, cell_height, safe_margin_x, safe_margin_y, fit_config)
                method = "slots-explicit"
            finalize_state(state, frames, frame_count, method)
            continue
        # 테이크 1급 계약: 한 상태의 프레임 풀 = primary 스트립 + 선언된 테이크들.
        # 각 스트립은 독립 생성이므로 따로 스냅하고(스트립별 합의 피치), 행 정합·
        # 팔레트·배치는 행 단위로 함께 한다. 어느 스트립 하나라도 실패하면 행 전체를
        # 이전 세대로 남긴다 (Atomicity — 부분 풀 게시 금지).
        specs: list[tuple[str, str, int, str]] | None = [
            (state, raw_rel(request, state), frame_count, "")]
        for take in takes_cfg:
            label = str(take.get("label") or "")
            if not label or "/" in label or label.startswith("."):
                all_errors.append(f"{state}: take needs a filesystem-safe label: {take!r}")
                specs = None
                break
            specs.append((f"{state}[{label}]", take_raw_rel(request, state, label),
                          int(take["frames"]), label))
        if specs is None:
            continue
        parts: list[dict[str, Any]] | None = []
        for tag, rel, count, label in specs:
            strip = _load_strip(state, tag, rel, count)
            part = _snap_strip(tag, strip, count) if strip is not None else None
            if part is None:
                parts = None
                break
            part.update({"label": label, "count": count, "raw": rel})
            parts.append(part)
        if parts is None:
            continue
        registered = register_row_frames([f for p in parts for f in p["logical"]])
        labels = None
        takes_summary = None
        if len(parts) > 1:
            labels = [
                f"{p['label']}#{i}" if p["label"] else ""
                for p in parts for i in range(p["count"])
            ]
            starts = [0]
            for p in parts[:-1]:
                starts.append(starts[-1] + p["count"])
            takes_summary = [
                {"label": p["label"] or None, "start": start, "frames": p["count"], "raw": p["raw"]}
                for p, start in zip(parts, starts)
            ]
        # 전/후 비교 쌍둥이(plain/orig)는 픽셀 언페이크 프레임의 최종 콘텐츠 bbox 가
        # 확정된 뒤(아래 pending 루프) 같은 풋프린트에 앉힌다 — 여기서는 원본
        # 컴포넌트만 보관한다. (이전: legacy fit 이 가용영역을 채워 pp 결과보다
        # 크게 앉음 → 토글 순간 크기가 튀어 품질 비교가 안 됐다.)
        pending.append({
            "state": state, "frame_count": len(registered), "method": parts[0]["method"],
            "pitch": parts[0]["pitch"] if len(parts) == 1 else [p["pitch"] for p in parts],
            "frames": registered,
            "components": [c for p in parts for c in p["components"]],
            "cut_edges": [e for p in parts for e in p["cut_edges"]],
            "labels": labels, "takes": takes_summary,
        })

    if pixel_unfake and pending:
        # 팔레트는 런 전체(모든 state 의 논리 프레임)에서 한 번 뽑아 공유한다 —
        # 프레임/행 간 색 흔들림(플리커) 제거 + 아이덴티티 색 고정.
        pinned = None if getattr(args, "repalette", False) else load_pinned_palette(run_dir)
        if pinned is not None:
            palette = pinned
            all_warnings.append(f"palette: pinned ({len(palette)} colors, {PALETTE_LOCK})")
        else:
            palette = build_shared_palette([f for entry in pending for f in entry["frames"]], palette_size)
            write_pinned_palette(run_dir, palette,
                                 f"batch:{','.join(entry['state'] for entry in pending)}")
            all_warnings.append(f"palette: rebuilt+pinned ({len(palette)} colors → {PALETTE_LOCK})")
        total_steps = progress_step + len(pending)  # 스킵된 행 반영 재산정
        outline_cfg = fit_config.get("outline", True)
        for entry in pending:
            _write_progress(run_dir, "extract", progress_step, total_steps, entry["state"], "pixel unfake")
            progress_step += 1
            quantized = [apply_palette(frame, palette) for frame in entry["frames"]]
            if outline_cfg:
                strength = 0.62 if outline_cfg is True else float(outline_cfg)
                quantized = [enforce_outline(frame, strength) for frame in quantized]
            left, top = row_placement(quantized, cell_width, cell_height, safe_margin_y, pp_scale, fit_config)
            ground_frames = bool(fit_config.get("ground_frames", True))
            # alpha-centroid 는 프레임별 가로 배치 — 행 union 공동 left 로는
            # register_row_frames 의 정합 잔차가 지터로 남는다.
            per_frame_centroid = str(fit_config.get("align_x", "foot-centroid")).lower() == "alpha-centroid"
            align_y = str(fit_config.get("align_y", "bottom")).lower()
            frames = [
                place_row_frame(
                    frame, cell_width, cell_height, pp_scale,
                    _alpha_centroid_row_left(frame, cell_width, pp_scale) if per_frame_centroid else left,
                    top, safe_margin_y, ground_frames, align_y)
                for frame in quantized
            ]
            # 전/후 비교 쌍둥이: 픽셀 언페이크 프레임의 최종 콘텐츠 bbox 와 같은 풋프린트에
            # 원본 컴포넌트를 앉힌다 (plain=셀 크기 굽기용, orig=S×셀 표시용). 빈 프레임은
            # 관측 가능하게 스킵 — 조용한 폴백 없음.
            plain_frames = []
            orig_scale = _orig_display_scale(entry["components"], frames)
            orig_frames = [] if orig_scale > 1 else None
            input_grids: list[dict | None] = []
            for index, (component, frame) in enumerate(zip(entry["components"], frames)):
                frame_bbox = frame.getbbox()
                if frame_bbox is None:
                    all_warnings.append(
                        f"{entry['state']}: frame {index} is empty — plain/orig twin skipped")
                    plain_frames.append(Image.new("RGBA", (cell_width, cell_height), (0, 0, 0, 0)))
                    if orig_frames is not None:
                        orig_frames.append(Image.new(
                            "RGBA", (cell_width * orig_scale, cell_height * orig_scale), (0, 0, 0, 0)))
                    input_grids.append(None)
                    continue
                plain, mapping = fit_component_to_bbox(component, cell_width, cell_height, frame_bbox)
                plain_frames.append(plain)
                if orig_frames is not None:
                    orig_frames.append(fit_component_to_bbox(
                        component, cell_width, cell_height, frame_bbox, orig_scale)[0])
                # 검출된 입력 격자(실제 절단선)를 쌍둥이(셀) 좌표로 매핑해 manifest 에 남긴다
                edges = entry["cut_edges"][index] if index < len(entry["cut_edges"]) else None
                if edges is not None and mapping is not None:
                    input_grids.append({
                        "x": [round(mapping["left"] + (e - mapping["crop_x"]) * mapping["ratio"], 1)
                              for e in edges[0]],
                        "y": [round(mapping["top"] + (e - mapping["crop_y"]) * mapping["ratio"], 1)
                              for e in edges[1]],
                    })
                else:
                    input_grids.append(None)
            finalize_state(entry["state"], frames, entry["frame_count"], entry["method"],
                           plain_frames=plain_frames, orig_frames=orig_frames,
                           input_grids=input_grids, labels=entry.get("labels"),
                           takes=entry.get("takes"))
        all_warnings.append(
            "pixel unfake: pitch=%s scale=%dx logical<=%dx%d palette=%d"
            % (",".join(str(entry["pitch"]) for entry in pending), pp_scale, logical_width, logical_height, len(palette))
        )

    result = {
        "ok": not all_errors,
        "engine": "component-row",
        "engine_revision": engine_revision(),
        # 이 생성에 실제로 쓰인 비기본 추출 플래그 — heal_run 재유도가 같은
        # 조건으로 재현하도록 기록한다 (조건 드리프트 = 조용한 결과 변화 금지).
        "extract_args": _nondefault_args(args),
        "run_dir": str(run_dir),
        "cell": request["cell"],
        "chroma_key": request["chroma_key"],
        "rows": rows,
        "errors": all_errors,
        "warnings": all_warnings,
    }
    all_state_names = set(request["states"])
    _clear_progress(run_dir)
    if result["ok"]:
        # Whole-generation atomicity: canonical frames/ only ever holds a COMPLETE generation.
        # Swap in the staging generation AND resolve this attempt's target states in the failure
        # evidence as ONE publish transaction — a subset --states extract does not touch the other
        # states, so their unresolved failures are preserved (file removed only once nothing
        # remains, Consistency), and a reader never sees new frames beside a stale failure record.
        atomic_write_text(frames_root / "frames-manifest.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        _commit_generation(run_dir, frames_root, result, target, all_state_names)
    else:
        # A failed extract — FIRST or re-extract — never publishes a partial/ok:false generation
        # to canonical frames/ (strict Atomicity: the operation fully succeeds or leaves canonical
        # state untouched). The per-state failure signal is recorded OUTSIDE frames/ as
        # extract-failure.json so it stays observable (No Silent Fallback) and still drives the
        # automatic correction loop (inspect._manifest_state_notes reads it → score → hint →
        # regenerate). The evidence merges per state, so one state's failure never overwrites
        # another's still-unresolved failure. A failed re-extract leaves the prior complete
        # generation byte-intact. Exit code 1 + printed errors signal the failure.
        _commit_generation(run_dir, None, result, target, all_state_names)
        shutil.rmtree(frames_root, ignore_errors=True)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def _nondefault_args(args: argparse.Namespace) -> dict[str, Any]:
    """파서 기본값과 다른 플래그만 추린다 (run_dir/states 제외) — manifest 기록용."""
    diff: dict[str, Any] = {}
    for action in _build_parser()._actions:
        dest = action.dest
        if dest in ("help", "run_dir", "states"):
            continue
        if dest == "min_used_pixels" and not getattr(args, "_min_used_pixels_explicit", True):
            continue  # 프로필 유래값 — heal 은 request 의 subject 에서 재해소한다
        value = getattr(args, dest, None)
        if value != action.default:
            diff[dest] = value
    return diff


_ENGINE_REVISION: str | None = None
_ENGINE_REVISION_KEY: tuple | None = None


def engine_revision() -> str:
    """추출 엔진 리비전 = 엔진 소스 해시. 프레임 파일은 (raw + request + 엔진)의
    파생 캐시고 이 값이 캐시 키다 — 행 스탬프와 불일치하면 stale.

    캐시 키 = 소스 파일 mtime. 프로세스-수명 캐시는 장수 프로세스(큐레이션 서버)가
    엔진 갱신 후에도 옛 리비전을 들고 있게 만들어, 새 리비전으로 스탬프하는
    서브프로세스 힐과 서로를 stale 로 보는 **힐 무한 루프**를 만들었다
    (회귀 2026-07-17: 3초 폴링마다 전체 36행 재추출 재점화 — 뷰 빈 화면, maintainer 발견)."""
    global _ENGINE_REVISION, _ENGINE_REVISION_KEY
    # The engine revision hashes this module plus layout.py. They no longer sit in
    # the same folder (extract=frames/, layout=spec/), so resolve layout by its
    # module file, not as a sibling of __file__. The modules whose code the extraction runs
    # are hashed with it: decontam, and the key fields and mask morphology it shares with
    # decontam (keyfield, morph) — a change in either re-derives the frames like one here.
    # The keying and pixel-grid code that moved out of this file (chroma_key, chroma_ycc,
    # pixel_grid) is hashed as if it were still here.
    import sprite_gen.spec.layout as _layout_module
    from sprite_gen.frames import chroma_key as _chroma_key_module, chroma_ycc as _chroma_ycc_module
    from sprite_gen.frames import keyfield as _keyfield_module, morph as _morph_module
    from sprite_gen.frames import pixel_grid as _pixel_grid_module
    sources = (Path(__file__), Path(_layout_module.__file__), Path(decontam_module.__file__),
               Path(_keyfield_module.__file__), Path(_morph_module.__file__),
               Path(_chroma_key_module.__file__), Path(_chroma_ycc_module.__file__), Path(_pixel_grid_module.__file__))
    key = tuple(source.stat().st_mtime_ns for source in sources)
    if _ENGINE_REVISION is None or _ENGINE_REVISION_KEY != key:
        digest = hashlib.sha256()
        for source in sources:
            digest.update(source.read_bytes())
        _ENGINE_REVISION = digest.hexdigest()[:12]
        _ENGINE_REVISION_KEY = key
    return _ENGINE_REVISION


def heal_run(run_dir: Path | str) -> dict[str, Any]:
    """파생 프레임 캐시의 자가치유 — 소비자(큐레이션 뷰/합성/내보내기) 진입점이 부른다.

    큐레이션 뷰에는 '재추출' 이라는 개념이 없다 (maintainer 확정 2026-07-14): 뷰가
    보여주는 것은 항상 (raw + request + 현재 엔진 + 큐레이션)의 실시간 결과다.
    frames/ 는 그 파생 캐시일 뿐이므로, 행의 engine_revision 이 현재 엔진과
    다르면 raw 에서 조용히 다시 굽는다. raw 가 없는 행은 재료가 없어 재유도할
    수 없다 — 지우지도 속이지도 않고 그대로 두되 노트로 관측 가능하게 남긴다.
    재추출은 상태별 독립 서브프로세스로 격리해 돌린다 — 한 행의 검증 실패가
    다른 행의 스왑을 막지 않고(부분 실패의 전체 연좌 금지, 2026-07-22), 실패한
    행은 스테이징+스왑 경로가 이전 세대를 바이트 그대로 남긴다 (Atomicity).
    """
    run_dir = Path(run_dir)
    report: dict[str, Any] = {"healed": [], "kept_stale": [], "notes": []}
    manifest_path = run_dir / "frames" / "frames-manifest.json"
    request_path = run_dir / REQUEST_FILENAME
    if not manifest_path.is_file() or not request_path.is_file():
        return report
    manifest = load_frames_manifest(manifest_path)
    request = load_request(run_dir)   # 게이트 경유 — heal 도 은퇴 키 런에서 돈다
    current = engine_revision()
    # 확정 행 동결 (maintainer 확정 2026-07-18): curation states.<state>.frozen == true 인
    # 행은 사용자가 승인·편집을 끝낸 확정본 — 엔진이 바뀌어도 자가치유가 절대
    # 재유도하지 않는다 (회귀: 서버 가동 중 엔진 편집 → heal 이 확정 down_idle 을
    # 재양자화해 드리프트). 해제는 사용자가 frozen 을 끄는 것뿐 — 관측 노트로 남긴다.
    frozen: set[str] = set()
    curation_path = run_dir / "curation.json"
    if curation_path.is_file():
        try:
            cur_states = json.loads(curation_path.read_text(encoding="utf-8")).get("states", {})
            frozen = {s for s, e in cur_states.items() if isinstance(e, dict) and e.get("frozen")}
        except ValueError:
            pass  # 손상 사이드카는 로드 경로가 별도로 보고한다
    frozen_kept = []
    for row in manifest.get("rows", []):
        state = row.get("state")
        if state not in request.get("states", {}) or row.get("engine_revision") == current:
            continue
        if state in frozen:
            frozen_kept.append(state)
            continue
        raws = [raw_rel(request, state)] + [
            take_raw_rel(request, state, str(take.get("label") or ""))
            for take in (request["states"][state].get("takes") or [])
        ]
        if all((run_dir / rel).is_file() for rel in raws):
            report["healed"].append(state)
        else:
            report["kept_stale"].append(state)
    if frozen_kept:
        report["frozen"] = frozen_kept
        report["notes"].append("frozen rows kept (user-approved, heal skipped): " + ", ".join(frozen_kept))
    if report["kept_stale"]:
        report["notes"].append(
            "stale rows kept as-is (raw missing, cannot re-derive): "
            + ", ".join(report["kept_stale"]))
    if report["healed"]:
        # 서브프로세스로 재추출한다: run-dir 락은 atexit 해제라, 장수 프로세스
        # (큐레이션 서버)가 인프로세스로 추출하면 락을 영구 보유해 이후의
        # compose/export 서브프로세스가 전부 죽는다. 자식 프로세스면 락 수명이
        # 그 실행으로 끝나고, 추출의 SystemExit 도 격리된다.
        #
        # 상태별 격리 (2026-07-22): 전체를 한 서브프로세스로 돌리고 exit code
        # 하나로 판정하면 부분 실패가 전체 실패로 연좌된다 (회귀 synthetic_fixture_a
        # heal — 검증 실패 6행이 33행 전체를 "재계산 실패"로 묶고 통과 가능한
        # 27행까지 이전 세대에 남겼다). 상태마다 독립 실행하면 통과 행은 그대로
        # 스왑되고(추출 커밋은 원래 상태 단위 병합) 실패 행만 failed 로 남는다.
        # extract-failure.json 병합/해소는 _commit_generation 이 상태 단위로
        # 처리하므로 durable 재시도 차단 evidence 도 호출 횟수와 무관하게
        # 미해결 상태 전체를 유지한다.
        extract_flags: list[str] = []
        for dest, value in (manifest.get("extract_args") or {}).items():
            flag = "--" + dest.replace("_", "-")
            if isinstance(value, bool):
                if value:
                    extract_flags.append(flag)
            else:
                extract_flags.extend([flag, str(value)])
        # 자식은 부모의 sys.path 조작(스크립트 shim 등)을 상속하지 않는다 —
        # 패키지 루트를 PYTHONPATH 로 명시해 어디서 불러도 -m 이 뜬다.
        env = dict(os.environ)
        env["PYTHONPATH"] = (str(Path(__file__).resolve().parents[1])
                             + os.pathsep + env.get("PYTHONPATH", ""))
        candidates = report["healed"]
        report["healed"] = []
        report["failed"] = []
        for state in candidates:
            cmd = [sys.executable, "-m", "sprite_gen.frames.extract",
                   "--run-dir", str(run_dir), "--states", state, *extract_flags]
            try:
                proc = local_tool.run(cmd, seconds=local_tool.CHILD_SECONDS, what=f"extract {state}", capture_output=True, text=True, env=env)
            except SystemExit:  # stopped at its bound: this state is not healed
                report["failed"].append(state)
                continue
            if proc.returncode == 0:
                report["healed"].append(state)
            else:
                report["failed"].append(state)
                tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
                report["notes"].append(
                    f"heal re-extract failed for {state} — prior generation kept: "
                    + " | ".join(tail))
        if not report["failed"]:
            del report["failed"]
    return report


PROGRESS_REL = ".sprite-gen.progress.json"


def _write_progress(run_dir: Path, op: str, done: int, total: int, label: str, phase: str) -> None:
    """오래 걸리는 파이프라인의 잘게 쪼갠 진행도 (maintainer 요청 2026-07-18 "잘게잘게").

    한 파일 SSoT — 큐레이터가 폴링해 퍼센트로 표시한다. 완료 시 삭제 (아래
    _clear_progress). 기록 실패는 파이프라인을 막지 않는다 (진행도는 관측용)."""
    import time as _time
    try:
        atomic_write_text(run_dir / PROGRESS_REL, json.dumps({
            "op": op, "done": done, "total": total, "label": label,
            "phase": phase, "ts": _time.time()}, ensure_ascii=False))
    except OSError:
        pass


def _clear_progress(run_dir: Path) -> None:
    try:
        (run_dir / PROGRESS_REL).unlink(missing_ok=True)
    except OSError:
        pass


def run(**kwargs: object):
    return _run(namespace_from_kwargs(_build_parser(), kwargs))

def main() -> int:
    utf8_streams()
    parser = _build_parser()
    args = parser.parse_args()
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
