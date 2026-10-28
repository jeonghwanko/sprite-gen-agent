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
        {"id": "idle", "source": "idle.strip.json", "display": {"height": CANVAS[1]}},
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


def weak(frame, size, score, sha=None):
    return {"frame": frame, "how": "measured", "size": size, "score": score, "sha256": sha or frame}


def test_weak_matches_count_only_when_three_distinct_drawings_of_the_state_agree():
    frames = [weak("run/0", 1.10, 0.76), weak("run/1", 1.11, 0.72), weak("run/2", 1.09, 0.70), weak("run/3", 0.93, 0.72)]
    scale_check._agree(frames, min_score=0.8)
    assert [f["how"] for f in frames] == ["agree", "agree", "agree", "measured"]  # the one off the shared size stays unsure
    held = [weak("hit/0", 1.10, 0.76, "a"), weak("hit/1", 1.10, 0.76, "a"), weak("hit/2", 1.10, 0.76, "a")]
    scale_check._agree(held, min_score=0.8)
    assert all(f["how"] == "measured" for f in held)  # one drawing held three times is one measurement
    noise = [weak(f"x/{i}", 1.1, 0.6) for i in range(4)]
    scale_check._agree(noise, min_score=0.8)
    assert all(f["how"] == "measured" for f in noise)  # below the floor nothing agrees


def test_frames_without_a_head_do_not_agree_their_way_through(tmp_path):
    proc, report, _walk = measure(tmp_path, hide=(0, 1, 2, 3))
    assert proc.returncode == 1
    assert report["unresolved"] == ["-/0", "-/1", "-/2", "-/3"]


def test_two_sources_with_one_state_name_keep_their_own_measurements():
    from sprite_gen.spec.assets import FrameSequence
    noise = Image.fromarray(np.random.default_rng(3).integers(0, 255, (CANVAS[1], CANVAS[0], 4), dtype=np.uint8), "RGBA")
    reference = FrameSequence((fighter(),), (0.1,))
    first = FrameSequence((fighter(scale=1.1), noise), (0.1, 0.1), metadata={"state": "combo", "source": "a"})
    second = FrameSequence((fighter(scale=1.1), fighter(scale=0.9, tilt=10)), (0.1, 0.1), metadata={"state": "combo", "source": "b"})
    report, _matches, _template = scale_check.check(reference, 0, [{"combo": first}, {"combo": second}])
    a, b = report["assets"]
    assert a["frames"][1]["how"] == "check" and a["frames"][1]["correction"] is None  # not the other source's head
    assert b["frames"][1]["how"] == "head" and b["frames"][1]["size"] == pytest.approx(0.9, abs=0.03)
    assert report["unresolved"] == ["combo/1"]


def test_a_request_and_its_report_still_pack_after_the_folder_moves(tmp_path):
    import shutil
    first = tmp_path / "first"
    first.mkdir()
    _proc, _report, _walk = measure(first)
    packed(first, {"report": "scale.json"})
    moved = tmp_path / "moved"
    shutil.copytree(first, moved, ignore=shutil.ignore_patterns("out"))
    result, _cells = packed(moved, {"report": "scale.json"})
    assert len(result["animations"]["hero"]["frame_scale"]) == len(POSES)


def body_strip(path, frames, body_h):
    strip(path, frames)
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "body_h": body_h}), encoding="utf-8")
    return path


