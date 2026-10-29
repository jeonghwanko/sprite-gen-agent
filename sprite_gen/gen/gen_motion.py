# SPDX-License-Identifier: Apache-2.0
"""Experimental one-pose generation with authored guides and reviewed anchors.

This is reference-image conditioning, not a pose-control model. Image generation
and observed motion approval are deliberately separate records.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path

from PIL import Image

from sprite_gen.gen import outcome
from sprite_gen.spec import pose_plan, pose_geometry, gait_inputs
from sprite_gen.spec.runio import (acquire_run_dir_lock, release_run_dir_lock,
                                   atomic_save_image, atomic_write_text, atomic_write_set)

POSE_CHECKS = ("identity", "physical_leg_identity", "hip_attachment", "pose_match", "contacts", "body_continuity")
SEQUENCE_CHECKS = ("alternation", "stance_sliding", "body_continuity", "loop_seam")
MAX_ATTEMPTS = 2
INPUT_CHECKS = ("proportions_and_placement", "physical_leg_mapping", "guide_readability", "contact_plan")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def _write(path: Path, value: object) -> None:
    atomic_write_text(path, _json(value))


def _hash(path: Path) -> str:
    return digest(path.read_bytes())


def prepare(plan_path: Path, run_dir: Path) -> dict:
    plan = pose_plan.load(plan_path)
    gait_inputs.verify(plan_path,plan)
    identity = (plan_path.parent / plan["identity"]).resolve()
    identity_bytes = identity.read_bytes()
    with Image.open(io.BytesIO(identity_bytes)) as im:
        im.verify()
    manifest_path = run_dir / "pose-run.json"
    # Existing runs retain their exact inputs/order. New preparation uses v2
    # input review and target-first references; never silently rewrite old runs.
    # Contract 5: per-frame guide geometry is diagnostic; visual review and
    # sequence contact coverage decide (MOCHA-LOCOMOTION-HANDOFF review).
    contract = _read(manifest_path)["contract"] if manifest_path.exists() else (5 if "gait_reference" in plan else 3 if plan["version"] == 2 else 2)
    if plan["version"] == 2:
        with Image.open(io.BytesIO(identity_bytes)) as im:
            if plan["body_reference"]["sha256"] != digest(identity_bytes) or plan["body_reference"]["canvas"] != list(im.size):
                raise ValueError("body reference calibration does not match original identity bytes/dimensions")
    binding = {"contract": contract, "plan": plan, "identity_sha256": digest(identity_bytes)}
    fingerprint = digest(_json(binding).encode("utf-8"))
    if manifest_path.exists():
        manifest = _read(manifest_path)
        if manifest["fingerprint"] != fingerprint or manifest["plan"] != plan or manifest["identity_sha256"] != binding["identity_sha256"]:
            raise ValueError("plan/identity changed; preserve this run and choose a new run directory")
        for name, expected in manifest["files"].items():
            if _hash(run_dir / name) != expected:
                raise ValueError(f"prepared input changed: {name}; choose a new run directory")
        return manifest
    # Never repair an interrupted preparation by overwriting unknown content.
    if any(p.name != ".sprite-gen.lock" for p in run_dir.iterdir()):
        raise ValueError("run directory is not empty and has no pose manifest; inspect it before continuing")
    files = {}
    ref_name = "identity" + identity.suffix.lower()
    atomic_write_set({run_dir / ref_name: identity_bytes})
    files[ref_name] = digest(identity_bytes)
    for frame in plan["frames"]:
        folder = run_dir / "poses" / ("pose-" + frame["id"])
        folder.mkdir(parents=True)
        atomic_save_image(pose_plan.render(plan, frame), folder / "guide.png")
        atomic_write_text(folder / "prompt.txt", pose_plan.prompt(plan, frame))
        for source,name in zip(frame.get('gait',{}).get('references',[]),gait_inputs.extra_names(frame)):
            data=(plan_path.parent/source['file']).read_bytes()
            if digest(data)!=source['sha256']:raise ValueError('gait reference changed before copy')
            atomic_write_set({folder/name:data})
        for name in ("guide.png", "prompt.txt", *gait_inputs.extra_names(frame)):
            path = folder / name
            files[path.relative_to(run_dir).as_posix()] = _hash(path)
    manifest = {**binding, "fingerprint": fingerprint, "identity_file": ref_name, "files": files}
    _write(manifest_path, manifest)
    _write(run_dir / "input-review.json", _review_template(_input_binding(manifest), INPUT_CHECKS))
    if contract >= 4:
        for frame in plan['frames']:
            _write(_folder(run_dir,frame['id'])/'input-review.json',_review_template(_frame_input_binding(manifest,frame['id']),INPUT_CHECKS))
    return manifest


def _folder(run_dir: Path, fid: str) -> Path:
    return run_dir / "poses" / ("pose-" + fid)


def _attempts(run_dir: Path, fid: str) -> list[Path]:
    return sorted(_folder(run_dir, fid).glob("attempt-*/attempt.json"))


def _review_template(binding: dict, checks: tuple[str, ...]) -> dict:
    return {"binding": binding, "reviewer": "", "checks": {
        check: {"status": "unverified", "observation": ""} for check in checks}}


def _review_status(path: Path, binding: dict, checks: tuple[str, ...]) -> str:
    review = _read(path)
    if review.get("binding") != binding:
        raise ValueError(f"review is stale or refers to different pixels: {path}")
    entries = review.get("checks", {})
    if not isinstance(entries, dict):
        raise ValueError(f"review checks must be an object: {path}")
    statuses = []
    for check in checks:
        entry = entries.get(check, {})
        if not isinstance(entry, dict):
            raise ValueError(f"review check must be an object: {path}, {check}")
        status = entry.get("status", "unverified")
        if status not in ("pass", "fail", "unverified"):
            raise ValueError(f"invalid review status: {path}, {check}")
        if status != "unverified" and (not isinstance(entry.get("observation"), str) or not entry["observation"].strip()):
            raise ValueError(f"observations required for {check}: {path}")
        statuses.append(status)
    if any(s != "unverified" for s in statuses) and (not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()):
        raise ValueError(f"reviewer required: {path}")
    if "fail" in statuses:
        return "fail"
    return "pass" if all(s == "pass" for s in statuses) else "unverified"


def _binding(receipt: dict) -> dict:
    keys = ("input_sha256", "prompt_sha256", "output_sha256", "report_sha256")
    return {key: receipt[key] for key in (*keys, "geometry_observations_sha256", "geometry_report_sha256") if key in receipt}


def _input_binding(manifest: dict) -> dict:
    return {"run_fingerprint": manifest["fingerprint"], "prepared_files": manifest["files"]}


def _frame_input_binding(manifest,fid):
    return {**_input_binding(manifest),'frame':fid,'scope':'one continuous gait target; full sequence separate'}


def _require_input_review(run_dir: Path, manifest: dict, fid=None) -> None:
    if manifest['contract']>=4:
        frames=manifest['plan']['frames']
        if manifest['plan']['gait_reference'].get('bundle_contract')!='pass' or any(f['gait']['design_contract']['status']!='pass' for f in frames):raise ValueError('full sequence gait input contract blocks ALL generation, including roots')
        if fid is not None:
            frame=next(f for f in frames if f['id']==fid)
            if frame['gait']['design_contract']['status']!='pass':raise ValueError('gait target raster/geometry contract blocks generation: '+fid)
            if _review_status(_folder(run_dir,fid)/'input-review.json',_frame_input_binding(manifest,fid),INPUT_CHECKS)!='pass':raise ValueError('actual per-target original/gait input review required: '+fid)
    if manifest["contract"] >= 2 and _review_status(run_dir / "input-review.json", _input_binding(manifest), INPUT_CHECKS) != "pass":
        raise ValueError("input-review.json must pass actual original/guide inspection before generation; authored coordinates alone are not approval")


def _retry_feedback(prior: Path, review_name: str = "review.json") -> tuple[str, dict]:
    review = _read(prior / review_name)
    # Generation gets only actionable failures, never hashes, successful checks,
    # uncertain observations or the entire machine review report.
    failures = {key: value["observation"] for key, value in review["checks"].items()
                if key in POSE_CHECKS and value.get("status") == "fail"}
    if any(len(note) > 600 for note in failures.values()):
        raise ValueError("retry observations must be concise (600 characters per failed check); keep detailed evidence separately")
    record = {"review_sha256": _hash(prior / review_name),
              "rejected_output_sha256": _hash(prior / "output.png"), "failures": failures}
    return "\nPrevious candidate rejected. Correct only these observed defects:\n" + "\n".join(
        f"- {key}: {note}" for key, note in failures.items()) + "\n", record


def _validate_receipt(path: Path, manifest: dict) -> dict:
    receipt = _read(path)
    if receipt["run_fingerprint"] != manifest["fingerprint"]:
        raise ValueError(f"attempt belongs to another plan: {path}")
    if receipt["status"] != "generated":
        raise ValueError(f"candidate is {receipt['status']}; inspect/reconcile before continuing: {path}")
    for name, key in (("output.png", "output_sha256"), ("gen-report.json", "report_sha256"), ("prompt.txt", "prompt_sha256")):
        if _hash(path.parent / name) != receipt[key]:
            raise ValueError(f"candidate changed after generation: {path.parent / name}")
    for ref in receipt["refs"]:
        if _hash(Path(ref["path"])) != ref["sha256"]:
            raise ValueError(f"generation reference changed: {ref['path']}")
    if receipt.get("canvas_transform"):
        transform = receipt["canvas_transform"]
        if _hash(path.parent / transform["source_file"]) != transform["source_sha256"]:
            raise ValueError("original provider canvas changed after normalization")
    return receipt


def _accepted(run_dir: Path, fid: str, manifest: dict, cache: dict | None = None) -> tuple[Path, dict]:
    _require_input_review(run_dir,manifest,fid)
    cache = {} if cache is None else cache
    if fid in cache:
        return cache[fid]
    paths = _attempts(run_dir, fid)
    if not paths:
        raise ValueError(f"anchor {fid} has not been generated/reviewed")
    path = paths[-1]
    receipt = _validate_receipt(path, manifest)
    output, receipt, review_path = _geometry_candidate(path, receipt, manifest)
    if _review_status(review_path, _binding(receipt), POSE_CHECKS) != "pass":
        raise ValueError(f"anchor {fid} needs actual visual review; fail/unverified cannot propagate")
    # Recheck upstream reviews too: revoking one blocks every dependent pose.
    frame = next(f for f in manifest["plan"]["frames"] if f["id"] == fid)
    for anchor in frame["anchors"]:
        anchor_path, _ = _accepted(run_dir, anchor, manifest, cache)
        if str(anchor_path) not in [r["path"] for r in receipt["refs"]]:
            raise ValueError(f"anchor version changed for {fid}; preserve this run and start a revised plan")
    cache[fid] = output, receipt
    return cache[fid]


def _geometry_frame(manifest, fid):
    if manifest["contract"] < 3:
        raise ValueError("measured registration requires a v2 calibrated plan; preserve legacy run and audit it separately")
    frame = next((f for f in manifest["plan"]["frames"] if f["id"] == fid), None)
    if frame is None:
        raise ValueError(f"unknown geometry frame: {fid}")
    return frame


def _audit_geometry(folder, manifest, fid, observations=None):
    frame = _geometry_frame(manifest, fid)
    observations = observations or _read(folder / "geometry-observations.json")
    with Image.open(folder / "output.png") as im:
        result = pose_geometry.audit(im.convert("RGBA"), manifest["plan"], frame, observations,
                                     _hash(folder / "output.png"), manifest["fingerprint"])
    result["observations_sha256"] = digest(_json(observations).encode())
    return result


def _geometry_candidate(path, receipt, manifest):
    folder = path.parent
    if manifest["contract"] < 3:
        return folder / "output.png", receipt, folder / "review.json"
    registered = folder / "registered"
    if manifest["contract"] >= 5 and not registered.exists():
        return folder / "output.png", receipt, folder / "review.json"
    if not registered.exists():
        raw_report = _audit_geometry(folder, manifest, receipt["frame"])
        if raw_report["status"] != "pass":
            raise ValueError(f"geometry gate: {raw_report['status']}; measure/register existing pixels, never bypass with a visual pass")
        bound = {**receipt, "geometry_observations_sha256":_hash(folder/"geometry-observations.json"),
                 "geometry_report_sha256":digest(_json(raw_report).encode())}
        return folder / "output.png", bound, folder / "geometry-review.json"
    reg = _read(registered / "receipt.json")
    if reg["source_binding"] != _binding(receipt) or reg["source_observations_sha256"] != _hash(folder / "geometry-observations.json"):
        raise ValueError("registered source/observations changed; preserve historical correction")
    for name,key in (("output.png","output_sha256"),("geometry-observations.json","geometry_observations_sha256"), ("geometry-report.json","geometry_report_sha256")):
        if _hash(registered/name) != reg[key]:
            raise ValueError("registered pixels or geometry evidence changed")
    if _audit_geometry(registered, manifest, receipt["frame"])["status"] != "pass":
        raise ValueError("registered geometry no longer passes")
    return registered/"output.png", reg, registered/"review.json"


def measure_geometry(run_dir, manifest, fid, *, register=False):
    frame = _geometry_frame(manifest, fid)
    paths = _attempts(run_dir, fid)
    if not paths:
        raise ValueError("no existing candidate to measure")
    path = paths[-1]
    receipt = _validate_receipt(path, manifest)
    folder = path.parent
    obs_path = folder / "geometry-observations.json"
    evidence = _read(obs_path)
    if evidence.get("status") == "unverified":
        return {"status":"awaiting_geometry_observations", "observations":str(obs_path)}
    report = _audit_geometry(folder, manifest, fid)
    _write(folder / "geometry-report.json", report)
    diagnostic = manifest["contract"] >= 5
    if report["status"] == "pass" and not (folder/"registered").exists() and not diagnostic:
        output, bound, review = _geometry_candidate(path, receipt, manifest)
        if not review.exists() or _read(review).get("binding") != _binding(bound):
            if review.exists():
                atomic_write_set({review.with_name(f"geometry-review-before-{_hash(review)[:16]}.json"):review.read_bytes()})
            _write(review, _review_template(_binding(bound), POSE_CHECKS))
    if not register:
        return {"status":report["status"], "report":str(folder/"geometry-report.json"),
                "review":str(folder/"geometry-review.json") if report["status"] == "pass" and not diagnostic else None}
    target = folder / "registered"
    if target.exists():
        output, reg, review = _geometry_candidate(path, receipt, manifest)
        return {"status":"awaiting_review", "output":str(output), "review":str(review)}
    if report["status"] != "registration_available":
        raise ValueError(f"registration refused: {report['status']}; no pixels changed and no generation requested")
    with Image.open(folder/"output.png") as im:
        corrected = pose_geometry.transform(im.convert("RGBA"), report["fit"])
    buffer = io.BytesIO()
    corrected.save(buffer, format="PNG")
    pixels = buffer.getvalue()
    derived = pose_geometry.transformed_evidence(evidence, report["fit"], digest(pixels))
    # Recheck the rasterized result; a predicted contact is not a measured one.
    checked = pose_geometry.audit(corrected, manifest["plan"], frame, derived, digest(pixels), manifest["fingerprint"])
    if checked["status"] != "pass":
        raise ValueError("registration raster check failed; preserve source, no corrected candidate accepted")
    reg = {**receipt, "source_binding":_binding(receipt), "source_observations_sha256":_hash(obs_path),
           "output_sha256":digest(pixels), "geometry_observations_sha256":digest(_json(derived).encode()),
           "geometry_report_sha256":digest(_json(checked).encode()), "body_registration":report["fit"]}
    target.mkdir()
    atomic_write_set({target/"output.png":pixels, target/"geometry-observations.json":_json(derived).encode(),
                      target/"geometry-report.json":_json(checked).encode(), target/"receipt.json":_json(reg).encode(),
                      target/"review.json":_json(_review_template(_binding(reg),POSE_CHECKS)).encode()})
    return {"status":"awaiting_review", "output":str(target/"output.png"), "review":str(target/"review.json"),
            "generation_calls":0, "transform":report["fit"]}


def run_gen(prompt_file: Path, out: Path, refs: list[Path], report: Path, *, model: str | None, log: Path) -> int:
    # Explicit codex: this command never auto-switches provider or API billing.
    cmd = [sys.executable, "-m", "sprite_gen.cli", "gen", "--provider", "codex",
           "--prompt-file", str(prompt_file), "--out", str(out), "--report", str(report),
           "--transparent", "--alpha-mode", "native", "--facing", "preserve", "--facing-fix", "none",
           "--no-timeout-retry", "--keep-session"]
    for ref in refs:
        cmd += ["--ref", str(ref)]
    if model:
        cmd += ["--model", model]
    # run_logged, not subprocess.run (2026-10-29): a stopped gen-motion sends its `gen` SIGTERM (`base.stop_children`), so
    # gen writes its report saying whether its request left; subprocess.run killed it outright and left no report.
    from sprite_gen.gen import base
    return base.run_logged(cmd, log)


def _normalize_canvas(path: Path, receipt: dict, target_size: list[int]) -> None:
    """Opt-in whole-canvas resolution conversion, never a silhouette fit or shift."""
    output = path.parent / "output.png"
    source = path.parent / "source-output.png"
    transform = receipt.get("canvas_transform")
    if transform:
        data = source.read_bytes() if source.exists() else output.read_bytes()
        if digest(data) != transform["source_sha256"]:
            raise ValueError("original provider canvas changed during normalization")
    else:
        if source.exists():
            raise ValueError("untracked source-output.png exists; preserve and inspect it")
        data = output.read_bytes()
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        w, h = im.size
        tw, th = target_size
        if im.format != "PNG" or w * th != h * tw:
            raise ValueError("canvas aspect ratio differs; whole-canvas normalization cannot crop or distort")
        if "A" not in im.getbands() or im.getchannel("A").getextrema()[0] == 255 or im.getchannel("A").getextrema()[1] == 0:
            raise ValueError("candidate must contain both visible character pixels and transparency")
        normalized = im.convert("RGBA").resize((tw, th), Image.Resampling.LANCZOS)
    receipt["canvas_transform"] = {"source_file": "source-output.png", "source_sha256": digest(data),
        "source_size": [w, h], "target_size": target_size, "scale": tw / w,
        "translation": [0, 0], "method": "full-canvas-lanczos"}
    _write(path, receipt)
    buffer = io.BytesIO()
    normalized.save(buffer, format="PNG")
    atomic_write_set({source: data, output: buffer.getvalue()})


def _complete(path: Path, receipt: dict, manifest: dict, *, normalize_canvas: bool = False) -> dict:
    """Only complete evidence can resolve an interrupted/unknown provider outcome."""
    report_path = path.parent / "gen-report.json"
    report = _read(report_path)
    if report.get("provider") != "codex":
        raise ValueError("generation report does not confirm the requested codex provider")
    output = path.parent / "output.png"
    with Image.open(output) as im:
        mismatch = list(im.size) != manifest["plan"]["canvas"]
    if receipt.get("canvas_transform") or (mismatch and normalize_canvas):
        _normalize_canvas(path, receipt, manifest["plan"]["canvas"])
    with Image.open(output) as im:
        im.load()
        if im.format != "PNG" or list(im.size) != manifest["plan"]["canvas"]:
            raise ValueError("generated canvas differs from pose coordinates; refuse per-frame resizing/cropping")
        if "A" not in im.getbands() or im.getchannel("A").getextrema()[0] == 255 or im.getchannel("A").getextrema()[1] == 0:
            raise ValueError("candidate must contain both visible character pixels and transparency")
    receipt.pop("reason", None)
    receipt.update(status="generated", output_sha256=_hash(output), report_sha256=_hash(report_path))
    _write(path, receipt)
    review = path.parent / "review.json"
    if review.exists() and _read(review).get("binding") != _binding(receipt):
        # Preserve observations about old pixels; never reuse their approval.
        archive = review.with_name(f"review-before-{_hash(review)[:16]}.json")
        if archive.exists() and archive.read_bytes() != review.read_bytes():
            raise ValueError("review archive conflict; preserve and inspect")
        atomic_write_set({archive: review.read_bytes()})
        _write(review, _review_template(_binding(receipt), POSE_CHECKS))
    elif not review.exists():
        _write(review, _review_template(_binding(receipt), POSE_CHECKS))
    if manifest["contract"] >= 3:
        frame = _geometry_frame(manifest, receipt["frame"])
        geometry_path = path.parent / "geometry-observations.json"
        wanted = pose_geometry.template(manifest["plan"], frame, receipt["output_sha256"], manifest["fingerprint"])
        if geometry_path.exists() and _read(geometry_path).get("binding") != wanted["binding"]:
            archive = geometry_path.with_name(f"geometry-before-{_hash(geometry_path)[:16]}.json")
            atomic_write_set({archive:geometry_path.read_bytes()})
            _write(geometry_path, wanted)
        elif not geometry_path.exists():
            _write(geometry_path, wanted)
        if manifest["contract"] >= 5:
            return {"status":"awaiting_review", "output":str(output), "review":str(review), "observations":str(geometry_path),
                    "geometry":"diagnostic; observe visible feet (declare hidden points) for sequence contact coverage"}
        return {"status":"awaiting_geometry_observations", "output":str(output), "review":str(review), "observations":str(geometry_path)}
    return {"status": "awaiting_review", "output": str(output), "review": str(review)}


def generate(run_dir: Path, manifest: dict, fid: str, *, model: str | None = None,
             retry: bool = False, reconcile: bool = False, normalize_canvas: bool = False, runner=None) -> dict:
    frame = next((f for f in manifest["plan"]["frames"] if f["id"] == fid), None)
    if frame is None:
        raise ValueError(f"unknown frame: {fid}")
    _require_input_review(run_dir, manifest, fid)
    refs = [run_dir / manifest["identity_file"], _folder(run_dir, fid) / "guide.png"]
    if manifest["contract"] >= 2:
        refs.reverse()
    refs += [_accepted(run_dir, anchor, manifest)[0] for anchor in frame["anchors"]]
    refs += [_folder(run_dir,fid)/name for name in gait_inputs.extra_names(frame)]
    inputs = {"run_fingerprint": manifest["fingerprint"], "frame": fid, "model": model,
              "provider": "codex", "refs": [{"path": str(p), "sha256": _hash(p)} for p in refs]}
    input_hash = digest(_json(inputs).encode("utf-8"))
    attempts = _attempts(run_dir, fid)
    again = None  # the folder of an attempt whose request never left: tried again as the same attempt
    if attempts:
        path = attempts[-1]
        previous = _read(path)
        if previous["input_sha256"] != input_hash:
            raise ValueError("attempt inputs/model changed; use a new run directory")
        if previous["status"] == "not-started":
            if retry or reconcile:
                raise ValueError("the last attempt sent nothing; run --frame again without --retry/--reconcile")
            again = path.parent
        elif reconcile:
            recoverable_canvas = previous["status"] == "invalid" and normalize_canvas and "canvas differs" in previous.get("reason", "")
            if previous["status"] not in ("started", "unknown") and not recoverable_canvas:
                raise ValueError("only an unknown/interrupted outcome needs reconciliation")
            return _complete(path, previous, manifest, normalize_canvas=normalize_canvas)
        elif previous["status"] in ("started", "unknown"):
            raise ValueError("generation outcome unknown; preserve files, inspect provider records, then --reconcile; no automatic retry")
        elif previous["status"] == "generated":
            _validate_receipt(path, manifest)
            status = _review_status(path.parent / "review.json", _binding(previous), POSE_CHECKS)
            if retry and 3 <= manifest["contract"] < 5:
                geometry = _audit_geometry(path.parent, manifest, fid)
                if geometry["status"] != "pass":
                    raise ValueError(f"geometry retry blocked: {geometry['status']}; measure/register existing pixels or revise the calibrated plan; do not repeat generation")
                output, bound, visual = _geometry_candidate(path, previous, manifest)
                if output.parent != path.parent:
                    raise ValueError("preserve registered candidate; do not repeat generation after geometric recovery")
                status = _review_status(visual, _binding(bound), POSE_CHECKS)
            if not retry:
                if manifest["contract"] >= 5:
                    state = {"pass":"reviewed","fail":"rejected","unverified":"awaiting_review"}[status]
                    result = {"status":state, "output":str(path.parent / "output.png"), "review":str(path.parent / "review.json")}
                    if _read(path.parent / "geometry-observations.json").get("status") != "unverified":
                        result["geometry"] = measure_geometry(run_dir, manifest, fid)["status"]
                    return result
                if manifest["contract"] >= 3:
                    measured = measure_geometry(run_dir, manifest, fid)
                    if measured["status"] != "pass" and not (path.parent/"registered").exists():
                        return measured
                    output, bound, review = _geometry_candidate(path, previous, manifest)
                    state = _review_status(review, _binding(bound), POSE_CHECKS)
                    return {"status":{"pass":"reviewed","fail":"rejected","unverified":"awaiting_review"}[state],
                            "output":str(output), "review":str(review)}
                return {"status": {"pass": "reviewed", "fail": "rejected", "unverified": "awaiting_review"}[status],
                        "output": str(path.parent / "output.png"), "review": str(path.parent / "review.json")}
            if status != "fail":
                raise ValueError("retry requires a failed visual review with observations")
        else:
            raise ValueError("structural rejection cannot trigger another generation; inspect/reconcile the existing output (resolution-only: --reconcile --normalize-canvas)")
        if again is None and len(attempts) >= MAX_ATTEMPTS:
            raise ValueError("two attempts exhausted; stop this pose approach and preserve evidence")
    elif retry or reconcile:
        raise ValueError("no prior candidate to retry/reconcile")
    folder = again or _folder(run_dir, fid) / f"attempt-{len(attempts)+1:02d}"
    if again:
        for name in ("output.png", "gen-report.json", "gen.log", "retry-feedback.json"):
            (folder / name).unlink(missing_ok=True)
    prompt = (_folder(run_dir, fid) / "prompt.txt").read_text(encoding="utf-8")
    feedback_record = None
    if attempts and again is None:
        prior = attempts[-1].parent
        feedback, feedback_record = _retry_feedback(prior, "geometry-review.json" if 3 <= manifest["contract"] < 5 else "review.json")
        prompt += feedback
    folder.mkdir(exist_ok=again is not None)
    if feedback_record:
        _write(folder / "retry-feedback.json", feedback_record)
    atomic_write_text(folder / "prompt.txt", prompt)
    path = folder / "attempt.json"
    receipt = {**inputs, "input_sha256": input_hash, "prompt_sha256": _hash(folder / "prompt.txt"), "status": "started"}
    _write(path, receipt)  # durable marker BEFORE the provider call, including timeout/crash cases
    try:
        rc = (runner or run_gen)(folder / "prompt.txt", folder / "output.png", refs,
                                 folder / "gen-report.json", model=model, log=folder / "gen.log")
    except BaseException as exc:
        # gen's own report says whether its request left (a stop reaches gen too, and it writes the report first)
        sent = outcome.child_submitted(1, folder / "gen-report.json")
        receipt.update(status="unknown" if sent else "not-started", reason=type(exc).__name__)
        _write(path, receipt)
        raise
    if rc != 0:
        if not outcome.child_submitted(rc, folder / "gen-report.json"):
            receipt.update(status="not-started", reason=f"provider exit {rc}; gen sent no request")
            _write(path, receipt)
            raise outcome.Stop(f"gen-motion: {receipt['reason']}; run --frame again (see {folder / 'gen.log'})",
                               outcome.NOT_STARTED_EXIT, outcome="preflight")
        receipt.update(status="unknown", reason=f"provider exit {rc}; outcome not confirmed")
        _write(path, receipt)
        raise ValueError(receipt["reason"])
    # A missing report/output is an unknown result, not evidence for another charge.
    if not (folder / "output.png").exists() or not (folder / "gen-report.json").exists():
        receipt.update(status="unknown", reason="provider returned without complete output/report")
        _write(path, receipt)
        raise ValueError(receipt["reason"])
    try:
        return _complete(path, receipt, manifest, normalize_canvas=normalize_canvas)
    except (ValueError, OSError) as exc:
        receipt.update(status="invalid", reason=str(exc))
        _write(path, receipt)
        raise


def export_asset(run_dir: Path, manifest: dict) -> dict:
    _require_input_review(run_dir, manifest)
    frames, hashes, decoded = [], [], set()
    accepted = {}
    for frame in manifest["plan"]["frames"]:
        path, receipt = _accepted(run_dir, frame["id"], manifest, accepted)
        with Image.open(path) as im:
            signature = digest(im.convert("RGBA").tobytes())
        if signature in decoded:
            raise ValueError("duplicate generated pose pixels; cannot export a duplicated gait")
        decoded.add(signature)
        frames.append({"file": path.relative_to(run_dir).as_posix(), "duration": frame["duration"]})
        hashes.append({"id": frame["id"], "duration": frame["duration"], "sha256": receipt["output_sha256"]})
    binding = {"run_fingerprint": manifest["fingerprint"], "frames": hashes}
    review_path = run_dir / "sequence-review.json"
    if not review_path.exists():
        _write(review_path, _review_template(binding, SEQUENCE_CHECKS))
    # A looping preview is available for the review, never labeled as approved.
    images = []
    for frame in frames:
        with Image.open(run_dir / frame["file"]) as im:
            images.append(im.convert("RGBA"))
    buffer = io.BytesIO()
    images[0].save(buffer, format="WEBP", save_all=True, append_images=images[1:], lossless=True,
                   duration=[max(1, round(f["duration"] * 1000)) for f in frames], loop=0)
    atomic_write_set({run_dir / "sequence-preview.webp": buffer.getvalue()})
    coverage_path = run_dir / "contact-coverage.json"
    coverage = None
    if manifest["contract"] >= 5:
        reports = {}
        for frame in manifest["plan"]["frames"]:
            folder = accepted[frame["id"]][0].parent
            observed = _read(folder / "geometry-observations.json")
            reports[frame["id"]] = None if observed.get("status") == "unverified" else _audit_geometry(folder, manifest, frame["id"], observed)
        coverage = pose_geometry.contact_coverage(manifest["plan"], reports)
        _write(coverage_path, coverage)
    if _review_status(review_path, binding, SEQUENCE_CHECKS) != "pass":
        return {"status": "awaiting_sequence_review", "review": str(review_path),
                "preview": str(run_dir / "sequence-preview.webp"),
                **({"contact_coverage": coverage["status"], "coverage": str(coverage_path)} if coverage else {})}
    if coverage and coverage["status"] != "pass":
        # Hidden or unobserved contacts stay unverified; never exported as pass.
        return {"status": f"contact_coverage_{coverage['status']}", "coverage": str(coverage_path),
                "preview": str(run_dir / "sequence-preview.webp")}
    asset = {"kind": "sprite-gen-asset", "version": 1, "frames": frames,
             "anchor": [manifest["plan"]["canvas"][0] / 2, manifest["plan"]["ground_y"]],
             "pose_plan_sha256": manifest["fingerprint"], "sequence_review_sha256": _hash(review_path)}
    if coverage:
        asset["contact_coverage_sha256"] = _hash(coverage_path)
    _write(run_dir / "asset.json", asset)
    return {"status": "reviewed_export", "asset": str(run_dir / "asset.json"),
            "preview": str(run_dir / "sequence-preview.webp")}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--plan", required=True, type=Path, help="authored sprite-gen-pose-plan JSON")
    parser.add_argument("--run-dir", required=True, type=Path, help="new, isolated pose run directory")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare-only", action="store_true", help="validate and render guides without generation")
    action.add_argument("--frame", help="generate one complete pose; reviewed anchors required")
    action.add_argument("--export", action="store_true", help="preview/review the cycle and export a standard asset descriptor")
    action.add_argument("--measure", metavar="FRAME", help="measure an existing candidate with hash-bound body/foot observations; no generation")
    action.add_argument("--register", metavar="FRAME", help="bounded whole-body scale/translation from body landmarks; feet validate independently; no generation")
    parser.add_argument("--model", help="explicit codex model; never falls back to another provider")
    parser.add_argument("--retry", action="store_true", help="new candidate after a confirmed rejection; maximum two attempts")
    parser.add_argument("--reconcile", action="store_true", help="inspect completed output/report after an unknown outcome; no generation")
    parser.add_argument("--normalize-canvas", action="store_true", help="opt in to same-aspect whole-canvas resolution conversion; preserve original bytes, scale and Y; no crop or bottom alignment")


def run(*, plan: Path, run_dir: Path, prepare_only: bool = False, frame: str | None = None,
        export: bool = False, model: str | None = None, retry: bool = False, reconcile: bool = False, normalize_canvas: bool = False,
        measure: str | None = None, register: str | None = None) -> int:
    if sum((prepare_only, frame is not None, export, measure is not None, register is not None)) != 1 or (retry and reconcile) or ((retry or reconcile) and frame is None):
        raise SystemExit("gen-motion: choose one action; retry/reconcile require --frame and cannot combine")
    run_dir = run_dir.expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    acquire_run_dir_lock(run_dir, "gen-motion")
    try:
        manifest = prepare(plan.expanduser().resolve(), run_dir)
        if prepare_only:
            result = {"status": "prepared_only", "run_dir": str(run_dir), "poses": len(manifest["plan"]["frames"])}
        elif export:
            result = export_asset(run_dir, manifest)
        elif measure is not None or register is not None:
            result = measure_geometry(run_dir, manifest, measure if measure is not None else register, register=register is not None)
        else:
            # Recording turns SIGTERM / SIGHUP into `Interrupted`, after the stop hook has sent the running `gen` SIGTERM
            # (`run_gen`); the attempt receipt then reads gen's report (`generate`).
            with outcome.recording("gen-motion"):
                result = generate(run_dir, manifest, frame, model=model, retry=retry, reconcile=reconcile, normalize_canvas=normalize_canvas)
        print(_json(result), end="")
        return 0
    except (ValueError, OSError) as exc:
        raise SystemExit(f"gen-motion: {exc}") from exc
    finally:
        release_run_dir_lock(run_dir)
