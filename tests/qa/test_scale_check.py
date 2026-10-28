# SPDX-License-Identifier: Apache-2.0
"""`scale-check` reads a figure's size from its head, not its silhouette, and `game-pack` applies only what was
measured on the very pixels it packs."""
import json
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image

from fighter_figure import CANVAS, GROUND, fighter
from sprite_gen.compose import game_pack
from sprite_gen.qa import scale_check

POSES = [{"scale": 1.12}, {"scale": 0.88}, {"crouch": 0.3}, {"reach": 0.8}, {"tilt": 20}, {"scale": 1.01}]


def strip(path, frames, anchor=(CANVAS[0] / 2, GROUND)):
    width, height = frames[0].size
    sheet = Image.new("RGBA", (width * len(frames), height))
    for index, frame in enumerate(frames):
        sheet.paste(frame, (index * width, 0))
    sheet.save(path.with_name(path.name.replace(".json", ".png")))
    path.write_text(json.dumps({"frames": len(frames), "w": width, "h": height, "delay_ms": 80,
                                "anchor": list(anchor)}), encoding="utf-8")
    return path


def assets(tmp_path, poses=POSES, *, hide=()):
    frames = [fighter(**pose) for pose in poses]
    for index in hide:  # paint over the head: nothing there to measure
        frames[index].paste(Image.new("RGBA", (90, 90), (90, 160, 90, 255)), (75, 40))
    return strip(tmp_path / "idle.strip.json", [fighter()]), strip(tmp_path / "walk.strip.json", frames)


def measure(tmp_path, **options):
    idle, walk = assets(tmp_path, **options)
    out = tmp_path / "scale.json"
    proc = subprocess.run([sys.executable, "-m", "sprite_gen.cli", "scale-check", "--reference", str(idle),
                           "--asset", str(walk), "--out", str(out), "--board", str(tmp_path / "board.png")],
                          capture_output=True, encoding="utf-8")
    return proc, json.loads(out.read_text(encoding="utf-8")), walk


def test_size_comes_from_the_head_so_a_crouch_or_a_reach_is_not_a_size(tmp_path):
    proc, report, _walk = measure(tmp_path)
    assert proc.returncode == 0, proc.stderr
    frames = report["assets"][0]["frames"]
    assert [f["frame"] for f in frames] == [f"-/{i}" for i in range(len(POSES))]
    assert all(f["how"] == "head" and f["score"] >= 0.9 for f in frames)
    assert frames[0]["size"] == pytest.approx(1.12, abs=0.025) and frames[0]["correction"] == pytest.approx(1 / 1.12, abs=0.02)
    assert frames[1]["size"] == pytest.approx(0.88, abs=0.025) and frames[1]["correction"] == pytest.approx(1 / 0.88, abs=0.03)
    # a shorter (crouch), wider (reach) or tilted figure at the same scale, and a slip inside the deadband, stay put
    assert [f["correction"] for f in frames[2:]] == [1.0, 1.0, 1.0, 1.0]
    assert abs(frames[4]["angle"]) == pytest.approx(20, abs=3)
    assert report["ok"] and report["unresolved"] == []
    assert (tmp_path / "board.png").is_file()


def test_a_frame_without_a_recognisable_head_awaits_an_eye_check(tmp_path):
    proc, report, _walk = measure(tmp_path, hide=(1,))
    assert proc.returncode == 1
    assert report["unresolved"] == ["-/1"] and not report["ok"]
    assert report["assets"][0]["frames"][1]["correction"] is None
    assert report["assets"][0]["state_correction"] is None
    assert "-/1" in proc.stderr and json.loads(proc.stdout)["unresolved"] == ["-/1"]


def figure_rows(cell):
    rows = np.flatnonzero((np.asarray(cell.getchannel("A")) >= 64).any(axis=1))
    return int(rows[0]), int(rows[-1]) + 1


