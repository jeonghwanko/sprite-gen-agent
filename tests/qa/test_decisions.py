# SPDX-License-Identifier: Apache-2.0
"""Offline API contract tests. Synthetic art/answers do not measure visual accuracy."""
from __future__ import annotations

import copy
import io
import json
import urllib.error
from pathlib import Path

import pytest
from PIL import Image

from sprite_gen.qa import decisions as d
from sprite_gen.qa.decision_eval import evaluate
from sprite_gen.qa.decision_inputs import from_pose_run


@pytest.fixture
def evidence(tmp_path):
    art = tmp_path / "art"
    art.mkdir()
    for i in range(3):
        Image.new("RGBA", (24, 24), (20 + 30*i, 60, 80, 255)).save(art / f"{i}.png")
    spec = {"kind": "sprite-gen-decision-spec", "version": 1, "stage": "sequence", "intent": "Synthetic walk only.",
            "loop": True, "references": [{"file": "0.png", "role": "identity"}],
            "frames": [{"id": "a", "file": "1.png", "duration": .1}, {"id": "b", "file": "2.png", "duration": .2}]}
    return spec, art, tmp_path / "review"


def response_for(request, choice="clear", confidence=.96):
    return {"model": d.MODEL, "usage": {"input_tokens": 123, "output_tokens": 0},
            "answers": [{"name": q["name"], "type": "choice", "choice": choice, "confidence": confidence,
                         "probabilities": [{"value": c, "probability": .96 if c == choice else .02} for c in d.CHOICES]}
                        for q in request["questions"]]}


def live(evidence, monkeypatch, transport=None):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-real-credential")
    spec, base, out = evidence
    return d.review(spec, base, out, execute=True, confirm_api_billing=True,
                    transport=transport or (lambda request, *_: (response_for(request), "test-request")))


