# SPDX-License-Identifier: Apache-2.0
"""Authored biped pose constraints, not observations of generated anatomy."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from sprite_gen.spec import pose_geometry, gait_inputs

LEGS = ("A", "B")
PARTS = ("hip", "knee", "ankle", "heel", "toe")
JOINTS = ("head", "neck", "pelvis", *(f"{leg}_{part}" for leg in LEGS for part in PARTS))
COLORS = {"A": "#df6740", "B": "#2879ce"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(f"pose plan: {message}")


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _keys(value: object, required: set[str], name: str) -> None:
    _require(isinstance(value, dict), f"{name} must be an object")
    _require(set(value) == required, f"{name} requires exactly: {', '.join(sorted(required))}")


def validate(plan: dict) -> dict:
    """Reject contradictory authored constraints before making any billed call.

    Pixels share one canvas. A/B identify physical limbs, never screen-left/right.
    Projected bone lengths may change with foreshortening: these checks do not
    claim to measure anatomy, proportions, or motion quality.
    """
    v2 = plan.get("version") == 2
    _keys(plan, {"kind", "version", "identity", "description", "leg_labels", "canvas",
                 "ground_y", "motion", "loop", "coordinate_source", "frames"} | ({"body_reference"} if v2 else set()) | ({"gait_reference"} if "gait_reference" in plan else set()), "plan")
    _require(plan["kind"] == "sprite-gen-pose-plan" and plan["version"] in (1, 2), "unsupported kind/version")
    for key in ("identity", "description"):
        _require(isinstance(plan[key], str) and bool(plan[key].strip()), f"{key} must be nonempty")
    _require(plan["coordinate_source"] == "authored", "coordinate_source must be authored; a plan is not measured gait")
    _keys(plan["leg_labels"], set(LEGS), "leg_labels")
    for label in plan["leg_labels"].values():
        _require(isinstance(label, str) and bool(label.strip()), "describe each physical leg")
    _require(plan["leg_labels"]["A"] != plan["leg_labels"]["B"], "physical legs need distinct descriptions")
    canvas = plan["canvas"]
    _require(isinstance(canvas, list) and len(canvas) == 2 and
             all(type(v) is int and 64 <= v <= 4096 for v in canvas), "canvas must be [width, height], 64..4096")
    width, height = canvas
    reference = None
    if v2:
        reference = plan["body_reference"]
        _keys(reference, {"sha256", "canvas", "landmarks", "reviewer", "observation"}, "body_reference")
        _require(isinstance(reference["sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", reference["sha256"]) is not None, "body_reference must bind original bytes")
        _require(isinstance(reference["canvas"], list) and len(reference["canvas"]) == 2 and all(type(n) is int and n > 0 for n in reference["canvas"]), "body_reference canvas required")
        _require(all(isinstance(reference[k], str) and reference[k].strip() for k in ("reviewer", "observation")), "body_reference needs actual original observations")
        pose_geometry.landmarks(reference["landmarks"], reference["canvas"])
    ground = plan["ground_y"]
    _require(_number(ground) and 0 < ground < height, "ground_y must be inside the canvas")
    _require(plan["motion"] in ("walk", "run") and plan["loop"] is True, "v1 supports looping biped walk/run only")
    frames = plan["frames"]
    _require(isinstance(frames, list) and 2 <= len(frames) <= 64, "frames must contain 2..64 poses in playback order")
    ids, poses, supported = set(), set(), set()
    flight = False
    for frame in frames:
        _keys(frame, {"id", "duration", "phase", "joints", "contacts", "occlusion_front", "anchors"} | ({"body_landmarks"} if v2 else set()) | ({"gait"} if "gait_reference" in plan else set()), "frame")
        if v2:
            pose_geometry.landmarks(frame["body_landmarks"], canvas)
            _require(set(frame["body_landmarks"]) == set(reference["landmarks"]), "body landmark names must match calibrated original")
        fid = frame["id"]
        _require(isinstance(fid, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", fid) is not None,
                 "frame id must be 1..40 lowercase letters/digits, underscores or hyphens")
        _require(fid not in ids, f"duplicate frame id: {fid}")
        ids.add(fid)
        _require(_number(frame["duration"]) and 0 < frame["duration"] <= 10, f"{fid}: duration must be seconds in (0,10]")
        _require(isinstance(frame["phase"], str) and bool(frame["phase"].strip()), f"{fid}: phase required")
        _require(frame["occlusion_front"] in LEGS, f"{fid}: occlusion_front must be A or B")
        joints = frame["joints"]
        _keys(joints, set(JOINTS), f"{fid}.joints")
        for name, xy in joints.items():
            _require(isinstance(xy, list) and len(xy) == 2 and all(_number(v) for v in xy), f"{fid}.{name}: finite [x,y] required")
            _require(0 <= xy[0] < width and 0 <= xy[1] < height, f"{fid}.{name}: outside canvas")
        signature = json.dumps(joints, sort_keys=True)
        _require(signature not in poses, f"{fid}: duplicate pose; author a distinct phase")
        poses.add(signature)
        _keys(frame["contacts"], set(LEGS), f"{fid}.contacts")
        stance = []
        for leg in LEGS:
            for a, b in (("hip", "knee"), ("knee", "ankle")):
                _require(math.dist(joints[f"{leg}_{a}"], joints[f"{leg}_{b}"]) > 0, f"{fid}: zero-length {leg} {a}-{b}")
            contacts = frame["contacts"][leg]
            _require(isinstance(contacts, list) and all(isinstance(p, str) and p in ("heel", "toe") for p in contacts)
                     and len(contacts) == len(set(contacts)), f"{fid}: contacts must list heel and/or toe")
            for part in ("heel", "toe"):
                y = joints[f"{leg}_{part}"][1]
                if "gait_reference" not in plan:
                    _require(y <= ground + 2, f"{fid}: {leg} {part} below ground")
                if part in contacts and "gait_reference" not in plan:
                    _require(abs(y - ground) <= 2, f"{fid}: declared contact is off ground")
            if contacts:
                stance.append(leg)
            elif "gait_reference" not in plan:
                _require(all(joints[f"{leg}_{p}"][1] < ground - 2 for p in ("heel", "toe")),
                         f"{fid}: non-contact foot must clear ground by more than 2px")
        if len(stance) == 1:
            supported.update(stance)
        flight |= not stance
        _require(plan["motion"] != "walk" or bool(stance), f"{fid}: walk cannot have a flight phase")
        anchors = frame["anchors"]
        _require(isinstance(anchors, list) and len(anchors) <= 2 and all(isinstance(a, str) for a in anchors)
                 and len(set(anchors)) == len(anchors) and fid not in anchors, f"{fid}: anchors must name up to two other poses")
    _require(supported == set(LEGS), "cycle must include single-foot support by each physical leg")
    _require(plan["motion"] != "run" or flight, "run requires a flight phase")
    by_id = {f["id"]: f for f in frames}
    for frame in frames:
        _require(all(a in by_id for a in frame["anchors"]), f"{frame['id']}: unknown anchor")
    _require(frames[0]["id"] in frames[-1]["anchors"], "closing pose must reference the opening pose for seam review")
    visiting, visited = set(), set()

    def visit(fid: str) -> None:
        _require(fid not in visiting, "anchor dependencies contain a cycle")
        if fid in visited:
            return
        visiting.add(fid)
        for anchor in by_id[fid]["anchors"]:
            visit(anchor)
        visiting.remove(fid)
        visited.add(fid)

    for fid in by_id:
        visit(fid)
    if v2:
        # Stable landmark distances constrain authored body scale independently
        # of moving feet/arms and silhouette height. Rotation is not zoom.
        names = list(reference["landmarks"])
        pairs = [(a,b) for i,a in enumerate(names) for b in names[i+1:]
                 if math.dist(reference["landmarks"][a], reference["landmarks"][b]) > min(reference["canvas"])*.1]
        scales = []
        for frame in frames:
            ratios = [math.dist(frame["body_landmarks"][a],frame["body_landmarks"][b]) /
                      math.dist(reference["landmarks"][a],reference["landmarks"][b]) for a,b in pairs]
            scale = sorted(ratios)[len(ratios)//2]
            _require(scale > 0 and all(abs(r/scale-1) <= .08 for r in ratios), "authored body shape differs from original calibration")
            scales.append(scale)
        _require(max(scales)/min(scales) <= 1.04, "authored body scale changes across frames; do not normalize pose silhouette heights")
    gait_inputs.validate(plan)
    return plan


def load(path: Path) -> dict:
    return validate(json.loads(path.read_text(encoding="utf-8")))


def render(plan: dict, frame: dict) -> Image.Image:
    """One pose, one shared canvas. No invented character geometry or measurements."""
    if "gait_reference" in plan:
        return gait_inputs.render(plan,frame)
    image = Image.new("RGB", tuple(plan["canvas"]), "#f6f5f1")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    stroke = max(2, round(min(image.size) / 100))
    radius = max(3, stroke)
    font = ImageFont.load_default(size=max(10, round(min(image.size) / 52)))
    joints = frame["joints"]
    if plan["version"] == 2:
        points = frame["body_landmarks"]
        for name,(x,y) in points.items():
            draw.ellipse((x-radius,y-radius,x+radius,y+radius), outline="#8051a5", width=2)
            draw.text((x+radius+3,y), name, font=font, fill="#8051a5")
    draw.text((8, 8), f"TARGET {frame['id']} | A orange / B blue", font=font, fill="#222222")
    draw.text((8, 10+font.size), "Joint targets only; appearance comes from the original", font=font, fill="#222222")
    draw.text((8, 12+2*font.size), f"Ground {100*plan['ground_y']/height:.1f}% | front limb {frame['occlusion_front']}", font=font, fill="#222222")
    draw.line([(0, plan["ground_y"]), (width - 1, plan["ground_y"])], fill="#60686f", width=max(2, stroke//2))
    # Separate placement crosses avoid implying that the long head/pelvis line
    # is the character's thin torso. No invented body silhouette or proportions.
    for name in ("head", "neck", "pelvis"):
        x, y = joints[name]
        draw.line((x-radius, y, x+radius, y), fill="#414952", width=2)
        draw.line((x, y-radius, x, y+radius), fill="#414952", width=2)
        draw.text((x+radius+4, y), name, font=font, fill="#414952")
    # The planned front limb is painted last; A/B colors never swap by x position.
    order = [leg for leg in LEGS if leg != frame["occlusion_front"]] + [frame["occlusion_front"]]
    for leg in order:
        color = COLORS[leg]
        x, y = joints[f"{leg}_hip"]
        draw.text((max(4, x-5*font.size) if leg == "A" else x+font.size, y-2*font.size),
                  f"{leg} HIP", font=font, fill=color)
        draw.line([tuple(joints["pelvis"]), tuple(joints[f"{leg}_hip"])], fill=color, width=stroke)
        points = [tuple(joints[f"{leg}_{p}"]) for p in ("hip", "knee", "ankle", "heel", "toe", "ankle")]
        draw.line(points, fill="#f6f5f1", width=stroke+4)
        draw.line(points, fill=color, width=stroke)
        for part in PARTS:
            x, y = joints[f"{leg}_{part}"]
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=color)
            if part in frame["contacts"][leg]:
                r = radius * 2
                draw.rectangle((x-r, y-r, x+r, y+r), outline="#22804a", width=stroke)
    for i, leg in enumerate(LEGS):
        contacts = frame['contacts'][leg]
        clearance = plan['ground_y'] - max(joints[f'{leg}_heel'][1], joints[f'{leg}_toe'][1])
        label = f"{leg}: CONTACT {'+'.join(contacts)}" if contacts else f"{leg}: AIR, clearance {100*clearance/height:.1f}%"
        draw.text((8, 16+(3+i)*font.size), label, font=font, fill=COLORS[leg])
    draw.text((5, height-font.size-5), "TARGET ONLY - omit all marks from finished character", font=font, fill="#222222")
    return image


def prompt(plan: dict, frame: dict) -> str:
    if "gait_reference" in plan:
        return gait_inputs.prompt(plan,frame)
    roles = ["Reference 1 is the TARGET: use its joint positions, contact state and framing.",
             "Reference 2 is APPEARANCE ONLY: preserve face, proportions, clothing and style; replace its neutral stance and framing with Reference 1."]
    roles += [f"Reference {i+3}: reviewed anchor pose {a}; continuity reference, NOT the target pose."
              for i, a in enumerate(frame["anchors"])]
    width, height = plan['canvas']
    targets = []
    for leg in LEGS:
        joints = frame['joints']
        heel, toe, hip = (joints[f'{leg}_{p}'] for p in ('heel', 'toe', 'hip'))
        offset = ((heel[0]+toe[0])/2 - hip[0]) / width * 100
        clearance = (plan['ground_y'] - max(heel[1], toe[1])) / height * 100
        targets.append(f"{leg}: {'CONTACT '+','.join(frame['contacts'][leg]) if frame['contacts'][leg] else 'AIRBORNE'}; "
                       f"sole centre {abs(offset):.1f}% canvas width to image-{'right' if offset >= 0 else 'left'} of its OWN hip; "
                       f"lowest sole clearance {clearance:.1f}% canvas height.")
    coordinates = ['Joint positions as percentages of full canvas (x right, y down):']
    coordinates += [f"{name}: ({100*x/width:.1f}%, {100*y/height:.1f}%)" for name, (x, y) in frame['joints'].items()]
    if plan["version"] == 2:
        coordinates += ["Calibrated visible body landmarks (purple guide marks):"]
        coordinates += [f"{name}: ({100*x/width:.1f}%, {100*y/height:.1f}%)" for name,(x,y) in frame["body_landmarks"].items()]
        coordinates += ["Keep these body proportions and placement across poses. Foot motion must not change character zoom or centering."]
    return "\n".join([
        "Generate ONE complete character in ONE frame, not a sheet or a collage.",
        *roles,
        f"TARGET PHASE: {frame['phase']}", *targets,
        f"Ground line: {100*plan['ground_y']/height:.1f}% of canvas height. Front physical limb: {frame['occlusion_front']}.",
        f"Plan coordinate canvas: {width}x{height}; preserve normalized positions if the provider returns another resolution.",
        "Guide controls placement and pose; original controls identity. Never copy the original's foot placement.",
        plan["description"],
        f"Physical leg A: {plan['leg_labels']['A']}. Physical leg B: {plan['leg_labels']['B']}.",
        "A/B stay attached to their own hip through the cycle. They never mean screen-left/right.",
        "Use the guide's hip, knee, ankle, heel and toe targets; preserve identity's anatomy and volume.",
        "Resolve overlap using occlusion_front. Preserve whole-body balance, head, torso and arm continuity.",
        "Draw the complete body together. Do not splice legs onto a frozen torso, mirror, or copy an anchor.",
        "Keep the character on the shared canvas. No crop, zoom, recentering or bottom alignment.",
        "Empty contacts mean that physical foot is airborne. Both empty means flight: preserve ground clearance.",
        "Remove all guide marks, letters, colored bones and the ground line from the finished art.",
        *coordinates,
        "These are authored targets, not measured anatomy. Deliver a single complete redrawn character with transparent background.",
    ]) + "\n"
