# SPDX-License-Identifier: Apache-2.0
"""Byte-identity guard for the P1 speed-ups (2026-10-30).

Each function below was a per-pixel Python loop, or did the same work more than once, and was
rewritten for speed with the output unchanged to the bit. These tests keep the **frozen
pre-change form** of each beside the live one and assert exact equality on varied inputs, so a
later edit that changes a value (not just the speed) trips here.
"""

from __future__ import annotations

import math
import random

import pytest
from PIL import Image

from sprite_gen._deps import np
from sprite_gen.frames import decontam, extract
from sprite_gen.qa import inspect, preview
from sprite_gen.serve import serve_curation
from sprite_gen.video import auto_motion, loop


# --- frozen pre-change forms -------------------------------------------------


def _ref_block_pitch(im: Image.Image) -> int:
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size

    def uniform(k):
        for by in range(0, h, k):
            for bx in range(0, w, k):
                first = px[bx, by]
                for y in range(by, by + k):
                    for x in range(bx, bx + k):
                        if px[x, y] != first:
                            return False
        return True

    for k in range(min(w, h), 1, -1):
        if w % k or h % k:
            continue
        if uniform(k):
            return k
    return 1


def _ref_alpha_centroid(image):
    alpha = image.getchannel("A")
    total = weighted_x = weighted_y = 0
    for y in range(image.height):
        for x in range(image.width):
            value = alpha.getpixel((x, y))
            if value <= 10:
                continue
            total += value
            weighted_x += x * value
            weighted_y += y * value
    if total == 0:
        return None
    return weighted_x / total, weighted_y / total


def _ref_checker(size, square=16):
    w, h = size
    bg = Image.new("RGBA", size, (210, 210, 210, 255))
    px = bg.load()
    for y in range(h):
        for x in range(w):
            if ((x // square) + (y // square)) % 2 == 0:
                px[x, y] = (235, 235, 235, 255)
    return bg


def _ref_distances(flat):
    n = len(flat)
    D = np.zeros((n, n), dtype=np.float32)
    for i in range(n):
        D[i] = np.abs(flat - flat[i]).mean(axis=1)
    return D


def _ref_box_sum(values, radius):
    height, width = values.shape[:2]
    table = np.zeros((height + 1, width + 1) + values.shape[2:], dtype=np.float64)
    table[1:, 1:] = values.cumsum(0).cumsum(1)
    y0 = np.clip(np.arange(height) - radius, 0, height)
    y1 = np.clip(np.arange(height) + radius + 1, 0, height)
    x0 = np.clip(np.arange(width) - radius, 0, width)
    x1 = np.clip(np.arange(width) + radius + 1, 0, width)
    return table[y1][:, x1] - table[y0][:, x1] - table[y1][:, x0] + table[y0][:, x0]


def _ref_edge_histograms(image):
    pixels = image.convert("RGBA").load()
    width, height = image.size
    col_edges = [0] * width
    row_edges = [0] * height
    for y in range(0, height, 2):
        for x in range(1, width):
            a, b = pixels[x, y], pixels[x - 1, y]
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) + abs(a[3] - b[3]) > 96:
                col_edges[x] += 1
    for x in range(0, width, 2):
        for y in range(1, height):
            a, b = pixels[x, y], pixels[x, y - 1]
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) + abs(a[3] - b[3]) > 96:
                row_edges[y] += 1
    return col_edges, row_edges, width, height


def _ref_chroma_adjacent_count(image, chroma_key, threshold):
    count = 0
    data = image.convert("RGBA").tobytes()
    for index in range(0, len(data), 4):
        red, green, blue, alpha = data[index: index + 4]
        if alpha > 16 and math.sqrt(sum(((red, green, blue)[i] - chroma_key[i]) ** 2 for i in range(3))) <= threshold:
            count += 1
    return count


def _ref_connected_components(image):
    data = image.getchannel("A").tobytes()
    width, height = image.size
    visited = bytearray(width * height)
    components = []
    for start, alpha_value in enumerate(data):
        if alpha_value <= 16 or visited[start]:
            continue
        stack, pixels = [start], []
        visited[start] = 1
        min_x, min_y, max_x, max_y = width, height, 0, 0
        while stack:
            current = stack.pop()
            pixels.append(current)
            x, y = current % width, current // width
            min_x, min_y, max_x, max_y = min(min_x, x), min(min_y, y), max(max_x, x), max(max_y, y)
            for neighbor in (current - 1, current + 1, current - width, current + width):
                if neighbor < 0 or neighbor >= len(data) or visited[neighbor]:
                    continue
                if abs(neighbor % width - x) > 1:
                    continue
                if data[neighbor] > 16:
                    visited[neighbor] = 1
                    stack.append(neighbor)
        components.append({"pixels": pixels, "area": len(pixels), "bbox": (min_x, min_y, max_x + 1, max_y + 1),
                           "center_x": (min_x + max_x + 1) / 2})
    return components


