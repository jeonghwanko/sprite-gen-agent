# SPDX-License-Identifier: Apache-2.0
"""`video-canvas` normalizes an off-key flat background to the exact key, and `video-frames`
tells leftover key background at the edge apart from a subject that left the frame
(plan sprite-gen/chroma-key-relative-threshold, step 3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from sprite_gen._deps import np
from sprite_gen.video import canvas as canvas_mod
from sprite_gen.video import frames as frames_mod

GREEN = (0, 255, 0)
MAGENTA = (255, 0, 255)
DARK_GREEN = (8, 162, 24)  # 96.38 from pure green: the 2026-09-11 cliff colour
DARK_MAGENTA = (170, 8, 180)
GROK_MAGENTA = (216, 46, 147)  # Grok's #FF00FF, 2026-09-11: blue/red = 0.68 — off the interior balance rule
LEAN_GREEN = (0, 200, 90)  # a green screen leaning toward cyan: blue lit over the dark bar, 105 from pure green
IVORY = (248, 247, 242)
SUBJECT = (200, 40, 40)
SIZE = (120, 160)
SUBJECT_BOX = (40, 40, 80, 150)  # x0, y0, x1, y1


def _still(tmp_path: Path, bg: tuple[int, int, int], name: str = "still.png") -> Path:
    im = Image.new("RGB", SIZE, bg)
    x0, y0, x1, y1 = SUBJECT_BOX
    for y in range(y0, y1):
        for x in range(x0, x1):
            im.putpixel((x, y), SUBJECT)
    p = tmp_path / name
    im.save(p)
    return p


def _corners(im: Image.Image) -> list[tuple[int, ...]]:
    w, h = im.size
    return [im.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]


@pytest.mark.parametrize("bg,kind,key", [(DARK_GREEN, "green", GREEN), (DARK_MAGENTA, "magenta", MAGENTA), (GROK_MAGENTA, "magenta", MAGENTA),
                                         (LEAN_GREEN, "green", GREEN)])
def test_dark_key_still_is_repainted_to_the_exact_key_and_not_refused(tmp_path: Path, bg, kind, key) -> None:
    still = Image.open(_still(tmp_path, bg))
    out, rep = canvas_mod.pad_canvas(still, canvas_mod.profile_for("jump"))  # tall: real padding above
    assert _corners(out) == [key] * 4 and rep["key_rgb"] == list(key) and rep["key"] == kind
    assert rep["corner_rgb"] == list(bg) and rep["key_painted"] == list(bg)
    ox, oy = rep["offset"]
    data = np.array(out)
    inner = data[oy:oy + SIZE[1], ox:ox + SIZE[0]]
    x0, y0, x1, y1 = SUBJECT_BOX
    subject = np.zeros(inner.shape[:2], dtype=bool)
    subject[y0:y1, x0:x1] = True
    assert (inner[subject] == SUBJECT).all(), "subject pixels must stay byte-identical"
    assert (inner[~subject] == key).all(), "every background pixel of the still becomes the exact key"
    assert rep["normalized_px"] == int((~subject).sum())
    # the padding outside the still is the exact key too
    outside = np.ones(data.shape[:2], dtype=bool)
    outside[oy:oy + SIZE[1], ox:ox + SIZE[0]] = False
    assert (data[outside] == key).all()


@pytest.mark.parametrize("bg,kind,key", [(GROK_MAGENTA, "magenta", MAGENTA), (LEAN_GREEN, "green", GREEN)])
def test_explicit_key_on_painted_key_corners_is_accepted_not_refused(tmp_path: Path, bg, kind, key) -> None:
    """A flat border in the key's hue is the key, whatever balance or lean the model painted it with."""
    assert canvas_mod.resolve_key(bg, kind) == kind
    assert canvas_mod.resolve_key(bg, "auto") == kind
    out, rep = canvas_mod.pad_canvas(Image.open(_still(tmp_path, bg)), canvas_mod.profile_for("walk"), key=kind)
    assert rep["key_painted"] == list(bg) and _corners(out) == [key] * 4


def test_exact_key_still_is_unchanged_and_reports_zero_painted_drift(tmp_path: Path) -> None:
    still = Image.open(_still(tmp_path, GREEN))
    out, rep = canvas_mod.pad_canvas(still, canvas_mod.profile_for("walk"))
    assert rep["key"] == "green" and rep["key_painted"] == list(GREEN) and _corners(out) == [GREEN] * 4
    ox, oy = rep["offset"]
    assert (np.array(out)[oy:oy + SIZE[1], ox:ox + SIZE[0]] == np.array(still)).all()


def test_non_key_base_keeps_the_corner_colour_padding(tmp_path: Path) -> None:
    still = Image.open(_still(tmp_path, IVORY))
    out, rep = canvas_mod.pad_canvas(still, canvas_mod.profile_for("jump"))
    assert rep["key"] is None and rep["key_rgb"] == list(IVORY) and rep["normalized_px"] == 0
    assert _corners(out) == [IVORY] * 4


