# SPDX-License-Identifier: Apache-2.0
"""Game-sized exports retain playback order, pivots and policies across pages."""
import json
import subprocess
import sys
import threading
import time

import pytest
from PIL import Image, ImageColor

from keyed_figure import colour_outside, figure
from sprite_gen.compose import game_pack
from sprite_gen.util.resample import resize_cell


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def strip(tmp_path):
    image = Image.new("RGBA", (36, 20))
    for index, color in enumerate(("red", "green", "blue")):
        image.paste(Image.new("RGBA", (12, 20), color), (index * 12, 0))
    image.save(tmp_path / "walk.strip.png")
    return write_json(tmp_path / "walk.strip.json", {
        "frames": 3, "w": 12, "h": 20, "delay_ms": 80,
        "body_h": 16, "anchor": [4, 18], "loop": False, "hold_last": True,
        "end_frame": 2, "events": [{"frame": 1, "name": "impact"}], "blend": "add",
        "direction": "NE",
    })


def request(tmp_path, entries):
    return write_json(tmp_path / "game.json", {"version": 1, "assets": entries})


def pixels(out, result, animation):
    frames = {key: (page, value) for page in result["textures"] for key, value in page["frames"].items()}
    found = []
    for key in result["animations"][animation]["frames"]:
        page, record = frames[key]
        rect = record["frame"]
        with Image.open(out / page["image"]) as im:
            found.append(im.crop((rect["x"], rect["y"], rect["x"]+rect["w"], rect["y"]+rect["h"])))
    return found


@pytest.mark.slow
@pytest.mark.parametrize("scale,resample", [(0.16, None), (0.5, "lanczos")])
def test_scaled_cutout_carries_no_colour_its_source_did_not_have(tmp_path, scale, resample):
    src = figure()
    src.save(tmp_path / "hero.png")
    entry = {"id": "hero", "source": "hero.png", "display": {"height": round(src.height * scale)}}
    if resample is not None:
        entry["resample"] = resample
    result = game_pack.pack(request(tmp_path, [entry]), tmp_path / "out")
    (cell,) = pixels(tmp_path / "out", result, "hero")
    assert colour_outside(src, cell) == 0
    assert cell.tobytes() == resize_cell(src, cell.size).tobytes()
    assert colour_outside(src, src.resize(cell.size, Image.Resampling.LANCZOS)) > 100


@pytest.mark.parametrize("pixel_metadata", [False, True])
def test_pixel_resampling_keeps_nearest_samples(tmp_path, pixel_metadata):
    src = figure()
    src.save(tmp_path / "hero.png")
    entry = {"id": "hero", "source": "hero.png", "display": {"height": 90}}
    if pixel_metadata:
        write_json(tmp_path / "hero.json", {
            "frames": 1, "w": src.width, "h": src.height, "delay_ms": 100, "frame_variant": "pixel",
        })
        entry["source"] = "hero.json"
    else:
        entry["resample"] = "nearest"
    result = game_pack.pack(request(tmp_path, [entry]), tmp_path / "out")
    (cell,) = pixels(tmp_path / "out", result, "hero")
    assert cell.tobytes() == src.resize(cell.size, Image.Resampling.NEAREST).tobytes()


