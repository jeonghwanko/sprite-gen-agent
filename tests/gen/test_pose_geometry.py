# SPDX-License-Identifier: Apache-2.0
"""Offline geometric recovery, not a visual gait quality claim."""
import copy
import hashlib
import json

import pytest
from PIL import Image, ImageDraw

from sprite_gen.gen import gen_motion as motion
from sprite_gen.spec import pose_geometry as geo, pose_plan
from tests.gen.test_gen_motion import plan_fixture, review

BODY = {"near_ear": [35,20], "muzzle": [75,25], "near_waist": [35,70], "far_waist": [75,70]}


def fixture(tmp_path, motion_name="walk"):
    plan = plan_fixture(motion_name)
    image = Image.new("RGBA", (128,128))
    d = ImageDraw.Draw(image)
    d.rectangle((30,15,80,75), fill="tan")
    d.rectangle((37,108,55,112), fill="brown")
    d.rectangle((80,97,96,101), fill="brown")
    original = tmp_path/"original.png"
    image.save(original)
    plan.update(version=2, body_reference={"sha256":geo.sha(original), "canvas":[128,128], "landmarks":BODY,
                                          "reviewer":"offline fixture", "observation":"synthetic body geometry only"})
    for f in plan["frames"]:
        f["body_landmarks"] = copy.deepcopy(BODY)
    path = tmp_path/"plan.json"
    path.write_text(json.dumps(plan))
    run = tmp_path/"run"
    run.mkdir()
    manifest = motion.prepare(path,run)
    review(run/"input-review.json")
    evidence = {"binding":{"output_sha256":geo.sha(original), "run_fingerprint":manifest["fingerprint"], "frame":"contact-a"},
                "reviewer":"synthetic fixture", "observation":"observed synthetic rectangles, not natural feet", "status":"observed",
                "body_landmarks":copy.deepcopy(BODY),
                "feet":{"A":{"outline":[[36,107],[56,107],[56,114],[36,114]],"heel":[40,112],"toe":[53,112]},
                        "B":{"outline":[[79,96],[97,96],[97,103],[79,103]],"heel":[82,101],"toe":[94,101]}}}
    return image,plan,run,manifest,evidence


def observe_candidate(run,manifest,image,evidence,fit=None):
    fid="contact-a"
    def runner(prompt,out,refs,report,**kw):
        image.save(out)
        report.write_text(json.dumps({"provider":"codex"}))
        return 0
    result=motion.generate(run,manifest,fid,runner=runner)
    obs_path=run/"poses/pose-contact-a/attempt-01/geometry-observations.json"
    data=copy.deepcopy(evidence) if fit is None else geo.transformed_evidence(evidence,fit,geo.sha(run/"poses/pose-contact-a/attempt-01/output.png"))
    data["binding"]["output_sha256"]=geo.sha(run/"poses/pose-contact-a/attempt-01/output.png")
    obs_path.write_text(json.dumps(data))
    return result,obs_path


def test_plan_binds_identity_and_rejects_authored_zoom(tmp_path):
    _,p,run,m,e=fixture(tmp_path)
    assert m["contract"]==3
    p["frames"][-1]["body_landmarks"]={k:[x*1.1,y*1.1] for k,(x,y) in BODY.items()}
    with pytest.raises(ValueError,match="body scale"):
        pose_plan.validate(p)
    p["frames"][-1]["body_landmarks"]=copy.deepcopy(BODY)
    p["body_reference"]["sha256"]="0"*64
    (tmp_path/"bad.json").write_text(json.dumps(p))
    new=tmp_path/"bad";new.mkdir()
    with pytest.raises(ValueError,match="calibration"):
        motion.prepare(tmp_path/"bad.json",new)


def test_exact_geometry_pass_does_not_replace_visual_review(tmp_path):
    image,p,run,m,e=fixture(tmp_path)
    result,obs=observe_candidate(run,m,image,e)
    assert result["status"]=="awaiting_geometry_observations"
    # Writing "pass" to the old free-text file cannot bypass geometry review.
    review(run/"poses/pose-contact-a/attempt-01/review.json")
    measured=motion.measure_geometry(run,m,"contact-a")
    assert measured["status"]=="pass"
    with pytest.raises(ValueError,match="actual visual review"):
        motion._accepted(run,"contact-a",m)
    review(run/"poses/pose-contact-a/attempt-01/geometry-review.json")
    assert motion._accepted(run,"contact-a",m)[0].name=="output.png"
    # Altering observed body/foot evidence revokes the visual approval.
    value=json.loads(obs.read_text());value["observation"]+=" corrected notes";obs.write_text(json.dumps(value))
    with pytest.raises(ValueError,match="stale"):
        motion._accepted(run,"contact-a",m)