def _ref_component_group_image(source, components, padding=4, *, preserve_y=False):
    width, height = source.size
    min_x = max(0, min(c["bbox"][0] for c in components) - padding)
    min_y = max(0, min(c["bbox"][1] for c in components) - padding)
    max_x = min(width, max(c["bbox"][2] for c in components) + padding)
    max_y = min(height, max(c["bbox"][3] for c in components) + padding)
    if preserve_y:
        min_y, max_y = 0, height
    output = Image.new("RGBA", (max_x - min_x, max_y - min_y), (0, 0, 0, 0))
    source_pixels, output_pixels = source.load(), output.load()
    for c in components:
        for index in c["pixels"]:
            x, y = index % width, index // width
            output_pixels[x - min_x, y - min_y] = source_pixels[x, y]
    return output


def _ref_correlate(region, kernel, rows, cols):
    spectrum = np.fft.rfft2(region) * np.conj(np.fft.rfft2(kernel, s=region.shape))
    return np.fft.irfft2(spectrum, s=region.shape)[:rows, :cols]


def _ref_match_costs(reference, moving, center, radius):
    (x, y, right, bottom), mask, feature, norm = reference
    cx, cy = center
    rx, ry = (radius, radius) if isinstance(radius, int) else radius
    height, width = mask.shape
    xs, xe = max(0, x - cx - rx), min(moving.shape[1] - width, x - cx + rx)
    ys, ye = max(0, y - cy - ry), min(moving.shape[0] - height, y - cy + ry)
    region = moving[ys:ye + height, xs:xe + width].astype(np.float64)
    rows, cols = ye - ys + 1, xe - xs + 1
    weights = mask.astype(np.float64)
    count = weights.sum()
    kernel = np.zeros((height, width, region.shape[2]))
    kernel[mask] = feature
    product, energy = np.zeros((rows, cols)), np.zeros((rows, cols))
    for c in range(region.shape[2]):
        values = region[:, :, c]
        total = _ref_correlate(values, weights, rows, cols)
        product += _ref_correlate(values, kernel[:, :, c], rows, cols) - total * kernel[:, :, c].sum() / count
        energy += _ref_correlate(values * values, weights, rows, cols) - total * total / count
    denominator = np.sqrt(np.maximum(energy, 0.0)) * norm
    return 1 - product / np.maximum(1e-10, denominator), xs, ys


# --- inputs --------------------------------------------------------------------


def _random_rgba(rng: random.Random, w: int, h: int, alphas=range(256), palette=None) -> Image.Image:
    im = Image.new("RGBA", (w, h))
    pick = (lambda: rng.choice(palette)) if palette else (lambda: tuple(rng.randrange(256) for _ in range(3)))
    im.putdata([(*pick(), rng.choice(alphas)) for _ in range(w * h)])
    return im


