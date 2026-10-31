# SPDX-License-Identifier: Apache-2.0
"""Mask morphology and window sums on arrays, shared by the keying engines.

8-connected growth (`grow_chebyshev`, `grow_into`), Chebyshev ring depth (`ring_depth`) and summed-area
window sums (`box_sum`, `box_sum_at`). Exact: growth is boolean, and the sums of integer-valued input stay
integers, in float64 or (boolean and narrow unsigned input) in an integer table.
"""

from __future__ import annotations

from sprite_gen._deps import np


def grow_into(seed: np.ndarray, allowed: np.ndarray) -> np.ndarray:
    """Every `allowed` pixel 8-connected to `seed` through `allowed` pixels (seed included)."""
    reached = seed & allowed
    while True:
        grown = grow_chebyshev(reached) & allowed
        if not (grown & ~reached).any():
            return reached
        reached = grown


def grow_chebyshev(mask: np.ndarray) -> np.ndarray:
    """One step of 8-connected growth: the 3x3 neighborhood of every set pixel.

    Shifts are explicit slices, not `np.roll`, because roll wraps the far edge
    into the near one and would hand a bottom-row pixel the depth of the top
    row. The two axes are applied in sequence — a 3x3 dilation is separable, and
    Chebyshev distance is exactly what repeating this step counts.
    """
    grown = mask.copy()
    grown[:, 1:] |= mask[:, :-1]
    grown[:, :-1] |= mask[:, 1:]
    spread = grown.copy()
    spread[1:, :] |= grown[:-1, :]
    spread[:-1, :] |= grown[1:, :]
    return spread


def ring_depth(seed: np.ndarray, rings: int) -> np.ndarray:
    """Chebyshev distance to `seed`, counted ring by ring: 0 on it, rings + 1 beyond the last ring."""
    depth = np.full(seed.shape, rings + 1, dtype=np.uint8)
    depth[seed] = 0
    reached = seed
    for ring in range(1, rings + 1):
        grown = grow_chebyshev(reached)
        depth[grown & ~reached] = ring
        reached = grown
    return depth


def _table(values: np.ndarray) -> np.ndarray:
    """Summed-area table of `values` (H, W, ...), a zero row and column ahead.

    Boolean and narrow unsigned input (8 or 16 bit) is summed in integers, int32 where the whole image's
    sum fits it: the same sums as float64 holds them, exact either way, at half the memory traffic. Any
    other input is summed in float64, as before."""
    height, width = values.shape[:2]
    dtype: np.dtype = np.dtype(np.float64)
    if values.dtype == np.bool_ or (values.dtype.kind == "u" and values.dtype.itemsize <= 2):
        top = 1 if values.dtype == np.bool_ else int(np.iinfo(values.dtype).max)
        dtype = np.dtype(np.int32 if top * height * width <= np.iinfo(np.int32).max else np.int64)
    table = np.zeros((height + 1, width + 1) + values.shape[2:], dtype=dtype)
    np.cumsum(values, 0, dtype=dtype, out=table[1:, 1:])
    np.cumsum(table[1:, 1:], 1, out=table[1:, 1:])
    return table


def box_sum(values: np.ndarray, radius: int) -> np.ndarray:
    """Sum over a (2r+1)^2 window, clipped at the image edge. Integer-valued input stays exact; boolean and
    narrow unsigned input comes back as integers (`_table`).

    The edge-padded table stands in for clipping each window's corners: padding by r repeats the
    first and last table rows and columns, so a slice of it reads the same corner values the clipped
    indices did, in the same `((a - b) - c) + d` order, as views instead of four full-size copies."""
    height, width = values.shape[:2]
    table = _table(values)
    r = radius
    p = np.pad(table, ((r, r), (r, r)) + ((0, 0),) * (values.ndim - 2), mode="edge")
    hi_y, lo_y = slice(2 * r + 1, 2 * r + 1 + height), slice(0, height)
    hi_x, lo_x = slice(2 * r + 1, 2 * r + 1 + width), slice(0, width)
    out = p[hi_y, hi_x] - p[lo_y, hi_x]
    out -= p[hi_y, lo_x]
    out += p[lo_y, lo_x]
    return out


def box_sum_at(values: np.ndarray, radius: int, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """`box_sum` at the given pixels only: one summed-area table, no full-size result. A (H, W, C) array gives
    (N, C)."""
    height, width = values.shape[:2]
    table = _table(values)
    y0 = np.clip(rows - radius, 0, height)
    y1 = np.clip(rows + radius + 1, 0, height)
    x0 = np.clip(cols - radius, 0, width)
    x1 = np.clip(cols + radius + 1, 0, width)
    return table[y1, x1] - table[y0, x1] - table[y1, x0] + table[y0, x0]