@pytest.mark.parametrize("drift", [
    {"scale":1.,"translation":[4.,-5.]},
    {"scale":.96,"translation":[3.,-2.]},
])
def test_body_only_recovery_preserves_source_and_requires_new_review(tmp_path,drift):
    image,p,run,m,e=fixture(tmp_path)
    moved=geo.transform(image,drift)
    result,obs=observe_candidate(run,m,moved,e,drift)
    original=(run/"poses/pose-contact-a/attempt-01/output.png").read_bytes()
    assert motion.measure_geometry(run,m,"contact-a")["status"]=="registration_available"
    fixed=motion.measure_geometry(run,m,"contact-a",register=True)
    assert fixed["generation_calls"]==0
    derived=json.loads((run/"poses/pose-contact-a/attempt-01/registered/geometry-observations.json").read_text())
    assert derived["provenance"]["kind"]=="transformed_observations"
    assert derived["provenance"]["source_binding"]["output_sha256"]==hashlib.sha256(original).hexdigest()
    assert abs(fixed["transform"]["scale"]-1/drift["scale"])<1e-9
    assert (run/"poses/pose-contact-a/attempt-01/output.png").read_bytes()==original
    with pytest.raises(ValueError,match="actual visual review"):
        motion._accepted(run,"contact-a",m)
    review(run/"poses/pose-contact-a/attempt-01/registered/review.json")
    assert "registered" in str(motion._accepted(run,"contact-a",m)[0])
    assert motion.measure_geometry(run,m,"contact-a",register=True)["output"]==fixed["output"]


def test_wrong_leg_contact_is_not_hidden_by_bottom_alignment(tmp_path):
    image,p,run,m,e=fixture(tmp_path)
    # Move only supporting foot upward; body is already in the right place.
    d=ImageDraw.Draw(image);d.rectangle((37,108,55,112),fill=(0,0,0,0));d.rectangle((37,102,55,106),fill="brown")
    for point in e["feet"]["A"]["outline"]:point[1]-=6
    for part in ("heel","toe"):e["feet"]["A"][part][1]-=6
    observe_candidate(run,m,image,e)
    assert motion.measure_geometry(run,m,"contact-a")["status"]=="blocked_pose_or_shape"
    with pytest.raises(ValueError,match="registration refused"):
        motion.measure_geometry(run,m,"contact-a",register=True)
    review(run/"poses/pose-contact-a/attempt-01/review.json","fail","contacts")
    seen=[]
    with pytest.raises(ValueError,match="geometry retry blocked"):
        motion.generate(run,m,"contact-a",retry=True,runner=lambda *a,**k:seen.append(1))
    assert seen==[] and not (run/"poses/pose-contact-a/attempt-02").exists()


def test_flight_is_never_floor_aligned(tmp_path):
    image,p,run,m,e=fixture(tmp_path,"run")
    frame=p["frames"][1]
    # The entire authored body also rises for flight.
    frame["body_landmarks"]={k:[x,y-12] for k,(x,y) in BODY.items()}
    shifted=geo.transform(image,{"scale":1.,"translation":[0.,-12.]})
    data=geo.transformed_evidence(e,{"scale":1.,"translation":[0.,-12.]},"test")
    data["binding"]["frame"]="flight"
    result=geo.audit(shifted,p,frame,data,"test",m["fingerprint"])
    assert result["status"]=="pass"
    assert result["raw"]["feet"]["A"]["clearance"]==12
    assert result["fit"]["translation"]==pytest.approx([0,0])


def test_two_feet_contact_residual_cannot_be_averaged_away(tmp_path):
    image,p,run,m,e=fixture(tmp_path)
    frame=copy.deepcopy(p["frames"][0]);frame["contacts"]["B"]=["heel","toe"]
    frame["joints"]["B_heel"][1]=frame["joints"]["B_toe"][1]=112
    result=geo.audit(image,p,frame,e,e["binding"]["output_sha256"],m["fingerprint"])
    assert result["status"]=="blocked_pose_or_shape"
    assert "B:contact_height" in result["issues"]
    assert result["fit"]["translation"]==pytest.approx([0,0])


