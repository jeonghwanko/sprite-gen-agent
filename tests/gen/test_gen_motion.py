# SPDX-License-Identifier: Apache-2.0
"""Offline plumbing/geometry tests. Synthetic fixtures are not gait approval."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from sprite_gen.gen import gen_motion as motion
from sprite_gen.spec import pose_plan
from sprite_gen.spec.assets import load_asset


def plan_fixture(motion_name="walk"):
    joints = {"head": [64, 25], "neck": [64, 42], "pelvis": [64, 70],
              "A_hip": [58, 72], "A_knee": [50, 89], "A_ankle": [44, 106], "A_heel": [40, 112], "A_toe": [53, 112],
              "B_hip": [68, 72], "B_knee": [78, 87], "B_ankle": [85, 96], "B_heel": [82, 101], "B_toe": [94, 101]}
    first = {"id": "contact-a", "duration": 0.1, "phase": "A stance, B swing", "joints": joints,
             "contacts": {"A": ["heel", "toe"], "B": []}, "occlusion_front": "A", "anchors": []}
    last = copy.deepcopy(first)
    last.update(id="contact-b", duration=0.12, phase="B stance, A swing", contacts={"A": [], "B": ["heel", "toe"]}, anchors=[first["id"]])
    for part in ("knee", "ankle", "heel", "toe"):
        last["joints"][f"A_{part}"], last["joints"][f"B_{part}"] = joints[f"B_{part}"], joints[f"A_{part}"]
    frames = [first, last]
    if motion_name == "run":
        flight = copy.deepcopy(first)
        flight.update(id="flight", phase="both feet airborne", contacts={"A": [], "B": []}, anchors=[first["id"]])
        flight["joints"] = {name: [x, y-12] for name, (x, y) in joints.items()}
        frames.insert(1, flight)
    return {"kind": "sprite-gen-pose-plan", "version": 1, "identity": "original.png", "description": "Synthetic test biped only.",
            "leg_labels": {"A": "physical leg near the camera", "B": "physical leg away from camera"},
            "canvas": [128, 128], "ground_y": 112, "motion": motion_name, "loop": True, "coordinate_source": "authored", "frames": frames}


def setup_run(tmp_path, motion_name="walk"):
    plan = plan_fixture(motion_name)
    Image.new("RGB", (80, 90), "tan").save(tmp_path / "original.png")
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    manifest = motion.prepare(path, run)
    review(run / 'input-review.json')
    return path, run, manifest


def fake_runner(seen=None, *, rc=0, size=(128, 128), duplicate=False):
    def runner(prompt, out, refs, report, *, model, log):
        if seen is not None:
            seen.append({"prompt": prompt.read_text(encoding="utf-8"), "refs": refs, "model": model})
        image = Image.new("RGBA", size)
        # Deliberately synthetic: tests approval plumbing, not anatomical success.
        y = 20 if duplicate or "contact-a" in str(out) else (8 if "pose-flight" in str(out) else 35)
        ImageDraw.Draw(image).rectangle((30, y, 55, y+45), fill="tan")
        image.save(out)
        report.write_text(json.dumps({"provider": "codex"}), encoding="utf-8")
        log.write_text("offline fake only", encoding="utf-8")
        return rc
    return runner


def review(path, status="pass", check=None):
    value = json.loads(path.read_text(encoding="utf-8"))
    value["reviewer"] = "synthetic offline test"
    for name, entry in value["checks"].items():
        entry.update(status=status if check is None or check == name else "unverified", observation="Synthetic fixture; not a real visual observation.")
    path.write_text(json.dumps(value), encoding="utf-8")


def generate_accept(run, manifest, fid, runner=None):
    result = motion.generate(run, manifest, fid, runner=runner or fake_runner())
    review(Path(result["review"]))
    return result


def test_guide_uses_actual_coordinates_fixed_ids_and_ground():
    plan = pose_plan.validate(plan_fixture())
    image = pose_plan.render(plan, plan["frames"][0])
    assert image.size == (128, 128)
    assert image.getpixel((50, 89)) == (223, 103, 64)  # A always orange
    assert image.getpixel((78, 87)) == (40, 121, 206)  # B always blue
    assert image.getpixel((120, 112)) == (96, 104, 111)


@pytest.mark.parametrize("mutate,match", [
    (lambda p: p["frames"][0]["joints"]["A_knee"].__setitem__(0, float("nan")), "finite"),
    (lambda p: p["frames"][0]["joints"]["A_toe"].__setitem__(1, 100), "off ground"),
    (lambda p: p["frames"][0]["joints"]["B_toe"].__setitem__(1, 112), "clear ground"),
    (lambda p: p["frames"][0]["joints"]["A_hip"].__setitem__(0, 200), "outside"),
    (lambda p: p["frames"][-1].__setitem__("anchors", []), "opening pose"),
    (lambda p: p["frames"][0].__setitem__("anchors", ["contact-b"]), "cycle"),
    (lambda p: p["frames"][0]["contacts"].__setitem__("A", [["toe"]]), "contacts"),
    (lambda p: p.__setitem__("motion", "run"), "flight"),
])
def test_rejects_contradictory_plan_before_generation(mutate, match):
    plan = plan_fixture()
    mutate(plan)
    with pytest.raises(ValueError, match=match):
        pose_plan.validate(plan)


def test_real_reference_list_and_numeric_prompt(tmp_path):
    _, run, manifest = setup_run(tmp_path)
    seen = []
    first = generate_accept(run, manifest, "contact-a", fake_runner(seen))
    second = motion.generate(run, manifest, "contact-b", runner=fake_runner(seen))
    assert second["status"] == "awaiting_review"
    assert len(seen[0]["refs"]) == 2 and len(seen[1]["refs"]) == 3
    assert seen[1]["refs"][0] == run / "poses/pose-contact-b/guide.png"
    assert seen[1]["refs"][1] == run / "identity.png"
    assert seen[1]["refs"][2] == Path(first["output"])
    assert 'A_knee: (60.9%, 68.0%)' in seen[1]["prompt"]
    assert 'APPEARANCE ONLY' in seen[1]["prompt"] and 'AIRBORNE' in seen[1]["prompt"]
    assert "Reference 3: reviewed anchor pose contact-a" in seen[1]["prompt"]
    assert "not a sheet or a collage" in seen[1]["prompt"]


def test_inbetween_uses_both_reviewed_bracketing_poses_and_keeps_flight_y(tmp_path):
    path, run, _ = setup_run(tmp_path, "run")
    plan = plan_fixture("run")
    plan["frames"][1]["anchors"] = ["contact-a", "contact-b"]
    path.write_text(json.dumps(plan), encoding="utf-8")
    run = tmp_path / "bracketed"
    run.mkdir()
    manifest = motion.prepare(path, run)
    review(run / 'input-review.json')
    opening = generate_accept(run, manifest, "contact-a")
    closing = generate_accept(run, manifest, "contact-b")
    seen = []
    generate_accept(run, manifest, "flight", fake_runner(seen))
    assert seen[0]["refs"][2:] == [Path(opening["output"]), Path(closing["output"])]
    result = motion.export_asset(run, manifest)
    review(Path(result["review"]))
    result = motion.export_asset(run, manifest)
    asset = load_asset(Path(result["asset"]))
    # Playback order differs from generation order; airborne root Y is retained.
    assert [image.getbbox()[1] for image in asset.frames] == [20, 8, 35]
    assert asset.durations == (0.1, 0.1, 0.12)


def test_foreign_provider_and_empty_alpha_cannot_become_reviewed_poses(tmp_path):
    _, run, manifest = setup_run(tmp_path)
    def foreign(prompt, out, refs, report, **kwargs):
        fake_runner()(prompt, out, refs, report, **kwargs)
        report.write_text(json.dumps({"provider": "grok"}), encoding="utf-8")
        return 0
    with pytest.raises(ValueError, match="requested codex provider"):
        motion.generate(run, manifest, "contact-a", runner=foreign)
    def invisible(prompt, out, refs, report, **kwargs):
        fake_runner()(prompt, out, refs, report, **kwargs)
        Image.new("RGBA", (128, 128)).save(out)
        return 0
    with pytest.raises(ValueError, match="structural rejection"):
        motion.generate(run, manifest, "contact-a", retry=True, runner=invisible)


@pytest.mark.parametrize("status", ["unverified", "fail"])
def test_unreviewed_or_failed_anchor_cannot_propagate(tmp_path, status):
    _, run, manifest = setup_run(tmp_path)
    result = motion.generate(run, manifest, "contact-a", runner=fake_runner())
    if status == "fail":
        review(Path(result["review"]), "fail", "physical_leg_identity")
    seen = []
    with pytest.raises(ValueError, match="actual visual review"):
        motion.generate(run, manifest, "contact-b", runner=fake_runner(seen))
    assert seen == []


def test_retry_preserves_versions_carries_failure_and_stops_at_two(tmp_path):
    _, run, manifest = setup_run(tmp_path)
    first = motion.generate(run, manifest, "contact-a", runner=fake_runner())
    original = Path(first["output"]).read_bytes()
    review(Path(first["review"]), "fail", "hip_attachment")
    seen = []
    second = motion.generate(run, manifest, "contact-a", retry=True, runner=fake_runner(seen))
    assert "Previous candidate rejected" in seen[0]["prompt"] and 'hip_attachment:' in seen[0]["prompt"]
    assert 'input_sha256' not in seen[0]['prompt'] and '"unverified"' not in seen[0]['prompt']
    assert (Path(second['output']).parent / 'retry-feedback.json').exists()
    assert Path(first["output"]).read_bytes() == original
    assert first["output"] != second["output"]
    review(Path(second["review"]), "fail", "hip_attachment")
    with pytest.raises(ValueError, match="two attempts exhausted"):
        motion.generate(run, manifest, "contact-a", retry=True, runner=fake_runner(seen))
    assert len(seen) == 1


def test_unknown_outcome_preserved_and_reconciled_without_duplicate_call(tmp_path):
    _, run, manifest = setup_run(tmp_path)
    seen = []
    with pytest.raises(ValueError, match="outcome not confirmed"):
        motion.generate(run, manifest, "contact-a", runner=fake_runner(seen, rc=1))
    with pytest.raises(ValueError, match="outcome unknown"):
        motion.generate(run, manifest, "contact-a", retry=True, runner=fake_runner(seen))
    result = motion.generate(run, manifest, "contact-a", reconcile=True, runner=fake_runner(seen))
    assert len(seen) == 1 and Path(result["output"]).exists()
    assert result["status"] == "awaiting_review"


@pytest.mark.parametrize("report", [{"provider": None, "provider_submitted": False}, None], ids=["report-says-not-sent", "exit-3-no-report"])
def test_a_gen_that_sent_nothing_is_not_started_and_the_same_attempt_runs_again(tmp_path, report):
    """gen failing before its request (exit 3 or `provider_submitted: false`) cost nothing: the pose is not stuck behind
    --reconcile, and the next --frame tries again in the same attempt folder, not as one of the two attempts."""
    from sprite_gen.gen import outcome

    _, run, manifest = setup_run(tmp_path)
    def not_sent(prompt, out, refs, report_path, *, model, log):
        if report is not None:
            report_path.write_text(json.dumps(report), encoding="utf-8")
        log.write_text("offline fake: refused before the request", encoding="utf-8")
        return outcome.NOT_STARTED_EXIT
    with pytest.raises(outcome.Stop, match="sent no request") as stopped:
        motion.generate(run, manifest, "contact-a", runner=not_sent)
    assert stopped.value.code == outcome.NOT_STARTED_EXIT
    attempt = run / "poses/pose-contact-a/attempt-01"
    assert json.loads((attempt / "attempt.json").read_text(encoding="utf-8"))["status"] == "not-started"
    for flag in ("retry", "reconcile"):
        with pytest.raises(ValueError, match="sent nothing"):
            motion.generate(run, manifest, "contact-a", runner=fake_runner(), **{flag: True})
    seen = []
    result = motion.generate(run, manifest, "contact-a", runner=fake_runner(seen))
    assert len(seen) == 1 and result["status"] == "awaiting_review"
    assert sorted(p.name for p in (run / "poses/pose-contact-a").glob("attempt-*")) == ["attempt-01"]
    assert json.loads((attempt / "attempt.json").read_text(encoding="utf-8"))["status"] == "generated"


def test_missing_report_is_unknown_not_retry_permission(tmp_path):
    _, run, manifest = setup_run(tmp_path)
    def runner(prompt, out, refs, report, **kwargs):
        Image.new("RGBA", (128, 128)).save(out)
        return 0
    with pytest.raises(ValueError, match="complete output/report"):
        motion.generate(run, manifest, "contact-a", runner=runner)
    assert (run / "poses/pose-contact-a/attempt-01/output.png").exists()
    with pytest.raises(ValueError, match="outcome unknown"):
        motion.generate(run, manifest, "contact-a", retry=True, runner=runner)


@pytest.mark.parametrize("changed", ["output.png", "prompt.txt", "gen-report.json"])
def test_mutated_candidate_invalidates_approval(tmp_path, changed):
    _, run, manifest = setup_run(tmp_path)
    first = generate_accept(run, manifest, "contact-a")
    target = Path(first["output"]).parent / changed
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed after generation"):
        motion.generate(run, manifest, "contact-b", runner=fake_runner())


def test_plan_identity_and_guide_changes_invalidate_run(tmp_path):
    path, run, _ = setup_run(tmp_path)
    Image.new("RGB", (80, 90), "red").save(tmp_path / "original.png")
    with pytest.raises(ValueError, match="plan/identity changed"):
        motion.prepare(path, run)
    Image.new("RGB", (80, 90), "tan").save(tmp_path / "original.png")
    guide = run / "poses/pose-contact-a/guide.png"
    guide.write_bytes(guide.read_bytes() + b" ")
    with pytest.raises(ValueError, match="prepared input changed"):
        motion.prepare(path, run)


def test_canvas_mismatch_is_rejected_without_resizing(tmp_path):
    _, run, manifest = setup_run(tmp_path)
    with pytest.raises(ValueError, match="canvas differs"):
        motion.generate(run, manifest, "contact-a", runner=fake_runner(size=(64, 64)))
    path = run / "poses/pose-contact-a/attempt-01/output.png"
    with Image.open(path) as image:
        assert image.size == (64, 64)


def test_canvas_reconciliation_preserves_original_and_relative_y_without_generation(tmp_path):
    _, run, manifest = setup_run(tmp_path)
    seen = []
    with pytest.raises(ValueError, match="canvas differs"):
        motion.generate(run, manifest, "contact-a", runner=fake_runner(seen, size=(256, 256)))
    folder = run / "poses/pose-contact-a/attempt-01"
    source_bytes = (folder / "output.png").read_bytes()
    result = motion.generate(run, manifest, "contact-a", reconcile=True, normalize_canvas=True, runner=fake_runner(seen))
    assert len(seen) == 1 and result["status"] == "awaiting_review"
    assert (folder / "source-output.png").read_bytes() == source_bytes
    receipt = json.loads((folder / "attempt.json").read_text())
    assert receipt["canvas_transform"]["scale"] == 0.5
    assert receipt["canvas_transform"]["translation"] == [0, 0]
    with Image.open(folder / "output.png") as image:
        assert image.size == (128, 128)
        assert image.getchannel("A").point(lambda a: 255 if a > 127 else 0).getbbox()[1] == 10
    review(Path(result["review"]))
    motion._accepted(run, "contact-a", manifest)
    (folder / "source-output.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="original provider canvas changed"):
        motion._accepted(run, "contact-a", manifest)


def test_canvas_normalization_refuses_aspect_distortion(tmp_path):
    _, run, manifest = setup_run(tmp_path)
    with pytest.raises(ValueError, match="aspect ratio differs"):
        motion.generate(run, manifest, "contact-a", normalize_canvas=True, runner=fake_runner(size=(256, 128)))
    with Image.open(run / "poses/pose-contact-a/attempt-01/output.png") as image:
        assert image.size == (256, 128)


def test_export_requires_sequence_review_and_preserves_pixels_y_and_timing(tmp_path):
    _, run, manifest = setup_run(tmp_path)
    originals = [generate_accept(run, manifest, f["id"])["output"] for f in manifest["plan"]["frames"]]
    before = [Path(p).read_bytes() for p in originals]
    result = motion.export_asset(run, manifest)
    assert result["status"] == "awaiting_sequence_review"
    assert Path(result["preview"]).exists() and not (run / "asset.json").exists()
    review(Path(result["review"]))
    result = motion.export_asset(run, manifest)
    asset = load_asset(Path(result["asset"]))
    assert asset.durations == (0.1, 0.12) and asset.anchor == (64.0, 112.0)
    assert [image.getbbox()[1] for image in asset.frames] == [20, 35]
    assert [Path(p).read_bytes() for p in originals] == before
    # A revoked upstream observation invalidates a previously accepted dependent.
    review(Path(originals[0]).parent / "review.json", "fail", "physical_leg_identity")
    with pytest.raises(ValueError, match="actual visual review"):
        motion.export_asset(run, manifest)


def test_duplicate_output_pixels_cannot_be_exported(tmp_path):
    _, run, manifest = setup_run(tmp_path)
    for frame in manifest["plan"]["frames"]:
        generate_accept(run, manifest, frame["id"], fake_runner(duplicate=True))
    with pytest.raises(ValueError, match="duplicate generated pose"):
        motion.export_asset(run, manifest)


def test_review_needs_observations_and_exact_binding(tmp_path):
    _, run, manifest = setup_run(tmp_path)
    first = generate_accept(run, manifest, "contact-a")
    path = Path(first["review"])
    value = json.loads(path.read_text())
    value["checks"]["contacts"]["observation"] = ""
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="observations required"):
        motion.generate(run, manifest, "contact-b", runner=fake_runner())
    review(path)
    value = json.loads(path.read_text())
    value["binding"]["output_sha256"] = "wrong"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="review is stale"):
        motion.generate(run, manifest, "contact-b", runner=fake_runner())


def test_real_cli_runner_explicit_provider_refs_and_no_bottom_trim(tmp_path, monkeypatch):
    seen = []
    def fake_run(command, log):  # run_logged: the child gets SIGTERM from a stopped gen-motion (base.stop_children)
        seen.extend(command)
        return 0
    from sprite_gen.gen import base
    monkeypatch.setattr(base, "run_logged", fake_run)
    refs = [tmp_path / "original.png", tmp_path / "pose.png", tmp_path / "anchor.png"]
    motion.run_gen(tmp_path / "prompt.txt", tmp_path / "out.png", refs, tmp_path / "report.json", model="chosen-model", log=tmp_path / "gen.log")
    assert seen[seen.index("--provider")+1] == "codex"
    assert [seen[i+1] for i, x in enumerate(seen) if x == "--ref"] == list(map(str, refs))
    assert "--transparent" in seen and "--trim-alpha" not in seen and "--layout-guide" not in seen
    assert '--no-timeout-retry' in seen and '--keep-session' in seen
    assert seen[seen.index('--alpha-mode')+1] == 'native'
    assert seen[seen.index("--model")+1] == "chosen-model"


def test_prepare_cli_does_not_call_provider(tmp_path, monkeypatch):
    from sprite_gen import cli
    path, _, _ = setup_run(tmp_path, "run")
    parser = __import__("argparse").ArgumentParser()
    cli.COMMANDS["gen-motion"][1](parser)
    args = parser.parse_args(["--plan", str(path), "--run-dir", str(tmp_path / "new-run"), "--prepare-only"])
    monkeypatch.setattr(motion, "run_gen", lambda *a, **kw: pytest.fail("prepare must not generate"))
    assert cli.COMMANDS["gen-motion"][2](**vars(args)) == 0


def test_input_review_is_required_and_revocable(tmp_path):
    _, run, manifest = setup_run(tmp_path)
    review(run / 'input-review.json', 'unverified')
    with pytest.raises(ValueError, match='input-review.json'):
        motion.generate(run, manifest, 'contact-a', runner=lambda *a, **kw: pytest.fail('must not generate'))
    review(run / 'input-review.json')
    generate_accept(run, manifest, 'contact-a')
    review(run / 'input-review.json', 'fail', 'physical_leg_mapping')
    with pytest.raises(ValueError, match='input-review.json'):
        motion.generate(run, manifest, 'contact-b', runner=lambda *a, **kw: pytest.fail('must not generate'))


def test_resolution_failure_never_uses_retry_budget_and_stale_review_is_archived(tmp_path):
    _, run, manifest = setup_run(tmp_path)
    seen = []
    with pytest.raises(ValueError, match='canvas differs'):
        motion.generate(run, manifest, 'contact-a', runner=fake_runner(seen, size=(256, 256)))
    folder = run / 'poses/pose-contact-a/attempt-01'
    old = {'binding': {'output_sha256': 'old'}, 'reviewer': 'old review'}
    (folder / 'review.json').write_text(json.dumps(old))
    with pytest.raises(ValueError, match='structural rejection'):
        motion.generate(run, manifest, 'contact-a', retry=True, runner=fake_runner(seen))
    assert len(seen) == 1 and not (folder.parent / 'attempt-02').exists()
    motion.generate(run, manifest, 'contact-a', reconcile=True, normalize_canvas=True)
    assert len(list(folder.glob('review-before-*.json'))) == 1
    new = json.loads((folder / 'review.json').read_text())
    assert all(c['status'] == 'unverified' for c in new['checks'].values())
    assert 'reason' not in json.loads((folder / 'attempt.json').read_text())


def test_timeout_does_not_call_provider_twice(tmp_path, monkeypatch):
    from sprite_gen import gen
    calls = []
    class Backend:
        def generate(self, request, workdir):
            calls.append(request)
            raise gen.GenTimeoutError('unknown remote result')
    monkeypatch.setattr(gen, '_make_provider', lambda *a, **kw: Backend())
    with pytest.raises(gen.GenTimeoutError):
        gen.generate_image('codex', 'test', tmp_path / 'out.png', timeout_retry=False)
    assert len(calls) == 1


def test_tool_trace_preserves_actual_inputs_without_session_context(tmp_path):
    from sprite_gen.gen.codex_provider import _image_tool_trace, _build_prompt
    source = 'const r = await tools.image_gen__imagegen({referenced_image_paths:["guide.png","identity.png"],prompt:"target"});'
    rows = [
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': 'private unrelated context'}},
        {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'name': 'exec', 'call_id': 'image1', 'input': source}},
        {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output', 'output': 'large base64 image'}},
    ]
    path = tmp_path / 'rollout.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in rows))
    trace = _image_tool_trace(path)
    assert trace['status'] == 'captured' and trace['calls'][0]['input'] == source
    assert 'private unrelated' not in json.dumps(trace) and 'large base64' not in json.dumps(trace)
    assert trace['calls'][0]['input_sha256'] == motion.digest(source.encode())
    prompt = _build_prompt('keep coordinates', refs=[tmp_path / 'guide.png', tmp_path / 'identity.png'])
    assert prompt.index('guide.png') < prompt.index('identity.png') and 'referenced_image_paths' in prompt
    assert _image_tool_trace(tmp_path / 'missing.jsonl')['status'] == 'unavailable'