def test_mixed_export_preserves_timing_order_policy_pivot_and_pixel_colors(tmp_path):
    strip(tmp_path)
    Image.new("RGBA", (4, 6), (61, 29, 243, 127)).save(tmp_path / "pixel.png")
    Image.new("RGBA", (12, 12), "yellow").save(tmp_path / "painting.png")
    source = request(tmp_path, [
        {"id": "walk", "source": "walk.strip.json", "display": {"body_height": 8}},
        {"id": "pixel", "source": "pixel.png", "display": {"height": 3}, "resample": "nearest"},
        {"id": "painting", "source": "painting.png", "display": {"width": 6}},
    ])
    out = tmp_path / "out"
    result = game_pack.pack(source, out)
    assert json.loads((out / "game-pack.json").read_text()) == result
    anim = result["animations"]["walk"]
    assert (anim["loop"], anim["hold_last"], anim["end_frame"], anim["blend"], anim["direction"]) == (False, True, 2, "add", "NE")
    assert anim["events"] == [{"frame": 1, "name": "impact"}]
    assert anim["durations_ms"] == [80, 80, 80]
    assert anim["anchor"] == [2, 9]
    assert [f.getpixel((1, 1)) for f in pixels(out, result, "walk")] == [(255, 0, 0, 255), (0, 128, 0, 255), (0, 0, 255, 255)]
    assert {f.size for f in pixels(out, result, "walk")} == {(6, 10)}
    assert pixels(out, result, "pixel")[0].getpixel((0, 0)) == (61, 29, 243, 127)
    # The hash fields consumed by Phaser 4.2.1 JSONHash, including normalized pivot.
    for page in result["textures"]:
        assert isinstance(page["frames"], dict)
        with Image.open(out / page["image"]) as png, Image.open(out / page["webp"]) as webp:
            assert png.size == webp.size
            assert png.convert("RGBA").tobytes() == webp.convert("RGBA").tobytes()
            assert max(png.size) <= 4096
            for value in page["frames"].values():
                box, size = value["frame"], value["sourceSize"]
                assert value["rotated"] is False and value["trimmed"] is False
                assert value["spriteSourceSize"] == {"x": 0, "y": 0, **size}
                assert 0 <= box["x"] <= png.width - box["w"]
                assert 0 <= box["y"] <= png.height - box["h"]
    frame = next(v for p in result["textures"] for k, v in p["frames"].items() if k == anim["frames"][0])
    assert frame["pivot"] == {"x": 1/3, "y": 0.9}


def test_direction_sheet_uses_declared_row_positions_and_lengths(tmp_path):
    sheet = Image.new("RGBA", (18, 16))
    colors = {"N": ["red", "green", "blue"], "S": ["yellow", "purple"]}
    for row, code in enumerate(("S", "N")):
        for col, color in enumerate(colors[code]):
            sheet.paste(Image.new("RGBA", (6, 8), color), (col * 6, row * 8))
    sheet.save(tmp_path / "set.png")
    write_json(tmp_path / "set.json", {
        "kind": "sprite-gen-video-set-sheet", "schema": 1, "sheet": "set.png",
        "columns": 3, "cell_w": 6, "cell_h": 8, "anchor": [2, 7], "order": ["N", "S"],
        "rows": [{"code": "N", "row": 1, "frames": 3, "delay_ms": 75, "body_h": 6},
                 {"code": "S", "row": 0, "frames": 2, "delay_ms": 120, "body_h": 6}],
    })
    source = request(tmp_path, [{"id": "walk", "source": "set.json", "display": {"body_height": 3}}])
    out = tmp_path / "out"
    result = game_pack.pack(source, out)
    assert list(result["animations"]) == ["walk/N", "walk/S"]
    for code in ("N", "S"):
        anim = result["animations"][f"walk/{code}"]
        assert anim["direction"] == code
        assert anim["loop"] is True and anim["hold_last"] is False
        assert anim["anchor"] == [1, 3.5]
        expected = [Image.new("RGBA", (1, 1), c).getpixel((0, 0)) for c in colors[code]]
        assert [f.getpixel((1, 1)) for f in pixels(out, result, f"walk/{code}")] == expected

    for direction in ("E", None):
        entry = {"id": "walk", "source": "set.json", "display": {"body_height": 3}, "direction": direction}
        overridden = request(tmp_path, [entry])
        with pytest.raises(ValueError, match="direction.*state"):
            game_pack.pack(overridden, tmp_path / "ambiguous")
        assert not (tmp_path / "ambiguous").exists()
        entry["state"] = "N"
        selected = game_pack.pack(request(tmp_path, [entry]), tmp_path / f"selected-{direction}")
        assert list(selected["animations"]) == ["walk/N"]
        assert selected["animations"]["walk/N"]["direction"] == direction


