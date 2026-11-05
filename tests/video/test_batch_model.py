# SPDX-License-Identifier: Apache-2.0
"""`video-set --model`: the clip model reaches each clip's `sprite-gen video`, the prompt is written for it (a Lite
walk's calm), a cached clip is reused only when it was asked for with the same model, and each item's `clip` carries
the model its report says it asked for and the one xAI named. Offline: the clip child is a stand-in."""
import json
from pathlib import Path

import pytest

from sprite_gen.gen import video as video_mod
from sprite_gen.video import batch

FIXTURE = Path(__file__).parents[1] / "fixtures" / "facing" / "left.png"
LITE = "grok-imagine-video-1.5-lite"


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(batch.facing_mod.vision, "grok_inspect", lambda *a, **kw: ("left", {}))
    monkeypatch.setattr(batch.frames_mod, "run_frames", lambda clip, out, **kw: {
        "fps": 24, "frames": 10, "alpha_zero_pct_min": 50, "alpha_zero_pct_max": 60,
        "keyed_dir": str(out)})
    monkeypatch.setattr(batch.loop_mod, "run_loop", lambda *a, **kw: {
        "cycle": {"length": 10, "period_global": 10, "ratio": 0.2},
        "resampled_seam_ratio": 0.2, "n_out": 10, "gif": {"file": "loop.gif"},
        "webp": {"file": "loop.webp"}, "strip": {"path": "strip.png"}})
    monkeypatch.setattr(batch, "_staggered_start", lambda gap: None)


class _Clip:
    """A `sprite-gen video` stand-in: writes the clip and a report the way the child does, `model_reported` as given."""

    def __init__(self, reported=None, *, legacy: bool = False):
        self.reported = reported
        self.legacy = legacy  # a report from before requested_model and model_reported were written
        self.calls: list[dict] = []

    def __call__(self, image, prompt, out, report, **kw):
        self.calls.append({"prompt": prompt, **kw})
        out.write_bytes(b"test-video")
        asked = kw.get("model", video_mod.DEFAULT_MODEL)
        said = {"prompt": prompt, "auth_source": "grok-login", "model": self.reported or asked}
        if not self.legacy:
            said |= {"requested_model": asked, "model_reported": self.reported}
        report.write_text(json.dumps(said), encoding="utf-8")
        return 0


def _item(tmp_path, clip, **options):
    return batch.run_item(item="side-attack", direction="side", state="attack", base=FIXTURE, root=tmp_path,
                          character=None, duration=3, resolution="720p", key="green", gap=0, video_runner=clip,
                          **{"force": False, **options})


def test_the_model_reaches_the_clip_and_its_report_reaches_the_item_and_the_set(tmp_path, offline):
    clip = _Clip(reported="grok-imagine-video-1.5-lite-20261101")
    result = batch.run_set(bases={"side": FIXTURE}, states=["walk"], root=tmp_path, character=None, duration=3,
                           resolution="720p", key="green", concurrency=1, force=False, gap=0, video_runner=clip,
                           align_cycles="off", model=LITE)
    assert [call["model"] for call in clip.calls] == [LITE]
    # The Lite walk's calm is in the prompt the clip was sent, as `video-prompt --model` writes it.
    assert batch.LITE_WALK_TEXT in clip.calls[0]["prompt"]
    assert clip.calls[0]["prompt"] == batch.build_prompt("side", "walk", None, facing="right", model=LITE)
    item = result["items"][0]
    assert (item["clip"]["requested_model"], item["clip"]["model_reported"]) == (LITE, "grok-imagine-video-1.5-lite-20261101")
    assert item["clip"]["auth_source"] == "grok-login" and result["model"] == LITE


def test_without_a_model_the_clip_names_none_and_xais_silence_stays_none(tmp_path, offline):
    clip = _Clip(reported=None)
    result = batch.run_set(bases={"side": FIXTURE}, states=["walk"], root=tmp_path, character=None, duration=3,
                           resolution="720p", key="green", concurrency=1, force=False, gap=0, video_runner=clip,
                           align_cycles="off")
    assert "model" not in clip.calls[0] and "model" not in result
    assert clip.calls[0]["prompt"] == batch.build_prompt("side", "walk", None, facing="right")
    assert batch.LITE_WALK_TEXT not in clip.calls[0]["prompt"]
    # The report's `model` is the one asked for when xAI named none; the item never reads it as xAI's.
    clip_said = result["items"][0]["clip"]
    assert (clip_said["requested_model"], clip_said["model_reported"]) == (video_mod.DEFAULT_MODEL, None)


def test_the_clip_child_is_sent_the_model_only_when_one_is_named(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(batch, "run_logged", lambda cmd, log: sent.append(cmd) or 0)
    args = (tmp_path / "canvas.png", "walks", tmp_path / "clip.mp4", tmp_path / "clip.report.json")
    batch.run_video_cli(*args, duration=3, resolution="720p", log=tmp_path / "clip.log")
    batch.run_video_cli(*args, duration=3, resolution="720p", log=tmp_path / "clip.log", model=LITE)
    assert "--model" not in sent[0]
    assert sent[1][sent[1].index("--model") + 1] == LITE


@pytest.mark.parametrize("model", [None, video_mod.DEFAULT_MODEL], ids=["no-model", "the-default-named"])
def test_a_clip_cached_before_the_model_was_recorded_is_reused_for_the_default(tmp_path, offline, model):
    first = _Clip(legacy=True)
    assert _item(tmp_path, first)["ok"]
    again = _Clip()
    result = _item(tmp_path, again, **({"model": model} if model else {}))
    assert result["ok"] and again.calls == []
    # What the cached report recorded: nothing, so None, not the default the reuse check took it for.
    assert result["clip"] == {"reused": True, "requested_model": None, "model_reported": None}


def test_a_clip_cached_for_one_model_is_not_reused_for_another(tmp_path, offline):
    assert _item(tmp_path, _Clip(legacy=True))["ok"]
    again = _Clip()
    refused = _item(tmp_path, again, model=LITE)
    # An attack's prompt is the same for both models: the model check is what refuses it, before anything is sent.
    assert not refused["ok"] and again.calls == [] and refused["provider_submitted"] is False
    assert refused["error"] == f"cached clip was asked for with {video_mod.DEFAULT_MODEL}, not {LITE}; use --force to regenerate"
    forced = _item(tmp_path, again, model=LITE, force=True)
    assert forced["ok"] and [call["model"] for call in again.calls] == [LITE]
    # Filmed for Lite, it is reused for Lite and refused for the default.
    reused = _item(tmp_path, _Clip(), model=LITE)
    assert reused["ok"] and reused["clip"] == {"reused": True, "requested_model": LITE, "model_reported": None}
    assert "use --force" in _item(tmp_path, _Clip())["error"]


def test_the_cli_threads_the_model_into_the_set(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(batch, "run_set", lambda **kw: seen.append(kw) or {"failed": []})
    base = ["--base", f"side={FIXTURE}", "--states", "attack", "--out-dir", str(tmp_path)]
    assert batch.main(base) == 0 and batch.main([*base, "--model", LITE]) == 0
    assert [kw["model"] for kw in seen] == [None, LITE]