@pytest.mark.parametrize("change,match", [
    (lambda e:e["binding"].update(output_sha256="changed"),"stale"),
    (lambda e:e.update(status="unverified"),"observations required"),
    (lambda e:e["body_landmarks"]["muzzle"].__setitem__(0,float("nan")),"finite"),
    (lambda e:e["body_landmarks"].update(muzzle=[110,20]),"visible character"),
    (lambda e:e["feet"]["A"].update(outline=[]),"outline"),
    (lambda e:e["feet"].update(B=copy.deepcopy(e["feet"]["A"])),"overlap"),
    (lambda e:e["feet"]["A"].update(heel=[45,100]),"sole boundary"),
])
def test_invalid_or_ambiguous_measurements_fail_closed(tmp_path,change,match):
    image,p,run,m,e=fixture(tmp_path);change(e)
    with pytest.raises(ValueError,match=match):
        geo.audit(image,p,p["frames"][0],e,geo.sha(tmp_path/"original.png"),m["fingerprint"])


def test_nonuniform_body_shape_is_not_fixed_by_whole_body_scale(tmp_path):
    image,p,run,m,e=fixture(tmp_path)
    e["body_landmarks"]["near_waist"]=[45,70]
    result=geo.audit(image,p,p["frames"][0],e,e["binding"]["output_sha256"],m["fingerprint"])
    assert result["status"]=="blocked_pose_or_shape"
    assert "body:shape_or_pose_mismatch" in result["issues"]


def test_registered_evidence_or_source_edits_revoke_acceptance(tmp_path):
    image,p,run,m,e=fixture(tmp_path)
    drift={"scale":1.,"translation":[4.,-5.]}
    observe_candidate(run,m,geo.transform(image,drift),e,drift)
    motion.measure_geometry(run,m,"contact-a",register=True)
    folder=run/"poses/pose-contact-a/attempt-01/registered"
    review(folder/"review.json")
    with (folder/"output.png").open("ab") as f:f.write(b"tamper")
    with pytest.raises(ValueError,match="changed"):
        motion._accepted(run,"contact-a",m)


def test_moka_fixed_floor_regression_numbers_are_rejected():
    # Actual v4 measured support bottoms, on normalized 1024 canvas. These
    # numbers are not body landmarks, so cannot authorize a translation.
    tolerance=max(1.,1024*geo.LIMITS["contact_fraction"])
    assert all(abs(y-970)>tolerance for y in (957,990,982,980))
    assert 990-957==33


def test_registration_refuses_clipping_even_when_feet_would_match(tmp_path):
    image,p,run,m,e=fixture(tmp_path)
    drift={"scale":1.,"translation":[-4.,-5.]}
    moved=geo.transform(image,drift)
    moved.putpixel((127,30),(255,255,255,255))
    observed=geo.transformed_evidence(e,drift,"test")
    result=geo.audit(moved,p,p["frames"][0],observed,"test",m["fingerprint"])
    assert result["status"]=="blocked_pose_or_shape"
    assert "body:would_clip_pixels" in result["issues"]


def test_registered_anchor_flows_into_export_with_correct_hashes(tmp_path):
    image,p,run,m,e=fixture(tmp_path)
    drift={"scale":1.,"translation":[4.,-5.]}
    observe_candidate(run,m,geo.transform(image,drift),e,drift)
    fixed=motion.measure_geometry(run,m,"contact-a",register=True)
    review(run/"poses/pose-contact-a/attempt-01/registered/review.json")
    second=image.copy();ImageDraw.Draw(second).rectangle((31,40,33,42),fill="orange")
    seen=[]
    def runner(prompt,out,refs,report,**kwargs):
        seen.extend(refs);second.save(out);report.write_text(json.dumps({"provider":"codex"}));return 0
    result=motion.generate(run,m,"contact-b",runner=runner)
    assert str(seen[-1])==fixed["output"]
    folder=run/"poses/pose-contact-b/attempt-01"
    observed=copy.deepcopy(e);observed["binding"].update(frame="contact-b",output_sha256=geo.sha(folder/"output.png"))
    observed["feet"]["A"],observed["feet"]["B"]=observed["feet"]["B"],observed["feet"]["A"]
    (folder/"geometry-observations.json").write_text(json.dumps(observed))
    assert motion.measure_geometry(run,m,"contact-b")["status"]=="pass"
    review(folder/"geometry-review.json")
    result=motion.export_asset(run,m)
    assert result["status"]=="awaiting_sequence_review"
    review(run/"sequence-review.json")
    result=motion.export_asset(run,m)
    asset=json.loads((run/"asset.json").read_text())
    assert "registered/output.png" in asset["frames"][0]["file"]
    assert asset["frames"][1]["duration"]==.12
    # Revoking an anchor's body evidence also invalidates its dependent pose.
    original=run/"poses/pose-contact-a/attempt-01/geometry-observations.json"
    value=json.loads(original.read_text());value["observation"]+=" changed";original.write_text(json.dumps(value))
    with pytest.raises(ValueError,match="source/observations changed"):
        motion.export_asset(run,m)