def packed(tmp_path, scale_check_entry, *, name="out"):
    request = tmp_path / "game.json"
    request.write_text(json.dumps({"version": 1, "assets": [
        {"id": "hero", "source": "walk.strip.json", "display": {"height": CANVAS[1]}, "scale_check": scale_check_entry}]}),
        encoding="utf-8")
    result = game_pack.pack(request, tmp_path / name)
    page = Image.open(tmp_path / name / result["textures"][0]["image"])
    cells = []
    for key in result["animations"]["hero"]["frames"]:
        r = result["textures"][0]["frames"][key]["frame"]
        cells.append(page.crop((r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"])))
    return result, cells


def test_game_pack_scales_each_frame_about_its_feet_to_the_reference_size(tmp_path):
    _proc, report, _walk = measure(tmp_path)
    result, cells = packed(tmp_path, {"report": "scale.json"})
    factors = result["animations"]["hero"]["frame_scale"]
    assert factors == [f["correction"] for f in report["assets"][0]["frames"]]
    top, bottom = figure_rows(fighter())
    for index in (0, 1):  # drawn 12 % too big and too small: back to the reference figure, feet on the same line
        t, b = figure_rows(cells[index])
        assert b == pytest.approx(bottom, abs=1)
        assert b - t == pytest.approx(bottom - top, rel=0.025)
    assert cells[2].tobytes() == fighter(crouch=0.3).tobytes()  # a correction of 1 packs the frame untouched


def test_game_pack_takes_the_state_median_when_asked(tmp_path):
    _proc, report, _walk = measure(tmp_path)
    result, _cells = packed(tmp_path, {"report": "scale.json", "per": "state"})
    assert set(result["animations"]["hero"]["frame_scale"]) == {report["assets"][0]["state_correction"]}


def test_game_pack_refuses_an_unresolved_frame_until_someone_looks(tmp_path):
    _proc, _report, _walk = measure(tmp_path, hide=(1,))
    with pytest.raises(ValueError, match=r'-/1 awaits an eye check.*fix\["-/1"\]'):
        packed(tmp_path, {"report": "scale.json"})
    result, _cells = packed(tmp_path, {"report": "scale.json", "fix": {"-/1": 1.0}}, name="fixed")
    assert result["animations"]["hero"]["frame_scale"][1] == 1.0
    with pytest.raises(ValueError, match="names no frame of this asset: -/9"):
        packed(tmp_path, {"report": "scale.json", "fix": {"-/1": 1.0, "-/9": 1.1}}, name="typo")


def test_game_pack_refuses_a_report_measured_on_other_pixels(tmp_path):
    _proc, _report, walk = measure(tmp_path)
    strip(walk, [fighter(**pose) for pose in reversed(POSES)])
    with pytest.raises(ValueError, match="measured other pixels"):
        packed(tmp_path, {"report": "scale.json"})


def test_fixes_are_held_to_a_plausible_range(tmp_path):
    _proc, _report, _walk = measure(tmp_path)
    with pytest.raises(ValueError, match="between 0.5 and 2"):
        packed(tmp_path, {"report": "scale.json", "fix": {"-/0": 3}})


def test_ncc_is_opencv_normalized_correlation():
    rng = np.random.default_rng(7)
    picture = rng.uniform(0, 255, (40, 50)).astype(np.float32)
    template = picture[10:22, 5:20].copy()
    scores = scale_check._Image(picture).ncc(template)
    assert scores.shape == (29, 36)
    assert np.unravel_index(np.argmax(scores), scores.shape) == (10, 5)
    assert scores[10, 5] == pytest.approx(1.0, abs=1e-6)
    y, x = 3, 30
    w = picture[y:y + 12, x:x + 15].astype(float) - picture[y:y + 12, x:x + 15].mean()
    t = template - template.mean()
    assert scores[y, x] == pytest.approx((w * t).sum() / np.sqrt((w * w).sum() * (t * t).sum()), abs=1e-6)