def test_runtime_atlas_repeated_rectangles_and_variable_durations(tmp_path):
    strip(tmp_path)
    rects = [{"x": i * 12, "y": 0, "w": 12, "h": 20} for i in (2, 0, 2, 1)]
    write_json(tmp_path / "atlas.json", {
        "sprite_sheet_alpha": "walk.strip.png", "frame_layout": {"rows": {"hit": rects}},
        "animation": {"rows": {"hit": {"durations_ms": [40, 160, 90, 100], "loop": False,
            "hold_last": True, "anchor": [5, 19], "events": [{"frame": 2, "name": "hit"}], "end_frame": 3}}},
    })
    src = request(tmp_path, [{"id": "actor", "source": "atlas.json", "display": {"height": 10}, "resample": "nearest"}])
    result = game_pack.pack(src, tmp_path / "out")
    anim = result["animations"]["actor/hit"]
    assert anim["durations_ms"] == [40, 160, 90, 100]
    assert anim["anchor"] == [2.5, 9.5]
    assert anim["events"][0]["frame"] == 2
    assert [im.getpixel((0, 0)) for im in pixels(tmp_path / "out", result, "actor/hit")] == [(0, 0, 255, 255), (255, 0, 0, 255), (0, 0, 255, 255), (0, 128, 0, 255)]


