# SPDX-License-Identifier: Apache-2.0
"""Prepare GPT motion inputs from a verified continuous gait; ZERO generation."""
from __future__ import annotations
import argparse,copy,hashlib,json,math,shutil
from types import SimpleNamespace
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont
from sprite_gen.scene import gait,gait_render
from sprite_gen.spec import pose_geometry,pose_plan,gait_inputs

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,indent=2)+'\n',encoding='utf-8')

def project(profile,state,p,settings):
    r=gait_render.Raster(scale=profile['camera']['pixels_per_unit'],yaw=profile['camera']['yaw_degrees'],elevation=profile['camera'].get('elevation_degrees',0))
    return (r.project(np.asarray(p)-state['root'])*settings['guide_scale']+settings['guide_translation']).tolist()

def safe_samples(profile,motion,original,settings):
    n=len(original);period=profile['motions'][motion]['period'];factor=profile['camera']['pixels_per_unit']*settings['guide_scale']*math.cos(math.radians(profile['camera'].get('elevation_degrees',0)))
    times=[s['time'] for s in original];changes=[]
    tolerance=max(1.,min(settings['canvas'])*pose_geometry.LIMITS['contact_fraction'])
    for i in range(n//2):
        def quality(t):
            scores=[]
            for j,k in ((i,t),(i+n//2,t+period/2)):
                s=gait.sample(profile,motion,k)
                if s['support']!=original[j]['support']:return None
                if any(not f['stance'] and f['sole'][2]*factor<=2.000001 for f in s['legs'].values()):return None
                frame=design_frame(profile,s,settings)
                report=gait_inputs.design_contract(frame,settings['canvas'],foot_masks(profile,s,settings))
                scores.append((report, min((min(v['minimum_raster_clearance'],*v['target_clearances'].values()) for leg,v in report['feet'].items() if not s['legs'][leg]['stance']),default=1e9)))
            return scores
        score=quality(times[i])
        if score is not None and all(x[0]['status']=='pass' for x in score):continue
        if settings['sample_policy']!='clearance-safe':raise ValueError('AIR projection/raster contract fails geometry tolerance: explicit clearance-safe resampling required')
        t=times[i];step=period/n
        candidates=sorted((t+k*step/1000 for k in range(-475,476)),key=lambda x:abs(x-t))
        chosen=None;best=None;best_score=-float('inf');satisfied=False
        for x in candidates:
            q=quality(x)
            if q is None:continue
            minimum=min(v[1] for v in q)
            if minimum>best_score:best,best_score=x,minimum
            if all(v[0]['status']=='pass' for v in q):chosen=x;satisfied=True;break
        if chosen is None:chosen=best
        if chosen is None:raise ValueError('No contact-preserving ordered sample meets even plan AIR>2; revise input before generation')
        for j,new in ((i,chosen),(i+n//2,chosen+period/2)):
            changes.append({'frame':j,'original_time':times[j],'new_time':new,'full_raster_contract_satisfied':satisfied,'geometry_contact_tolerance':tolerance,'reason':'actual rasterized foot self-consistency; no contact relabel or floor movement. Unsolved frames remain blocked for generation.'})
            times[j]=new
    if any(a>=b for a,b in zip(times,times[1:])):raise ValueError('Resampling changed frame order')
    states=[gait.sample(profile,motion,t) for t in times]
    # Hold each actual sample until the next; exact integer milliseconds sum to period.
    cumulative=[round((t-times[0])*1000) for t in times]+[round(period*1000)]
    durations=[b-a for a,b in zip(cumulative,cumulative[1:])]
    if min(durations)<=0:raise ValueError('Nonpositive prepared duration')
    return states,durations,changes

def ground_model(profile,state,settings):
    yaw=math.radians(profile['camera']['yaw_degrees']);e=math.radians(profile['camera']['elevation_degrees'])
    scale=profile['camera']['pixels_per_unit']*settings['guide_scale']
    origin=np.array([144.,375.])*settings['guide_scale']+settings['guide_translation']
    slope=math.sin(e)*math.tan(yaw);feet={}
    for leg,f in state['legs'].items():
        # Visible near-side sole boundary of the authored box. Centerline world
        # joints remain in state; these edge targets are explicitly derived.
        edge_y=f['sole'][1]-profile['body']['foot_width']/2
        intercept=float(origin[1]-slope*origin[0]-scale*math.sin(e)/math.cos(yaw)*edge_y)
        feet[leg]={'slope':slope,'intercept':intercept,'lane_world_y':f['sole'][1],'sole_boundary_world_y':edge_y,
                   'target_clearance':scale*math.cos(e)*f['sole'][2],
                   'meaning':'conditional projected near-side sole boundary, authored lane/camera, NOT recovered artwork depth'}
    return {'kind':'orthographic-world-plane','elevation_degrees':profile['camera']['elevation_degrees'],'yaw_degrees':profile['camera']['yaw_degrees'],'feet':feet,
            'meaning':'World Z=0 projects to per-foot sloped boundaries, not a single screenY. Image residual is conditional on authored projection.'}

def design_frame(profile,state,settings):
    joints={}
    for leg,f in state['legs'].items():
        for key in ('heel','toe'):
            xyz=list(f[key]);xyz[1]-=profile['body']['foot_width']/2
            joints[leg+'_'+key]=project(profile,state,xyz,settings)
    return {'joints':joints,'contacts':{k:['heel','toe'] if f['stance'] else [] for k,f in state['legs'].items()},'gait':{'ground':ground_model(profile,state,settings)}}

def foot_masks(profile,state,settings):
    result={};b=profile['body'];cam=profile['camera'];s=settings['guide_scale'];tx,ty=settings['guide_translation']
    for leg,part in [('A',1),('B',2)]:
        origin=np.array([144.,375.])*s+[tx,ty]
        r=gait_render.Raster(width=settings['canvas'][0],height=settings['canvas'][1],origin=origin,scale=cam['pixels_per_unit']*s,yaw=cam['yaw_degrees'],elevation=cam['elevation_degrees'])
        center=np.array(state['legs'][leg]['sole'])-state['root']+[b['foot_length']*.25,0,b['foot_height']/2]
        r.shape(center,[b['foot_length']/2,b['foot_width']/2,b['foot_height']/2],part,box=True)
        mask=Image.fromarray((r.ids==part).astype('uint8')*255)
        result[leg]=mask # native final-canvas rays: NO low-resolution upscaling
    return result

def verify_source(source):
    inventory=json.loads((source/'file-hashes.json').read_text())
    for v in inventory:
        p=(source/v['file']).resolve()
        if not p.is_relative_to(source.resolve()) or sha(p)!=v['sha256']:raise ValueError('Approved guide source changed: '+v['file'])
    profile=gait.validate(json.loads((source/'profile.json').read_text()))
    result={}
    for motion in ('walk','run'):
        seq=json.loads((source/motion/'poses.json').read_text())['frames']
        if len(seq)!=(16 if motion=='walk' else 12):raise ValueError('AI Fighter needs16/12 continuous samples')
        for i,s in enumerate(seq):
            expected=gait.sample(profile,motion,s['time'])
            if s['support']!=expected['support'] or not np.allclose([s['legs'][k]['sole'] for k in ('A','B')],[expected['legs'][k]['sole'] for k in ('A','B')],atol=1e-9):raise ValueError('Source pose does not match continuous model')
            r=gait_render.render_proxy(profile,s);ids=np.array(Image.open(source/motion/f'part-id-{i:02}.png'))
            depth=np.load(source/motion/f'depth-{i:02}.npy',allow_pickle=False)
            if ids.shape!=(420,320) or not np.array_equal(ids,r.ids) or not np.allclose(depth,np.where(np.isfinite(r.depth),r.depth,np.nan),atol=1e-6,equal_nan=True):raise ValueError('Source part/depth projection mismatch')
            for k,part in (('A',1),('B',2)):
                if not np.array_equal(np.array(Image.open(source/motion/f'amodal-{k}-{i:02}.png'))>0,r.amodal[part]):raise ValueError('Source amodal mismatch')
        result[motion]=seq
    return profile,result

def place(im,settings,canvas,nearest=True):
    # Shared positive isotropic canvas transform. No silhouette fitting.
    s=settings['guide_scale'];tx,ty=settings['guide_translation']
    return im.transform(tuple(canvas),Image.Transform.AFFINE,(1/s,0,-tx/s,0,1/s,-ty/s),Image.Resampling.NEAREST if nearest else Image.Resampling.BILINEAR,fillcolor=(246,246,246))

def prepare_inputs(guide_dir,identity,body_reference,settings,out_dir):
    source=Path(guide_dir).resolve();out=Path(out_dir).resolve();identity=Path(identity).resolve()
    if out.exists():raise ValueError('Preserve versions: prepared output directory must not exist')
    cfg=json.loads(Path(settings).read_text());cal=json.loads(Path(body_reference).read_text())
    required={'canvas','guide_scale','guide_translation','body_scale','body_translation','elevation_degrees','sample_policy','description'}
    if set(cfg)!=required or cfg['sample_policy'] not in ('preserve','clearance-safe'):raise ValueError('Explicit projection/body/sample settings required')
    for key in ('guide_scale','body_scale'):
        if type(cfg[key]) not in (int,float) or not math.isfinite(cfg[key]) or cfg[key]<=0:raise ValueError('Positive common scale required')
    if len(cfg['canvas'])!=2 or any(type(v)!=int or not 64<=v<=4096 for v in cfg['canvas']):raise ValueError('Valid shared canvas required')
    for key in ('guide_translation','body_translation'):
        if len(cfg[key])!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in cfg[key]):raise ValueError('Finite shared translation required')
    if min(cfg['guide_translation'])<0 or any(cfg['guide_translation'][i]+cfg['guide_scale']*[320,420][i]>cfg['canvas'][i] for i in (0,1)):raise ValueError('Guide canvas would clip; no bbox crop')
    with Image.open(identity) as im:
        if cal['sha256']!=sha(identity) or cal['canvas']!=list(im.size):raise ValueError('Original calibration hash/canvas mismatch')
    pose_geometry.landmarks(cal['landmarks'],cal['canvas'])
    original,sequences=verify_source(source);profile=copy.deepcopy(original);profile['camera']['elevation_degrees']=cfg['elevation_degrees'];gait.validate(profile)
    sampled={};changes={};durations={}
    for m in ('walk','run'):sampled[m],durations[m],changes[m]=safe_samples(profile,m,sequences[m],cfg)
    out.mkdir(parents=True);shutil.copyfile(identity,out/'identity.png');write(out/'body-reference.json',cal);write(out/'settings.json',cfg)
    # Preserve the actual approved inputs before any derived projection.
    originals=['profile.json','report.json','file-hashes.json']
    for m in ('walk','run'):
        originals += [m+'/poses.json']
        for i in range(len(sequences[m])):originals += [f'{m}/{stem}-{i:02}.{ext}' for stem,ext in [('part-id','png'),('amodal-A','png'),('amodal-B','png'),('depth','npy')]]
    for rel in originals:
        p=out/'source'/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/rel,p)
    write(out/'derived/profile.json',profile);write(out/'resampling.json',changes)
    rasters={};bounds=[]
    for m,seq in sampled.items():
        for i,state in enumerate(seq):
            origin=np.array([144.,375.])*cfg['guide_scale']+cfg['guide_translation']
            r=gait_render.render_proxy(profile,state,width=cfg['canvas'][0],height=cfg['canvas'][1],origin=origin,pixel_scale=cfg['guide_scale'])
            if r.clipped:raise ValueError('Derived proxy clips; preserve diagnostic preparation')
            p=out/'derived'/m;p.mkdir(parents=True,exist_ok=True)
            Image.fromarray(r.ids).save(p/f'part-id-{i:02}.png')
            depth=np.where(np.isfinite(r.depth),r.depth,np.nan).astype(np.float32);np.save(p/f'depth-{i:02}.npy',depth)
            for k,part in [('A',1),('B',2)]:Image.fromarray((r.amodal[part]*255).astype('uint8')).save(p/f'amodal-{k}-{i:02}.png')
            for k,mask in foot_masks(profile,state,cfg).items():mask.save(p/f'projected-foot-{k}-{i:02}.png')
            finite=depth[np.isfinite(depth)];bounds.extend([float(finite.min()),float(finite.max())]);rasters[m,i]=gait_render.occlusion_evidence(r)
        write(out/f'derived/{m}/poses.json',{'kind':'authored-gait-poses','coordinates':'world XYZ, continuous source model; body-follow elevated orthographic camera','durations_ms':durations[m],'frames':seq})
    depth_bounds=[min(bounds),max(bounds)];plans={}
    for m,seq in sampled.items():
        n=len(seq);rootids={}
        for leg in ('A','B'):
            eligible=[i for i,s in enumerate(seq) if s['support']==leg]
            rootids[leg]=max(eligible,key=lambda i:seq[i]['legs']['B' if leg=='A' else 'A']['sole'][2])
        flights=[i for i,s in enumerate(seq) if s['support']=='flight']
        roots=set(rootids.values())|set(flights);frames=[]
        for i,state in enumerate(seq):
            p=out/'derived'/m
            r=SimpleNamespace(ids=np.array(Image.open(p/f'part-id-{i:02}.png')),depth=np.load(p/f'depth-{i:02}.npy',allow_pickle=False))
            joints={};ground=ground_model(profile,state,cfg)
            for leg,f in state['legs'].items():
                for key in ('hip','knee','ankle','heel','toe'):
                    xyz=list(f[key])
                    if key in ('heel','toe'):xyz[1]-=profile['body']['foot_width']/2
                    joints[leg+'_'+key]=project(profile,state,xyz,cfg)
            bob=-(state['hip_z']-profile['motions'][m]['hip_height'])*profile['camera']['pixels_per_unit']*cfg['guide_scale']*math.cos(math.radians(cfg['elevation_degrees']))
            body={k:[x*cfg['body_scale']+cfg['body_translation'][0],y*cfg['body_scale']+cfg['body_translation'][1]+bob] for k,(x,y) in cal['landmarks'].items()}
            bpts=list(body.values());joints.update(head=[sum(v[0] for v in bpts[:3])/3,sum(v[1] for v in bpts[:3])/3],neck=[sum(v[0] for v in bpts)/len(bpts),min(v[1] for v in bpts)+.45*(max(v[1] for v in bpts)-min(v[1] for v in bpts))],pelvis=np.mean([joints['A_hip'],joints['B_hip']],axis=0).tolist())
            # Visible masks and depth are separate panels, never amodal pixels.
            palette=np.array([(246,246,246),(239,126,46),(42,133,209)]+[(170,174,181)]*6,np.uint8)
            vis=Image.fromarray(palette[r.ids]);dep=np.where(np.isfinite(r.depth),np.clip((r.depth-depth_bounds[0])/(depth_bounds[1]-depth_bounds[0])*255,0,255),246).astype('uint8')
            depth=Image.fromarray(dep).convert('RGB');board=Image.new('RGB',(cfg['canvas'][0]*2,cfg['canvas'][1]),'#f6f6f6');board.paste(vis,(0,0));board.paste(depth,(cfg['canvas'][0],0));d=ImageDraw.Draw(board);d.text((8,8),'VISIBLE PART IDs ONLY / NOT APPEARANCE',fill='#111111');d.text((cfg['canvas'][0]+8,8),'DEPTH: lighter nearer / common range / background246',fill='#111111');board.save(p/f'visible-depth-{i:02}.png')
            a=np.array(Image.open(p/f'amodal-A-{i:02}.png'))>0;b=np.array(Image.open(p/f'amodal-B-{i:02}.png'))>0;rgb=np.full((*a.shape,3),246,np.uint8);rgb[a]=(239,126,46);rgb[b]=(42,133,209);rgb[a&b]=(142,93,167)
            xray=Image.fromarray(rgb);xd=ImageDraw.Draw(xray)
            for leg,col in [('A','#c24310'),('B','#154c9d')]:xd.line([tuple(joints[leg+'_'+k]) for k in ('hip','knee','ankle','heel','toe')],fill=col,width=3)
            xd.text((8,8),'AMODAL / X-RAY DESIGN: includes HIDDEN regions. NOT visible evidence.',fill='#111111');xray.save(p/f'amodal-xray-{i:02}.png')
            visibility=rasters[m,i];visibility['depth_range_common']=depth_bounds
            visibility['projected_joint_surface_owner']={}
            for name,xy in joints.items():
                x,y=np.round(xy).astype(int)
                visibility['projected_joint_surface_owner'][name]=int(r.ids[y,x]) if 0<=x<cfg['canvas'][0] and 0<=y<cfg['canvas'][1] else None
            fid=f'{m}-{i:02}';anchors=[] if i in roots else [f'{m}-{rootids[k]:02}' for k in ('A','B')]
            if i==n-1 and i not in roots:anchors=[f'{m}-00',f'{m}-{rootids["B"]:02}']
            if i==0 and i in roots and n-1 in roots:raise ValueError('Root/seam dependency needs explicit revision')
            refs=[{'role':role,'file':str((p/f'{stem}-{i:02}.png').relative_to(out)).replace('\\','/'),'sha256':sha(p/f'{stem}-{i:02}.png')} for role,stem in [('visible-parts-and-depth','visible-depth'),('amodal-and-xray','amodal-xray')]]
            frame={'id':fid,'duration':durations[m][i]/1000,'phase':f'continuous phase {state["phase"]:.6f}, physical support {state["support"]}','joints':joints,'body_landmarks':body,'contacts':{k:['heel','toe'] if f['stance'] else [] for k,f in state['legs'].items()},'occlusion_front':'A','anchors':anchors,'gait':{'meaning':'authored design only','source_frame':i,'state':state,'ground':ground,'visibility':visibility,'references':refs,'foot_masks':{k:str((p/f'projected-foot-{k}-{i:02}.png').relative_to(out)).replace('\\','/') for k in ('A','B')}}}
            frame['gait']['design_contract']=gait_inputs.design_contract(frame,cfg['canvas'],foot_masks(profile,state,cfg));frames.append(frame)
        # Even a root in the last slot must depend on opening for export; actual
        # Mocha source has last flight run, so select flight as continuity anchor
        # rather than an independent root at closing boundary.
        if not frames[-1]['anchors']:frames[-1]['anchors']=[frames[0]['id']]
        files={str(p.relative_to(out)).replace('\\','/'):sha(p) for p in out.rglob('*') if p.is_file() and p.suffix in ('.json','.png','.npy') and not p.name.endswith('-plan.json')}
        plan={'kind':'sprite-gen-pose-plan','version':2,'identity':'identity.png','body_reference':cal,'description':cfg['description'],'canvas':cfg['canvas'],'ground_y':float(375*cfg['guide_scale']+cfg['guide_translation'][1]),'motion':m,'loop':True,'coordinate_source':'authored','leg_labels':{'A':'negative worldY, near-camera physical leg; original neutral near shorts opening; never rename by screen x','B':'positive worldY, far-camera physical leg; original neutral far shorts opening; never rename by screen x'},'frames':frames,'gait_reference':{'kind':'sprite-gen-gait-motion-inputs','version':1,'sources':files,'projection':cfg,'source_guide':str(source),'meaning':'authored camera16 etc assumptions, not recovered hidden dimensions or generated artwork observations'}}
        pose_plan.validate(plan);write(out/f'{m}-plan.json',plan);plans[m]={'plan':str(out/f'{m}-plan.json'),'independent_single_supports':{k:f'{m}-{v:02}' for k,v in rootids.items()},'flight_ids':[f'{m}-{i:02}' for i in flights],'changes':changes[m],'full_design_contract':'pass' if all(f['gait']['design_contract']['status']=='pass' for f in frames) else 'blocked','blocked_frames':[f['id'] for f in frames if f['gait']['design_contract']['status']!='pass']}
        images=[pose_plan.render(plan,f) for f in frames]
        for suffix,mult in [('',1),('-slow',3)]:images[0].save(out/f'{m}-input-guide{suffix}.webp',save_all=True,append_images=images[1:],duration=[x*mult for x in durations[m]],loop=0,lossless=True)
    bundle={'status':'pass' if all(v['full_design_contract']=='pass' for v in plans.values()) else 'blocked','motions':{m:{'frames':len(sampled[m]),'status':v['full_design_contract'],'blocked_frames':v['blocked_frames']} for m,v in plans.items()},'renderer':'native final-canvas orthographic ray intersections; no320x420 mask enlargement'}
    write(out/'bundle-contract.json',bundle)
    for m in plans:
        path=out/f'{m}-plan.json';p=json.loads(path.read_text());p['gait_reference']['sources']['bundle-contract.json']=sha(out/'bundle-contract.json');p['gait_reference']['bundle_contract']=bundle['status'];write(path,p)
    write(out/'preparation-report.json',{'generation_calls':0,'source_guide':str(source),'source_camera':original['camera'],'derived_camera':profile['camera'],'source_resolution':[320,420],'native_render_resolution':cfg['canvas'],'bundle_contract':bundle,'assumptions':'Elevation is authored based on game camera convention, not measured from original. Preserve original right three-quarter appearance. No flat screen floor.','projection':cfg,'motions':plans,'resampling':changes})
    return {'status':'prepared_gait_inputs','generation_calls':0,'out_dir':str(out),'motions':plans}

def add_arguments(p):
    for name in ('guide-dir','identity','body-reference','settings','out-dir'):p.add_argument('--'+name,type=Path,required=True)
def run(guide_dir,identity,body_reference,settings,out_dir):
    print(json.dumps(prepare_inputs(guide_dir,identity,body_reference,settings,out_dir),indent=2));return 0
