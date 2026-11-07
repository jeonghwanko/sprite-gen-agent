# SPDX-License-Identifier: Apache-2.0
# Modified for sprite-gen-agent (text files read and written as UTF-8; see NOTICE).
"""`background-tile --axis xy`: one run, seamless on both axes, a report that measures both joins.

Synthetic oracles: a doubly periodic texture (an exact tile exists), textures that can only be
joined on one axis (the report must say which), and the seam search that keeps the first axis's
join when the second axis is quilted.
"""

import json

import numpy as np
import pytest
from PIL import Image

from sprite_gen.background import tile
from sprite_gen.background.tile import inspect_tile, make_tile


def periodic(period=16, width=None, height=None):
    width, height = width or 3 * period + 7, height or 3 * period + 5
    y, x = np.indices((height, width))
    u, v = 2 * np.pi * x / period, 2 * np.pi * y / period
    pixels = np.stack((110 + 50 * np.sin(u) + 30 * np.cos(v),
                       120 + 40 * np.cos(u + v),
                       90 + 35 * np.sin(v) * np.cos(u),
                       np.full_like(x, 255)), axis=-1)
    return Image.fromarray(pixels.round().astype(np.uint8))


def ramp(axis, period=16, size=60):
    """Rows (or columns) brighten steadily: periodic along one axis, impossible to wrap along the other."""
    y, x = np.indices((size, size))
    along = y if axis == "y" else x
    pixels = np.stack((40 + 3 * along, 60 + 2 * along, 30 + along, np.full_like(x, 255)), axis=-1)
    return Image.fromarray(pixels.astype(np.uint8))


def test_doubly_periodic_source_gives_an_exact_crop_and_both_joins_pass():
    source = periodic()
    before = source.tobytes()
    result, report = make_tile(source, 16, axis="xy", overlap=5)
    assert result.size == (16, 16) and result.mode == "RGBA"
    x0, y0, x1, y1 = report["core_crop"]
    assert (x1 - x0, y1 - y0) == (16, 16)
    assert result.tobytes() == source.crop((x0, y0, x1, y1)).tobytes()
    assert source.tobytes() == before
    assert report["axis"] == "xy"
    assert report["status"] == "pass" and report["review_reasons"] == []
    assert set(report["axes"]) == {"x", "y"}
    for axis in ("x", "y"):
        measured = report["axes"][axis]
        assert measured["status"] == "pass"
        assert measured["edge_color_difference"] == inspect_tile(result, axis)["edge_color_difference"]
        assert "quilt_seam" in measured
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("bad", ["x", "y"])
def test_a_join_that_stays_visible_on_one_axis_fails_the_report_and_names_that_axis(bad):
    good = "y" if bad == "x" else "x"
    _, report = make_tile(ramp(bad), 16, axis="xy", overlap=5)
    assert report["status"] == "needs-review"
    assert report["axes"][bad]["status"] == "needs-review"
    assert report["axes"][good]["status"] == "pass"
    assert report["review_reasons"]
    assert all(reason.startswith(f"{bad}: ") for reason in report["review_reasons"])


@pytest.mark.parametrize("bad", ["x"])
def test_inspection_of_both_axes_catches_a_tile_seamless_on_only_one(bad):
    # Periodic along one axis, a hard break along the other: one-axis inspection along the good axis passes.
    good = "y" if bad == "x" else "x"
    pixels = np.asarray(periodic(16, 16, 16)).copy()
    if bad == "x":
        pixels[:, 8:, :3] = 255 - pixels[:, 8:, :3]
    else:
        pixels[8:, :, :3] = 255 - pixels[8:, :, :3]
    image = Image.fromarray(pixels)
    assert inspect_tile(image, good)["status"] == "pass"
    report = tile.inspect_tile_xy(image)
    assert report["status"] == "needs-review"
    assert report["axes"][bad]["status"] == "needs-review"
    assert report["axes"][good]["status"] == "pass"


def test_cyclic_seam_keeps_its_two_ends_within_one_step():
    # A zero-error path that drifts from column 0 to column 3: the open search follows it exactly,
    # the cyclic one must end within one step of where it starts (rows 0 and -1 are wrap neighbours).
    error = np.full((8, 6), 100.0)
    path = [0, 0, 1, 1, 2, 2, 3, 3]
    for row, col in enumerate(path):
        error[row, col] = 0
    open_seam, open_score = tile._minimum_seam(error)
    assert open_seam.tolist() == path and open_score == 0
    seam, score = tile._minimum_seam(error, cyclic=True)
    assert abs(int(seam[0]) - int(seam[-1])) <= 1
    assert np.all(np.abs(np.diff(seam)) <= 1)
    assert score > 0


def test_second_pass_keeps_the_first_axis_join_exact_on_a_texture_with_a_drifting_match():
    # Rows repeat every 16 px; columns repeat every 16 px except that the rows' best match drifts
    # across x. The y quilt's seam may not differ by more than one step between the last and the
    # first column, so the x join (a wrap neighbour pair) stays a source adjacency or one seam step.
    rng = np.random.default_rng(7)
    base = periodic(16, 60, 60)
    pixels = np.asarray(base).astype(np.int16)
    pixels[..., :3] += rng.integers(-3, 4, pixels[..., :3].shape)
    result, report = make_tile(Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8)), 16, axis="xy", overlap=6)
    seam = report["axes"]["y"]["quilt_seam"]["positions"]
    assert len(seam) == 16
    assert abs(seam[0] - seam[-1]) <= 1
    assert report["axes"]["x"]["status"] == "pass"


def test_xy_needs_the_period_plus_overlap_on_both_axes():
    with pytest.raises(ValueError, match="along y"):
        make_tile(Image.new("RGBA", (40, 20)), 16, axis="xy", overlap=5)
    with pytest.raises(ValueError, match="along x"):
        make_tile(Image.new("RGBA", (20, 40)), 16, axis="xy", overlap=5)


def test_cli_accepts_axis_xy(tmp_path):
    source = tmp_path / "ground.png"
    periodic().save(source)
    out = tmp_path / "tile.png"
    assert tile.main(["--source", str(source), "--out", str(out), "--period", "16", "--overlap", "5",
                      "--axis", "xy"]) == 0
    report = json.loads((tmp_path / "tile.report.json").read_text(encoding="utf-8"))
    assert report["axis"] == "xy" and report["status"] == "pass"
    assert report["source"] == str(source) and report["output"] == str(out)
    with Image.open(out) as image:
        assert image.size == (16, 16)
