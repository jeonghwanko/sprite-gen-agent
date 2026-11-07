# SPDX-License-Identifier: Apache-2.0
"""A strip's cells are read at their exact rate (sprite_gen/spec/assets.py `frame_seconds`, docs/scene.md).

`video-loop` writes `delay_ms` rounded to hundredths (41.67 for 24 fps). Read as 0.04167 s, a 24 fps strip in a 24 fps
scene drifted a cell late from its second frame, and a scene frame on a cell boundary showed the cell before it. A value
within `SNAP_MS` of 1000 over a whole number is read as that rate's exact time; a whole number of milliseconds, and a
rate that is not whole, are read as written. Ported from upstream 4.0.0 (`tests/scene/test_scene_fill_held.py`), without
its `--fill-held` part."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen.scene.model import load_scene
from sprite_gen.scene.render import Renderer

N = 24


def strip(root: Path, name: str, cells: list[Image.Image], *, delay_ms: float) -> str:
    """A loop strip and its sidecar as `video-loop` writes them: the cells side by side, `delay_ms` rounded to hundredths."""
    w, h = cells[0].size
    sheet = Image.new("RGBA", (w * len(cells), h))
    for k, cell in enumerate(cells):
        sheet.paste(cell, (k * w, 0))
    meta = {"frames": len(cells), "w": w, "h": h, "delay_ms": delay_ms, "loop": True, "kind": "periodic"}
    sheet.save(root / f"{name}.strip.png")
    (root / f"{name}.strip.json").write_text(json.dumps(meta), encoding="utf-8")
    return f"{name}.strip.json"


def test_a_24_fps_strip_in_a_24_fps_scene_shows_each_cell_once(tmp_path):
    # `build_strip` writes delay_ms 41.67 for 24 fps, which drifted the scene a cell late from its second frame
    # (pixels 40, 40, 80, 120). Read at the strip's own rate it shows cells 0, 1, 2, 3.
    from sprite_gen.video.loop import build_strip
    frames = [Image.new("RGBA", (16, 16), (40 * (i + 1), 20, 30, 255)) for i in range(4)]
    sheet, meta = build_strip(frames, cycle_seconds=4 / 24)
    sheet.save(tmp_path / "probe.strip.png")
    (tmp_path / "probe.strip.json").write_text(json.dumps(meta), encoding="utf-8")
    spec = {"kind": "sprite-gen-scene", "version": 1, "canvas": {"width": 64, "height": 32, "background": [0, 0, 0]},
            "fps": 24, "duration": 4 / 24, "assets": {"probe": "probe.strip.json"},
            "layers": [{"id": "probe", "asset": "probe", "at": [32, 32]}]}
    (tmp_path / "scene.json").write_text(json.dumps(spec), encoding="utf-8")
    renderer = Renderer(load_scene(tmp_path / "scene.json"))
    assert meta["delay_ms"] == 41.67
    assert [renderer.frame(i / 24).getpixel((32, 28))[0] for i in range(4)] == [40, 80, 120, 160]


@pytest.mark.parametrize("rate", [24, 60, 15])
def test_a_frame_on_a_cell_boundary_shows_the_cell_that_starts_there(rate, tmp_path):
    # A strip at `rate` in a 20 fps scene: scene frame i falls in cell i * rate / 20, exactly. Where that is whole the
    # frame is on the boundary (0.25 s of a 24 fps strip is cell 6, not 5): the strip's rounded delay (41.67, 16.67,
    # 66.67 ms) read as it was drifted those frames a cell late.
    cells = [Image.new("RGBA", (16, 16), (4 * k + 10, 20, 30, 255)) for k in range(rate)]
    source = strip(tmp_path, "cells", cells, delay_ms=round(1000 / rate, 2))
    spec = {"kind": "sprite-gen-scene", "version": 1, "canvas": {"width": 64, "height": 32, "background": [0, 0, 0]},
            "fps": 20, "duration": 1.0, "assets": {"cells": source},
            "layers": [{"id": "cells", "asset": "cells", "at": [32, 32]}]}
    (tmp_path / "scene.json").write_text(json.dumps(spec), encoding="utf-8")
    renderer = Renderer(load_scene(tmp_path / "scene.json"))
    got = [(renderer.frame(i / 20).getpixel((32, 28))[0] - 10) // 4 for i in range(20)]
    assert got == [i * rate // 20 for i in range(20)]
    if rate == 24:
        assert got[5] == 6  # 0.25 s x 24


def test_a_delay_rounded_to_hundredths_is_read_as_the_whole_rate_it_rounds_and_nothing_else_moves():
    from sprite_gen.spec.assets import frame_seconds
    assert frame_seconds(41.67) == 1 / 24 and frame_seconds(16.67) == 1 / 60 and frame_seconds(33.33) == 1 / 30
    assert frame_seconds(66.67) == 1 / 15 and frame_seconds(83.33) == 1 / 12
    # A whole number of milliseconds is what its writer meant (a pixel drawing's 100 ms, an atlas's 42 ms), and a rate
    # that is not whole (23.976 fps: 41.71 ms) is not taken for one.
    assert frame_seconds(100) == 0.1 and frame_seconds(42) == 0.042 and frame_seconds(7) == 0.007
    assert frame_seconds(41.71) == 41.71 / 1000 and frame_seconds(16.68) == 16.68 / 1000


def test_a_stride_report_measured_on_the_rounded_delay_names_the_cells_read_at_their_exact_rate(tmp_path):
    from sprite_gen.spec.assets import load_asset, same_identity, sequence_identity
    cells = [Image.new("RGBA", (16, 16), (8 * k, 20, 30, 255)) for k in range(N)]
    source = strip(tmp_path, "walk", cells, delay_ms=41.67)
    seq = load_asset(tmp_path / source)
    old = {**sequence_identity(seq), "durations_seconds": [0.04167] * N}
    assert seq.durations == (1 / 24,) * N
    assert same_identity(old, seq)
    assert not same_identity({**old, "durations_seconds": [0.0417] * N}, seq)
