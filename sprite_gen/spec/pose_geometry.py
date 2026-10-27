# SPDX-License-Identifier: Apache-2.0
"""Measured body registration with independent foot constraints; never a pose rig.

Only a positive uniform scale and XY translation are fitted, from visible body
landmarks alone. Feet cannot pull the character onto the floor. Unknown anatomy
stays unknown; hash-bound manual observations are required, not inferred from a
silhouette or the authored target. No generation or credential access here.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageChops
import numpy as np

LIMITS = {"scale_min": .85, "scale_max": 1.15, "shift_fraction": .08,
          "body_fraction": .012, "contact_fraction": .003,
          "pose_fraction": .02, "alpha_threshold": 128,
          # Provisional design tolerance for hand-marked sole points. Blind
          # repeatability (2026-10-23): pair differences <=3.2px at 1024.
          "coverage_contact_fraction": .006,
          # Coverage drops a frame's feet only when body points disagree this
          # much. body_fraction (1.2%) flipped by annotator on real outputs
          # (12.4/11.5/10.7px); their genuine shape residuals reach ~16.5px.
          "coverage_body_fraction": .024}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def point(value, canvas):
    if not (isinstance(value, list) and len(value) == 2 and
            all(type(v) in (int, float) and math.isfinite(v) for v in value) and
            0 <= value[0] < canvas[0] and 0 <= value[1] < canvas[1]):
        raise ValueError("geometry: finite observed [x,y] inside canvas required")
    return value


def landmarks(value, canvas):
    if not isinstance(value, dict) or not 4 <= len(value) <= 12:
        raise ValueError("geometry: 4..12 named visible body landmarks required")
    if any(not isinstance(k, str) or not k.strip() for k in value):
        raise ValueError("geometry: body landmarks need stable names")
    pts = [point(p, canvas) for p in value.values()]
    # A cluster, or a line on a single limb, cannot establish body size/placement.
    if any(max(p[a] for p in pts)-min(p[a] for p in pts) < canvas[a]*.15 for a in (0, 1)):
        raise ValueError("geometry: landmarks must span the head and trunk in both axes")
    a = pts[0]
    if max(abs((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))
           for b in pts for c in pts) < min(canvas)**2*.005:
        raise ValueError("geometry: body landmarks must not be collinear")
    return value


def fit_body(observed: dict, target: dict) -> dict:
    """Least squares isotropic scale + translation, NO foot points/rotation/warp."""
    if set(observed) != set(target):
        raise ValueError("geometry: observed/target body landmark names differ")
    src, dst = list(observed.values()), [target[k] for k in observed]
    n = len(src)
    a = [sum(p[i] for p in src)/n for i in (0, 1)]
    b = [sum(p[i] for p in dst)/n for i in (0, 1)]
    den = sum(sum((p[i]-a[i])**2 for i in (0, 1)) for p in src)
    if den <= 0:
        raise ValueError("geometry: degenerate body landmarks")
    scale = sum(sum((p[i]-a[i])*(q[i]-b[i]) for i in (0, 1)) for p, q in zip(src, dst))/den
    shift = [b[i]-scale*a[i] for i in (0, 1)]
    errors = {k: math.dist([scale*v+shift[i] for i, v in enumerate(observed[k])], target[k]) for k in observed}
    return {"scale": scale, "translation": shift, "body_errors": errors,
            "max_body_error": max(errors.values()), "method": "body-only-uniform-scale-translation"}


def template(plan: dict, frame: dict, image_hash: str, fingerprint: str) -> dict:
    return {"binding": {"output_sha256": image_hash, "run_fingerprint": fingerprint, "frame": frame["id"]},
            "reviewer": "", "observation": "", "status": "unverified",
            "body_landmarks": {k: None for k in frame["body_landmarks"]},
            "feet": {leg: {"outline": [], "heel": None, "toe": None, "hidden": []} for leg in ("A", "B")}}


def _measure_foot(image, value, canvas):
    if not isinstance(value, dict) or not {"outline", "heel", "toe"} <= set(value) <= {"outline", "heel", "toe", "hidden"}:
        raise ValueError("geometry: both feet need a fresh outline, heel and toe")
    # A hidden point is declared, never guessed: null AND listed. It stays
    # unknown in every report and can never count toward a pass.
    hidden = value.get("hidden", [])
    if not isinstance(hidden, list) or len(set(hidden)) != len(hidden) or not set(hidden) <= {"heel", "toe"}:
        raise ValueError("geometry: hidden may list heel and/or toe only")
    for part in ("heel", "toe"):
        if (part in hidden) != (value[part] is None):
            raise ValueError(f"geometry: {part} must be observed, or null and listed as hidden; never guess it")
    if value["outline"] == [] and len(hidden) == 2:
        return {"bbox": None, "bottom_y": None, "points": {}, "hidden": hidden}, None
    outline = value["outline"]
    if not isinstance(outline, list) or not 3 <= len(outline) <= 128:
        raise ValueError("geometry: each visible foot needs a 3..128 point outline")
    outline = [point(p, canvas) for p in outline]
    mask = Image.new("L", image.size)
    ImageDraw.Draw(mask).polygon([tuple(p) for p in outline], fill=255)
    alpha = image.getchannel("A").point(lambda a: 255 if a >= LIMITS["alpha_threshold"] else 0)
    pixels = ImageChops.multiply(alpha, mask)
    bbox = pixels.getbbox()
    if bbox is None or sum(pixels.histogram()[1:]) < 9:
        raise ValueError("geometry: foot outline contains insufficient opaque pixels")
    points = {}
    for part in ("heel", "toe"):
        if part in hidden:
            continue
        x, y = point(value[part], canvas)
        ix, iy = round(x), round(y)
        # Require observed sole points near the lower visible boundary, rather
        # than accepting a typed point on an empty authored floor line.
        patch = pixels.crop((max(0, ix-2), max(0, iy-2), min(canvas[0], ix+3), min(canvas[1], iy+3)))
        if patch.getbbox() is None or (iy+4 < canvas[1] and pixels.getpixel((min(ix,canvas[0]-1), iy+4))):
            raise ValueError(f"geometry: {part} must be observed on the visible sole boundary")
        points[part] = [x, y]
    return {"bbox": list(bbox), "bottom_y": bbox[3]-1, "points": points, "hidden": hidden}, pixels


def audit(image: Image.Image, plan: dict, frame: dict, evidence: dict,
          image_hash: str, fingerprint: str) -> dict:
    binding = {"output_sha256": image_hash, "run_fingerprint": fingerprint, "frame": frame["id"]}
    if evidence.get("binding") != binding:
        raise ValueError("geometry: observations are stale or belong to different pixels")
    if evidence.get("status") != "observed" or not all(isinstance(evidence.get(k), str) and evidence[k].strip() for k in ("reviewer", "observation")):
        raise ValueError("geometry: actual body/physical-foot observations required; uncertain points stay unverified")
    if image.mode != "RGBA" or list(image.size) != plan["canvas"]:
        raise ValueError("geometry: normalized RGBA canvas required before measurement")
    source = landmarks(evidence.get("body_landmarks"), plan["canvas"])
    target = landmarks(frame["body_landmarks"], plan["canvas"])
    for x,y in source.values():
        if image.getpixel((min(round(x),image.width-1),min(round(y),image.height-1)))[3] < LIMITS["alpha_threshold"]:
            raise ValueError("geometry: body landmarks must be observed on visible character pixels")
    if not isinstance(evidence.get("feet"), dict) or set(evidence["feet"]) != {"A", "B"}:
        raise ValueError("geometry: observed physical feet A and B required")
    feet, masks = {}, {}
    for leg in ("A", "B"):
        feet[leg], masks[leg] = _measure_foot(image, evidence["feet"][leg], plan["canvas"])
    if masks["A"] is not None and masks["B"] is not None and ImageChops.multiply(masks["A"], masks["B"]).getbbox() is not None:
        raise ValueError("geometry: foot masks overlap; physical assignment is ambiguous")
    unknown = [f"{leg}:{part}" for leg in ("A", "B") for part in feet[leg]["hidden"]]
    fit = fit_body(source, target)
    unit = min(plan["canvas"])
    contact_tol, pose_tol = max(1., unit*LIMITS["contact_fraction"]), max(2., unit*LIMITS["pose_fraction"])
    body_tol = max(2., unit*LIMITS["body_fraction"])
    def check(scale, shift):
        problems, measured = [], {}
        for leg, foot in feet.items():
            if masks[leg] is None:
                measured[leg] = {"hidden": foot["hidden"], "points": {}}
                continue
            # A partly hidden foot's lowest visible pixel is not its sole, so
            # whole-mask contact checks only run on a fully observed foot.
            whole = not foot["hidden"]
            pts = {k: [scale*p[0]+shift[0], scale*p[1]+shift[1]] for k, p in foot["points"].items()}
            bottom = scale*foot["bottom_y"]+shift[1]
            err = {k: math.dist(p, frame["joints"][f"{leg}_{k}"]) for k,p in pts.items()}
            contacts = frame["contacts"][leg]
            seen = [k for k in contacts if k in pts]
            ground=frame.get('gait',{}).get('ground',{}).get('feet',{}).get(leg)
            if ground:
                # Conditional authored projection, NOT inferred artwork depth.
                yy,xx=np.nonzero(np.asarray(masks[leg]))
                clearance=ground['slope']*(scale*xx+shift[0])+ground['intercept']-(scale*yy+shift[1])
                minimum=float(clearance.min())
                contact_errors={k:ground['slope']*p[0]+ground['intercept']-p[1] for k,p in pts.items()}
                if minimum < -contact_tol:problems.append(f'{leg}:projected_ground_penetration')
                if contacts and (any(abs(contact_errors[k])>contact_tol for k in seen) or (whole and abs(minimum)>contact_tol)):problems.append(f'{leg}:projected_contact_residual')
                if not contacts and minimum<=contact_tol:problems.append(f'{leg}:lost_projected_clearance')
            elif bottom > plan["ground_y"]+contact_tol:
                problems.append(f"{leg}:ground_penetration")
            if contacts and not ground:
                if any(abs(pts[k][1]-plan["ground_y"]) > contact_tol for k in seen) or (whole and abs(bottom-plan["ground_y"]) > contact_tol):
                    problems.append(f"{leg}:contact_height")
            elif not contacts and not ground and plan["ground_y"]-bottom <= contact_tol:
                problems.append(f"{leg}:lost_flight_clearance")
            if err and max(err.values()) > pose_tol:
                problems.append(f"{leg}:foot_pose")
            measured[leg] = {"bottom_y": bottom, "clearance": plan["ground_y"]-bottom,
                             "points": pts, "target_error": err, "hidden": foot["hidden"]}
            if ground:
                measured[leg].update(clearance=minimum,projected_contact_residual=contact_errors,
                                    interpretation='conditional on authored elevated camera/foot lane; screenY difference alone is not physical penetration')
        return {"issues": problems, "feet": measured}
    raw = check(1., [0.,0.])
    fitted = check(fit["scale"], fit["translation"])
    issues = list(fitted["issues"])
    if not LIMITS["scale_min"] <= fit["scale"] <= LIMITS["scale_max"]:
        issues.append("body:scale_out_of_bounds")
    if any(abs(t) > unit*LIMITS["shift_fraction"] for t in fit["translation"]):
        issues.append("body:translation_out_of_bounds")
    if fit["max_body_error"] > body_tol:
        issues.append("body:shape_or_pose_mismatch")
    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError("geometry: empty character")
    s, (tx,ty) = fit["scale"], fit["translation"]
    if s*bbox[0]+tx < 0 or s*bbox[1]+ty < 0 or s*bbox[2]+tx > image.width or s*bbox[3]+ty > image.height:
        issues.append("body:would_clip_pixels")
    raw_body_error = max(math.dist(source[k], target[k]) for k in source)
    raw_pass = not raw["issues"] and raw_body_error <= body_tol
    status = "pass" if raw_pass else ("registration_available" if not issues else "blocked_pose_or_shape")
    if unknown and status != "blocked_pose_or_shape":
        status = "unverified_hidden"  # no contradiction seen, but a hidden point is never a pass
    return {"binding": binding, "status": status, "limits": LIMITS, "raw_body_error": raw_body_error,
            "fit": fit, "raw": raw, "registered_prediction": fitted, "issues": issues, "unknown": unknown,
            "retry_allowed": False, "meaning": "Measured geometry only; visual anatomy/identity/cycle review still required"}


def transform(image: Image.Image, fit: dict) -> Image.Image:
    s, (tx,ty) = fit["scale"], fit["translation"]
    if s <= 0:
        raise ValueError("geometry: positive scale required")
    return image.transform(image.size, Image.Transform.AFFINE,
                           (1/s, 0, -tx/s, 0, 1/s, -ty/s), Image.Resampling.BICUBIC)


def transformed_evidence(evidence: dict, fit: dict, output_hash: str) -> dict:
    import copy
    value = copy.deepcopy(evidence)
    value["provenance"] = {"kind":"transformed_observations", "source_binding":copy.deepcopy(evidence["binding"]),
                           "transform":copy.deepcopy(fit),
                           "meaning":"Body/sole point coordinates derived from source observations; output foot alpha is remeasured. Fresh visual review required."}
    s, shift = fit["scale"], fit["translation"]
    conv = lambda p: [s*p[i]+shift[i] for i in (0,1)]
    value["binding"]["output_sha256"] = output_hash
    value["body_landmarks"] = {k:conv(p) for k,p in value["body_landmarks"].items()}
    for foot in value["feet"].values():
        foot["outline"] = [conv(p) for p in foot["outline"]]
        for part in ("heel", "toe"):
            if foot[part] is not None:
                foot[part] = conv(foot[part])
    return value


def _runs(flags):
    """Consecutive true frames; a run touching both ends of a loop is one run."""
    runs = []
    for i, flag in enumerate(flags):
        if flag and i and flags[i-1]:
            runs[-1].append(i)
        elif flag:
            runs.append([i])
    if len(runs) > 1 and flags[0] and flags[-1]:
        runs[-1] += runs.pop(0)
    return runs


def contact_coverage(plan: dict, reports: dict) -> dict:
    """Sequence contact evidence per physical stance interval and flight.

    Separate from guide agreement: foot_pose and body placement belong to the
    per-frame audit and never decide this report. Feet are judged in each
    frame's body-registered space against the authored projected ground, so a
    whole-character offset is not a contact failure (playback drift is a
    sequence-review item). `reports` maps frame id -> audit report or None.
    A stance interval passes only with enough observed, consistent frames and
    no contradiction; hidden or unobserved points never count as evidence.
    """
    unit = min(plan["canvas"])
    tol = max(1., unit*LIMITS["coverage_contact_fraction"])
    body_tol = max(2., unit*LIMITS["coverage_body_fraction"])
    frames = plan["frames"]
    if any("gait" not in f for f in frames):
        raise ValueError("geometry: contact coverage needs per-foot projected ground from a gait plan")

    def foot(fid, leg):
        report = reports.get(fid)
        if report is None:
            return None, "unobserved"
        if report["fit"]["max_body_error"] > body_tol:
            return None, "body landmarks disagree; registered foot positions unreliable"
        value = report["registered_prediction"]["feet"][leg]
        return (value, None) if value["points"] else (None, "hidden")

    def interval(kind, members, judge, need):
        rows, consistent, contradictions = [], 0, []
        for i in members:
            row = judge(i)
            rows.append(row)
            consistent += row["state"] == "consistent"
            if row["state"] == "contradiction":
                contradictions.append(frames[i]["id"])
        status = "fail" if contradictions else "pass" if consistent >= need else "unverified"
        return {"kind": kind, "frames": [frames[i]["id"] for i in members], "required_observed": need,
                "observed_consistent": consistent, "contradictions": contradictions, "status": status, "evidence": rows}

    result = []
    for leg in ("A", "B"):
        for members in _runs([bool(f["contacts"][leg]) for f in frames]):
            offsets = {}
            def stance(i, leg=leg, offsets=offsets):
                f = frames[i]
                value, why = foot(f["id"], leg)
                if value is None:
                    return {"frame": f["id"], "state": "unknown", "reason": why}
                residual = {k: v for k, v in value.get("projected_contact_residual", {}).items() if k in f["contacts"][leg]}
                if not residual:
                    return {"frame": f["id"], "state": "unknown", "reason": "no planned contact point observed"}
                for k, p in value["points"].items():
                    target = f["joints"][f"{leg}_{k}"]
                    offsets.setdefault(k, []).append([p[0]-target[0], p[1]-target[1]])
                bad = value["clearance"] < -tol or any(abs(v) > tol for v in residual.values())
                return {"frame": f["id"], "state": "contradiction" if bad else "consistent", "contact_residual_px": residual}
            report = interval(f"{leg}-stance", members, stance, min(2, len(members)))
            # The planted sole is fixed in WORLD; its offset from the moving
            # projected target must stay constant across the interval.
            slip = max((math.dist(a, b) for v in offsets.values() for a in v for b in v), default=0.)
            report["max_slip_px"] = slip
            if slip > 2*tol:
                report["status"] = "fail"
                report["contradictions"].append("slip")
            result.append(report)
    for members in _runs([not f["contacts"]["A"] and not f["contacts"]["B"] for f in frames]):
        def flight(i):
            f = frames[i]
            values = [foot(f["id"], leg) for leg in ("A", "B")]
            # A partly hidden foot may touch the ground out of sight.
            if any(v is None or v["hidden"] for v, _ in values):
                return {"frame": f["id"], "state": "unknown", "reason": "a foot is hidden or unobserved"}
            gaps = {leg: v["clearance"] for leg, (v, _) in zip(("A", "B"), values)}
            return {"frame": f["id"], "state": "contradiction" if min(gaps.values()) <= tol else "consistent", "clearance_px": gaps}
        result.append(interval("flight", members, flight, 1))
    statuses = {r["status"] for r in result}
    fits = [r["fit"] for r in reports.values() if r is not None]
    return {"status": "fail" if "fail" in statuses else "pass" if statuses == {"pass"} else "unverified",
            "tolerance_px": tol, "body_tolerance_px": body_tol, "intervals": result,
            "body_fit_range": {"scale": [min(f["scale"] for f in fits), max(f["scale"] for f in fits)],
                               "translation_x": [min(f["translation"][0] for f in fits), max(f["translation"][0] for f in fits)],
                               "translation_y": [min(f["translation"][1] for f in fits), max(f["translation"][1] for f in fits)]} if fits else None,
            "meaning": "Contact/flight evidence from hand observations in body-registered space. Not guide-coordinate agreement, identity or motion approval."}
