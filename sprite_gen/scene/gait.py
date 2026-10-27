# SPDX-License-Identifier: Apache-2.0
"""Deterministic authored biped guides. No provider, image editing or IK clamping.

World axes: X forward, Y lateral (A negative/near), Z up. One phase drives both
legs. Stance locks WORLD foot positions; camera-follow must not lock screen X.
These are proxy design coordinates, never observations of generated artwork.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from sprite_gen._deps import np
from sprite_gen.util.console import utf8_streams


def _positive(value, name, low=0., high=100.):
    if type(value) not in (int, float) or not math.isfinite(value) or not low < value < high:
        raise ValueError(f"{name}: finite number in ({low},{high}) required")


def validate(profile):
    if profile.get("kind") != "sprite-gen-gait-profile" or profile.get("version") != 1:
        raise ValueError("sprite-gen-gait-profile version 1 required")
    if not isinstance(profile.get("provenance"), str) or not profile["provenance"].strip():
        raise ValueError("Authored proxy proportions need explicit provenance")
    body = profile["body"]
    for key in ("upper_leg", "lower_leg", "hip_half_width", "foot_length", "foot_width", "foot_height", "thigh_radius", "shin_radius"):
        _positive(body[key], key)
    if body["foot_width"] >= 2*body["hip_half_width"]:
        raise ValueError("Foot lanes intersect; reduce width or increase hip spacing")
    if 2*max(body["thigh_radius"],body["shin_radius"]) >= 2*body["hip_half_width"]:
        raise ValueError("Leg volumes intersect across lanes")
    for key in ("torso_radii", "head_radii", "muzzle_radii"):
        if not isinstance(body[key], list) or len(body[key]) != 3:
            raise ValueError(f"{key}: three positive radii required")
        for v in body[key]: _positive(v, key)
    for key in ("torso_height", "head_height", "muzzle_forward", "muzzle_height"):
        _positive(body[key], key)
    camera=profile["camera"]
    _positive(camera["yaw_degrees"], "yaw_degrees", 0, 89)
    _positive(camera["pixels_per_unit"], "pixels_per_unit", 20, 500)
    elevation=camera.get("elevation_degrees",0.)
    if type(elevation) not in (int,float) or not math.isfinite(elevation) or not 0 <= elevation < 60:
        raise ValueError("elevation_degrees must be finite in [0,60)")
    for name in ("walk", "run"):
        g=profile["motions"][name]
        for key in ("period", "speed", "clearance", "hip_height", "bob"):
            _positive(g[key], name+"."+key)
        _positive(g["duty"], name+".duty", .05, .95)
        if (name == "walk" and g["duty"] <= .5) or (name == "run" and g["duty"] >= .5):
            raise ValueError("Walk needs double support; run needs flight")
        if type(g["frames"]) is not int or not 4 <= g["frames"] <= 120 or g["frames"] % 2:
            raise ValueError("Even frame count 4..120 required for opposite phases")
        lift=g.get("flight_lift",0.)
        if type(lift) not in (int,float) or not math.isfinite(lift) or not 0 <= lift < 1:
            raise ValueError(name+".flight_lift must be finite in [0,1)")
        if name == "walk" and lift:
            raise ValueError("Walk has no flight; flight_lift is run-only")
    return profile


def ik_two_bone(hip, ankle, upper, lower):
    """Sagittal 2-bone solve, fixed +X knee pole. Unreachable targets fail."""
    h, a = np.asarray(hip, dtype=float), np.asarray(ankle, dtype=float)
    if not np.isfinite(np.r_[h,a,upper,lower]).all() or upper <= 0 or lower <= 0:
        raise ValueError("IK requires finite points and positive bone lengths")
    if abs(h[1]-a[1]) > 1e-9:
        raise ValueError("This proxy solver requires a fixed lateral foot lane")
    delta=a-h; distance=float(np.linalg.norm(delta))
    if not abs(upper-lower)+1e-8 < distance < upper+lower-1e-8:
        raise ValueError(f"Unreachable IK target: {distance:.6f}; no clamping")
    axis=delta/distance
    along=(upper*upper-lower*lower+distance*distance)/(2*distance)
    radius=math.sqrt(max(0.,upper*upper-along*along))
    pole=np.array([-axis[2],0.,axis[0]])
    knee=h+along*axis+radius*pole
    return knee.tolist()


def smootherstep(u):
    return u*u*u*(10+u*(-15+6*u))


def frame_times(profile, motion):
    g=profile["motions"][motion]
    # Mid-exposure samples avoid a zero-duration toe-off event being displayed
    # as an entire airborne frame. No repeated closing frame.
    return [(i+.5)*g["period"]/g["frames"] for i in range(g["frames"])]


def sample(profile, motion, time):
    """Continuous world motion; fractional cycle uses stable physical A/B IDs."""
    b,g=profile["body"],profile["motions"][motion]
    period, speed, duty=g["period"],g["speed"],g["duty"]
    q=time/period; root=speed*time
    # Two vertical body oscillations/cycle. Running rises during flight.
    bob=(-g["bob"]*math.cos(4*math.pi*q) if motion=="walk" else
         g["bob"]*math.cos(4*math.pi*(q-(duty/2+.25))))
    hip_z=g["hip_height"]+bob
    # Flight happens only where one foot has just lifted (u~0) and the other is
    # about to land (u~1); the swing arc is near zero at both ends, so a run's
    # flight is unreadable unless both feet also rise with the airborne body.
    # The authored lift is a C2 bump confined to each flight window.
    lift=0.
    window=.5-duty
    if motion=="run" and window>0:
        s=((q%1.)-duty)%.5/window
        if s<1:lift=g.get("flight_lift",0.)*64*s**3*(1-s)**3
    result={"time":time,"phase":q%1.,"root":[root,0.,0.],"hip_z":hip_z,"legs":{}}
    for leg,offset,side in (("A",0.,-1),("B",.5,1)):
        step=math.floor(q+offset); phase=q+offset-step
        touchdown=(step-offset)*period
        plant=speed*touchdown + speed*period*duty/2
        contact=phase < duty
        x,z=plant,0.
        if not contact:
            u=(phase-duty)/(1-duty)
            x+=speed*period*smootherstep(u)
            z=g["clearance"]*64*u**3*(1-u)**3+lift
        y=side*b["hip_half_width"]
        hip=[root,y,hip_z]; ankle=[x,y,z+b["foot_height"]]
        knee=ik_two_bone(hip,ankle,b["upper_leg"],b["lower_leg"])
        result["legs"][leg]={"phase":phase,"stance":contact,"plant_id":step,
          "hip":hip,"knee":knee,"ankle":ankle,"sole":[x,y,z],
          "heel":[x-b["foot_length"]*.25,y,z],"toe":[x+b["foot_length"]*.75,y,z],
          "plant_world":[plant,y,0.] if contact else None}
    result["support"]="".join(k for k,v in result["legs"].items() if v["stance"]) or "flight"
    return result


def screen_audit(profile, motion, times=None, min_flight_px=4.):
    """Necessary sampling/projection gate, NOT visible-pixel or AI compliance.

    Measure against each foot's own projected ground point. Near/far feet do
    not share one screen Y at nonzero elevation. Transition samples may have
    subpixel clearance; each half-cycle must ALSO contain a resolved flight.
    The 4px default is an explicit design tolerance, not a physical constant.
    Use the final camera scale and exact video sample times before generation.
    """
    validate(profile)
    _positive(min_flight_px, "min_flight_px", 0, 1000)
    times=frame_times(profile,motion) if times is None else list(times)
    if not times or any(type(t) not in (float,int) or not math.isfinite(t) for t in times):
        raise ValueError("Finite, nonempty sample times required")
    if any(b<=a for a,b in zip(times,times[1:])):
        raise ValueError("Sample times must increase")
    camera=profile["camera"]
    vertical_scale=camera["pixels_per_unit"]*math.cos(math.radians(camera.get("elevation_degrees",0)))
    records=[];resolved=set();supports=set()
    for index,time in enumerate(times):
        s=sample(profile,motion,time);supports.add(s["support"])
        heights={leg:float(f["sole"][2]*vertical_scale) for leg,f in s["legs"].items()}
        gap=min(heights.values());half=int(s["phase"]>=.5)
        readable=s["support"]=="flight" and gap>=min_flight_px
        if readable:resolved.add(half)
        records.append({"frame":index,"time":time,"phase":s["phase"],"support":s["support"],
          "sole_clearance_px":heights,"both_feet_min_clearance_px":gap,"resolved_flight":readable})
    checks={"both_single_supports_sampled":{"A","B"}<=supports,
      "sampled_support_schedule":("AB" in supports and "flight" not in supports) if motion=="walk" else ("AB" not in supports),
      "resolved_flight_in_both_half_cycles":motion!="run" or resolved=={0,1}}
    return {"status":"pass" if all(checks.values()) else "fail","checks":checks,
      "min_flight_px":min_flight_px,"vertical_pixels_per_unit":vertical_scale,
      "resolved_flight_count":sum(x["resolved_flight"] for x in records),
      "max_simultaneous_clearance_px":max(x["both_feet_min_clearance_px"] for x in records),
      "frames":records,"scope":"Projected authored soles only. Inspect raster foot visibility, depth order and full foot shape separately; never approve generated pixels from this report."}


def audit(profile, motion, samples=1200):
    """Dense numerical evidence from evaluated joints, not copied phase labels."""
    validate(profile)
    g,b=profile["motions"][motion],profile["body"]
    period=g["period"]
    states=[sample(profile,motion,i*period/samples) for i in range(2*samples+1)]
    bones=contact=sliding=lock=penetration=0.
    groups={}; supports=set(); max_swing=0.; min_lane_gap=math.inf
    for s in states:
        supports.add(s["support"])
        min_lane_gap=min(min_lane_gap,abs(s["legs"]["A"]["sole"][1]-s["legs"]["B"]["sole"][1])-b["foot_width"])
        for leg,f in s["legs"].items():
            bones=max(bones,abs(math.dist(f["hip"],f["knee"])-b["upper_leg"]),abs(math.dist(f["knee"],f["ankle"])-b["lower_leg"]))
            penetration=max(penetration,-f["heel"][2],-f["toe"][2])
            if f["stance"]:
                contact=max(contact,abs(f["heel"][2]),abs(f["toe"][2]))
                lock=max(lock,math.dist(f["sole"],f["plant_world"]))
                key=(leg,f["plant_id"])
                if key in groups: sliding=max(sliding,math.dist(f["sole"],groups[key]))
                else: groups[key]=f["sole"]
            else: max_swing=max(max_swing,f["sole"][2])
    # Compare root-relative poses at t and t+period; no duplicated endpoint in output.
    seam=0.; exchange=0.
    for i in range(61):
        t=i*period/61; a=sample(profile,motion,t); c=sample(profile,motion,t+period); other=sample(profile,motion,t+period/2)
        for leg in ("A","B"):
            for joint in ("hip","knee","ankle","heel","toe"):
                av=np.array(a["legs"][leg][joint])-a["root"]
                cv=np.array(c["legs"][leg][joint])-c["root"]
                seam=max(seam,float(np.linalg.norm(av-cv)))
                bv=np.array(other["legs"]["B" if leg=="A" else "A"][joint])-other["root"]
                exchange=max(exchange,float(np.linalg.norm(av[[0,2]]-bv[[0,2]])))
    # Touchdown/toe-off are C2 in WORLD X and Z. Check derivatives across events.
    velocity_jump=0.; eps=period*1e-5
    for offset in (0.,.5):
        leg="A" if offset==0 else "B"
        for phase in (0.,g["duty"]):
            t=(1-offset+phase)*period
            points=[np.array(sample(profile,motion,t+k*eps)["legs"][leg]["sole"]) for k in (-1,0,1)]
            velocity_jump=max(velocity_jump,float(np.linalg.norm((points[2]-2*points[1]+points[0])/eps)))
    rendered_support={sample(profile,motion,t)["support"] for t in frame_times(profile,motion)}
    checks={"fixed_bone_lengths":bones<1e-8,"world_stance_lock":max(lock,sliding)<1e-8,
      "sole_on_ground":contact<1e-8,"no_ground_penetration":penetration<1e-8,
      "physical_leg_half_cycle_exchange":exchange<1e-8,"loop_periodicity":seam<1e-8,
      "smooth_foot_transition":velocity_jump<1e-4,"foot_lanes_separate":min_lane_gap>0,
      "both_single_supports_visible_in_samples":{"A","B"}<=rendered_support,
      "support_schedule":("flight" not in supports and "AB" in rendered_support) if motion=="walk" else ("AB" not in supports and "flight" in rendered_support)}
    projected=screen_audit(profile,motion)
    checks["sampled_screen_clearance"]=projected["status"]=="pass"
    return {"status":"pass" if all(checks.values()) else "fail","checks":checks,
      "screen_projection":projected,
      "max_bone_length_error":bones,"max_stance_world_drift":sliding,"max_lock_error":lock,
      "max_contact_height_error":contact,"max_ground_penetration":penetration,
      "max_loop_error":seam,"max_half_cycle_exchange_error":exchange,
      "max_transition_velocity_jump":velocity_jump,"max_swing_clearance":max_swing,
      "min_lateral_foot_gap":min_lane_gap,"support_states":sorted(supports),
      "dense_samples":len(states),"units":"authored proxy units, not measured artwork",
      "scope":"Kinematic proxy only; no dynamics, balance or generated-image compliance claim"}


def add_arguments(parser):
    parser.add_argument("--profile",type=Path,required=True)
    parser.add_argument("--out-dir",type=Path,required=True)
    parser.add_argument("--motion",choices=("walk","run","both"),default="both")


def run(profile, out_dir, motion="both"):
    from sprite_gen.scene.gait_render import deliver
    profile_path=Path(profile);out_dir=Path(out_dir)
    profile=validate(json.loads(profile_path.read_text(encoding="utf-8")))
    if out_dir.exists():
        raise ValueError("Preserve previous guides: --out-dir must be a new directory")
    motions=("walk","run") if motion=="both" else (motion,)
    reports={m:audit(profile,m) for m in motions}
    if any(r["status"]!="pass" for r in reports.values()):
        raise ValueError("Kinematic audit failed: "+json.dumps(reports))
    states={m:[sample(profile,m,t) for t in frame_times(profile,m)] for m in motions}
    out_dir.mkdir(parents=True)
    out_dir.joinpath("profile.json").write_text(json.dumps(profile,indent=2),encoding="utf-8")
    result=deliver(profile,states,out_dir)
    report={"kind":"sprite-gen-gait-guide-report","version":1,"generation_calls":0,
      "profile_sha256":hashlib.sha256(profile_path.read_bytes()).hexdigest(),
      "provenance":profile["provenance"],"kinematics":reports,"rendering":result,
      "meaning":"Authored motion and depth-tested proxy guides; not generated Moka animation or game-pack approval"}
    out_dir.joinpath("report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"status":"pass","out_dir":str(out_dir),"generation_calls":0,
                      "motions":{m:len(states[m]) for m in motions}}))
    return 0


def main():
    utf8_streams()
    parser=argparse.ArgumentParser(description=__doc__);add_arguments(parser)
    return run(**vars(parser.parse_args()))


if __name__=="__main__":
    raise SystemExit(main())