def test_multipage_and_repeated_export_are_byte_identical(tmp_path):
    strip(tmp_path)
    src = request(tmp_path, [{"id": "walk", "source": "walk.strip.json", "display": {"height": 10}}])
    result = game_pack.pack(src, tmp_path / "first", page_size=12)
    assert len(result["textures"]) == 3
    # The pages are encoded at once: each page's two files still hold that page's frame, and the same pixels.
    for page, colour in zip(result["textures"], ("red", "green", "blue")):
        (box,) = (value["frame"] for value in page["frames"].values())
        with Image.open(tmp_path / "first" / page["image"]) as png, Image.open(tmp_path / "first" / page["webp"]) as webp:
            assert png.convert("RGBA").tobytes() == webp.convert("RGBA").tobytes(), page["webp"]
            centre = (box["x"] + box["w"] // 2, box["y"] + box["h"] // 2)
            assert png.convert("RGBA").getpixel(centre) == ImageColor.getcolor(colour, "RGBA"), (page["image"], colour)
    game_pack.pack(src, tmp_path / "second", page_size=12)
    before = {p.name: p.read_bytes() for p in (tmp_path / "first").iterdir()}
    assert before == {p.name: p.read_bytes() for p in (tmp_path / "second").iterdir()}
    game_pack.pack(src, tmp_path / "first", page_size=12)
    assert before == {p.name: p.read_bytes() for p in (tmp_path / "first").iterdir()}
    # Changing a published pack is explicit: a fresh output directory is required.
    with pytest.raises(ValueError, match="different|existing|exists"):
        game_pack.pack(src, tmp_path / "first", page_size=24)
    assert before == {p.name: p.read_bytes() for p in (tmp_path / "first").iterdir()}


def test_no_atlas_image_is_saved_by_two_encodes(tmp_path, monkeypatch):
    # Pillow keeps a save's options on the image object until the save ends, so two saves of one object at once can
    # read each other's (a WebP read without `lossless` is lossy): each atlas file is encoded from an image of its own.
    strip(tmp_path)
    src = request(tmp_path, [{"id": "walk", "source": "walk.strip.json", "display": {"height": 10}}])
    encoded, encode = [], game_pack._encode_image
    monkeypatch.setattr(game_pack, "_encode_image", lambda image, fmt: (encoded.append(image), encode(image, fmt))[1])
    game_pack.pack(src, tmp_path / "out", page_size=12)
    assert len(encoded) == 6
    assert len({id(image) for image in encoded}) == len(encoded)   # all kept alive above, so ids are distinct objects


def test_no_more_pages_are_held_than_are_in_flight(tmp_path, monkeypatch):
    # A 4096 px page is 64 MB and its lossless WebP encode several times that: the next page is built only once a page
    # in flight is done, so at most PAGES_IN_FLIGHT WebP encodes run at once however many cores encode.
    strip(tmp_path)
    src = request(tmp_path, [{"id": "walk", "source": "walk.strip.json", "display": {"height": 10}}])
    monkeypatch.setattr(game_pack, "ENCODE_JOBS", 4)
    monkeypatch.setattr(game_pack, "PAGES_IN_FLIGHT", 2)
    lock, running, most, encode = threading.Lock(), [0], [0], game_pack._encode_image

    def slow(image, fmt):
        if fmt == "WEBP":
            with lock:
                running[0] += 1
                most[0] = max(most[0], running[0])
            time.sleep(0.2)
        try:
            return encode(image, fmt)
        finally:
            if fmt == "WEBP":
                with lock:
                    running[0] -= 1

    monkeypatch.setattr(game_pack, "_encode_image", slow)
    game_pack.pack(src, tmp_path / "out", page_size=12)   # three pages
    assert most[0] == 2


@pytest.mark.parametrize("change,match", [
    ({"display": {"height": 0}}, "positive"),
    ({"display": {"height": 5000}}, "4096|page"),
    ({"events": [{"frame": 3, "name": "bad"}]}, "event"),
    ({"loop": "false"}, "bool"),
    ({"end_frame": -1}, "end_frame"),
    ({"resample": "typo"}, "resample"),
])
def test_bad_input_never_publishes_partial_output(tmp_path, change, match):
    strip(tmp_path)
    entry = {"id": "walk", "source": "walk.strip.json", "display": {"height": 10}, **change}
    src = request(tmp_path, [entry])
    with pytest.raises(ValueError, match=match):
        game_pack.pack(src, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_size_limit_counts_both_images_and_manifest_before_publish(tmp_path):
    strip(tmp_path)
    src = request(tmp_path, [{"id": "walk", "source": "walk.strip.json", "display": {"height": 10}}])
    with pytest.raises(ValueError, match="byte|MB"):
        game_pack.pack(src, tmp_path / "out", max_bytes=10)
    assert not (tmp_path / "out").exists()
    for kwargs in ({"page_size": 4097}, {"max_bytes": 50_000_001}):
        with pytest.raises(ValueError):
            game_pack.pack(src, tmp_path / "out", **kwargs)
    # Over the budget on a later page, while that page and the next are encoding: the same error, nothing published.
    game_pack.pack(src, tmp_path / "whole", page_size=12)
    first_page = sum((tmp_path / "whole" / f"atlas-000.{ext}").stat().st_size for ext in ("png", "webp"))
    with pytest.raises(ValueError, match=r"exceeds \d+ bytes \(50 MB maximum\)$"):
        game_pack.pack(src, tmp_path / "out", page_size=12, max_bytes=first_page + 1)
    assert not (tmp_path / "out").exists()


def test_cli_dispatch_and_real_files(tmp_path):
    strip(tmp_path)
    src = request(tmp_path, [{"id": "walk", "source": "walk.strip.json", "display": {"height": 10}}])
    call = subprocess.run([sys.executable, "-m", "sprite_gen.cli", "game-pack", "--request", str(src),
                           "--out-dir", str(tmp_path / "out")], capture_output=True, encoding="utf-8")
    assert call.returncode == 0, call.stderr
    assert json.loads(call.stdout)["ok"] is True
    assert (tmp_path / "out" / "game-pack.json").is_file()


def test_staging_write_error_cannot_publish_half_a_pack(tmp_path, monkeypatch):
    strip(tmp_path)
    src = request(tmp_path, [{"id": "walk", "source": "walk.strip.json", "display": {"height": 10}}])
    import os
    fsync = os.fsync
    writes = []

    def full_disk(fd):
        writes.append(fd)
        if len(writes) == 2:
            raise OSError("disk full")
        fsync(fd)

    monkeypatch.setattr(os, "fsync", full_disk)
    with pytest.raises(OSError, match="disk full"):
        game_pack.pack(src, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".out-*"))