def test_explicit_key_that_the_corners_are_not_is_refused(tmp_path: Path) -> None:
    still = Image.open(_still(tmp_path, IVORY))
    with pytest.raises(SystemExit, match="not a green key family colour"):
        canvas_mod.pad_canvas(still, canvas_mod.profile_for("walk"), key="green")
    with pytest.raises(SystemExit, match="unknown --key"):
        canvas_mod.pad_canvas(still, canvas_mod.profile_for("walk"), key="blue")
    # --key white opts out of normalization even on a green base
    out, rep = canvas_mod.pad_canvas(Image.open(_still(tmp_path, DARK_GREEN, "g.png")), canvas_mod.profile_for("walk"), key="white")
    assert rep["key"] is None and _corners(out) == [DARK_GREEN] * 4


def test_run_canvas_accepts_key_and_records_it(tmp_path: Path) -> None:
    rep = canvas_mod.run_canvas(_still(tmp_path, DARK_GREEN), tmp_path / "o" / "c.png", state="walk", shape=None, facing="right", headroom=None, lead=None, report_path=None, key="green")
    assert rep["key"] == "green" and rep["key_painted"] == list(DARK_GREEN)
    assert _corners(Image.open(tmp_path / "o" / "c.png")) == [GREEN] * 4


def test_normalized_dark_still_keys_like_the_pure_key_still(tmp_path: Path) -> None:
    """The canvas output of a dark-green still and of the same still on pure green must key to the
    same alpha in `video-frames` (the double safety: normalization + relative key both close it)."""
    dark, _ = canvas_mod.pad_canvas(Image.open(_still(tmp_path, DARK_GREEN, "d.png")), canvas_mod.profile_for("walk"))
    pure, _ = canvas_mod.pad_canvas(Image.open(_still(tmp_path, GREEN, "p.png")), canvas_mod.profile_for("walk"))
    (tmp_path / "raw").mkdir()
    dark.save(tmp_path / "raw" / "frame-0000.png")
    pure.save(tmp_path / "raw" / "frame-0001.png")
    rep = frames_mod.key_frames(sorted((tmp_path / "raw").glob("*.png")), tmp_path / "keyed", key="green")
    assert rep["edge_contacts"] == [] and rep["alpha_zero_pct_min"] == rep["alpha_zero_pct_max"]
    a = np.array(Image.open(tmp_path / "keyed" / "frame-0000.png").convert("RGBA"))[..., 3]
    b = np.array(Image.open(tmp_path / "keyed" / "frame-0001.png").convert("RGBA"))[..., 3]
    assert (a == b).all()


# --- frames: residual background vs subject at the edge ---------------------------


def _frames(tmp_path: Path, *, residual: bool, touch_top: bool, touch_from: int = 0) -> list[Path]:
    """Three frames of a body; with `touch_top` it reaches the top edge from frame `touch_from` on. A body on the
    edge in the first frame is a drawing cut by its own frame (`video-frames` reads that side as its border)."""
    d = tmp_path / "raw"
    d.mkdir(exist_ok=True)
    files = []
    for k in range(3):
        im = Image.new("RGB", (80, 100), GREEN)
        top = 0 if touch_top and k >= touch_from else 20
        for y in range(top, 90):
            for x in range(25 + k, 55 + k):
                im.putpixel((x, y), (180, 60, 30))
        if residual:  # a dark-green patch of leftover background hugging the left edge
            for y in range(40, 60):
                for x in range(0, 10):
                    im.putpixel((x, y), DARK_GREEN)
        p = d / f"frame-{k:04d}.png"
        im.save(p)
        files.append(p)
    return files


def test_leftover_key_background_at_the_edge_is_reported_as_residual_not_framing(tmp_path: Path) -> None:
    files = _frames(tmp_path, residual=True, touch_top=False)
    with pytest.raises(SystemExit, match="leftover chroma background") as info:
        frames_mod.key_frames(files, tmp_path / "keyed", key="green")
    assert "framed too tight" not in str(info.value) and "video-canvas" in str(info.value)
    rep = frames_mod.key_frames(files, tmp_path / "keyed2", key="green", check_edges=False)
    assert rep["frames"] == 3


def test_subject_contact_is_still_framing_and_carries_the_split(tmp_path: Path) -> None:
    files = _frames(tmp_path, residual=False, touch_top=True, touch_from=1)
    with pytest.raises(SystemExit, match="framed too tight") as info:
        frames_mod.key_frames(files, tmp_path / "keyed", key="green")
    assert "leftover" not in str(info.value)
    raw = Image.open(files[1])
    keyed = Image.open(tmp_path / "keyed.refused" / files[1].name)  # a refused run keeps its frames under this name
    split = frames_mod.classify_edge_contact(raw, keyed, GREEN)
    assert split["subject"] > 0 and split["residual"] == 0


def test_mixed_contact_names_both(tmp_path: Path) -> None:
    files = _frames(tmp_path, residual=True, touch_top=True, touch_from=1)
    with pytest.raises(SystemExit, match="framed too tight") as info:
        frames_mod.key_frames(files, tmp_path / "keyed", key="green")
    assert "also carry leftover chroma background" in str(info.value)
    raw = Image.open(files[1])
    keyed = Image.open(tmp_path / "keyed.refused" / files[1].name)
    split = frames_mod.classify_edge_contact(raw, keyed, GREEN)
    assert split["subject"] > 0 and split["residual"] > 0