def test_a_correction_counts_the_display_scale_the_asset_already_has(tmp_path):
    # Each strip carries its own body_h, so display.body_height already sizes each one; the head-measured correction is
    # only what is left after that, never applied on top of it a second time.
    top, bottom = figure_rows(fighter())
    body_strip(tmp_path / "idle.strip.json", [fighter()], bottom - top)
    drawn = [fighter(scale=1.15), fighter(scale=1.15, reach=0.6)]
    big_top, big_bottom = figure_rows(drawn[0])
    body_strip(tmp_path / "run.strip.json", drawn, big_bottom - big_top)
    out = tmp_path / "scale.json"
    assert scale_check.main(["--reference", str(tmp_path / "idle.strip.json"), "--asset", str(tmp_path / "run.strip.json"),
                             "--out", str(out)]) == 0
    request = tmp_path / "game.json"
    request.write_text(json.dumps({"version": 1, "assets": [
        {"id": "idle", "source": "idle.strip.json", "display": {"body_height": 200}},
        {"id": "run", "source": "run.strip.json", "display": {"body_height": 200}, "scale_check": {"report": "scale.json"}}]}),
        encoding="utf-8")
    result = game_pack.pack(request, tmp_path / "out")
    page = Image.open(tmp_path / "out" / result["textures"][0]["image"])
    def rows(key):
        r = result["textures"][0]["frames"][key]["frame"]
        return figure_rows(page.crop((r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"])))
    idle_top, idle_bottom = rows(result["animations"]["idle"]["frames"][0])
    run_top, run_bottom = rows(result["animations"]["run"]["frames"][0])
    assert run_bottom - run_top == pytest.approx(idle_bottom - idle_top, rel=0.03)
    assert result["animations"]["run"]["frame_scale"][0] == pytest.approx(1.0, abs=0.03)


def test_game_pack_needs_the_reference_frame_in_the_pack(tmp_path):
    _proc, _report, _walk = measure(tmp_path)
    request = tmp_path / "game.json"
    request.write_text(json.dumps({"version": 1, "assets": [{"id": "hero", "source": "walk.strip.json",
        "display": {"height": CANVAS[1]}, "scale_check": {"report": "scale.json"}}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="reference frame of scale.json is in no asset"):
        game_pack.pack(request, tmp_path / "out")


def test_a_raised_weapon_is_not_the_head_when_the_head_box_is_given(tmp_path):
    from sprite_gen.spec.assets import FrameSequence
    reference = fighter()
    staff = reference.copy()
    staff.paste(Image.new("RGBA", (8, 60), (120, 80, 40, 255)), (150, 0))
    sequence = FrameSequence((fighter(scale=1.12),), (0.1,), metadata={"state": "x", "source": "x"})
    box = scale_check._head_box(reference)
    given = (box[0], box[1], box[2] - box[0], box[3] - box[1])
    report, _m, _t = scale_check.check(FrameSequence((staff,), (0.1,)), 0, [{"x": sequence}], head_box=given)
    assert report["assets"][0]["frames"][0]["size"] == pytest.approx(1.12, abs=0.025)
    assert report["reference"]["head_box_from"] == "given"


def test_a_head_too_small_to_measure_is_refused():
    from sprite_gen.spec.assets import FrameSequence
    tiny = fighter().resize((60, 75), Image.Resampling.BOX)
    with pytest.raises(ValueError, match="under 24 px"):
        scale_check.check(FrameSequence((tiny,), (0.1,)), 0, [{"x": FrameSequence((tiny,), (0.1,))}])


def test_a_size_on_the_edge_of_the_range_is_not_a_measurement():
    from sprite_gen.spec.assets import FrameSequence
    reference = FrameSequence((fighter(),), (0.1,))
    sequence = FrameSequence((fighter(scale=1.15), fighter(scale=1.05)), (0.1, 0.1), metadata={"state": "x", "source": "x"})
    report, _m, _t = scale_check.check(reference, 0, [{"x": sequence}], size_range=(0.8, 1.1))
    first, second = report["assets"][0]["frames"]
    assert first["how"] == "check" and first["edge"]  # 1.15 lies beyond 1.1: clamping it would be a wrong correction
    assert second["how"] == "head" and second["size"] == pytest.approx(1.05, abs=0.025)


def test_a_frame_never_settles_itself_with_its_own_head(tmp_path):
    from sprite_gen.spec.assets import FrameSequence
    reference = FrameSequence((fighter(),), (0.1,))
    sequence = FrameSequence((fighter(scale=1.12), fighter(scale=0.9)), (0.1, 0.1), metadata={"state": "x", "source": "x"})
    report, _m, _t = scale_check.check(reference, 0, [{"x": sequence}], min_score=0.999)
    assert all(f["how"] == "check" for f in report["assets"][0]["frames"])
