# SPDX-License-Identifier: Apache-2.0
"""`ui-kit`: a transparent sheet drawn on the fixed layout becomes nine-slice elements.

The sheet is synthetic and drawn into the layout's own boxes: frames with known corner
ornaments (one with a long thin flourish along its top edge), a bar frame with an end gem
taller than the bar, a round badge, light colour noise, a faint haze ring around every
element and loose specks. Insets are checked to the pixel: every ornament column and row
lies inside a fixed slice, and nothing more than the 2 px margin does.

A tall (2:3) sheet holds the same seven frames at the same places and, in the band below them,
two bar fills with end caps of their own colour and a top-to-bottom shade, a slot and a counter
plate with corner ornaments, two pips and a joystick base and knob drawn as discs. A fill fits
its bar frame's content area exactly, in the report and in the pack; round parts never stretch.
A square sheet's report, element pixels and packs are frozen from the splitter before the tall
sheet existed.

The seven frames are the kit: an empty frame box refuses the sheet. The band's elements are
optional: an empty band box leaves that element out of the report and the pack and names it under
`missing`. The cut reads the drawing once, exactly: the reading that keeps the parts clearest of
their boxes' edges under any drift it considers, cut when no other reading comes within 1 px of
it. A sheet drawn a few percent shorter than the guide, so that the lower elements' centres lie in
the boxes above theirs, is read under its drift. With any set of band elements left out, under any
drift within the accepted range (along each axis a scale within a tenth of 1 and an offset within
96 px at 1024), exactly the elements not drawn are named missing and every drawn one is cut as
itself, never a neighbour under its name; an element drawn in the gap between two boxes, or a
drawing that drifted further, refuses the sheet with its own reason. So does a drawing that fits
the layout as well under two drifts that read it differently (small frames that lie inside their
boxes either way, and one dot on either pip's centre), while one that fits one drift clearly
better is named by it. A sheet whose every part lies inside its box under an accepted drift (small
drawings anywhere inside their boxes, or full-size frames with small band drawings) is cut
exactly as drawn, or refused as fitting the layout two ways, with two readings this file checks
by its own evaluation of the reading; and no drift sampled at random reads a drawing better than
the least cost the cut found, nor reads a cut sheet otherwise as well.

A kit is written whole into a new or empty directory: one that holds anything is refused and left as
it was. The kit is published at one step, the rename that gives it the out-dir's name or, with the
report outside, the link that gives the report its name. A failure or a kill just before or just
after any filesystem step of the publish leaves nothing before that step and the whole kit from it on;
an interrupt of any kind at any line or instruction of the publish leaves nothing or the whole kit;
and a run that another run's kit or report reaches first takes its own back out.
"""

import contextlib
import copy
import errno
import hashlib
import io
import json
import math
import os
import re
import shutil
import signal
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from sprite_gen import cli
from sprite_gen.compose import game_pack
from sprite_gen.frames import ui_kit
from sprite_gen.util.resample import resize_cell

