# SPDX-License-Identifier: Apache-2.0
"""Hash-bound gait design inputs. Never generated-image observations."""
from __future__ import annotations
import hashlib,json,math,re
import numpy as np
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont

ROLES=('visible-parts-and-depth','amodal-and-xray')

def design_contract(frame,canvas,masks):
    """Self-check actual rasterized proxy FOOT masks, not generated art."""
    from sprite_gen.spec.pose_geometry import LIMITS
    tolerance=max(1.,min(canvas)*LIMITS['contact_fraction']);issues=[];feet={}
    for leg,mask in masks.items():
        yy,xx=np.nonzero(np.asarray(mask)>0)
        if not len(xx):raise ValueError('empty rasterized design foot mask')
        g=frame['gait']['ground']['feet'][leg]
        minimum=float((g['slope']*xx+g['intercept']-yy).min())
        errors={p:g['slope']*frame['joints'][leg+'_'+p][0]+g['intercept']-frame['joints'][leg+'_'+p][1] for p in ('heel','toe')}
        if minimum < -tolerance:issues.append(leg+':raster_projected_penetration')
        if frame['contacts'][leg]:
            if abs(minimum)>tolerance or any(abs(v)>tolerance for v in errors.values()):issues.append(leg+':raster_contact_residual')
        elif minimum<=tolerance or min(errors.values())<=tolerance:issues.append(leg+':raster_or_target_lost_clearance')
        feet[leg]={'minimum_raster_clearance':minimum,'target_clearances':errors}
    return {'status':'pass' if not issues else 'blocked','contact_tolerance':tolerance,'feet':feet,'issues':issues,'meaning':'actual rasterized proxy foot self-consistency, NOT generated-image observations or approval'}

def validate(plan):
    ref=plan.get('gait_reference')
    if ref is None:return
    if plan.get('version')!=2 or ref.get('kind')!='sprite-gen-gait-motion-inputs' or ref.get('version')!=1:
        raise ValueError('gait inputs require calibrated version2 plan')
    if not isinstance(ref.get('sources'),dict) or not ref['sources']:
        raise ValueError('gait source hashes required')
    for path,sha in ref['sources'].items():
        if Path(path).is_absolute() or '..' in Path(path).parts or not re.fullmatch('[a-f0-9]{64}',sha):
            raise ValueError('gait sources require contained relative paths and SHA256')
    for f in plan['frames']:
        g=f.get('gait',{})
        if g.get('meaning')!='authored design only' or not isinstance(g.get('state'),dict):
            raise ValueError('gait state must remain design evidence')
        refs=g.get('references',[])
        if [r.get('role') for r in refs]!=list(ROLES):raise ValueError('separate visible/depth and amodal/Xray references required')
        for r in refs:
            if ref['sources'].get(r.get('file'))!=r.get('sha256'):raise ValueError('gait reference not bound to source hashes')
        ground=g.get('ground',{})
        if ground.get('kind')!='orthographic-world-plane' or set(ground.get('feet',{}))!={'A','B'}:
            raise ValueError('per-foot projected world ground required')
        for leg,v in ground['feet'].items():
            if not all(type(v.get(k)) in (int,float) and math.isfinite(v[k]) for k in ('slope','intercept','target_clearance')):
                raise ValueError('finite projected ground parameters required')
            contacts=f['contacts'][leg]
            if bool(contacts)!=bool(g['state']['legs'][leg]['stance']):raise ValueError('contact labels contradict continuous source state')
            for p in ('heel','toe'):
                x,y=f['joints'][leg+'_'+p];clear=v['slope']*x+v['intercept']-y
                if (p in contacts and abs(clear)>2) or (not contacts and clear<=2):
                    raise ValueError('projected contact/AIR conflicts with world-plane design')

def verify(plan_path,plan):
    if 'gait_reference' not in plan:return
    base=plan_path.parent.resolve()
    for rel,sha in plan['gait_reference']['sources'].items():
        p=(base/rel).resolve()
        if not p.is_relative_to(base) or hashlib.sha256(p.read_bytes()).hexdigest()!=sha:
            raise ValueError('gait source changed: '+rel)
    states=json.loads((base/'derived'/plan['motion']/'poses.json').read_text(encoding='utf-8'))['frames']
    bundle=json.loads((base/'bundle-contract.json').read_text(encoding='utf-8'))
    if bundle['status']!=plan['gait_reference']['bundle_contract']:raise ValueError('gait bundle contract differs from hash-bound report')
    for f in plan['frames']:
        if f['gait']['state']!=states[f['gait']['source_frame']]:raise ValueError('gait state differs from hashed derived poses')
        masks={k:Image.open(base/v) for k,v in f['gait']['foot_masks'].items()}
        if design_contract(f,plan['canvas'],masks)!=f['gait']['design_contract']:raise ValueError('rasterized gait design contract changed')

def extra_names(frame):
    return ['gait-visible-depth.png','gait-amodal-xray.png'] if 'gait' in frame else []