def _blobs(rng: random.Random, w: int, h: int) -> Image.Image:
    """Opaque rectangles (some touching, some wrapping a row end) on transparency."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for _ in range(rng.randint(1, 6)):
        x0, y0 = rng.randrange(w), rng.randrange(h)
        im.paste((rng.randrange(256), rng.randrange(256), rng.randrange(256), rng.randrange(17, 256)),
                 (x0, y0, min(w, x0 + rng.randint(1, 12)), min(h, y0 + rng.randint(1, 12))))
    return im


# --- the guards ----------------------------------------------------------------


def test_block_pitch_is_the_per_block_scan():
    rng = random.Random(7)
    images = [Image.new("RGBA", (1, 1)), Image.new("RGBA", (6, 4)), Image.new("RGB", (8, 8), (1, 2, 3)),
              _random_rgba(rng, 13, 9)]
    for _ in range(60):
        lw, lh, k = rng.randint(1, 8), rng.randint(1, 8), rng.randint(1, 6)
        small = _random_rgba(rng, lw, lh, alphas=(0, 255), palette=[(0, 0, 0), (255, 255, 255), (255, 0, 0)])
        images.append(small.resize((lw * k, lh * k), Image.NEAREST))
    for im in images:
        assert serve_curation._block_pitch(im) == _ref_block_pitch(im), im.size


def test_alpha_centroid_and_checker_are_the_per_pixel_loops():
    rng = random.Random(11)
    for w, h in [(1, 1), (3, 7), (64, 48), (37, 1)]:
        for alphas in (range(256), range(11), (0,)):
            im = _random_rgba(rng, w, h, alphas=alphas)
            assert inspect._alpha_centroid(im) == _ref_alpha_centroid(im)
        for square in (1, 5, 16):
            got = preview.checker((w, h), square)
            assert got.mode == "RGBA" and got.tobytes() == _ref_checker((w, h), square).tobytes()


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_distance_matrices_mirror_the_full_square(dtype):
    rng = np.random.default_rng(3)
    for n, f in [(1, 3), (2, 1), (7, 1000), (40, 9217)]:
        flat = (rng.random((n, f)) * 255).astype(dtype)
        assert np.array_equal(loop._distances(flat), _ref_distances(flat))


def test_match_costs_is_the_per_correlation_fft():
    rng = np.random.default_rng(5)
    checked = 0
    for trial in range(30):
        h, w, ch = int(rng.integers(4, 30)), int(rng.integers(4, 30)), int(rng.integers(1, 5))
        H, W = h + int(rng.integers(4, 40)), w + int(rng.integers(4, 40))
        x, y = int(rng.integers(0, W - w)), int(rng.integers(0, H - h))
        mask = rng.random((h, w)) > 0.3
        feature = rng.standard_normal((int(mask.sum()), ch)).astype(np.float32)
        reference = ((x, y, x + w, y + h), mask, feature, float(np.linalg.norm(feature)))
        moving = rng.random((H, W, ch)).astype(np.float32)
        center = (int(rng.integers(-3, 4)), int(rng.integers(-3, 4)))
        radius = int(rng.integers(1, 10)) if trial % 2 else (int(rng.integers(1, 10)), int(rng.integers(1, 10)))
        try:
            want = _ref_match_costs(reference, moving, center, radius)
        except ValueError:
            continue
        got = auto_motion._match_costs(reference, moving, center, radius)
        assert got[1:] == want[1:] and np.array_equal(got[0], want[0])
        checked += 1
    assert checked >= 10


def test_box_sum_is_the_clipped_corner_lookup():
    rng = np.random.default_rng(9)
    for h, w in [(1, 1), (1, 9), (17, 5), (40, 33)]:
        for values in (rng.integers(0, 2, (h, w)), rng.integers(0, 256, (h, w, 3)), rng.random((h, w))):
            for radius in (0, 1, 3, 12, 50):
                assert decontam._box_sum(values, radius).tobytes() == _ref_box_sum(values, radius).tobytes()


def test_frame_scans_are_the_per_pixel_loops():
    rng = random.Random(13)
    for w, h in [(1, 1), (2, 3), (31, 17), (64, 40)]:
        im = _random_rgba(rng, w, h, alphas=(0, 8, 17, 128, 255))
        assert extract._edge_histograms(im) == _ref_edge_histograms(im)
        for key in ((0, 255, 0), (255, 0, 255)):
            for threshold in (40.0, 96.0, 180.0, 255.0):
                assert extract.chroma_adjacent_count(im, key, threshold) == _ref_chroma_adjacent_count(im, key, threshold)


def test_components_and_their_group_image_keep_pixel_order_and_bytes():
    rng = random.Random(17)
    for w, h in [(1, 1), (9, 1), (1, 9), (40, 30), (64, 64)]:
        for _ in range(4):
            im = _blobs(rng, w, h)
            got, want = extract.connected_components(im), _ref_connected_components(im)
            assert got == want
            if want:
                for preserve_y in (False, True):
                    assert (extract.component_group_image(im, got, preserve_y=preserve_y).tobytes()
                            == _ref_component_group_image(im, want, preserve_y=preserve_y).tobytes())


def _ref_grid_phase(image, pitch):
    pixels = image.convert("RGBA").load()
    width, height = image.size
    col_hits, row_hits = [0] * pitch, [0] * pitch
    for y in range(0, height, 2):
        for x in range(1, width):
            a, b = pixels[x, y], pixels[x - 1, y]
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) + abs(a[3] - b[3]) > 96:
                col_hits[x % pitch] += 1
    for x in range(0, width, 2):
        for y in range(1, height):
            a, b = pixels[x, y], pixels[x, y - 1]
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]) + abs(a[3] - b[3]) > 96:
                row_hits[y % pitch] += 1
    return max(range(pitch), key=lambda i: col_hits[i]), max(range(pitch), key=lambda i: row_hits[i])


def test_grid_phase_is_the_per_pixel_scan():
    rng = random.Random(19)
    for w, h in [(1, 1), (3, 2), (40, 30), (64, 48)]:
        small = _random_rgba(rng, max(1, w // 4), max(1, h // 4), alphas=(0, 255))
        for im in (_random_rgba(rng, w, h), small.resize((small.width * 4, small.height * 4), Image.NEAREST)):
            for pitch in (2, 3, 4, 7, 80):
                assert extract._grid_phase(im, pitch) == _ref_grid_phase(im, pitch)
