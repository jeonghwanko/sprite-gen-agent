"""`video-frames` keys a clip's frames with their matte and decontamination preparation computed ahead in
worker processes (`KEY_WORKERS`); the keyed frames and the report are the ones one process makes."""
from __future__ import annotations

import multiprocessing
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageFilter

from sprite_gen.frames.cutout import prepare_cutout
from sprite_gen.video import frames as frames_mod

W, H = 160, 200


def _raw(tmp_path: Path, n: int = 7) -> list[Path]:
    """A body that walks across a green key, its edge blurred into the key as a decoded frame's is, with noise."""
    d = tmp_path / "raw"
    d.mkdir()
    rng = np.random.default_rng(7)
    files = []
    for k in range(n):
        image = Image.new("RGB", (W, H), (12, 236, 30))
        image.paste((190, 70, 40), (40 + 4 * k, 60, 100 + 4 * k, 200))
        image.paste((240, 210, 120), (55 + 4 * k, 30, 85 + 4 * k, 64))
        image.paste((30, 160, 60), (60 + 4 * k, 120, 66 + 4 * k, 140))  # a small key-coloured part
        image = image.filter(ImageFilter.GaussianBlur(1.2))
        noisy = np.asarray(image).astype(np.int16) + rng.integers(-3, 4, (H, W, 3))
        files.append(d / f"frame-{k:04d}.png")
        Image.fromarray(np.clip(noisy, 0, 255).astype(np.uint8)).save(files[-1])
    return files


def _keyed(tmp_path: Path, files: list[Path], name: str, monkeypatch, workers: int, **kw) -> tuple[dict, list[bytes]]:
    monkeypatch.setattr(frames_mod, "KEY_WORKERS", workers)
    report = frames_mod.key_frames(files, tmp_path / name, key="green", **kw)
    return report, [(tmp_path / name / f.name).read_bytes() for f in files]


@pytest.mark.parametrize("decontam", ["auto", "off"])
def test_frames_keyed_in_workers_are_the_ones_one_process_keys(tmp_path: Path, monkeypatch, decontam: str) -> None:
    files = _raw(tmp_path)
    alone, alone_bytes = _keyed(tmp_path, files, "alone", monkeypatch, 1, decontam=decontam)
    ahead, ahead_bytes = _keyed(tmp_path, files, "ahead", monkeypatch, 3, decontam=decontam)
    assert ahead_bytes == alone_bytes
    assert {k: v for k, v in ahead.items() if k != "keyed_dir"} == {k: v for k, v in alone.items() if k != "keyed_dir"}
    if decontam == "auto":
        assert alone["decontam"]["applied_frames"] == len(files)  # the clip's history ran through every frame


def test_prepared_frames_come_back_in_clip_order(tmp_path: Path, monkeypatch) -> None:
    files = _raw(tmp_path, n=9)
    monkeypatch.setattr(frames_mod, "KEY_WORKERS", 2)
    ahead = list(frames_mod._prepared(files, key="green", decontam="off"))
    assert len(ahead) == len(files)
    for src, prepared in zip(files, ahead):
        here = prepare_cutout(src, key="green", decontam="off")
        assert np.array_equal(prepared["matte"]["data"], here["matte"]["data"])
        assert "source_rgb" not in prepared["matte"]  # the main process reads the frame again rather than receive it


def test_a_clip_refused_partway_leaves_no_worker_running(tmp_path: Path, monkeypatch) -> None:
    """The pool keying ahead is shut down when a frame is refused, on the run's own thread, not when the abandoned
    generator is collected: left to collection, the shutdown ran on whatever thread collected it and could hang."""
    files = _raw(tmp_path, n=9)
    Image.new("RGB", (W, H), (190, 70, 40)).save(files[2])  # no key anywhere: refused, with frames still ahead
    monkeypatch.setattr(frames_mod, "KEY_WORKERS", 3)
    with pytest.raises(SystemExit, match="frame-0002.png keyed to 0% transparency") as refused:
        frames_mod.key_frames(files, tmp_path / "keyed", key="green", decontam="off")
    assert refused.value.__traceback__ is not None  # the run's frames, and anything they hold, are still alive here
    assert multiprocessing.active_children() == []


def test_a_refusal_leaves_every_frame_handed_to_the_saver_on_disk_and_no_later_one(tmp_path: Path) -> None:
    images = [Image.new("RGBA", (300, 300), (k, 0, 0, 255)) for k in range(6)]
    with pytest.raises(RuntimeError, match="refused"):
        with frames_mod._Saver() as save:
            for k, image in enumerate(images):
                save(image, tmp_path / f"{k}.png")
                assert save.last is image
                if k == 3:
                    raise RuntimeError("refused")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["0.png", "1.png", "2.png", "3.png"]
    assert all(Image.open(tmp_path / f"{k}.png").tobytes() == images[k].tobytes() for k in range(4))


def test_a_frame_saved_again_is_the_last_one_on_disk(tmp_path: Path) -> None:
    with frames_mod._Saver() as save:
        for k in range(5):
            save(Image.new("RGBA", (64, 64), (k, 0, 0, 255)), tmp_path / "frame.png")
    assert Image.open(tmp_path / "frame.png").getpixel((0, 0)) == (4, 0, 0, 255)
