# SPDX-License-Identifier: Apache-2.0
"""Read-only adapters from pose runs to optional Decisions evidence."""
from __future__ import annotations

import json
from pathlib import Path

from sprite_gen.qa.decisions import digest, read_json


def _bound_file(root, relative, expected):
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or digest(path.read_bytes()) != expected:
        raise ValueError("pose evidence is stale or outside the run")
    return path


def from_pose_run(root, stage, frame_id=None, candidate=None):
    if stage not in ("input", "pose", "sequence"):
        raise ValueError("pose-run requires stage input, pose or sequence")
    if stage == "sequence" and (frame_id is not None or candidate is not None):
        raise ValueError("sequence reviews every selected frame; frame/candidate are not allowed")
    if stage != "sequence" and frame_id is None:
        raise ValueError("input/pose review requires an explicit frame")
    if (stage == "pose") != (candidate is not None):
        raise ValueError("only pose review requires --candidate pointing to an exact output.png")
    root = root.resolve()
    manifest_path = root / "pose-run.json"
    manifest = read_json(manifest_path)
    plan = manifest["plan"]
    binding = {"contract": manifest["contract"], "plan": plan, "identity_sha256": manifest["identity_sha256"]}
    # gen-motion's manifest uses its own established JSON serialization.
    fingerprint = digest((json.dumps(binding, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    if fingerprint != manifest["fingerprint"]:
        raise ValueError("pose manifest fingerprint mismatch")
    identity = _bound_file(root, manifest["identity_file"], manifest["identity_sha256"])
    sources = [{"file": str(manifest_path)}]
    for relative, expected in manifest["files"].items():
        sources.append({"file": str(_bound_file(root, relative, expected))})
    refs = [{"file": str(identity), "role": "identity"}]
    frames, intent = [], []
    if stage == "sequence":
        review_path = root / "sequence-review.json"
        selected = read_json(review_path)["binding"]
        expected_frames = plan["frames"]
        if selected.get("run_fingerprint") != manifest["fingerprint"] or len(selected["frames"]) != len(expected_frames):
            raise ValueError("sequence review does not match the pose run")
        sources.append({"file": str(review_path)})
        for entry, target in zip(selected["frames"], expected_frames):
            if entry["id"] != target["id"] or entry["duration"] != target["duration"]:
                raise ValueError("sequence order or duration changed")
            # Resolve only the exact selected bytes, including registered candidates.
            folder = root / "poses" / ("pose-" + entry["id"])
            matches = [p for p in folder.glob("attempt-*/**/output.png") if digest(p.read_bytes()) == entry["sha256"]]
            if len(matches) != 1:
                raise ValueError("selected sequence pixels missing or ambiguous; use an explicit spec")
            output = matches[0].resolve()
            if not output.is_relative_to(root):
                raise ValueError("sequence candidate outside run")
            frames.append({"file": str(output), "id": entry["id"], "duration": entry["duration"]})
            intent.append({"id": entry["id"], "phase": target["phase"], "contacts": target["contacts"]})
    else:
        target = next((f for f in plan["frames"] if f["id"] == frame_id), None)
        if target is None:
            raise ValueError("unknown frame id")
        relative = f"poses/pose-{frame_id}/guide.png"
        guide = _bound_file(root, relative, manifest["files"][relative])
        from sprite_gen.spec.gait_inputs import extra_names
        for name in extra_names(target):
            extra_relative = f"poses/pose-{frame_id}/{name}"
            extra = _bound_file(root, extra_relative, manifest["files"][extra_relative])
            refs.append({"file": str(extra), "role": "guide"})
        if stage == "input":
            output = guide
            # Label the expected generation roles explicitly: some anchors may not
            # exist yet, and review reference indices must not masquerade as theirs.
            refs[0]["label"] = "Prepared generation Reference2: appearance only."
            for index, ref in enumerate(refs[1:]):
                ref["label"] = f"Prepared generation Reference{3 + len(target['anchors']) + index}: diagnostic guide."
            prompt_path = root / "poses" / ("pose-" + frame_id) / "prompt.txt"
            prompt_context = "The target frame below is prepared generation Reference1. Planned anchor references are not supplied in this input-only review; their suitability remains unverified."
        else:
            output = Path(candidate).resolve()
            pose_folder = (root / "poses" / ("pose-" + frame_id)).resolve()
            if not output.is_relative_to(pose_folder) or output.name != "output.png":
                raise ValueError("candidate must be an output.png inside the selected pose")
            registered = output.parent.name == "registered"
            receipt_path = output.parent / ("receipt.json" if registered else "attempt.json")
            receipt = read_json(receipt_path)
            if digest(output.read_bytes()) != receipt.get("output_sha256"):
                raise ValueError("candidate pixels differ from receipt")
            original_path = output.parent.parent / "attempt.json" if registered else receipt_path
            from sprite_gen.gen.gen_motion import _validate_receipt
            original = _validate_receipt(original_path, manifest)
            if original.get("status") != "generated" or original.get("run_fingerprint") != manifest["fingerprint"] or original.get("frame") != frame_id:
                raise ValueError("candidate generation outcome or binding is not verified")
            sources.extend([{"file": str(receipt_path)}, {"file": str(original_path)}])
            prompt_path = original_path.parent / "prompt.txt"
            sources.append({"file": str(prompt_path)})
            prompt_context = "Generation ReferenceN maps to review reference RN below. This is the saved generation request, not proof of the backend's actual dispatched prompt."
            if registered:
                from sprite_gen.gen.gen_motion import _binding
                if receipt.get("source_binding") != _binding(original):
                    raise ValueError("registered candidate belongs to a different source")
            guide_files = {str(guide), *(r["file"] for r in refs if r["role"] == "guide")}
            refs = []
            for index, reference in enumerate(original.get("refs", [])):
                path = Path(reference["path"]).resolve()
                if digest(path.read_bytes()) != reference["sha256"]:
                    raise ValueError("generation reference changed")
                role = "identity" if path == identity else "guide" if str(path) in guide_files else "anchor"
                refs.append({"file": str(path), "role": role, "label": f"Saved generation Reference{index+1}."})
        frames = [{"file": str(output), "id": frame_id, "duration": target["duration"]}]
        intent.append({"id": frame_id, "phase": target["phase"], "contacts": target["contacts"],
                       "prompt_context": prompt_context, "saved_prompt_not_dispatch_evidence": prompt_path.read_text(encoding="utf-8")})
    spec = {"kind": "sprite-gen-decision-spec", "version": 1, "stage": stage,
            "intent": json.dumps({"description": plan["description"], "leg_labels": plan["leg_labels"],
                                   "targets_not_observations": intent}, ensure_ascii=False),
            "loop": plan["loop"], "references": refs, "frames": frames, "sources": sources}
    return spec, root