def _transparent_still(tmp_path: Path, subject: tuple[int, int, int], name: str = "clear.png") -> Path:
    im = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    im.paste(Image.new("RGBA", (SUBJECT_BOX[2] - SUBJECT_BOX[0], SUBJECT_BOX[3] - SUBJECT_BOX[1]), (*subject, 255)), SUBJECT_BOX[:2])
    path = tmp_path / name
    im.save(path)
    return path


@pytest.mark.parametrize(("subject", "expected"), [((40, 200, 60), "magenta"), ((230, 40, 200), "green"), (SUBJECT, None)])
def test_a_transparent_still_is_laid_on_the_key_farther_from_its_subject(tmp_path, subject, expected):
    """Issue #1: transparency flattened to black, which no key reads, so the clip was bought and then refused."""
    rep = canvas_mod.run_canvas(_transparent_still(tmp_path, subject), tmp_path / "c.png", state="walk", shape=None,
                                facing="right", headroom=None, lead=None, report_path=None)
    t = rep["transparent"]
    assert t["transparent_px"] > 0 and t["chosen"] == "farther from the subject"
    kind = expected or max(t["subject_distance"], key=t["subject_distance"].get)
    assert t["flattened_on"] == kind and rep["key"] == kind
    assert tuple(rep["corner_rgb"]) == tuple(rep["key_rgb"]) == (MAGENTA if kind == "magenta" else GREEN)
    out = np.asarray(Image.open(tmp_path / "c.png").convert("RGB"))
    assert (out[0, 0] != 0).any(), "the padding is black: the still was not laid on its key"


@pytest.mark.parametrize(("key", "fill"), [("green", GREEN), ("magenta", MAGENTA), ("white", (255, 255, 255))])
def test_a_transparent_still_is_laid_on_the_key_asked_for(tmp_path, key, fill):
    rep = canvas_mod.run_canvas(_transparent_still(tmp_path, SUBJECT), tmp_path / "c.png", state="walk", shape=None,
                                facing="right", headroom=None, lead=None, report_path=None, key=key)
    assert rep["transparent"]["flattened_on"] == key and rep["transparent"]["chosen"] == "requested"
    assert tuple(rep["corner_rgb"]) == fill and rep["key"] == (None if key == "white" else key)


def test_a_transparent_still_comes_back_out_of_its_key_whole(tmp_path):
    """What `video-frames` will erase is the background the still was laid on, and not the subject."""
    canvas_mod.run_canvas(_transparent_still(tmp_path, SUBJECT), tmp_path / "c.png", state="walk", shape=None,
                          facing="right", headroom=None, lead=None, report_path=None, key="green")
    keyed, _ = canvas_mod.extract_route(Image.open(tmp_path / "c.png").convert("RGBA"), "green")
    alpha = np.asarray(keyed)[..., 3]
    assert alpha[0, 0] == 0 and int((alpha == 255).sum()) == (SUBJECT_BOX[2] - SUBJECT_BOX[0]) * (SUBJECT_BOX[3] - SUBJECT_BOX[1])


def test_an_opaque_still_is_not_touched_and_a_still_clear_everywhere_is_refused(tmp_path):
    rep = canvas_mod.run_canvas(_still(tmp_path, GREEN), tmp_path / "c.png", state="walk", shape=None,
                                facing="right", headroom=None, lead=None, report_path=None)
    assert rep["transparent"] is None
    empty = tmp_path / "empty.png"
    Image.new("RGBA", SIZE, (0, 0, 0, 0)).save(empty)
    with pytest.raises(SystemExit, match="transparent everywhere"):
        canvas_mod.run_canvas(empty, tmp_path / "e.png", state="walk", shape=None, facing="right", headroom=None, lead=None,
                              report_path=None)


def test_video_set_hands_its_key_to_the_canvas(tmp_path, monkeypatch):
    """`--key` reached only `video-frames`; the canvas resolved `auto` whatever the set was asked for."""
    from sprite_gen.video import batch as batch_mod

    seen: dict[str, object] = {}

    def canvas_stop(still, out, **kw):
        seen["key"] = kw.get("key")
        raise SystemExit("stop after the canvas")

    monkeypatch.setattr(batch_mod.facing_mod.vision, "grok_inspect", lambda *a, **kw: ("right", {}))
    monkeypatch.setattr(batch_mod.canvas_mod, "run_canvas", canvas_stop)
    base = tmp_path / "base.png"
    Image.new("RGB", (32, 32), MAGENTA).save(base)
    batch_mod.run_set(bases={"side": base}, states=["idle"], root=tmp_path / "set", character=None, duration=None,
                      resolution="480p", key="magenta", concurrency=1, force=False, gap=0.0,
                      video_runner=lambda *a, **kw: pytest.fail("a clip was asked for"))
    assert seen["key"] == "magenta"