SIZE = 512
TALL = (SIZE, SIZE * 3 // 2)
BORDER = (200, 200, 220, 255)
ORNAMENT = (120, 220, 200, 255)
FLOURISH = (255, 180, 60, 255)
INTERIOR = (20, 30, 60, 210)
TRACK = (10, 10, 20, 180)
GEM = (220, 40, 80, 255)
FILL = (60, 200, 120, 255)
FILL_CAP = (200, 255, 220, 255)
PIP_ON = (255, 220, 90, 255)
PIP_OFF = (70, 60, 50, 255)
KNOB = (180, 190, 210, 255)


def slow_but(values, fast):
    """``values`` for a parametrize: those ``fast`` takes stay in the fast bundle, the rest are marked slow and run by
    `pytest --full` — the same property read on more samples, or the same sweep at more places."""
    return [value if fast(value) else pytest.param(value, marks=pytest.mark.slow) for value in values]


def fill(pixels, x0, y0, x1, y1, color):
    pixels[y0:y1, x0:x1] = color


def draw_frame(pixels, x, y, w, h, k, flourish=0):
    fill(pixels, x, y, x + w, y + h, BORDER)
    fill(pixels, x + 3, y + 3, x + w - 3, y + h - 3, INTERIOR)
    for cx, cy in ((x, y), (x + w - k, y), (x, y + h - k), (x + w - k, y + h - k)):
        fill(pixels, cx, cy, cx + k, cy + k, ORNAMENT)
    if flourish:
        fill(pixels, x + k, y + 4, x + k + flourish, y + 6, FLOURISH)


def draw_bar(pixels, x, y, w, h, radius, cap):
    fill(pixels, x + radius, y + 6, x + w, y + h - 6, BORDER)
    fill(pixels, x + radius, y + 9, x + w - 3, y + h - 9, TRACK)
    fill(pixels, x + w - cap, y + 2, x + w, y + h - 2, ORNAMENT)
    yy, xx = np.indices((h, 2 * radius))
    disc = np.hypot(xx - radius + 0.5, yy - h / 2 + 0.5) <= radius
    region = pixels[y:y + h, x:x + 2 * radius]
    region[disc] = GEM


def draw_badge(pixels, x, y, d):
    yy, xx = np.indices((d, d))
    r = np.hypot(xx - d / 2 + 0.5, yy - d / 2 + 0.5)
    region = pixels[y:y + d, x:x + d]
    region[r <= d / 2] = BORDER
    region[r <= d / 2 - 3] = INTERIOR


def draw_fill(pixels, x, y, w, h, cap):
    """A bar fill: one colour along its length, shaded from top to bottom, with an end cap of its own colour."""
    shade = np.linspace(1.0, 0.6, h)[:, None]
    region = pixels[y:y + h, x:x + w]
    region[..., 3] = 255
    for channel in range(3):
        region[..., channel] = (FILL[channel] * shade).round().astype(np.uint8)
    fill(pixels, x, y, x + cap, y + h, FILL_CAP)
    fill(pixels, x + w - cap, y, x + w, y + h, FILL_CAP)


def draw_disc(pixels, x, y, d, color, ring=0):
    yy, xx = np.indices((d, d))
    r = np.hypot(xx - d / 2 + 0.5, yy - d / 2 + 0.5)
    region = pixels[y:y + d, x:x + d]
    region[r <= d / 2] = BORDER if ring else color
    region[r <= d / 2 - ring] = color


# element -> (offset inside its box, drawing parameters); every element sits 4 px inside its box
PLAN = {"panel": dict(k=18, flourish=60), "button": dict(k=12), "button_pressed": dict(k=12),
        "card": dict(k=16), "hp_bar_frame": dict(radius=20, cap=10), "xp_bar_frame": dict(radius=20, cap=10),
        "timer_badge": dict(), "hp_bar_fill": dict(cap=6), "xp_bar_fill": dict(cap=6),
        "slot": dict(k=14), "badge": dict(k=10), "pip_on": dict(color=PIP_ON, ring=3),
        "pip_off": dict(color=PIP_OFF, ring=3), "joystick_base": dict(color=INTERIOR, ring=6),
        "joystick_knob": dict(color=KNOB)}


def synthetic_sheet(*, size=(SIZE, SIZE), haze=True, noise=True, stray=False, missing=(), crest=False,
                    squeeze=1.0, drift=None, nudge=None):
    """The sheet, and where each element was drawn. `missing` names the boxes left empty; `squeeze` draws
    the layout that much shorter, from the top edge down, as image models draw it; `drift`, a scale and an
    offset along x and then along y (offsets in sheet pixels), draws every box moved that way instead; `nudge`
    moves single elements off their boxes by (dx, dy) sheet pixels."""
    missing = {missing} if isinstance(missing, str) else set(missing)
    sx, tx, sy, ty = drift if drift is not None else (1, 0, squeeze, 0)
    nudge = nudge or {}
    pixels = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    drawn = {}
    for name, (bx, by, bw, bh) in ui_kit.boxes(size).items():
        if name in missing:
            continue
        bx, bw = round(bx * sx + tx), round(bw * sx)
        by, bh = round(by * sy + ty), round(bh * sy)
        dx, dy = nudge.get(name, (0, 0))
        x, y, w, h = bx + 4 + dx, by + 4 + dy, bw - 8, bh - 8
        params = PLAN[name]
        if name.endswith("bar_frame"):
            draw_bar(pixels, x, y, w, h, **params)
        elif name.endswith("bar_fill"):
            draw_fill(pixels, x, y, w, h, **params)
        elif name == "timer_badge":
            w = h = min(w, h)
            draw_badge(pixels, x, y, w)
        elif name.startswith(("pip_", "joystick_")):
            w = h = min(w, h)
            draw_disc(pixels, x, y, w, **params)
        else:
            draw_frame(pixels, x, y, w, h, **params)
            if crest and name == "card":
                fill(pixels, x + w // 2 - 6, y, x + w // 2 + 6, y + 14, FLOURISH)
        drawn[name] = (x, y, w, h)
    solid = pixels[..., 3] > 0
    if noise:
        rng = np.random.default_rng(3)
        jitter = rng.integers(-4, 5, pixels[..., :3].shape)
        pixels[..., :3] = np.where(solid[..., None], np.clip(pixels[..., :3].astype(int) + jitter, 0, 255), 0)
    if haze:
        near = np.zeros_like(solid)
        for dy in range(-6, 7):
            for dx in range(-6, 7):
                near |= np.roll(np.roll(solid, dy, 0), dx, 1)
        ring = near & ~solid
        pixels[ring] = (150, 150, 170, 22)
        for sx, sy in ((5, 500), (500, 5), (330, 470)):
            pixels[sy, sx] = (200, 200, 200, 40)
    if stray:
        fill(pixels, 250, 410, 270, 430, BORDER)
    return Image.fromarray(pixels), drawn


def split(**kwargs):
    image, drawn = synthetic_sheet(**kwargs)
    elements, report = ui_kit.split_ui_kit(image)
    return image, drawn, elements, report


def offset(report, drawn, name):
    rect = report["elements"][name]["rect"]
    x, y, w, h = drawn[name]
    return x - rect["x"], y - rect["y"], rect["x"] + rect["w"] - (x + w), rect["y"] + rect["h"] - (y + h)


FRAMES = ["panel", "button", "button_pressed", "card", "hp_bar_frame", "xp_bar_frame", "timer_badge"]
BAND = ["hp_bar_fill", "xp_bar_fill", "slot", "badge", "pip_on", "pip_off", "joystick_base", "joystick_knob"]


def test_layout_is_the_documented_table_and_scales_with_the_sheet():
    assert [entry.name for entry in ui_kit.LAYOUT] == FRAMES + BAND
    full = ui_kit.boxes((1024, 1024))
    assert list(full) == FRAMES
    assert full["panel"] == (48, 48, 576, 400)
    assert full["card"] == (704, 400, 272, 576)
    assert full["timer_badge"] == (48, 816, 160, 160)
    assert ui_kit.boxes((512, 512))["card"] == (352, 200, 136, 288)
    assert {e.name: e.stretch for e in ui_kit.LAYOUT} == {
        "panel": "xy", "button": "xy", "button_pressed": "xy", "card": "xy",
        "hp_bar_frame": "x", "xp_bar_frame": "x", "timer_badge": "none",
        "hp_bar_fill": "x", "xp_bar_fill": "x", "slot": "xy", "badge": "xy",
        "pip_on": "none", "pip_off": "none", "joystick_base": "none", "joystick_knob": "none"}
    # the 2:3 sheet keeps the seven frames where the square sheet has them and adds the band below
    tall = ui_kit.boxes((1024, 1536))
    assert list(tall) == FRAMES + BAND
    assert {name: tall[name] for name in FRAMES} == full
    assert tall["hp_bar_fill"] == (48, 1024, 608, 64)
    assert tall["xp_bar_fill"] == (48, 1136, 608, 64)
    assert tall["slot"] == (48, 1248, 192, 192)
    assert tall["badge"] == (288, 1248, 176, 112)
    assert tall["pip_on"] == (512, 1248, 96, 96)
    assert tall["pip_off"] == (512, 1392, 96, 96)
    assert tall["joystick_base"] == (704, 1024, 272, 272)
    assert tall["joystick_knob"] == (768, 1344, 144, 144)
    assert ui_kit.boxes(TALL)["joystick_base"] == (352, 512, 136, 136)
    # boxes never touch: 48 px apart at 1024, and 48 px in from the sheet's edges
    for layout, (width, height) in ((full, (1024, 1024)), (tall, (1024, 1536))):
        names = list(layout)
        for i, a in enumerate(names):
            ax, ay, aw, ah = layout[a]
            assert ax >= 48 and ay >= 48 and ax + aw + 48 <= width and ay + ah + 48 <= height, a
            for b in names[i + 1:]:
                bx, by, bw, bh = layout[b]
                assert ax + aw + 48 <= bx or bx + bw + 48 <= ax or ay + ah + 48 <= by or by + bh + 48 <= ay


def test_frame_insets_cover_every_corner_ornament_and_the_long_flourish():
    _, drawn, elements, report = split()
    assert report["status"] == "pass", report["review_reasons"]
    for name in ("panel", "button", "button_pressed", "card"):
        entry = report["elements"][name]
        left, top, right, bottom = offset(report, drawn, name)
        k = PLAN[name]["k"]
        reach = k + PLAN[name].get("flourish", 0)
        assert entry["stretch"] == "xy"
        assert entry["insets"] == {"left": left + reach + 2, "right": right + k + 2,
                                   "top": top + k + 2, "bottom": bottom + k + 2}, name
        assert elements[name].size == (entry["size"]["w"], entry["size"]["h"])


def test_bar_insets_cover_the_gem_and_the_cap_and_padding_is_the_track():
    _, drawn, elements, report = split()
    for name in ("hp_bar_frame", "xp_bar_frame"):
        entry = report["elements"][name]
        left, top, right, bottom = offset(report, drawn, name)
        radius, cap = PLAN[name]["radius"], PLAN[name]["cap"]
        assert entry["stretch"] == "x"
        # the cap is taller than the bar, so its kept 2 px anti-aliased edge lies left of it, inside the
        # element: that edge belongs to the cap's fixed slice too
        assert entry["insets"] == {"left": left + 2 * radius + 2, "right": right + cap + 2 + 2, "top": 0, "bottom": 0}
        # the track: right of the gem, left of the cap, inside the border rows (9 px from the drawn top/bottom)
        assert entry["padding"] == {"left": left + 2 * radius + 2, "right": right + cap + 2,
                                    "top": top + 9 + 2, "bottom": bottom + 9 + 2}


def test_badge_does_not_stretch_and_its_content_fits_inside_the_ring():
    _, drawn, elements, report = split()
    entry = report["elements"]["timer_badge"]
    assert entry["stretch"] == "none"
    assert entry["insets"] == {"left": 0, "right": 0, "top": 0, "bottom": 0}
    left, top, _, _ = offset(report, drawn, "timer_badge")
    d = drawn["timer_badge"][2]
    centre = (left + d / 2, top + d / 2)
    pad, size = entry["padding"], entry["size"]
    corners = [(pad["left"], pad["top"]), (size["w"] - pad["right"], pad["top"]),
               (pad["left"], size["h"] - pad["bottom"]), (size["w"] - pad["right"], size["h"] - pad["bottom"])]
    for cx, cy in corners:
        assert math.hypot(cx - centre[0], cy - centre[1]) <= d / 2 - 3
    inner = d / 2 - 3
    assert size["w"] - pad["left"] - pad["right"] >= 1.2 * inner


def test_frame_padding_keeps_text_off_the_border_and_the_ornaments():
    _, drawn, elements, report = split()
    for name in ("panel", "card", "button"):
        entry = report["elements"][name]
        left, top, right, bottom = offset(report, drawn, name)
        pad = entry["padding"]
        k = PLAN[name]["k"]
        for side, edge in (("left", left), ("right", right), ("top", top), ("bottom", bottom)):
            assert edge + k <= pad[side] <= edge + k + 4, (name, side, pad)


def test_haze_is_cleared_before_cutting_and_reported():
    _, drawn, elements, report = split()
    cleanup = report["alpha_cleanup"]
    assert cleanup["floor"] == 64 and cleanup["edge_px"] == 2
    assert cleanup["haze_fraction_before"] > 0.01 and cleanup["haze_fraction_after"] == 0
    assert cleanup["changed_solid_pixels"] == 0 and cleanup["changed_edge_pixels"] == 0
    assert report["dropped_parts"] == 0
    assert report["unassigned"] == []
    # the element keeps at most its two-pixel anti-aliased edge of haze, never the six-pixel ring
    for name in drawn:
        assert all(0 <= value <= 2 for value in offset(report, drawn, name)), name


def test_ornament_reaching_the_middle_of_an_edge_is_flagged():
    _, _, _, report = split(crest=True)
    assert report["status"] == "needs-review"
    assert any(reason.startswith("card:") and "x" in reason for reason in report["review_reasons"])


def test_a_large_part_outside_every_box_is_reported():
    _, _, _, report = split(stray=True)
    assert report["status"] == "needs-review"
    assert report["unassigned"] == [{"rect": {"x": 250, "y": 410, "w": 20, "h": 20}, "pixels": 400}]


def test_an_empty_box_fails():
    image, _ = synthetic_sheet(missing="button_pressed")
    with pytest.raises(ValueError, match="^button_pressed: its box holds no element; generate the sheet again$"):
        ui_kit.split_ui_kit(image)


def test_a_sheet_that_is_neither_square_nor_2_3_fails():
    for size in ((512, 400), (512, 900), (768, 512)):
        with pytest.raises(ValueError, match=r"square \(1:1.*2:3"):
            ui_kit.split_ui_kit(Image.new("RGBA", size))


def test_cli_writes_element_pngs_and_a_report_the_pack_reads(tmp_path):
    image, drawn = synthetic_sheet()
    sheet = tmp_path / "uikit.png"
    image.save(sheet)
    before = sheet.read_bytes()
    out = tmp_path / "ui"
    assert ui_kit.main(["--sheet", str(sheet), "--out-dir", str(out)]) == 0
    assert sheet.read_bytes() == before
    report = json.loads((out / "ui-kit.json").read_text(encoding="utf-8"))
    assert report["kind"] == "sprite-gen-ui-kit" and report["version"] == 1
    assert report["sheet"] == str(sheet.resolve()) and report["sheet_size"] == [SIZE, SIZE]
    assert list(report["elements"]) == FRAMES
    # every element of a square sheet is required, so its report has no `missing`, as before the 2:3 sheet
    assert "missing" not in report and "layout_fit" not in report
    assert sorted(p.name for p in out.iterdir()) == sorted([f"{name}.png" for name in FRAMES] + ["ui-kit.json"])
    cleaned, _ = ui_kit.clear_haze(image.convert("RGBA"), floor=64, edge=2)
    for name, entry in report["elements"].items():
        assert entry["file"] == f"{name}.png"
        assert entry["box"] == dict(zip("xywh", ui_kit.boxes((SIZE, SIZE))[name]))
        rect = entry["rect"]
        with Image.open(out / entry["file"]) as element:
            assert element.size == (entry["size"]["w"], entry["size"]["h"]) == (rect["w"], rect["h"])
            crop = cleaned.crop((rect["x"], rect["y"], rect["x"] + rect["w"], rect["y"] + rect["h"]))
            assert element.convert("RGBA").tobytes() == crop.tobytes()
    request = tmp_path / "game.json"
    request.write_text(json.dumps({"version": 2, "ui": {"kit": "ui/ui-kit.json"}}), encoding="utf-8")
    result = game_pack.pack(request, tmp_path / "pack")
    for name, entry in report["elements"].items():
        packed = result["ui"]["nine_slice"][name]
        assert packed["insets"] == entry["insets"] and packed["padding"] == entry["padding"]
        assert packed["size"] == entry["size"] and packed["stretch"] == entry["stretch"]


def test_cli_refuses_to_write_into_the_sheets_directory_over_the_sheet(tmp_path):
    image, _ = synthetic_sheet()
    sheet = tmp_path / "panel.png"
    image.save(sheet)
    with pytest.raises(SystemExit, match="sheet"):
        ui_kit.main(["--sheet", str(sheet), "--out-dir", str(tmp_path)])


# --out-dir, --report (None: the default <out-dir>/ui-kit.json) and the `file` the report gives the
# panel: the PNG's path from the report's own directory, with "/" separators
REPORT_PLACES = [
    ("ui", None, "panel.png"),
    ("elements", "reports/kit.json", "../elements/panel.png"),
    ("ui", "ui/reports/kit.json", "../panel.png"),
    ("reports/elements", "reports/kit.json", "elements/panel.png"),
]


@pytest.mark.slow
def test_a_report_anywhere_names_the_written_pngs_and_packs_the_same(tmp_path):
    image, _ = synthetic_sheet()
    sheet = tmp_path / "uikit.png"
    image.save(sheet)
    packs = []
    for index, (out_dir, report, panel) in enumerate(REPORT_PLACES):
        root = tmp_path / f"place-{index}"
        args = ["ui-kit", "--sheet", str(sheet), "--out-dir", str(root / out_dir)]
        if report is not None:
            args += ["--report", str(root / report)]
        assert cli.main(args) == 0
        report_path = root / (report or f"{out_dir}/ui-kit.json")
        written = json.loads(report_path.read_text(encoding="utf-8"))
        assert written["elements"]["panel"]["file"] == panel
        for name, entry in written["elements"].items():
            assert (report_path.parent / entry["file"]).resolve() == (root / out_dir / f"{name}.png").resolve()
        request = root / "game.json"
        request.write_text(json.dumps({"version": 2, "ui": {"kit": report_path.relative_to(root).as_posix()}}), encoding="utf-8")
        assert cli.main(["game-pack", "--request", str(request), "--out-dir", str(root / "pack")]) == 0
        packs.append({path.name: path.read_bytes() for path in (root / "pack").iterdir()})
    assert len(json.loads(packs[0]["game-pack.json"])["ui"]["nine_slice"]) == len(FRAMES)
    assert packs == [packs[0]] * len(REPORT_PLACES)


def test_a_report_path_that_is_not_json_is_refused_before_anything_is_written(tmp_path):
    image, _ = synthetic_sheet()
    sheet = tmp_path / "uikit.png"
    image.save(sheet)
    out = tmp_path / "ui"
    # the report would replace an element PNG of the same name
    with pytest.raises(SystemExit, match=r"--report must be a \.json path"):
        ui_kit.main(["--sheet", str(sheet), "--out-dir", str(out), "--report", str(out / "panel.png")])
    assert not out.exists()


def test_guide_draws_every_box_and_the_text_names_every_element(tmp_path):
    path = tmp_path / "guide.png"
    cell = ui_kit.draw_guide(path, (1024, 1024))
    with Image.open(path) as guide:
        guide = guide.convert("RGB")
        assert guide.size == (1024, 1024)
        for x, y, w, h in ui_kit.boxes((1024, 1024)).values():
            assert guide.getpixel((x + w // 2, y)) == ui_kit.GUIDE_BOX
            assert guide.getpixel((x, y + h // 2)) == ui_kit.GUIDE_BOX
            assert guide.getpixel((x + w // 2, y + h // 2)) == ui_kit.GUIDE_BACKGROUND
    assert cell == {"kind": "ui-kit", "width": 1024, "height": 1024,
                    "boxes": {name: dict(zip("xywh", box)) for name, box in ui_kit.boxes((1024, 1024)).items()}}
    text = ui_kit.guide_text()
    assert ui_kit.guide_text((1024, 1024)) == text
    positions = [text.index(entry.what) for entry in ui_kit.LAYOUT[:len(FRAMES)]]
    assert positions == sorted(positions)
    assert "last attached image is a layout guide" in text
    assert "no text" in text
    assert not any(entry.what in text for entry in ui_kit.LAYOUT[len(FRAMES):])


def test_tall_guide_draws_the_square_boxes_and_the_band_and_names_every_element(tmp_path):
    path = tmp_path / "guide.png"
    cell = ui_kit.draw_guide(path, (683, 1024))
    assert {slot.name for slot in ui_kit.LAYOUT if slot.round} == set(ROUND)
    with Image.open(path) as guide:
        guide = guide.convert("RGB")
        assert guide.size == (683, 1024)
        for name, (x, y, w, h) in ui_kit.boxes((683, 1024)).items():
            assert on_guide_line(guide, (x + w // 2, y), (0, 1)) and on_guide_line(guide, (x, y + h // 2), (1, 0))
            assert guide.getpixel((x + w // 2, y + h // 2)) == ui_kit.GUIDE_BACKGROUND
            # a round part's place is a circle, so the corner of its box is the guide's grey, not its outline
            assert (guide.getpixel((x, y)) == ui_kit.GUIDE_BACKGROUND) == (name in ROUND), name
    assert cell == {"kind": "ui-kit", "width": 683, "height": 1024,
                    "boxes": {name: dict(zip("xywh", box)) for name, box in ui_kit.boxes((683, 1024)).items()}}
    assert list(cell["boxes"]) == FRAMES + BAND
    text = ui_kit.guide_text((683, 1024))
    assert text == ui_kit.guide_text((1024, 1536))
    positions = [text.index(entry.what) for entry in ui_kit.LAYOUT]
    assert positions == sorted(positions)
    assert "15 blue boxes and circles" in text and "last attached image is a layout guide" in text
    assert "no text" in text and "no plate, tile or backing" in text and "no frame, rim or end pieces" in text


ROUND = ("pip_on", "pip_off", "joystick_base", "joystick_knob")


def on_guide_line(guide, point, step):
    """The guide's outline passes ``point`` or one of the two pixels inward from it along ``step``."""
    return any(guide.getpixel((point[0] + i * step[0], point[1] + i * step[1])) == ui_kit.GUIDE_BOX for i in range(3))


def test_a_round_part_drawn_on_a_square_plate_is_flagged():
    image, drawn = synthetic_sheet(size=TALL, noise=False, haze=False)
    pixels = np.asarray(image).copy()
    # a square plate behind the lit dot: the box's corners around the disc are painted too
    x, y, w, h = drawn["pip_on"]
    plate = pixels[y:y + h, x:x + w]
    plate[plate[..., 3] == 0] = BORDER
    _, report = ui_kit.split_ui_kit(Image.fromarray(pixels))
    assert report["status"] == "needs-review"
    note = "its corners are filled: it was drawn on a square plate or backing, not as a circle; regenerate it"
    assert [reason for reason in report["review_reasons"] if reason.startswith(ROUND)] == [f"pip_on: {note}"]
    plated = Image.fromarray(np.ascontiguousarray(pixels[y:y + h, x:x + w]))
    assert ui_kit.measure_round(plated) == [note]
    assert ui_kit.measure_round(Image.fromarray(np.ascontiguousarray(np.asarray(image)[y:y + h, x:x + w]))) == []


def tall_split(**kwargs):
    return split(size=TALL, **kwargs)


def test_tall_sheet_cuts_every_element_of_the_whole_kit():
    _, drawn, elements, report = tall_split()
    assert report["status"] == "pass", report["review_reasons"]
    assert report["sheet_size"] == list(TALL)
    assert list(report["elements"]) == list(elements) == FRAMES + BAND
    assert report["unassigned"] == [] and report["dropped_parts"] == 0
    # nothing is missing, and every box held its element, so the layout was not fitted
    assert report["missing"] == [] and "layout_fit" not in report
    for name in FRAMES + BAND:
        entry = report["elements"][name]
        assert entry["file"] == f"{name}.png" and entry["parts"] == 1
        assert entry["box"] == dict(zip("xywh", ui_kit.boxes(TALL)[name]))
        assert elements[name].size == (entry["size"]["w"], entry["size"]["h"])
        # the cut is the drawn element and at most its kept two-pixel haze edge
        assert all(0 <= value <= 2 for value in offset(report, drawn, name)), name


def test_tall_sheet_cuts_the_seven_frames_as_the_square_sheet_does():
    _, _, square_elements, square = split(noise=False)
    _, _, tall_elements, tall = tall_split(noise=False)
    for name in FRAMES:
        assert tall["elements"][name] == square["elements"][name]
        assert tall_elements[name].tobytes() == square_elements[name].tobytes()


def test_fills_stretch_in_width_and_fit_the_content_area_of_their_bar_frame():
    _, drawn, elements, report = tall_split()
    for fill, frame in (("hp_bar_fill", "hp_bar_frame"), ("xp_bar_fill", "xp_bar_frame")):
        entry, bar = report["elements"][fill], report["elements"][frame]
        left, top, right, bottom = offset(report, drawn, fill)
        cap = PLAN[fill]["cap"]
        assert entry["stretch"] == "x"
        assert entry["insets"] == {"left": left + cap + 2, "right": right + cap + 2, "top": 0, "bottom": 0}
        # a fill holds no content: its padding is 0, as a frame's insets are 0 along an axis it keeps
        assert entry["padding"] == {"left": 0, "right": 0, "top": 0, "bottom": 0}
        pad, size = bar["padding"], bar["size"]
        rect = {"x": pad["left"], "y": pad["top"], "w": size["w"] - pad["left"] - pad["right"],
                "h": size["h"] - pad["top"] - pad["bottom"]}
        assert entry["inside"] == {"element": frame, "rect": rect, "scale": round(rect["h"] / entry["size"]["h"], 6)}
        # scaled by `scale`, the fill is as tall as the content area to the pixel and its two ends fit inside it
        assert abs(entry["size"]["h"] * entry["inside"]["scale"] - rect["h"]) < 0.01
        assert (entry["insets"]["left"] + entry["insets"]["right"]) * entry["inside"]["scale"] < rect["w"]
    for name in FRAMES + BAND:
        if name not in ("hp_bar_fill", "xp_bar_fill"):
            assert "inside" not in report["elements"][name], name


def test_pips_and_joystick_parts_never_stretch_and_keep_their_round_shape():
    _, drawn, elements, report = tall_split()
    for name in ("pip_on", "pip_off", "joystick_base", "joystick_knob"):
        entry = report["elements"][name]
        assert entry["stretch"] == "none"
        assert entry["insets"] == entry["padding"] == {"left": 0, "right": 0, "top": 0, "bottom": 0}
        d = drawn[name][2]
        assert d == drawn[name][3]
        assert d <= entry["size"]["w"] <= d + 4 and d <= entry["size"]["h"] <= d + 4
        assert abs(entry["size"]["w"] - entry["size"]["h"]) <= 2, name
    # the two pips are drawn in boxes of one size, so one dot swaps for the other in place
    assert ui_kit.boxes(TALL)["pip_on"][2:] == ui_kit.boxes(TALL)["pip_off"][2:]


def test_slot_and_badge_insets_cover_their_corner_ornaments_and_padding_keeps_content_off_them():
    _, drawn, elements, report = tall_split()
    for name in ("slot", "badge"):
        entry = report["elements"][name]
        left, top, right, bottom = offset(report, drawn, name)
        k = PLAN[name]["k"]
        assert entry["stretch"] == "xy"
        assert entry["insets"] == {"left": left + k + 2, "right": right + k + 2,
                                   "top": top + k + 2, "bottom": bottom + k + 2}, name
        for side, edge in (("left", left), ("right", right), ("top", top), ("bottom", bottom)):
            assert edge + k <= entry["padding"][side] <= edge + k + 4, (name, side, entry["padding"])


def test_the_frames_are_required_and_the_band_is_optional():
    assert [slot.name for slot in ui_kit.LAYOUT if not slot.optional] == FRAMES
    assert [slot.name for slot in ui_kit.LAYOUT if slot.optional] == BAND


@pytest.mark.parametrize("missing", ["pip_off", tuple(BAND)])
def test_an_empty_box_of_the_band_leaves_that_element_out_of_the_kit(missing):
    gone = [missing] if isinstance(missing, str) else list(missing)
    image, drawn = synthetic_sheet(size=TALL, missing=missing)
    elements, report = ui_kit.split_ui_kit(image)
    kept = [name for name in FRAMES + BAND if name not in gone]
    assert report["missing"] == gone
    assert list(report["elements"]) == list(elements) == kept
    # a missing element is not a measurement to review: the elements that are there are measured as ever
    assert report["status"] == "pass", report["review_reasons"]
    assert "layout_fit" not in report
    for name in kept:
        assert all(0 <= value <= 2 for value in offset(report, drawn, name)), name


def test_every_empty_frame_box_is_named_and_an_empty_band_box_is_not():
    image, _ = synthetic_sheet(size=TALL, missing=("panel", "card", "pip_on"))
    with pytest.raises(ValueError, match="^panel, card: their boxes hold no element; generate the sheet again$"):
        ui_kit.split_ui_kit(image)


SQUEEZE = 0.93


def test_a_sheet_drawn_shorter_than_the_guide_is_fitted_before_its_parts_are_assigned():
    # Drawn 7 % shorter from the top edge down, the lower pip's and the lower fill's centres lie in the
    # widened boxes above theirs (half the gap, 12 px here): assigned by the layout's own boxes, three boxes
    # would be empty (badge, pip_off, joystick_knob), two would hold two elements each, and the upper fill
    # would lie in no box.
    image, drawn = synthetic_sheet(size=TALL, squeeze=SQUEEZE)
    boxes, reach = ui_kit.boxes(TALL), 12
    for name, above in (("pip_off", "pip_on"), ("xp_bar_fill", "hp_bar_fill")):
        _, y, _, h = drawn[name]
        _, ay, _, ah = boxes[above]
        assert ay - reach <= y + h / 2 < ay + ah + reach, name
    elements, report = ui_kit.split_ui_kit(image)
    assert report["missing"] == [] and report["unassigned"] == []
    assert list(elements) == FRAMES + BAND
    for name in FRAMES + BAND:
        entry = report["elements"][name]
        assert entry["parts"] == 1, name
        assert all(0 <= value <= 2 for value in offset(report, drawn, name)), name
        # each element's box is its layout box moved onto the drawing, and the drawn element lies inside it
        x, y, w, h = drawn[name]
        box = entry["box"]
        assert box["x"] - 2 <= x and x + w <= box["x"] + box["w"] + 2, name
        assert box["y"] - 2 <= y and y + h <= box["y"] + box["h"] + 2, name
    fit = report["layout_fit"]
    assert set(fit) == {"scale", "offset"}
    assert abs(fit["scale"]["x"] - 1) < 0.005 and abs(fit["offset"]["x"]) < 2
    assert abs(fit["scale"]["y"] - SQUEEZE) < 0.005 and abs(fit["offset"]["y"]) < 3


def test_a_squeezed_sheet_with_an_empty_band_box_is_fitted_and_names_it():
    image, drawn = synthetic_sheet(size=TALL, squeeze=SQUEEZE, missing="joystick_knob")
    elements, report = ui_kit.split_ui_kit(image)
    assert report["missing"] == ["joystick_knob"] and "layout_fit" in report
    assert list(elements) == [name for name in FRAMES + BAND if name != "joystick_knob"]
    assert all(report["elements"][name]["parts"] == 1 for name in elements)


@pytest.mark.parametrize("name", FRAMES)
def test_a_squeezed_sheet_with_an_empty_frame_box_still_refuses_it(name):
    # fitting the layout to the drawing never fills an empty frame's box with a neighbour: the sheet is refused
    # by that frame's own name
    image, _ = synthetic_sheet(size=TALL, squeeze=SQUEEZE, missing=name)
    with pytest.raises(ValueError, match=f"^{name}: its box holds no element; generate the sheet again$"):
        ui_kit.split_ui_kit(image)


# The drift a cut accepts (docs/ui-kit.md "Reading the drawing"): along each axis a scale within a
# tenth of 1 and an offset within 96 px at 1024.
ACCEPTED_SCALE = (0.9, 1.1)
ACCEPTED_OFFSET = 96


def accepted_drift(rng, size=TALL):
    """A drift drawn at random within the accepted range, among those that keep every box 8 px inside the sheet."""
    unit = size[0] / ui_kit.REFERENCE
    while True:
        sx, sy = rng.uniform(*ACCEPTED_SCALE, size=2)
        tx, ty = rng.uniform(-ACCEPTED_OFFSET, ACCEPTED_OFFSET, size=2) * unit
        if all(8 <= x * sx + tx and (x + w) * sx + tx <= size[0] - 8 and 8 <= y * sy + ty
               and (y + h) * sy + ty <= size[1] - 8 for x, y, w, h in ui_kit.boxes(size).values()):
            return float(sx), float(tx), float(sy), float(ty)


def assert_cut_as_drawn(report, elements, drawn, drift):
    """Exactly the band elements that were not drawn are missing, and every drawn element is cut whole as itself:
    its cut is its own drawing to within its 2 px edge, so it holds no neighbour's. A fitted layout is the drift the
    sheet was drawn with, to within a hundredth in scale and 4 px in offset, and puts each drawing inside its
    element's fitted box."""
    kept = [name for name in FRAMES + BAND if name in drawn]
    assert report["missing"] == [name for name in BAND if name not in drawn]
    assert list(report["elements"]) == list(elements) == kept
    for name in kept:
        assert report["elements"][name]["parts"] == 1, name
        assert all(0 <= value <= 2 for value in offset(report, drawn, name)), (name, offset(report, drawn, name))
    if "layout_fit" in report:
        fit = report["layout_fit"]
        for axis, scale, shift in (("x", *drift[:2]), ("y", *drift[2:])):
            assert abs(fit["scale"][axis] - scale) <= 0.01 and abs(fit["offset"][axis] - shift) <= 4, (fit, drift)
        for name in kept:
            x, y, w, h = drawn[name]
            box = report["elements"][name]["box"]
            assert box["x"] - 2 <= x and x + w <= box["x"] + box["w"] + 2, name
            assert box["y"] - 2 <= y and y + h <= box["y"] + box["h"] + 2, name


PROBE_MISSING = ["hp_bar_fill", "pip_off", "joystick_knob"]


def test_a_squeezed_sheet_missing_several_band_elements_names_those_and_cuts_no_neighbour_under_their_names(tmp_path):
    # Drawn 6 % shorter with three band elements left out, the lower fill's centre lies in the empty upper fill's
    # widened box and the lit dot's in the lower fill's: assigned by the layout's own boxes, the lower fill would be
    # cut as `hp_bar_fill`, the lit dot as `xp_bar_fill`, and the lit dot named missing. The CLI, an outside report
    # and the pack name exactly the elements drawn.
    image, drawn = synthetic_sheet(size=TALL, squeeze=0.94, missing=PROBE_MISSING)
    boxes, reach = ui_kit.boxes(TALL), 12
    for name, other in (("xp_bar_fill", "hp_bar_fill"), ("pip_on", "xp_bar_fill")):
        x, y, w, h = drawn[name]
        ox, oy, ow, oh = boxes[other]
        assert ox - reach <= x + w / 2 < ox + ow + reach and oy - reach <= y + h / 2 < oy + oh + reach, name
    elements, report = ui_kit.split_ui_kit(image)
    assert_cut_as_drawn(report, elements, drawn, (1, 0, 0.94, 0))
    sheet = tmp_path / "source.png"
    image.save(sheet)
    out, report_path = tmp_path / "parts", tmp_path / "reports" / "kit.json"
    assert ui_kit.main(["--sheet", str(sheet), "--out-dir", str(out), "--report", str(report_path)]) == 0
    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written["missing"] == PROBE_MISSING
    kept = [name for name in FRAMES + BAND if name not in PROBE_MISSING]
    assert sorted(p.stem for p in out.glob("*.png")) == sorted(kept)
    request = tmp_path / "game.json"
    request.write_text(json.dumps({"version": 2, "ui": {"kit": "reports/kit.json", "scale": 0.5}}), encoding="utf-8")
    packed = game_pack.pack(request, tmp_path / "pack")
    assert sorted(packed["ui"]["nine_slice"]) == sorted(kept)


@pytest.mark.parametrize("squeeze", [0.9, 0.91, 0.92, 0.93, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99])
def test_a_sheet_squeezed_from_the_top_by_any_accepted_amount_names_the_missing_band_elements(squeeze):
    image, drawn = synthetic_sheet(size=TALL, squeeze=squeeze, missing=PROBE_MISSING)
    elements, report = ui_kit.split_ui_kit(image)
    assert_cut_as_drawn(report, elements, drawn, (1, 0, squeeze, 0))


# fast: no element left out, and each left out alone
@pytest.mark.parametrize("subset", slow_but(range(2 ** len(BAND)), lambda subset: subset & (subset - 1) == 0))
def test_every_set_of_missing_band_elements_under_a_random_accepted_drift_is_named_and_the_rest_cut_as_drawn(subset):
    # Each of the 256 sets of band elements left out, on a sheet drawn under a drift picked at random (seeded by the
    # set) within the accepted range: a scale and an offset along x and along y.
    rng = np.random.default_rng(subset)
    missing = [name for bit, name in enumerate(BAND) if subset >> bit & 1]
    drift = accepted_drift(rng)
    image, drawn = synthetic_sheet(size=TALL, drift=drift, missing=missing)
    elements, report = ui_kit.split_ui_kit(image)
    assert_cut_as_drawn(report, elements, drawn, drift)


@pytest.mark.parametrize("name", FRAMES)
def test_an_empty_frame_box_under_a_random_accepted_drift_still_refuses_the_sheet_by_its_name(name):
    rng = np.random.default_rng(FRAMES.index(name))
    missing = [name] + [band for band in BAND if rng.random() < 0.5]
    image, _ = synthetic_sheet(size=TALL, drift=accepted_drift(rng), missing=missing)
    with pytest.raises(ValueError, match=f"^{name}: its box holds no element; generate the sheet again$"):
        ui_kit.split_ui_kit(image)


def test_the_accepted_drift_is_the_documented_range():
    assert (1 - ui_kit.DRIFT_SCALE, 1 + ui_kit.DRIFT_SCALE) == ACCEPTED_SCALE
    assert ui_kit.DRIFT_OFFSET == ACCEPTED_OFFSET


@pytest.mark.parametrize("nudge, name", [(30, "pip_on"), (42, "pip_off")])
def test_a_band_element_drawn_in_the_gap_between_two_boxes_refuses_the_sheet_with_its_own_reason(nudge, name):
    # The lit dot drawn so far below its box that its centre lies in the gap between its box and the unlit dot's
    # (left empty): either dot could be meant, so the sheet is refused, naming the box that holds it, and never cut
    # under a guessed name.
    image, _ = synthetic_sheet(size=TALL, missing="pip_off", nudge={"pip_on": (0, nudge)})
    refusal = f"^{name}: its box holds a part centred outside it .*; generate the sheet again$"
    with pytest.raises(ValueError, match=refusal):
        ui_kit.split_ui_kit(image)


def test_a_sheet_drawn_further_off_its_guide_than_a_cut_accepts_is_refused_with_its_own_reason():
    image, _ = synthetic_sheet(size=TALL, squeeze=0.8)
    with pytest.raises(ValueError, match="^the drawing drifted further from the layout than a cut accepts"):
        ui_kit.split_ui_kit(image)


def draw_ellipse(pixels, cx, cy, w, h, color):
    """An ellipse ``w`` x ``h`` px whose bounding box is centred on (cx, cy)."""
    yy, xx = np.indices((h, w))
    region = pixels[cy - h // 2:cy - h // 2 + h, cx - w // 2:cx - w // 2 + w]
    region[((xx - w / 2 + 0.5) / (w / 2)) ** 2 + ((yy - h / 2 + 0.5) / (h / 2)) ** 2 <= 1] = color


def two_way_sheet(dot):
    """A sheet that fits the layout two ways (the counterexample to PR #125's cut): the seven frames drawn small, 20 x 8
    px, each inside its own box both where the layout puts the box and with the layout squeezed to 0.9 from the top
    edge, and one dot, ``dot`` px wide and tall, centred on pip_off's box so squeezed, which is the centre of pip_on's
    own box. The sheet, and the frames' rects and the dot's."""
    boxes = ui_kit.boxes(TALL)
    pixels = np.zeros((TALL[1], TALL[0], 4), dtype=np.uint8)
    drawn = {}
    for name in FRAMES:
        x, y, w, h = boxes[name]
        cx, cy = x + w // 2, min(y + h // 2, int(0.9 * (y + h)) - 6)
        fill(pixels, cx - 10, cy - 4, cx + 10, cy + 4, BORDER)
        drawn[name] = (cx - 10, cy - 4, 20, 8)
    x, y, w, h = boxes["pip_off"]
    cx, cy = x + w // 2, round(0.9 * (y + h / 2))
    draw_ellipse(pixels, cx, cy, *dot, PIP_OFF)
    drawn["dot"] = (cx - dot[0] // 2, cy - dot[1] // 2, *dot)
    return Image.fromarray(pixels), drawn


@pytest.mark.slow
@pytest.mark.parametrize("dot", [(18, 18), (60, 18)])
def test_a_drawing_that_fits_the_layout_two_ways_is_refused_with_its_own_reason(tmp_path, dot):
    # Where the layout puts the boxes, and with the layout squeezed to 0.9, every frame lies inside its own box, clear
    # of its edges, and the dot is centred on pip_on's box in the first and on pip_off's in the second: the drawing says
    # neither which drift it was drawn with nor which dot it is. A dot 60 px wide overflows either box alike. The sheet
    # is refused with its own reason, by the CLI too, which writes nothing.
    image, drawn = two_way_sheet(dot)
    boxes = ui_kit.boxes(TALL)
    for sy, pip in ((1.0, "pip_on"), (0.9, "pip_off")):
        for name in FRAMES:
            x, y, w, h = drawn[name]
            bx, by, bw, bh = boxes[name]
            assert bx + 1 <= x and x + w <= bx + bw - 1, (sy, name)
            assert sy * by + 1 <= y and y + h <= sy * (by + bh) - 1, (sy, name)
        x, y, w, h = drawn["dot"]
        bx, by, bw, bh = boxes[pip]
        assert x + w / 2 == bx + bw / 2 and y + h / 2 == pytest.approx(sy * (by + bh / 2)), (sy, pip)
    refusal = ("^pip_on, pip_off: under another drift the cut accepts, the drawing fits the layout at least as well "
               r"\(to within 1 px\) with these elements named differently, so it does not say which elements they "
               "are; generate the sheet again$")
    with pytest.raises(ValueError, match=refusal):
        ui_kit.split_ui_kit(image)
    sheet, out = tmp_path / "uikit.png", tmp_path / "ui"
    image.save(sheet)
    with pytest.raises(SystemExit, match="^ui-kit: pip_on, pip_off: under another drift the cut accepts"):
        ui_kit.main(["--sheet", str(sheet), "--out-dir", str(out)])
    assert not out.exists()


def squeezed_sheet():
    """A model's drawing of the layout squeezed to 0.9 from the top edge with pip_off alone in the band: each frame 96 %
    as wide and 55 % as tall as its squeezed box (the timer badge round), inside that box and as near its own box's
    centre as that allows, and the dot 72 % as wide as its box, on pip_off's squeezed centre."""
    boxes = ui_kit.boxes(TALL)
    pixels = np.zeros((TALL[1], TALL[0], 4), dtype=np.uint8)
    for name in FRAMES:
        x, y, w, h = boxes[name]
        rw, rh = 0.96 * w, 0.55 * 0.9 * h
        if name == "timer_badge":
            rw = rh = 0.55 * 0.9 * min(w, h)
        cx, cy = x + w / 2, min(max(y + h / 2, 0.9 * y + rh / 2 + 1), 0.9 * (y + h) - rh / 2 - 1)
        fill(pixels, round(cx - rw / 2), round(cy - rh / 2), round(cx + rw / 2) + 1, round(cy + rh / 2) + 1, BORDER)
    x, y, w, h = boxes["pip_off"]
    r, cx, cy = 0.4 * 0.9 * min(w, h), x + w / 2, 0.9 * (y + h / 2)
    draw_disc(pixels, round(cx - r), round(cy - r), round(cx + r) - round(cx - r) + 1, PIP_OFF)
    return Image.fromarray(pixels)


def test_a_drawing_that_fits_one_drift_better_is_named_by_it():
    # Squeezed to 0.9, the layout holds every frame clear of its box's edges and the dot on pip_off's centre. The dot
    # would be pip_on under a layout stretched a little and moved up, but no such drift keeps the frames and the dot
    # clear of the edges: the nearest leaves them more than a pixel short of it. The dot is pip_off.
    elements, report = ui_kit.split_ui_kit(squeezed_sheet())
    assert report["missing"] == [name for name in BAND if name != "pip_off"]
    assert list(elements) == FRAMES + ["pip_off"]


def scattered_sheet(seed):
    """A 2:3 sheet drawn under a drift picked at random within the accepted range, with a random set of band elements
    left out, each element a rectangle a random share of its box across (from a twentieth to nine tenths) anywhere
    inside its moved box, at least 1 px clear of the edges. The sheet, and each element's rect by name."""
    rng = np.random.default_rng(seed)
    sx, tx, sy, ty = accepted_drift(rng)
    share = rng.choice([0.05, 0.1, 0.2, 0.4, 0.7, 0.9])
    missing = [name for name in BAND if rng.random() < 0.6]
    pixels = np.zeros((TALL[1], TALL[0], 4), dtype=np.uint8)
    drawn = {}
    for name, (bx, by, bw, bh) in ui_kit.boxes(TALL).items():
        if name in missing:
            continue
        x0, x1, y0, y1 = bx * sx + tx, (bx + bw) * sx + tx, by * sy + ty, (by + bh) * sy + ty
        w = max(5, int((x1 - x0) * share * rng.uniform(0.5, 1)))
        h = max(5, int((y1 - y0) * share * rng.uniform(0.5, 1)))
        w, h = min(w, int(x1 - x0) - 4), min(h, int(y1 - y0) - 4)
        x = int(rng.integers(math.ceil(x0) + 1, math.floor(x1) - w))
        y = int(rng.integers(math.ceil(y0) + 1, math.floor(y1) - h))
        fill(pixels, x, y, x + w, y + h, BORDER)
        drawn[name] = (x, y, w, h)
    return Image.fromarray(pixels), drawn


def anchored_sheet(seed):
    """A 2:3 sheet drawn under a drift picked at random within the accepted range: the seven frames full size, as
    `synthetic_sheet` draws them, and a random half of the band elements, each a rectangle a random share of its box
    across (from a twentieth to seven tenths) anywhere inside its moved box, at least 1 px clear of the edges. The
    sheet, and each element's rect by name."""
    rng = np.random.default_rng(100_000 + seed)
    drift = accepted_drift(rng)
    sx, tx, sy, ty = drift
    image, drawn = synthetic_sheet(size=TALL, drift=drift, missing=BAND, haze=False, noise=False)
    pixels = np.asarray(image).copy()
    share = rng.choice([0.05, 0.1, 0.2, 0.4, 0.7])
    for name in BAND:
        if rng.random() < 0.5:
            continue
        bx, by, bw, bh = ui_kit.boxes(TALL)[name]
        x0, x1, y0, y1 = bx * sx + tx, (bx + bw) * sx + tx, by * sy + ty, (by + bh) * sy + ty
        w = max(5, int((x1 - x0) * share * rng.uniform(0.5, 1)))
        h = max(5, int((y1 - y0) * share * rng.uniform(0.5, 1)))
        w, h = min(w, int(x1 - x0) - 4), min(h, int(y1 - y0) - 4)
        x = int(rng.integers(math.ceil(x0) + 1, math.floor(x1) - w))
        y = int(rng.integers(math.ceil(y0) + 1, math.floor(y1) - h))
        fill(pixels, x, y, x + w, y + h, BORDER)
        drawn[name] = (x, y, w, h)
    return Image.fromarray(pixels), drawn


# The reading of a drawing (docs/ui-kit.md "Reading the drawing"), evaluated here at given drifts, apart from the cut's
# own search. A drift (sx, tx, sy, ty) moves each layout box (x, y, w, h) to (x sx + tx, y sy + ty, w sx, h sy). Each
# part goes to the first box, in layout order, whose moved box widened by half the gap (the widening scaled with it)
# holds its centroid, or to none. A part in a box costs how far each of its four sides falls short of lying CLEARANCE px
# inside the moved box; a part in no box costs half the gap, CLEARANCE and half its smaller extent. A reading's cost is
# the sum. The cut considers the drifts within READ_SCALE and READ_OFFSET (px at 1024) along each axis, and two readings
# whose costs differ by at most TIE px fit the drawing equally well.
CLEARANCE = TIE = 1
READ_SCALE = (0.875, 1.125)
READ_OFFSET = 120


def read_at(parts, size, drifts):
    """At each of ``drifts``: the reading's cost, and each part's box (its index in layout order, -1 for none)."""
    layout = np.asarray(list(ui_kit.boxes(size).values()), dtype=float)
    reach = ui_kit.GAP / 2 * size[0] / ui_kit.REFERENCE
    centroid = np.asarray([part["centroid"] for part in parts], dtype=float).reshape(-1, 2)
    bbox = np.asarray([part["bbox"] for part in parts], dtype=float).reshape(-1, 4)
    drifts = np.asarray(drifts, dtype=float).reshape(-1, 4)
    held = np.ones((len(drifts), len(parts), len(layout)), dtype=bool)  # [drift, part, box]
    short = np.zeros(held.shape)
    for axis in (0, 1):
        s, t = drifts[:, 2 * axis, None, None], drifts[:, 2 * axis + 1, None, None]
        low, high = layout[:, axis], layout[:, axis] + layout[:, 2 + axis]
        at = centroid[None, :, axis, None]
        held &= ((low - reach) * s + t <= at) & (at < (high + reach) * s + t)
        short += np.maximum(0, low * s + t + CLEARANCE - bbox[None, :, axis, None])
        short += np.maximum(0, bbox[None, :, 2 + axis, None] - (high * s + t) + CLEARANCE)
    placed = held.any(axis=2)
    box = np.where(placed, held.argmax(axis=2), -1)
    loose = reach + CLEARANCE + np.minimum(bbox[:, 2] - bbox[:, 0], bbox[:, 3] - bbox[:, 1]) / 2
    inside = np.take_along_axis(short, np.maximum(box, 0)[..., None], axis=2)[..., 0]
    return np.where(placed, inside, loose).sum(axis=1), box


def assert_two_readings(refusal, size):
    """The two readings an ambiguity refusal names, each evaluated here at its drift: it reads the drawing as the
    refusal says, every frame holds a part, it costs at most TIE px (the drawing lies inside its boxes, so the least
    cost is 0), and the two read the drawing differently."""
    assert refusal.reason == "ambiguous", str(refusal)
    names = list(ui_kit.boxes(size))
    one, other = refusal.witnesses
    for reading in (one, other):
        cost, box = read_at(refusal.parts, size, [reading.drift])
        assert [names[i] if i >= 0 else None for i in box[0]] == list(reading.boxes), (reading, str(refusal))
        assert set(FRAMES) <= set(reading.boxes), reading
        assert cost[0] <= TIE + 1e-6, (reading, cost[0])
    assert one.boxes != other.boxes


def assert_read_as_drawn(image, drawn, edge=0):
    """The sheet, every part of it inside its box under the drift it was drawn with, is cut as drawn or refused as
    fitting the layout two ways (`assert_two_readings`), never refused otherwise. Cut as drawn: exactly the band
    elements not drawn are missing, and each drawn element is cut as its own drawing, its rect within ``edge`` px of the
    drawn one on every side, inside its element's box (the layout's, moved by `layout_fit`) to within a pixel."""
    try:
        elements, report = ui_kit.split_ui_kit(image)
    except ui_kit.Refusal as refusal:
        assert_two_readings(refusal, image.size)
        return
    assert report["missing"] == [name for name in BAND if name not in drawn]
    assert list(elements) == list(report["elements"]) == [name for name in FRAMES + BAND if name in drawn]
    for name, (x, y, w, h) in drawn.items():
        rect, box = report["elements"][name]["rect"], report["elements"][name]["box"]
        assert all(abs(value) <= edge for value in offset(report, drawn, name)), (name, rect, drawn[name])
        assert box["x"] - 1 <= rect["x"] and rect["x"] + rect["w"] <= box["x"] + box["w"] + 1, (name, rect, box)
        assert box["y"] - 1 <= rect["y"] and rect["y"] + rect["h"] <= box["y"] + box["h"] + 1, (name, rect, box)


@pytest.mark.parametrize("seed", [2, 5, 6])
def test_a_sheet_of_small_drawings_that_fits_the_layout_one_way_is_cut_as_drawn(tmp_path, seed):
    # Drawn wholly inside their boxes under a drift within the accepted range, with no other reading within 1 px: these
    # sheets were refused as drifted further than a cut accepts (fitted at a scale of 0.758, 1.371 and 0.798), the
    # least-squares fit of box centres dragged by small drawings placed off their boxes' centres. They are cut as drawn,
    # by the CLI too.
    image, drawn = scattered_sheet(seed)
    elements, report = ui_kit.split_ui_kit(image)
    assert report["missing"] == [name for name in BAND if name not in drawn]
    assert list(elements) == list(drawn)
    for name, (x, y, w, h) in drawn.items():
        assert report["elements"][name]["rect"] == {"x": x, "y": y, "w": w, "h": h}, name
    sheet, out = tmp_path / "uikit.png", tmp_path / "ui"
    image.save(sheet)
    assert ui_kit.main(["--sheet", str(sheet), "--out-dir", str(out)]) == 0
    written = json.loads((out / "ui-kit.json").read_text(encoding="utf-8"))
    assert written["missing"] == report["missing"] and written["elements"] == report["elements"]
    assert sorted(p.name for p in out.iterdir()) == sorted([f"{name}.png" for name in drawn] + ["ui-kit.json"])


@pytest.mark.parametrize("seed", slow_but(range(512), lambda seed: seed < 16))
def test_a_sheet_of_small_drawings_under_a_random_accepted_drift_is_cut_as_drawn_or_refused_as_two_readings(seed):
    # Small drawings anywhere inside their boxes leave the drift less certain, so a sheet may fit the layout under two
    # drifts that read it differently, and is then refused, naming both readings. It is never refused for another
    # reason, and whenever it is cut, exactly the band elements not drawn are missing and every element drawn is cut as
    # its own drawing, never under another's name.
    image, drawn = scattered_sheet(seed)
    assert_read_as_drawn(image, drawn)


@pytest.mark.parametrize("seed", slow_but(range(256), lambda seed: seed < 8))
def test_full_size_frames_and_small_band_drawings_are_cut_as_drawn_or_refused_as_two_readings(seed):
    # The full-size frames pin the drift; the band's small drawings lie anywhere inside their boxes, off their centres.
    image, drawn = anchored_sheet(seed)
    assert_read_as_drawn(image, drawn, edge=2)


def sampled_drifts(rng, size, around):
    """Drifts at random within the drifts the cut considers: uniform, and near each drift of ``around``."""
    unit = size[0] / ui_kit.REFERENCE
    low = np.array([READ_SCALE[0], -READ_OFFSET * unit] * 2)
    high = np.array([READ_SCALE[1], READ_OFFSET * unit] * 2)
    near = [np.asarray(drift) + rng.normal(0, [0.004, 2.0, 0.004, 2.0], (2000, 4)) for drift in around]
    drifts = np.concatenate([rng.uniform(low, high, (4000, 4))] + near)
    return drifts[((drifts >= low) & (drifts <= high)).all(axis=1)]


COUNTEREXAMPLE_SHEETS = ([f"scattered-{seed}" for seed in range(1, 9)] + [f"anchored-{seed}" for seed in range(4)]
                         + ["two-way-18", "two-way-60", "squeezed", "nudged", "stray"])


def counterexample_sheet(label):
    """A sheet for the counterexample search, and the drift it was drawn with."""
    number = int(label.rpartition("-")[2]) if label[-1].isdigit() else None
    if label.startswith("scattered"):
        return scattered_sheet(number)[0], accepted_drift(np.random.default_rng(number))
    if label.startswith("anchored"):
        return anchored_sheet(number)[0], accepted_drift(np.random.default_rng(100_000 + number))
    if label.startswith("two-way"):
        return two_way_sheet((number, 18))[0], (1, 0, 1, 0)
    if label == "squeezed":
        return squeezed_sheet(), (1, 0, 0.9, 0)
    if label == "nudged":
        return synthetic_sheet(size=TALL, missing="pip_off", nudge={"pip_on": (0, 30)})[0], (1, 0, 1, 0)
    return synthetic_sheet(size=TALL, stray=True)[0], (1, 0, 1, 0)


@pytest.mark.parametrize("label", slow_but(COUNTEREXAMPLE_SHEETS, lambda label: label in {"scattered-1", "anchored-0"}))
def test_no_sampled_drift_reads_the_drawing_better_than_the_cut_found(label):
    # A search for counterexamples, which proves nothing when it finds none: thousands of drifts at random, uniform over
    # the drifts the cut considers and near the drift the sheet was drawn with and the cut's, evaluated here. None reads
    # the drawing at a lower cost than the least the cut's search found (`_read`, the judgment `split_ui_kit` decides
    # by); and when the sheet is cut, no reading that holds every frame, within TIE of the least such cost, reads it
    # otherwise than the cut.
    image, drawn_drift = counterexample_sheet(label)
    judgment = ui_kit._read(image)
    try:
        ui_kit.split_ui_kit(image)
    except ui_kit.Refusal:
        cut = None
    else:
        (cut,) = judgment.candidates
    around = [drawn_drift] + ([cut.drift] if cut is not None else [])
    drifts = sampled_drifts(np.random.default_rng(COUNTEREXAMPLE_SHEETS.index(label)), image.size, around)
    cost, box = read_at(judgment.parts, image.size, drifts)
    assert cost.min() >= judgment.least - 1e-6, (label, cost.min(), judgment.least)
    if cut is not None:
        names = list(ui_kit.boxes(image.size))
        valid = np.all([(box == names.index(name)).any(axis=1) for name in FRAMES], axis=0)
        near = valid & (cost <= judgment.least_valid + TIE - 1e-6)
        assert near.any(), label
        want = np.array([names.index(name) if name is not None else -1 for name in cut.boxes])
        assert (box[near] == want).all(), label


def test_a_mark_between_two_boxes_whose_widened_boxes_touch_is_never_read_in_no_box():
    # The gap between the two dots' boxes is twice the reach, so their widened boxes touch: under every drift that
    # leaves the mark inside the dots' columns, its centroid lies in one of them. A tall thin mark centred in that gap
    # costs less in no box than in either dot's box, but no drift reads it in no box, so no reading that does is cut or
    # named. Both readings that put it in a dot's box fit the sheet as well: the sheet is refused, and each of the two
    # readings the refusal names reads the sheet as it says.
    image, _ = synthetic_sheet(size=TALL, haze=False, noise=False)
    pixels = np.asarray(image).copy()
    x, y, w, h = ui_kit.boxes(TALL)["pip_on"]
    below = ui_kit.boxes(TALL)["pip_off"][1]
    cx, cy = x + w // 2, (y + h + below) // 2
    fill(pixels, cx - 2, cy - 10, cx + 2, cy + 10, BORDER)
    with pytest.raises(ui_kit.Refusal, match="^pip_on, pip_off: under another drift the cut accepts") as refused:
        ui_kit.split_ui_kit(Image.fromarray(pixels))
    names = list(ui_kit.boxes(TALL))
    for reading in refused.value.witnesses:
        cost, box = read_at(refused.value.parts, TALL, [reading.drift])
        assert [names[i] if i >= 0 else None for i in box[0]] == list(reading.boxes), reading
        assert None not in reading.boxes and cost[0] <= refused.value.least + TIE + 1e-6, (reading, cost[0])


@pytest.mark.slow
@pytest.mark.timeout(10)
def test_a_sheet_of_many_small_parts_is_cut_or_refused_in_seconds():
    # Every element a 4 px square (16 px, the smallest part kept) anywhere inside its moved box, with about half the
    # band left out, and sixty more such squares scattered over the sheet: most squares lie in no box under any drift,
    # so no reading keeps the drawing clear of its boxes' edges; the search proves that within its work bound, and the
    # sheet is decided or refused as off the layout within seconds.
    rng = np.random.default_rng(47)
    sx, tx, sy, ty = accepted_drift(rng)
    pixels = np.zeros((TALL[1], TALL[0], 4), dtype=np.uint8)
    for name, (bx, by, bw, bh) in ui_kit.boxes(TALL).items():
        if name in BAND and rng.random() < 0.5:
            continue
        x0, x1, y0, y1 = bx * sx + tx, (bx + bw) * sx + tx, by * sy + ty, (by + bh) * sy + ty
        x = int(rng.integers(math.ceil(x0) + 1, math.floor(x1) - 5))
        y = int(rng.integers(math.ceil(y0) + 1, math.floor(y1) - 5))
        fill(pixels, x, y, x + 4, y + 4, BORDER)
    for _ in range(60):
        x, y = rng.integers(0, TALL[0] - 4), rng.integers(0, TALL[1] - 4)
        fill(pixels, x, y, x + 4, y + 4, BORDER)
    with contextlib.suppress(ValueError):
        ui_kit.split_ui_kit(Image.fromarray(pixels))


def test_the_cli_names_the_missing_elements_and_writes_no_png_for_them(tmp_path, capsys):
    image, _ = synthetic_sheet(size=TALL, missing=("pip_off", "joystick_knob"))
    sheet = tmp_path / "uikit.png"
    image.save(sheet)
    out = tmp_path / "ui"
    assert ui_kit.main(["--sheet", str(sheet), "--out-dir", str(out)]) == 0
    report = json.loads((out / "ui-kit.json").read_text(encoding="utf-8"))
    assert report["missing"] == ["pip_off", "joystick_knob"]
    kept = [name for name in FRAMES + BAND if name not in report["missing"]]
    assert sorted(p.name for p in out.iterdir()) == sorted([f"{name}.png" for name in kept] + ["ui-kit.json"])
    assert capsys.readouterr().out.rstrip().endswith("; missing, left out of the kit: pip_off, joystick_knob")


def tree(root):
    """The visible files (by their bytes) and directories (None) under ``root``, and the hidden entries apart:
    each by its path, not what it holds."""
    visible, hidden = {}, []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            if not any(part.startswith(".") for part in rel.parts[:-1]):
                hidden.append(rel.as_posix())
            continue
        visible[rel.as_posix()] = path.read_bytes() if path.is_file() else None
    return visible, hidden


def test_a_kit_is_written_into_a_new_or_empty_out_dir_and_one_that_holds_anything_is_refused(tmp_path):
    # A rerun into the directory an earlier kit filled, with another sheet, is refused by the directory's name and
    # leaves it byte for byte as it was, as does any other file in it, a hidden one too, or a file at its path. An
    # empty directory takes a kit, a refused sheet writes nothing, and a --report outside --out-dir must be new.
    sheet, out = tmp_path / "uikit.png", tmp_path / "ui"

    def run(out_dir, size=TALL, missing=(), report=None):
        synthetic_sheet(size=size, missing=missing)[0].save(sheet)
        return ui_kit.main(["--sheet", str(sheet), "--out-dir", str(out_dir)]
                           + (["--report", str(report)] if report else []))

    def refused(out_dir, held):
        return (rf"^ui-kit: --out-dir {re.escape(str(out_dir.resolve()))} {held}; "
                "a kit is written whole into a new or empty directory$")

    assert run(out) == 0
    kit = tree(out)
    for size, missing in ((TALL, "pip_off"), ((SIZE, SIZE), ())):
        with pytest.raises(SystemExit, match=refused(out, "holds badge.png and 15 more")):
            run(out, size, missing)
        assert tree(out) == kit
    for name in ("logo.png", ".hidden"):
        other = tmp_path / f"other{name}"
        other.mkdir()
        (other / name).write_bytes(b"not a kit")
        with pytest.raises(SystemExit, match=refused(other, f"holds {re.escape(name)}")):
            run(other)
        assert tree(other) == (({}, [name]) if name.startswith(".") else ({name: b"not a kit"}, []))
    (tmp_path / "taken").write_bytes(b"a file")
    with pytest.raises(SystemExit, match=refused(tmp_path / "taken", "is a file")):
        run(tmp_path / "taken")
    assert (tmp_path / "taken").read_bytes() == b"a file"
    (tmp_path / "empty").mkdir()
    assert run(tmp_path / "empty", missing="pip_off") == 0
    kept = [name for name in FRAMES + BAND if name != "pip_off"]
    assert sorted(tree(tmp_path / "empty")[0]) == sorted([f"{name}.png" for name in kept] + ["ui-kit.json"])
    with pytest.raises(SystemExit, match="^ui-kit: panel: its box holds no element; generate the sheet again$"):
        run(tmp_path / "new", missing="panel")
    assert not (tmp_path / "new").exists()
    report = tmp_path / "kit.json"
    report.write_text("{}\n", encoding="utf-8")
    with pytest.raises(SystemExit, match=rf"^ui-kit: --report {re.escape(str(report.resolve()))} already exists; "
                                         "a report outside --out-dir is written to a new path$"):
        run(tmp_path / "elements", report=report)
    assert report.read_text(encoding="utf-8") == "{}\n" and not (tmp_path / "elements").exists()
    # no hidden directory or temp file of a run is left anywhere
    assert [path.name for path in tmp_path.rglob(".*") if path.name != ".hidden"] == []


# the out-dir and the report (None: in the out-dir) of each placement; the outside one's directories are made by
# the run, so a run that fails must remove them again
PUBLISH_PLACES = {"inside": ("ui", None), "outside": ("kit/elements", "reports/kit.json")}
FS_CALLS = ("open", "fsync", "rename", "replace", "link", "unlink", "rmdir", "mkdir")
KILLED = 97


class Faults:
    """Every filesystem call the publish makes once the sheet is cut, counted. The k-th one fails once, just before it
    runs or just after it returned: by an OSError (EIO), or by ending the process there, as a kill would. An interrupt
    at a call is an interrupt at that call's instruction, which the instruction sweep below raises."""

    def __init__(self, monkeypatch, cut):
        self.armed, self.calls, self.fail_at, self.when, self.mode, self.fired = False, [], None, None, None, False
        for name in FS_CALLS:
            monkeypatch.setattr(os, name, self.wrap(name, getattr(os, name)))

        def split_then_arm(image, **kwargs):
            self.armed = True
            return cut[0], copy.deepcopy(cut[1])

        monkeypatch.setattr(ui_kit, "split_ui_kit", split_then_arm)

    def fail(self, name):
        self.fired = True
        if self.mode == "kill":
            os._exit(KILLED)
        raise OSError(errno.EIO, f"injected {name} failure")

    def wrap(self, name, call):
        def faulty(*args, **kwargs):
            if not self.armed:
                return call(*args, **kwargs)
            paths = [Path(str(arg)).name for arg in args[:2] if isinstance(arg, (str, os.PathLike))]
            self.calls.append(f"{name}({', '.join(paths)})")
            here = len(self.calls) == self.fail_at
            if here and self.when == "before":
                self.fail(name)
            result = call(*args, **kwargs)
            if here and self.when == "after":
                if name == "open":
                    os.close(result)  # the run never gets it, so it would stay open in this process
                self.fail(name)
            return result
        return faulty

    def run(self, args, fail_at=None, when=None, mode=None):
        """The command's exit code or its refusal; a kill runs it in a child process and gives the child's code."""
        self.calls, self.fail_at, self.when, self.mode, self.fired = [], fail_at, when, mode, False
        if mode != "kill":
            try:
                return ui_kit.main(args)
            except SystemExit as exc:
                return str(exc)
            finally:
                self.armed = False
        pid = os.fork()
        if pid == 0:
            code = 1
            try:
                sys.stdout = sys.stderr = io.StringIO()
                code = ui_kit.main(args)
            finally:
                os._exit(code)
        self.armed = False
        return os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])


def files_of(state):
    return {path: data for path, data in state.items() if data is not None}


# A kill forks the test process, which pytest-xdist runs with threads of its own: the child makes only the publish's
# filesystem calls and leaves by os._exit, and one that hung would end the test by its timeout.
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded, use of fork:DeprecationWarning")
@pytest.mark.parametrize("mode", ["raise", pytest.param("kill", marks=pytest.mark.skipif(
    not hasattr(os, "fork"), reason="a kill is a child process ended at the fault"))])
@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("start", slow_but(["absent", "empty", "empty, removed first"], lambda start: start == "absent"))
@pytest.mark.parametrize("placement", slow_but(sorted(PUBLISH_PLACES), lambda placement: placement == "inside"))
def test_a_failure_or_a_kill_at_any_step_of_the_publish_leaves_nothing_or_the_whole_kit(
        tmp_path, monkeypatch, placement, start, when, mode):
    # Each filesystem call of the publish fails once, just before it runs or just after it returned, in a copy of the
    # same start. One of those calls publishes the kit: the rename that gives it --out-dir's name or, with the report
    # outside, the link that gives the report its name. An error before that leaves the start exactly, with nothing
    # hidden; from it on, the whole kit. A kill leaves what the run had made: the start, the whole kit or, killed
    # between the rename and the link, the whole --out-dir and no report yet; never part of a kit under the out-dir's
    # name, and nothing hidden but the run's own staging directory or report temp beside them. "removed first" is the
    # Windows rename, which takes no existing directory's name, so an empty --out-dir is removed just before it.
    out_rel, report_rel = PUBLISH_PLACES[placement]
    sheet = tmp_path / "uikit.png"
    image, _ = synthetic_sheet(size=TALL, missing="pip_off")
    image.save(sheet)
    faults = Faults(monkeypatch, ui_kit.split_ui_kit(image))
    if start == "empty, removed first":
        monkeypatch.setattr(ui_kit, "RENAME_ONTO_EMPTY_DIR", False)
    template = tmp_path / "start"
    template.mkdir()
    if start != "absent":
        (template / out_rel).mkdir(parents=True)

    def copy_of_start(name):
        return Path(shutil.copytree(template, tmp_path / name))

    def args(root):
        return (["--sheet", str(sheet), "--out-dir", str(root / out_rel)]
                + (["--report", str(root / report_rel)] if report_rel else []))

    before, _ = tree(template)
    reference = copy_of_start("reference")
    assert faults.run(args(reference)) == 0
    calls = faults.calls
    complete, litter = tree(reference)
    assert litter == []
    report = report_rel or f"{out_rel}/ui-kit.json"
    kit_files = [path for path in files_of(complete) if path != report]
    assert len(kit_files) == 14 and report in complete
    # the sweep reaches each file of the kit, the rename of the out-dir and the report's own commit
    reached = {name for call in calls for name in call[call.index("(") + 1:-1].split(", ")}
    assert {Path(path).name for path in kit_files} | {Path(out_rel).name, Path(report).name} <= reached, calls

    def naming(op, target):
        """The number of the call ``op(…, target)``, which gives ``target`` its name."""
        return next(k for k, call in enumerate(calls, 1) if call.startswith(f"{op}(") and call.endswith(f", {target})"))

    renamed = naming("rename", Path(out_rel).name)
    published = naming("link", Path(report_rel).name) if report_rel else renamed
    without_report = {path: data for path, data in files_of(complete).items() if path != report}
    beside = [f"{Path(out_rel).parent / f'.{Path(out_rel).name}.ui-kit-'}".removeprefix("./")]
    if report_rel:
        beside.append(f"{Path(report_rel).parent / f'.{Path(report_rel).name}.'}".removeprefix("./"))
    for k, call in enumerate(calls, 1):
        root = copy_of_start(f"k{k}")
        outcome = faults.run(args(root), fail_at=k, when=when, mode=mode)
        after, hidden = tree(root)
        where = (k, when, call, outcome, sorted(after), hidden)

        def ran(step):
            return step < k or (step == k and when == "after")

        assert all(any(entry.startswith(prefix) for prefix in beside) for entry in hidden), where
        if mode == "kill":
            assert outcome == KILLED, where
            left = complete if ran(published) else without_report if ran(renamed) else before
            assert files_of(after) == files_of(left), where
            directories = {path for path, data in after.items() if data is None}
            kept = {path for path, data in before.items() if data is None}
            if start == "empty, removed first":
                kept.discard(out_rel)
            assert kept <= directories <= kept | {path for path, data in complete.items() if data is None}, where
            continue
        assert faults.fired, where
        assert outcome in (0, f"ui-kit: [Errno {errno.EIO}] injected {call[:call.index('(')]} failure"), where
        if ran(published):
            # hidden: only the report's temp, when removing it after the kit was published is what failed
            assert after == complete and (hidden == [] or (
                report_rel and mode == "raise" and all(entry.startswith(beside[-1]) for entry in hidden))), where
        else:
            assert outcome != 0 and after == before and hidden == [], where


class Halt(BaseException):
    """An exception of no standard kind."""


# How each kind of interrupt arrives and what it ends the run with: a real signal through Python's own SIGINT
# handler, or an exception of no standard kind. A SIGTERM handler that exits ends the run through the same
# `except BaseException`, with no signal-specific code, so it is not swept again.
INTERRUPTS = {
    "SIGINT": (signal.SIGINT, signal.default_int_handler, KeyboardInterrupt),
    "BaseException": (None, None, Halt),
}


# Interrupted after a file is opened and before its with statement holds it, or as that statement closes it, a run
# leaves the file object to be closed when the run's frames go, which Python reports as an unclosed file.
@pytest.mark.filterwarnings("ignore:unclosed file:ResourceWarning")
@pytest.mark.parametrize("kind", sorted(INTERRUPTS))
@pytest.mark.parametrize("granularity", ["line", "instruction"])
@pytest.mark.parametrize("start", slow_but(["absent", "empty", "empty, removed first"], lambda start: start == "absent"))
@pytest.mark.parametrize("placement", slow_but(sorted(PUBLISH_PLACES), lambda placement: placement == "inside"))
def test_an_interrupt_at_any_line_or_instruction_of_the_publish_leaves_nothing_or_the_whole_kit(
        tmp_path, monkeypatch, placement, start, granularity, kind):
    # Python may raise an interrupt at any instruction: a signal's handler runs between instructions, and so does a
    # tracer's or a debugger's callback, which can receive the signal or raise its own exception there. A tracer on the
    # publish's own functions raises the interrupt once per run, at one line, or one instruction, of the publish, each
    # in turn. Every run ends with that interrupt and leaves the start exactly, with nothing hidden, or the whole kit.
    # The publish is given a small kit directly (one run per instruction); with the report inside --out-dir, it is in
    # a directory of its own there. What ended a run is kept without its traceback, so the run's frames, and a file
    # object only they hold, go when the run ends; a run interrupted between opening a file and wrapping the descriptor
    # cannot close it, so each descriptor the run opened and left open is closed after it.
    out_rel, report_rel = PUBLISH_PLACES[placement]
    signum, handler, ends = INTERRUPTS[kind]
    if start == "empty, removed first":
        monkeypatch.setattr(ui_kit, "RENAME_ONTO_EMPTY_DIR", False)
    event = "line" if granularity == "line" else "opcode"
    data = b'{"kind": "sprite-gen-ui-kit"}\n'
    opened, open_fd = [], os.open

    def recorded_open(*args, **kwargs):
        fd = open_fd(*args, **kwargs)
        opened.append((fd, os.fstat(fd)))
        return fd

    monkeypatch.setattr(os, "open", recorded_open)

    def publish(root, target=None):
        """Publish the kit into ``root``, interrupted at ``target``, one event of the run: (function, instruction
        offset, occurrence). Answers the tree before and after, the run's events as such targets with where each
        was, whether the interrupt was raised, and what ended the run (0 when nothing did)."""
        root.mkdir()
        if start != "absent":
            (root / out_rel).mkdir(parents=True)
        report = root / report_rel if report_rel else None
        files = {"one.png": b"one", "two.png": b"two"} | ({} if report else {"reports/kit.json": data})
        given = tree(root)
        seen, events, raised = Counter(), [], []
        opened.clear()

        def each(frame, what, arg):
            if what == event:
                key = (id(frame.f_code), frame.f_lasti)
                seen[key] += 1
                events.append((*key, seen[key], f"{frame.f_code.co_name}:{frame.f_lineno}@{frame.f_lasti}"))
                if events[-1][:3] == target and not raised:
                    raised.append(True)
                    sys.settrace(None)
                    if signum is None:
                        raise Halt
                    signal.raise_signal(signum)
            return each

        def calls(frame, what, arg):
            if frame.f_code.co_filename != ui_kit.__file__:
                return None
            if event == "opcode":
                frame.f_trace_opcodes, frame.f_trace_lines = True, False
            return each

        previous = signal.signal(signum, handler) if signum is not None else None
        tracer = sys.gettrace()
        sys.settrace(calls)
        try:
            ui_kit._write_kit(root / out_rel, files, report, data)
            ended = 0
        except BaseException as exc:
            ended = exc.with_traceback(None)
        finally:
            sys.settrace(tracer)
            if signum is not None:
                signal.signal(signum, previous)
            for fd, stat in opened:
                with contextlib.suppress(OSError):  # closed by the run: gone, or another file's number now
                    if os.path.samestat(os.fstat(fd), stat):
                        os.close(fd)
        return given, tree(root), events, bool(raised), ended

    # the first opcode-traced run of a function reports none of its instructions on Python 3.12, and some from 3.13
    publish(tmp_path / "warm-up")
    before, complete, events, _, ended = publish(tmp_path / "reference")
    assert ended == 0 and complete[1] == [] and complete != before
    assert {Path(path).name for path in files_of(complete[0])} == {"one.png", "two.png", "kit.json"}
    missed, bad = [], []
    for n, (*target, at) in enumerate(events):
        given, after, _, raised, ended = publish(tmp_path / f"{n}", tuple(target))
        if not raised:  # the run never reached that event (see below)
            missed.append(at)
            continue
        assert given == before
        if not (isinstance(ended, ends) and after in (before, complete)):
            bad.append((f"{at}#{target[2]}", repr(ended), sorted(after[0]), after[1]))
    assert bad == []
    # Every line event of the reference run recurs in a faulted one. A tracer's instruction events can differ a little
    # between runs on Python 3.13 and later, so one a faulted run never reaches gets no interrupt; most do.
    assert (missed == [] if granularity == "line" else len(missed) < len(events) / 2), missed


@pytest.mark.parametrize("placement", sorted(PUBLISH_PLACES))
def test_a_kit_that_reaches_the_out_dir_first_refuses_this_run_and_stays_whole(tmp_path, monkeypatch, placement):
    # Two runs into one new --out-dir: the other run's kit lands there just before this run renames its own in.
    # This run is refused, removes what it made (its staging directory, the report's directory) and leaves the
    # other kit as it was.
    out_rel, report_rel = PUBLISH_PLACES[placement]
    sheet, other = tmp_path / "uikit.png", tmp_path / "other"
    synthetic_sheet(size=TALL)[0].save(sheet)
    assert ui_kit.main(["--sheet", str(sheet), "--out-dir", str(other)]) == 0
    synthetic_sheet(size=TALL, missing="pip_off", noise=False)[0].save(sheet)
    out = (tmp_path / out_rel).resolve()
    rename = os.rename

    def the_other_kit_first(src, dst, *args, **kwargs):
        if Path(dst) == out and not out.exists():
            shutil.copytree(other, out)
        return rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "rename", the_other_kit_first)
    with pytest.raises(SystemExit, match=rf"^ui-kit: --out-dir {re.escape(str(out))} was filled while this kit "
                                         r"was cut: it holds badge\.png and 15 more; nothing was written$"):
        ui_kit.main(["--sheet", str(sheet), "--out-dir", str(out)]
                    + (["--report", str(tmp_path / report_rel)] if report_rel else []))
    assert tree(out) == tree(other)
    assert [path.name for path in tmp_path.rglob(".*")] == []
    assert not (tmp_path / "reports").exists()


def test_a_report_that_appears_first_refuses_this_run_and_is_kept(tmp_path, monkeypatch):
    # Two runs with their own --out-dir and one --report outside them: the other run's report lands just before
    # this run's own. This run is refused, takes its kit back out, and the other report stays as it was.
    sheet, out, report = tmp_path / "uikit.png", tmp_path / "elements", tmp_path / "reports" / "kit.json"
    synthetic_sheet(size=TALL)[0].save(sheet)
    theirs = b'{"kind": "sprite-gen-ui-kit", "the other run": true}\n'
    link = os.link

    def the_other_report_first(src, dst, *args, **kwargs):
        if Path(dst) == report.resolve() and not report.exists():
            report.write_bytes(theirs)
        return link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", the_other_report_first)
    with pytest.raises(SystemExit, match=rf"^ui-kit: --report {re.escape(str(report.resolve()))} appeared while "
                                         "this kit was cut; nothing was written$"):
        ui_kit.main(["--sheet", str(sheet), "--out-dir", str(out), "--report", str(report)])
    assert report.read_bytes() == theirs
    assert not out.exists()
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == [
        "reports", "reports/kit.json", "uikit.png"]


def test_a_fill_whose_ends_do_not_fit_its_bar_frame_is_flagged():
    image, drawn = synthetic_sheet(size=TALL, noise=False, haze=False)
    pixels = np.asarray(image).copy()
    # end caps 112 px long on a 296 px fill, short of its middle fifth: scaled to the 26 px track's height
    # (the fill is 24 px tall), the two fixed ends are 247 px wide, and the track is 242 px long
    x, y, w, h = drawn["xp_bar_fill"]
    fill(pixels, x, y, x + 112, y + h, FILL_CAP)
    fill(pixels, x + w - 112, y, x + w, y + h, FILL_CAP)
    _, report = ui_kit.split_ui_kit(Image.fromarray(pixels))
    entry = report["elements"]["xp_bar_fill"]
    assert entry["insets"]["left"] == entry["insets"]["right"] == 114
    rect = entry["inside"]["rect"]
    assert (entry["insets"]["left"] + entry["insets"]["right"]) * entry["inside"]["scale"] >= rect["w"]
    assert report["status"] == "needs-review"
    assert [reason for reason in report["review_reasons"] if reason.startswith("xp_bar_fill:")] == [
        "xp_bar_fill: scaled to the height of xp_bar_frame's content area, its fixed ends are wider than that "
        "area; a full bar would overflow it"]


def pack_tall_kit(tmp_path, scale, **kwargs):
    image, drawn = synthetic_sheet(size=TALL, **kwargs)
    sheet = tmp_path / "uikit.png"
    image.save(sheet)
    assert ui_kit.main(["--sheet", str(sheet), "--out-dir", str(tmp_path / "ui")]) == 0
    request = tmp_path / "game.json"
    request.write_text(json.dumps({"version": 2, "ui": {"kit": "ui/ui-kit.json", "scale": scale}}), encoding="utf-8")
    out = tmp_path / "pack"
    return game_pack.pack(request, out), out


def stored_frame(out, result, frame):
    for page in result["textures"]:
        if frame in page["frames"]:
            box = page["frames"][frame]["frame"]
            with Image.open(out / page["image"]) as atlas:
                return atlas.convert("RGBA").crop((box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"]))
    raise AssertionError(frame)


@pytest.mark.parametrize("scale", [1, 0.5, 0.37])
def test_a_packed_fill_is_stored_as_its_bar_frames_content_area(tmp_path, scale):
    result, out = pack_tall_kit(tmp_path, scale)
    nine = result["ui"]["nine_slice"]
    assert list(nine) == sorted(FRAMES + BAND)
    for fill_name, frame in (("hp_bar_fill", "hp_bar_frame"), ("xp_bar_fill", "xp_bar_frame")):
        entry, bar = nine[fill_name], nine[frame]
        assert set(entry) == {"frame", "size", "stretch", "insets", "padding", "inside"}
        assert all("inside" not in nine[name] for name in nine if not name.endswith("_fill"))
        pad = bar["padding"]
        rect = {"x": pad["left"], "y": pad["top"], "w": bar["size"]["w"] - pad["left"] - pad["right"],
                "h": bar["size"]["h"] - pad["top"] - pad["bottom"]}
        # the fill is stored at the frame's content area: at full width it covers it to the pixel, never past it
        assert entry["inside"] == {"element": frame, "rect": rect}
        assert entry["size"] == {"w": rect["w"], "h": rect["h"]}
        assert rect["x"] + rect["w"] + pad["right"] == bar["size"]["w"]
        assert rect["y"] + rect["h"] + pad["bottom"] == bar["size"]["h"]
        assert entry["stretch"] == "x" and entry["insets"]["top"] == entry["insets"]["bottom"] == 0
        assert entry["insets"]["left"] + entry["insets"]["right"] < rect["w"]
        assert entry["padding"] == {"left": 0, "right": 0, "top": 0, "bottom": 0}
        stored = stored_frame(out, result, entry["frame"])
        assert stored.size == (rect["w"], rect["h"])
        # its ends are the fill's own ends resized by one factor, never stretched: the factor makes it as tall
        # as the content area
        with Image.open(tmp_path / "ui" / f"{fill_name}.png") as source:
            source = source.convert("RGBA")
        factor = rect["h"] / source.height
        resized = resize_cell(source, (max(1, round(source.width * factor)), rect["h"]))
        left, right = entry["insets"]["left"], entry["insets"]["right"]
        cells = np.asarray(stored)
        ends = np.asarray(resized)
        assert np.array_equal(cells[:, :left], ends[:, :left])
        assert np.array_equal(cells[:, rect["w"] - right:], ends[:, resized.width - right:])
    # round parts keep their aspect: scaled by `scale` alone, never stretched
    report = json.loads((tmp_path / "ui" / "ui-kit.json").read_text(encoding="utf-8"))
    for name in ("pip_on", "pip_off", "joystick_base", "joystick_knob"):
        entry, source = nine[name], report["elements"][name]["size"]
        assert entry["stretch"] == "none" and set(entry["insets"].values()) == {0}
        assert entry["size"] == {"w": max(1, round(source["w"] * scale)), "h": max(1, round(source["h"] * scale))}
        assert stored_frame(out, result, entry["frame"]).size == (entry["size"]["w"], entry["size"]["h"])


@pytest.mark.parametrize("missing", ["xp_bar_fill", tuple(BAND)])
def test_a_partial_kit_packs_only_the_elements_it_holds(tmp_path, missing):
    gone = {missing} if isinstance(missing, str) else set(missing)
    result, out = pack_tall_kit(tmp_path, 0.5, missing=missing)
    nine = result["ui"]["nine_slice"]
    assert set(nine) == set(FRAMES + BAND) - gone
    # the manifest names only frames the atlas holds, and the written manifest is the one returned
    assert {name for page in result["textures"] for name in page["frames"]} == {f"ui/{name}" for name in nine}
    assert json.loads((out / "game-pack.json").read_text(encoding="utf-8"))["ui"]["nine_slice"] == nine


@pytest.mark.parametrize("size", [TALL])
@pytest.mark.parametrize("scale", [0.75, 0.5, 0.37, 0.25])
def test_packed_kit_keeps_ornament_fringes_out_of_the_stretched_middle(tmp_path, scale, size):
    image, _ = synthetic_sheet(size=size, noise=False, haze=False)
    sheet = tmp_path / "uikit.png"
    image.save(sheet)
    assert ui_kit.main(["--sheet", str(sheet), "--out-dir", str(tmp_path / "ui")]) == 0
    request = tmp_path / "game.json"
    request.write_text(json.dumps({"version": 2, "ui": {"kit": "ui/ui-kit.json", "scale": scale}}), encoding="utf-8")
    out = tmp_path / "pack"
    result = game_pack.pack(request, out)
    frames = {name: (page, record) for page in result["textures"] for name, record in page["frames"].items()}
    for name, entry in result["ui"]["nine_slice"].items():
        page, record = frames[entry["frame"]]
        box = record["frame"]
        with Image.open(out / page["image"]) as atlas:
            stored = np.asarray(atlas.convert("RGBA").crop(
                (box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"]))).astype(float)
        stored[..., :3] *= stored[..., 3:] / 255
        insets, (w, h) = entry["insets"], (entry["size"]["w"], entry["size"]["h"])
        if entry["stretch"] in ("xy", "x"):
            band = stored[:, insets["left"]:w - insets["right"]]
            # every stretched column looks like the band's own median column, within 8 of 255
            assert np.abs(band - np.median(band, axis=1)[:, None]).max() <= 8, (name, scale, "x")
        if entry["stretch"] == "xy":
            band = stored[insets["top"]:h - insets["bottom"]]
            assert np.abs(band - np.median(band, axis=0)[None]).max() <= 8, (name, scale, "y")


def test_padding_follows_a_smooth_gradient_face_to_its_border():
    # a button face that darkens smoothly from top to bottom (70 units over its height), inside a sharp
    # 4 px silver border: the content area is the whole face, not a band around its centre
    w, h = 160, 64
    pixels = np.zeros((h, w, 4), dtype=np.uint8)
    pixels[:, :] = BORDER
    face = np.linspace(110, 40, h - 8).round().astype(np.uint8)
    pixels[4:h - 4, 4:w - 4, 0] = 20
    pixels[4:h - 4, 4:w - 4, 1] = face[:, None] // 2
    pixels[4:h - 4, 4:w - 4, 2] = face[:, None]
    pixels[4:h - 4, 4:w - 4, 3] = 255
    padding, notes = ui_kit.measure_padding(Image.fromarray(pixels))
    assert padding == {"left": 6, "right": 6, "top": 6, "bottom": 6}
    assert notes == []


# A square sheet, split and packed by `ui-kit` and `game-pack` before the 2:3 sheet existed (base bfcab36):
# the guide, the report (sheet path aside) and every element's pixels, and the pack's manifest bytes and page
# pixels at two scales. Encoded PNG bytes depend on the zlib build, so images are compared as pixels.
SQUARE_FROZEN = {
    "guide_text": "6dccc85d1b063e3d7714435ca629ad0ade3b42b720e01ae2b63bd74723383b43",
    "guide_image": "66fe2622be0544604774c577bf1eb0619abd48f430a0eafe983a4f9132acaf91",
    "report": "56d2b589e613f436fd18c295d74e6031505ffb5114397384929cca9f1e959888",
    "elements": {
        "button.png": "080fcf70864cb593dc41bbce0ca25f53aa98dc0046b376f2244970e130ff3d97",
        "button_pressed.png": "cd789c638d312ad99406db8886c892d3caeb9a2754b31a9a4eb2af7a03f735a2",
        "card.png": "79e53083c231e2cffabad9f96204d907c9fc7e9e0e32ba631aa19a6c14e34b67",
        "hp_bar_frame.png": "d79732398bf77c5cc378a050d4340e7824f8353539a03cc6d5116fb914267f51",
        "panel.png": "e1cb7ffd6c22205d8de77417ceb8d4193a76cb30e06ac2b7186cd61a2e45048d",
        "timer_badge.png": "18e772b599a5c07004eab4c06cf43650b8617690ee151e265d0de9b32d4e9588",
        "xp_bar_frame.png": "9209c935bc75c4dfb465c9060b7f3b8783186f2f69a0ebb85b7ecd161ef9bc00",
    },
    "pack": {
        0.5: ("1c1e4f1f8a844c8ab9eafd2ebbe0adea3a62b79d960d1518f2c55b6f62576a86",
              ["8c107995d96e71ba7693c30a763b8b47df5c497a5041506ec5152fb465e9e640"]),
        0.37: ("698822abe6d887c04cc54e3ed40526366da298f164bda81c77980574c2c94445",
               ["a819f45d7b812e5c420a3f9908dd7bb48e80ba8bac94354859ffd2af09bb40d2"]),
    },
}


def pixel_digest(path):
    with Image.open(path) as image:
        return hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest()


def test_a_square_sheet_is_split_and_packed_as_before_the_tall_sheet(tmp_path):
    ui_kit.draw_guide(tmp_path / "guide.png", (1024, 1024))
    assert hashlib.sha256(ui_kit.guide_text().encode()).hexdigest() == SQUARE_FROZEN["guide_text"]
    assert pixel_digest(tmp_path / "guide.png") == SQUARE_FROZEN["guide_image"]
    image, _ = synthetic_sheet()
    sheet = tmp_path / "uikit.png"
    image.save(sheet)
    assert ui_kit.main(["--sheet", str(sheet), "--out-dir", str(tmp_path / "ui")]) == 0
    text = (tmp_path / "ui" / "ui-kit.json").read_text(encoding="utf-8").replace(json.dumps(str(sheet.resolve())), '"<sheet>"')
    assert hashlib.sha256(text.encode()).hexdigest() == SQUARE_FROZEN["report"]
    assert {png.name: pixel_digest(png) for png in sorted((tmp_path / "ui").glob("*.png"))} == SQUARE_FROZEN["elements"]
    for scale, (manifest, pages) in SQUARE_FROZEN["pack"].items():
        request = tmp_path / f"game-{scale}.json"
        request.write_text(json.dumps({"version": 2, "ui": {"kit": "ui/ui-kit.json", "scale": scale}}), encoding="utf-8")
        out = tmp_path / f"pack-{scale}"
        result = game_pack.pack(request, out)
        assert hashlib.sha256((out / "game-pack.json").read_bytes()).hexdigest() == manifest, scale
        assert [pixel_digest(out / page["image"]) for page in result["textures"]] == pages, scale