def test_prepare_is_offline_and_binds_pixels_order_durations(evidence, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    spec, base, out = evidence
    report = d.review(spec, base, out, transport=lambda *_: pytest.fail("offline must not call"))
    assert report["status"] == "prepared" and not report["visual_approval"]
    request, binding = d.prepare_request(spec, base)
    assert "sequence.loop_seam" in [q["name"] for q in request["questions"]]
    assert [im["duration"] for im in binding["images"] if "duration" in im] == [.1, .2]
    changed = copy.deepcopy(spec)
    changed["frames"][0]["duration"] = .15
    assert d.prepare_request(changed, base)[1] != binding
    assert "data:image" not in (out / "request-summary.json").read_text()
    assert not (base / "review.json").exists()


@pytest.mark.parametrize("change", [
    lambda s: s["frames"][0].update(duration=float("nan")),
    lambda s: s["frames"][0].update(duration=0),
    lambda s: s["frames"][1].update(id="a"),
    lambda s: s.update(loop="true"),
    lambda s: s["references"][0].update(role="unknown"),
    lambda s: s.update(stage="unknown"),
])
def test_invalid_spec_never_contacts_provider(evidence, change):
    spec, base, _ = evidence
    change(spec)
    with pytest.raises(ValueError):
        d.prepare_request(spec, base)


def test_reject_animated_image_and_oversize_request(evidence, monkeypatch):
    spec, base, _ = evidence
    Image.new("RGB", (24, 24), "red").save(base / "loop.webp", save_all=True,
        append_images=[Image.new("RGB", (24, 24), "blue")], duration=[100, 200], loop=0)
    spec["frames"][0]["file"] = "loop.webp"
    with pytest.raises(ValueError, match="individual frames"):
        d.prepare_request(spec, base)
    spec["frames"][0]["file"] = "1.png"
    monkeypatch.setattr(d, "MAX_REQUEST_BYTES", 10)
    with pytest.raises(ValueError, match="cap"):
        d.prepare_request(spec, base)


def test_execute_requires_explicit_billing_and_key(evidence, monkeypatch):
    spec, base, out = evidence
    with pytest.raises(ValueError, match="confirm-api-billing"):
        d.review(spec, base, out, execute=True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        d.review(spec, base, out, execute=True, confirm_api_billing=True)
    assert d.read_json(out / "decision-review.json")["status"] == "prepared"


def test_live_report_reused_without_second_charge(evidence, monkeypatch):
    calls = []
    def transport(request, *_):
        calls.append(request)
        return response_for(request), "req-test"
    first = live(evidence, monkeypatch, transport)
    second = live(evidence, monkeypatch, transport)
    assert first == second and len(calls) == 1
    assert first["status"] == "completed" and first["visual_approval"] is False
    assert all(c["status"] == "clear" for c in first["checks"].values())
    assert first["performance_claim"] == "unmeasured"


def test_stale_report_pixels_or_timing_cannot_be_reused(evidence, monkeypatch):
    live(evidence, monkeypatch)
    spec, base, out = evidence
    spec["frames"][0]["duration"] = .3
    with pytest.raises(ValueError, match="different evidence"):
        d.review(spec, base, out)
    spec["frames"][0]["duration"] = .1
    Image.new("RGBA", (24, 24), "red").save(base / "1.png")
    with pytest.raises(ValueError, match="different evidence"):
        d.review(spec, base, out)


@pytest.mark.parametrize("mutation", [
    lambda r: r["answers"].pop(),
    lambda r: r["answers"][0].update(name="invented"),
    lambda r: r["answers"][1].update(name=r["answers"][0]["name"]),
    lambda r: r.update(model="another-model"),
    lambda r: r["answers"][0].update(confidence=float("nan")),
    lambda r: r["answers"][0].update(probabilities=[]),
    lambda r: r["answers"][0].update(type="score"),
])
def test_malformed_answers_are_unverified_and_not_retried(evidence, monkeypatch, mutation):
    calls = []
    def transport(request, *_):
        calls.append(1)
        response = response_for(request)
        mutation(response)
        return response, "req-test"
    report = live(evidence, monkeypatch, transport)
    assert report["status"] == "unverified"
    assert report["request_id"] == "req-test"
    assert all(c["status"] == "unverified" for c in report["checks"].values())
    with pytest.raises(ValueError, match="unknown outcome"):
        live(evidence, monkeypatch, transport)
    assert len(calls) == 1


def test_refusal_and_low_confidence_abstain(evidence):
    spec, base, _ = evidence
    request, _ = d.prepare_request(spec, base)
    response = response_for(request, confidence=.6)
    response["answers"][0] = {"name": request["questions"][0]["name"], "type": "refusal"}
    checks = d.parse_answers(response, request, .8)
    assert all(c["status"] == "unverified" for c in checks.values())


def test_timeout_is_recorded_without_retry_or_secret(evidence, monkeypatch):
    def timeout(*_):
        raise d.RequestFailure("transport_outcome_unknown")
    report = live(evidence, monkeypatch, timeout)
    assert report["error_code"] == "transport_outcome_unknown"
    assert "test-key" not in (evidence[2] / "decision-review.json").read_text()
    with pytest.raises(ValueError, match="unknown outcome"):
        live(evidence, monkeypatch)


def test_interruption_is_durable_and_cannot_repeat(evidence, monkeypatch):
    def interrupt(*_):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        live(evidence, monkeypatch, interrupt)
    assert d.read_json(evidence[2] / "decision-review.json")["status"] == "request_started"
    with pytest.raises(ValueError, match="unknown outcome"):
        live(evidence, monkeypatch)


def test_mid_request_input_change_invalidates_result(evidence, monkeypatch):
    def transport(request, *_):
        Image.new("RGBA", (24, 24), "red").save(evidence[1] / "1.png")
        return response_for(request), "test"
    assert live(evidence, monkeypatch, transport)["status"] == "unverified"


def test_output_cannot_be_an_input_directory(evidence):
    spec, base, _ = evidence
    with pytest.raises(ValueError, match="separate"):
        d.review(spec, base, base)


def test_http_endpoint_timeout_and_no_redirect(monkeypatch):
    seen = []
    class Reply(io.BytesIO):
        headers = {"x-request-id": "req-offline"}
    class Opener:
        def open(self, request, timeout):
            seen.append((request, timeout))
            return Reply(b'{"answers":[],"model":"gpt-6-luna"}')
    monkeypatch.setattr(d.urllib.request, "build_opener", lambda handler: Opener())
    result, rid = d.post_request({"model": d.MODEL}, "fake", 17)
    assert rid == "req-offline" and result["model"] == d.MODEL
    assert seen[0][0].full_url == d.ENDPOINT and seen[0][1] == 17
    assert d._NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid") is None


def test_provider_error_body_is_not_exposed(monkeypatch):
    class Opener:
        def open(self, *_args, **_kwargs):
            raise urllib.error.HTTPError(d.ENDPOINT, 429, "secret-body", {"x-request-id": "req-http-error"}, io.BytesIO(b"secret-body"))
    monkeypatch.setattr(d.urllib.request, "build_opener", lambda handler: Opener())
    with pytest.raises(d.RequestFailure, match="^http_429$") as error:
        d.post_request({}, "fake", 1)
    assert error.value.request_id == "req-http-error"


def test_invalid_json_keeps_request_id_in_unverified_report(evidence, monkeypatch):
    class Reply(io.BytesIO):
        headers = {"x-request-id": "req-invalid-json"}
    class Opener:
        def open(self, *_args, **_kwargs):
            return Reply(b"invalid-secret-response-body")
    monkeypatch.setattr(d.urllib.request, "build_opener", lambda handler: Opener())
    report = live(evidence, monkeypatch, d.post_request)
    assert report["status"] == "unverified"
    assert report["error_code"] == "invalid_response_json"
    assert report["request_id"] == "req-invalid-json"
    assert "invalid-secret-response-body" not in (evidence[2] / "decision-review.json").read_text()
    with pytest.raises(ValueError, match="unknown outcome"):
        live(evidence, monkeypatch, d.post_request)


def dataset_for(report, out):
    names = list(report["checks"])
    return {"kind": "sprite-gen-decision-eval", "version": 1, "cases": [
        {"id": "case1", "character": "synthetic-only", "split": "test", "labeler": "test author, not a visual benchmark",
         "report": str(out / "decision-review.json"), "request_sha256": report["binding"]["request_sha256"],
         "expected": {names[0]: "defect", names[1]: "clear", names[2]: "unverified"},
         "baseline": {names[0]: "defect", names[1]: "defect", names[2]: "unverified"}, "baseline_review_seconds": 3}]}


def test_evaluation_counts_missed_defects_and_unsupported_passes(evidence, monkeypatch):
    report = live(evidence, monkeypatch)
    result = evaluate(dataset_for(report, evidence[2]), evidence[1])
    measured = result["results"]["test"]
    assert measured["decisions"]["defect_miss_rate"] == 1
    assert measured["baseline"]["defect_miss_rate"] == 0
    assert measured["decisions"]["unsupported_clear_count"] == 1
    assert result["review_timing"]["baseline_seconds"] == 3
    assert result["end_to_end_improvement"].startswith("unmeasured")


def test_evaluation_rejects_leakage_and_unbound_labels(evidence, monkeypatch):
    report = live(evidence, monkeypatch)
    dataset = dataset_for(report, evidence[2])
    second = copy.deepcopy(dataset["cases"][0])
    second.update(id="case2", split="calibration")
    dataset["cases"].append(second)
    with pytest.raises(ValueError, match="both calibration and test"):
        evaluate(dataset, evidence[1])
    dataset["cases"].pop()
    dataset["cases"][0]["request_sha256"] = "incorrect"
    with pytest.raises(ValueError, match="not bound"):
        evaluate(dataset, evidence[1])


def test_evaluation_preserves_abstentions(evidence, monkeypatch):
    report = live(evidence, monkeypatch, lambda req, *_: (response_for(req, "unverified"), "test"))
    result = evaluate(dataset_for(report, evidence[2]), evidence[1])
    metric = result["results"]["test"]["decisions"]
    assert metric["abstention_rate"] == 1 and metric["defect_detection_rate"] == 0


def test_pose_run_adapter_reads_exact_art_and_does_not_approve(tmp_path):
    from gen.test_gen_motion import setup_run, fake_runner
    from sprite_gen.gen import gen_motion
    _, root, manifest = setup_run(tmp_path)
    before = (root / "input-review.json").read_bytes()
    spec, base = from_pose_run(root, "input", "contact-a")
    d.prepare_request(spec, base)
    assert spec["frames"][0]["file"].endswith("guide.png")
    result = gen_motion.generate(root, manifest, "contact-a", runner=fake_runner())
    candidate = Path(result["output"])
    original_review = Path(result["review"]).read_bytes()
    spec, base = from_pose_run(root, "pose", "contact-a", candidate)
    assert spec["frames"][0]["file"] == str(candidate.resolve())
    assert {r["role"] for r in spec["references"]} == {"identity", "guide"}
    assert [r["role"] for r in spec["references"]] == ["guide", "identity"]
    assert spec["references"][0]["label"] == "Saved generation Reference1."
    d.review(spec, base, tmp_path / "advisory")
    assert Path(result["review"]).read_bytes() == original_review
    assert (root / "input-review.json").read_bytes() == before
    with pytest.raises(ValueError, match="requires --candidate"):
        from_pose_run(root, "pose", "contact-a")
    Image.new("RGBA", (128, 128), "red").save(candidate)
    with pytest.raises(ValueError, match="pixels differ"):
        from_pose_run(root, "pose", "contact-a", candidate)


def test_sequence_adapter_uses_selected_hashes_and_timing(tmp_path):
    from gen.test_gen_motion import setup_run, generate_accept
    from sprite_gen.gen import gen_motion
    _, root, manifest = setup_run(tmp_path)
    for fid in ("contact-a", "contact-b"):
        generate_accept(root, manifest, fid)
    gen_motion.export_asset(root, manifest)
    spec, base = from_pose_run(root, "sequence")
    assert [f["id"] for f in spec["frames"]] == ["contact-a", "contact-b"]
    assert [f["duration"] for f in spec["frames"]] == [.1, .12]
    d.prepare_request(spec, base)
    path = root / "sequence-review.json"
    review = d.read_json(path)
    review["binding"]["frames"][0]["duration"] = .5
    d.write_json(path, review)
    with pytest.raises(ValueError, match="duration changed"):
        from_pose_run(root, "sequence")


def test_cli_prepares_and_default_never_calls_api(evidence):
    from sprite_gen.cli import main
    spec, base, out = evidence
    path = base / "spec.json"
    d.write_json(path, spec)
    assert main(["review-decisions", "--spec", str(path), "--out-dir", str(out)]) == 0
    assert d.read_json(out / "decision-review.json")["status"] == "prepared"


def test_eval_cli_output_never_overwrites(evidence, monkeypatch):
    from sprite_gen.cli import main
    report = live(evidence, monkeypatch)
    dataset_path = evidence[2].parent / "dataset.json"
    d.write_json(dataset_path, dataset_for(report, evidence[2]))
    result_path = evidence[2].parent / "evaluation.json"
    command = ["evaluate-decisions", "--dataset", str(dataset_path), "--output", str(result_path)]
    assert main(command) == 0
    before = result_path.read_bytes()
    with pytest.raises(SystemExit):
        main(command)
    assert result_path.read_bytes() == before


def test_input_gait_guides_keep_xray_and_visible_roles(tmp_path):
    from gen.test_gen_motion import setup_run
    _, root, manifest = setup_run(tmp_path)
    frame = manifest["plan"]["frames"][0]
    frame["gait"] = {"synthetic": True}
    for name in ("gait-visible-depth.png", "gait-amodal-xray.png"):
        path = root / "poses" / "pose-contact-a" / name
        Image.new("RGB", (24, 24), "white").save(path)
        manifest["files"][path.relative_to(root).as_posix()] = d.digest(path.read_bytes())
    from sprite_gen.gen.gen_motion import _json
    binding = {"contract": manifest["contract"], "plan": manifest["plan"], "identity_sha256": manifest["identity_sha256"]}
    manifest["fingerprint"] = d.digest(_json(binding).encode())
    d.write_json(root / "pose-run.json", manifest)
    spec, base = from_pose_run(root, "input", "contact-a")
    assert [r["role"] for r in spec["references"]] == ["identity", "guide", "guide"]
    assert "Reference3" in spec["references"][1]["label"]
    assert "Reference4" in spec["references"][2]["label"]
    d.prepare_request(spec, base)


def test_a_dated_snapshot_of_the_model_asked_for_is_the_same_model():
    from sprite_gen.qa.decisions import same_model
    assert same_model("gpt-6-luna", "gpt-6-luna") and same_model("gpt-6-luna-2026-10-01", "gpt-6-luna")
    assert not same_model("gpt-6-lunar", "gpt-6-luna") and not same_model("gpt-6-sol", "gpt-6-luna") and not same_model(None, "gpt-6-luna")


def test_an_unverified_review_exits_1_not_the_nothing_sent_2(tmp_path, monkeypatch):
    from sprite_gen.qa import decisions
    monkeypatch.setattr(decisions, "review", lambda *a, **kw: {"status": "unverified"})
    spec = tmp_path / "spec.json"
    spec.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(decisions, "read_json", lambda path: {})
    assert decisions.run(out_dir=tmp_path / "out", spec=spec) == 1
    monkeypatch.setattr(decisions, "review", lambda *a, **kw: {"status": "completed"})
    assert decisions.run(out_dir=tmp_path / "out", spec=spec) == 0