def _leg_words(leg):
    """Name a world-space leg pose in words. An image model follows pose
    vocabulary; it does not execute pixel coordinates (run02: every coordinate
    sentence reached the tool, yet size, placement and foot targets missed)."""
    hip,knee,ankle=(np.asarray(leg[k],dtype=float) for k in ('hip','knee','ankle'))
    length=leg['toe'][0]-leg['heel'][0]
    ahead=(leg['sole'][0]-hip[0])/length
    where=('directly under its hip' if abs(ahead)<.35 else
           f'about {abs(ahead):.1f} foot-lengths {"ahead of" if ahead>0 else "behind"} its hip')
    a,b=hip-knee,ankle-knee
    bend=180-math.degrees(math.acos(np.clip(a@b/np.linalg.norm(a)/np.linalg.norm(b),-1,1)))
    knee_words='knee nearly straight' if bend<25 else 'knee bent' if bend<60 else 'knee strongly bent'
    if leg['stance']:
        return f'foot PLANTED flat on the ground {where}; {knee_words}, carrying weight'
    lifted=round(100*leg['sole'][2]/hip[2])
    return f'foot OFF the ground, sole lifted about {lifted}% of hip height, {where}; {knee_words}'

def _support_words(support):
    return {'flight':'Support: BOTH feet are OFF the ground (flight). Leave a visible gap under each foot.',
            'AB':'Support: BOTH feet are on the ground (double support).'}.get(
            support,f'Support: only physical leg {support} touches the ground; the other foot is in the air.')

def prompt(plan,frame):
    g=frame['gait'];start=3+len(frame['anchors'])
    width,height=plan['canvas'];state=g['state']
    aspect='square' if width==height else f'{width}:{height}'
    roles=['Reference1: TARGET pose and placement on the shared canvas. Its bones are X-ray design marks; hidden joints are not visible artwork.',
           'Reference2: immutable APPEARANCE ONLY, not a placement or pose reference. Keep its face, body shape, proportions, clothing, colors and three-quarter view.']
    roles += [f'Reference{i+3}: accepted anchor {a}, continuity only, NOT target pose.' for i,a in enumerate(frame['anchors'])]
    roles += [f'Reference{start}: which leg is visible where (left panel) and camera depth (right panel). Only visible surfaces.',
              f'Reference{start+1}: full leg shapes including parts hidden behind the body or the other leg. Hidden parts stay hidden in the art.']
    return '\n'.join(['Generate ONE complete whole-body character on a transparent background, no sheet or collage.',
       f'Output a {aspect} {width} x {height} transparent PNG framed like Reference1: same body size and placement, empty margins on every side. Do not crop, zoom, recenter, or push the feet to the bottom edge.',
       *roles,plan['description'],
       'TARGET POSE (Reference1 places it; these words name it):',
       _support_words(state['support']),
       *(f'- Leg {k} ({plan["leg_labels"][k]}): {_leg_words(state["legs"][k])}.' for k in ('A','B')),
       'A and B are fixed physical legs attached to their own hips; never swap them by screen side. Where they overlap, follow the visible-leg reference.',
       'Do NOT copy proxy orange/blue colors, box feet, ellipsoids, geometric silhouette or diagnostic style. Original alone controls appearance.',
       'Do NOT copy the original neutral foot placement. Redraw the whole body together, with the body bobbing naturally; no frozen torso, pasted legs, mirroring or duplicated anchor.',
       'Remove every colored bone, label, marker, ground line and backdrop.'])+'\n'

def render(plan,frame):
    im=Image.new('RGB',tuple(plan['canvas']),'#f6f5f1');d=ImageDraw.Draw(im);font=ImageFont.load_default(size=17)
    d.text((8,8),f'TARGET {frame["id"]}: X-RAY DESIGN JOINTS / NOT visible surfaces',font=font,fill='#172b42')
    d.text((8,32),'Appearance from ORIGINAL only. Visibility / depth in separate references.',font=font,fill='#172b42')
    for name,(x,y) in frame['body_landmarks'].items():
        d.ellipse((x-6,y-6,x+6,y+6),outline='#8051a5',width=2);d.text((x+8,y),name,font=font,fill='#8051a5')
    for leg,color in [('B','#2879ce'),('A','#df6740')]:
        pts={k:tuple(frame['joints'][leg+'_'+k]) for k in ('hip','knee','ankle','heel','toe')}
        d.line([pts[k] for k in ('hip','knee','ankle','heel','toe')],fill=color,width=6)
        for name,(x,y) in pts.items():
            d.ellipse((x-5,y-5,x+5,y+5),fill=color)
            if name in frame['contacts'][leg]:d.rectangle((x-10,y-10,x+10,y+10),outline='#22804a',width=3)
        d.text((pts['knee'][0]+8,pts['knee'][1]),leg,font=font,fill=color)
        line=frame['gait']['ground']['feet'][leg];a=max(0,min(pts['heel'][0],pts['toe'][0])-65);b=min(im.width-1,max(pts['heel'][0],pts['toe'][0])+65)
        d.line((a,line['slope']*a+line['intercept'],b,line['slope']*b+line['intercept']),fill='#6f7782',width=2)
    d.text((8,im.height-45),'Separate per-foot projected ground. A/B color is identity notation, NOT appearance.',font=font,fill='#172b42')
    return im
