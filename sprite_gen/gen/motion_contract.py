# SPDX-License-Identifier: Apache-2.0
"""AI Fighter's explicit GPT row contract. Plans are instructions, never observations."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from PIL import Image

REVIEW_KEYS = ("identity", "anatomy", "alternation", "contacts", "bodyContinuity")


def normalize_motion(value, frames):
    motion = copy.deepcopy(value)
    if not isinstance(motion, dict) or motion.get("kind") not in ("walk", "run") or motion.get("stage") not in ("keyposes", "sequence"):
        raise SystemExit("motion requires kind walk/run and stage keyposes/sequence")
    phases = motion.get("phases")
    if not isinstance(phases, list) or len(phases) != frames:
        raise SystemExit("motion.phases must describe every requested frame")
    for phase in phases:
        if (not isinstance(phase, dict) or not isinstance(phase.get("pose"), str) or not phase["pose"].strip()
                or not isinstance(phase.get("support"), list) or any(foot not in ("near", "far") for foot in phase["support"])
                or len(set(phase["support"])) != len(phase["support"]) or (motion["kind"] == "walk" and not phase["support"])):
            raise SystemExit("Invalid planned pose or support feet")
    if not all(any(phase["support"] == [foot] for phase in phases) for foot in ("near", "far")):
        raise SystemExit("motion must plan single support for both anatomical feet")
    if motion["kind"] == "run" and not any(not phase["support"] for phase in phases):
        raise SystemExit("run motion must plan a flight phase")
    refs = motion.get("references")
    if not isinstance(refs, list) or not refs or len(refs) > 4:
        raise SystemExit("motion requires 1–4 explicit image references")
    for ref in refs:
        if (not isinstance(ref, dict) or ref.get("role") not in ("phase-guide", "keyposes")
                or not isinstance(ref.get("file"), str) or not ref["file"].strip()
                or not isinstance(ref.get("sha256"), str) or len(ref["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in ref["sha256"])):
            raise SystemExit("Each motion reference needs role, file and measured SHA-256")
    if not any(ref["role"] == "phase-guide" for ref in refs):
        raise SystemExit("motion phase-guide reference is required")
    return motion


def motion_refs(run_dir: Path, motion, frames):
    motion = normalize_motion(motion, frames)
    refs = []
    reviewed = False
    for ref in motion["references"]:
        file = (run_dir / ref["file"]).resolve()
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != ref["sha256"]:
            raise SystemExit(f"Motion reference missing or changed: {file}")
        try:
            with Image.open(file) as image:
                image.verify()
        except (OSError, ValueError) as error:
            raise SystemExit(f"Motion reference is not a decodable image: {file}") from error
        if file in refs:
            raise SystemExit("Duplicate motion reference")
        refs.append(file)
        if ref["role"] == "keyposes":
            review = ref.get("review", {})
            if not all(review.get(key) == "pass" for key in REVIEW_KEYS) or not str(review.get("notes", "")).strip():
                raise SystemExit("Key poses need actual identity/anatomy/alternation/contacts/bodyContinuity review and notes")
            reviewed = True
    if motion["stage"] == "sequence" and not reviewed:
        raise SystemExit("MOTION_KEYPOSE_REVIEW_REQUIRED: inspect both contacts and passing poses before sequence generation")
    return refs


def prompt_text(motion):
    phases = "\n".join(f"- Frame {i}: {p['pose']}; planned support: {', '.join(p['support']) or 'flight'}."
                       for i, p in enumerate(motion["phases"]))
    return ("\n\nMotion contract (instructions, not measured results):\n"
            "Near and far are the SAME physical legs throughout, never names based on screen x. "
            "Use the phase guide for timing and the reviewed key poses for motion; the identity anchor owns appearance. "
            "Keep each leg attached to its own hip, consistent occlusion, proportions and whole-body balance. "
            "Do not freeze/paste a torso onto separately posed legs. Preserve deliberate pelvis bob and flight height. "
            "Use one camera, scale and source ground line for the entire row.\n" + phases)


def input_fingerprint(prompt: Path, refs, motion, provider, model):
    value = {"prompt": hashlib.sha256(prompt.read_bytes()).hexdigest(), "motion": motion,
             "refs": [hashlib.sha256(ref.read_bytes()).hexdigest() for ref in refs], "provider": provider, "model": model}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
