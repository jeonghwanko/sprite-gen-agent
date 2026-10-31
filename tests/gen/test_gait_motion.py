# SPDX-License-Identifier: Apache-2.0
"""Offline input/geometry contracts. Synthetic fixtures are NOT art approval."""
import copy,json,hashlib
from pathlib import Path
import numpy as np
import pytest
from PIL import Image,ImageDraw
from sprite_gen.gen import gait_motion as bridge,gen_motion
from sprite_gen.scene import gait,gait_render
from sprite_gen.spec import pose_geometry,gait_inputs

# The module's `prepared` fixture renders and checks every walk and run frame at 1024 x 1024 and is billed to the first
# test that asks for it: about 2 minutes on a Windows desktop alone (2026-11-01), over the default 120 s, more beside
# other workers. Its tests are given that, a hang still a named failure.
pytestmark = pytest.mark.timeout(600)

def profile():return json.loads((Path(__file__).resolve().parents[2]/'examples/gait-mocha.json').read_text())
def settings():return {'canvas':[1024,1024],'guide_scale':2.4,'guide_translation':[128,8],'body_scale':2.,'body_translation':[10,50],'elevation_degrees':16.,'sample_policy':'clearance-safe','description':'SYNTHETIC fixture only; not character art.'}

def prompt_fixture(motion,time):
    p=profile();state=gait.sample(p,motion,time)
    plan={'canvas':[1024,1024],'description':'Synthetic contract fixture','leg_labels':{'A':'near','B':'far'}}
    frame={'anchors':[],'phase':'synthetic','joints':{'A_toe':[400.123456789,950.987654321]},'body_landmarks':{'head':[500,200]},
           'gait':{'ground':{'feet':{}},'state':state,'visibility':{'leg_overlap_pixels':284}}}
    return plan,frame,state

def test_gait_prompt_names_the_pose_in_words_not_coordinates_through_transport():
    from sprite_gen.gen.codex_provider import _build_prompt
    plan,frame,state=prompt_fixture('walk',.2)
    source=gait_inputs.prompt(plan,frame)
    transported=_build_prompt(source,native_alpha=True,refs=[Path('target.png'),Path('original.png')])
    assert source in transported # transport must not summarize away the request
    assert state['support']=='A' and 'only physical leg A touches the ground' in source
    assert '- Leg A (near): foot PLANTED flat on the ground' in source
    assert '- Leg B (far): foot OFF the ground, sole lifted about' in source
    assert 'square 1024 x 1024 transparent PNG' in source and 'APPEARANCE ONLY' in source
    # Run02 showed coordinate dumps reach the tool and are still not followed.
    for gone in ('400.123','Absolute joint targets','"plant_world"','leg_overlap_pixels','"slope"','{"left"'):
        assert gone not in source

def test_flight_prompt_requires_a_visible_gap_under_both_feet():
    p=profile();t=next(t for t in gait.frame_times(p,'run') if gait.sample(p,'run',t)['support']=='flight')
    plan,frame,_=prompt_fixture('run',t)
    source=gait_inputs.prompt(plan,frame)
    assert 'BOTH feet are OFF the ground (flight)' in source and source.count('foot OFF the ground')==2

def test_elevated_world_ground_is_not_one_screen_y():
    p=profile();p['camera']['elevation_degrees']=16;r=gait_render.Raster(scale=145,yaw=50,elevation=16)
    assert r.project([0,-.26,0])[1]>r.project([0,.26,0])[1]
    assert r.project([.5,0,0])[1]!=r.project([0,0,0])[1]
    a=gait.sample(p,'walk',.02);b=gait.sample(p,'walk',.2)
    assert a['legs']['A']['sole']==b['legs']['A']['sole']
    qa=bridge.project(p,a,a['legs']['A']['sole'],settings());qb=bridge.project(p,b,b['legs']['A']['sole'],settings())
    assert qb[0]<qa[0] and qb[1]<qa[1] # body-follow projection, WORLD remains fixed
    flat=gait_render.Raster(scale=145,yaw=50)
    np.testing.assert_allclose(flat.project([.3,.2,.7]),[144+145*(np.cos(np.deg2rad(50))*.3+np.sin(np.deg2rad(50))*.2),375-145*.7])

def test_native_rays_remove_low_resolution_flight_quantization_without_relaxing_tolerance():
    p=profile();p['camera']['elevation_degrees']=16;s=gait.sample(p,'run',.45*p['motions']['run']['period']);cfg=settings()
    frame=bridge.design_frame(p,s,cfg);masks=bridge.foot_masks(p,s,cfg)
    report=gait_inputs.design_contract(frame,cfg['canvas'],masks)
    assert all(im.size==(1024,1024) for im in masks.values())
    assert report['contact_tolerance']==3.072 and report['status']=='pass'
    for f in report['feet'].values():
        assert f['minimum_raster_clearance']>=min(f['target_clearances'].values())-1e-7
        assert min(f['target_clearances'].values())>3.072

@pytest.mark.parametrize('motion',['walk','run'])
def test_resampling_is_explicit_contact_preserving_and_half_cycle(motion):
    p=profile();p['camera']['elevation_degrees']=16
    if motion=='run':
        # flight_lift makes the canonical run samples clear the raster contract unchanged.
        canonical=[gait.sample(p,motion,t) for t in gait.frame_times(p,motion)]
        cfg=settings();cfg['sample_policy']='preserve'
        assert bridge.safe_samples(p,motion,canonical,cfg)[2]==[]
        p['motions']['run']['flight_lift']=0 # without it, the v6 resampling regression remains
    seq=[gait.sample(p,motion,t) for t in gait.frame_times(p,motion)]
    cfg=settings();cfg['sample_policy']='preserve'
    with pytest.raises(ValueError,match='AIR projection'):bridge.safe_samples(p,motion,seq,cfg)
    cfg['sample_policy']='clearance-safe';states,durations,changes=bridge.safe_samples(p,motion,seq,cfg)
    assert changes and [x['support'] for x in states]==[x['support'] for x in seq]
    assert min(durations)>0 and sum(durations)==round(p['motions'][motion]['period']*1000)
    factor=145*2.4*np.cos(np.deg2rad(16));n=len(states)
    for i,s in enumerate(states):
        for f in s['legs'].values():assert f['stance'] or f['sole'][2]*factor>2
        if i<n//2:assert abs(states[i+n//2]['time']-s['time']-p['motions'][motion]['period']/2)<1e-10

@pytest.fixture(scope='module')
def prepared(tmp_path_factory):
    root=tmp_path_factory.mktemp('gait-bridge');p=profile();path=root/'profile.json';path.write_text(json.dumps(p))
    gait.run(path,root/'guide');image=root/'original.png';Image.new('RGB',(256,256),'tan').save(image)
    cal={'sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'canvas':[256,256],'landmarks':{'head_left':[60,25],'head_right':[160,25],'trunk_left':[50,140],'trunk_right':[180,140]},'reviewer':'OFFLINE SYNTHETIC','observation':'Synthetic visible marks for pipeline tests only.'}
    c=root/'body.json';c.write_text(json.dumps(cal));s=root/'settings.json';s.write_text(json.dumps(settings()))
    result=bridge.prepare_inputs(root/'guide',image,c,s,root/'prepared');return root,result

def test_preparation_binds_actual_depth_masks_and_separates_reference_roles(prepared):
    root,result=prepared;path=root/'prepared/walk-plan.json';plan=json.loads(path.read_text());gait_inputs.verify(path,plan)
    assert len(plan['frames'])==16 and plan['gait_reference']['projection']['elevation_degrees']==16
    assert plan['frames'][4]['contacts']!={}
    run=root/'run';run.mkdir();manifest=gen_motion.prepare(path,run);assert manifest['contract']==5
    fid=result['motions']['walk']['independent_single_supports']['A']
    local=run/'poses'/('pose-'+fid)/'input-review.json'
    d=json.loads(local.read_text());d['reviewer']='synthetic offline'
    for x in d['checks'].values():x.update(status='pass',observation='OFFLINE fixture, no image quality claim')
    local.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='input-review|full sequence'):gen_motion._require_input_review(run,manifest)
    blocked=copy.deepcopy(manifest);blocked['plan']['gait_reference']['bundle_contract']='blocked'
    with pytest.raises(ValueError,match='ALL generation'):gen_motion._require_input_review(run,blocked,fid)
    global_review=json.loads((run/'input-review.json').read_text());global_review['reviewer']='offline synthetic full input review'
    for x in global_review['checks'].values():x.update(status='pass',observation='offline fixture, no artwork approval')
    (run/'input-review.json').write_text(json.dumps(global_review))
    seen=[]
    def runner(prompt,out,refs,report,**kw):
        seen.append(refs);im=Image.new('RGBA',(1024,1024));ImageDraw.Draw(im).rectangle((200,100,700,900),fill='tan');im.save(out);report.write_text('{"provider":"codex","offline":true}');return 0
    first=gen_motion.generate(run,manifest,fid,runner=runner)
    assert first['status']=='awaiting_review' and 'diagnostic' in first['geometry']
    assert [p.name for p in seen[0]]==['guide.png','identity.png','gait-visible-depth.png','gait-amodal-xray.png']
    folder=run/'poses'/('pose-'+fid)/'attempt-01';d=json.loads((folder/'review.json').read_text());d['reviewer']='offline'
    for x in d['checks'].values():x.update(status='fail',observation='offline rejection')
    (folder/'review.json').write_text(json.dumps(d))
    # Contract 5: unmeasured guide geometry no longer blocks a visual retry.
    gen_motion.generate(run,manifest,fid,retry=True,runner=runner)
    assert len(seen)==2 and 'Previous candidate rejected' in (folder.parent/'attempt-02'/'prompt.txt').read_text()
    second=folder.parent/'attempt-02'/'review.json';d=json.loads(second.read_text());d['reviewer']='offline'
    for x in d['checks'].values():x.update(status='fail',observation='offline rejection')
    second.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='two attempts exhausted'):gen_motion.generate(run,manifest,fid,retry=True,runner=runner)
    assert len(seen)==2
    bound=run/'poses'/('pose-'+fid)/'gait-visible-depth.png';original=bound.read_bytes();bound.write_bytes(original+b'x')
    with pytest.raises(ValueError,match='prepared input changed'):gen_motion.prepare(path,run)

def test_source_mutation_is_detected(prepared):
    root,_=prepared;path=root/'prepared/walk-plan.json';plan=json.loads(path.read_text());p=root/'prepared/derived/walk/depth-00.npy';old=p.read_bytes()
    try:
        p.write_bytes(old+b'x')
        with pytest.raises(ValueError,match='gait source changed'):gait_inputs.verify(path,plan)
    finally:p.write_bytes(old)

def geometry_fixture(shift_b=0):
    im=Image.new('RGBA',(256,256));d=ImageDraw.Draw(im);d.rectangle((50,10,190,115),fill='tan')
    body={'head_l':[60,20],'head_r':[180,30],'waist_l':[65,100],'waist_r':[180,110]}
    feet={};joints={};ground={'kind':'orthographic-world-plane','feet':{}}
    for leg,x,intercept,dy in [('A',20,100,0),('B',70,120,shift_b)]:
        heel=[x,.2*x+intercept+dy];toe=[x+20,.2*(x+20)+intercept+dy]
        poly=[[x,heel[1]-6],[x+20,toe[1]-6],toe,heel];d.polygon([tuple(p) for p in poly],fill='brown')
        feet[leg]={'outline':poly,'heel':heel,'toe':toe};joints[leg+'_heel']=[x,.2*x+intercept];joints[leg+'_toe']=[x+20,.2*(x+20)+intercept]
        ground['feet'][leg]={'slope':.2,'intercept':intercept}
    frame={'id':'a','body_landmarks':body,'joints':joints,'contacts':{'A':['heel','toe'],'B':['heel','toe']},'gait':{'ground':ground}}
    evidence={'binding':{'output_sha256':'hash','run_fingerprint':'plan','frame':'a'},'status':'observed','reviewer':'synthetic','observation':'offline fixture','body_landmarks':body,'feet':feet}
    return im,{'canvas':[256,256],'ground_y':112},frame,evidence

def test_two_different_projected_ground_heights_pass_without_bottom_alignment():
    im,p,f,e=geometry_fixture();r=pose_geometry.audit(im,p,f,e,'hash','plan')
    assert r['status']=='pass' and r['fit']['scale']==1 and r['fit']['translation']==[0,0]
    assert r['raw']['feet']['A']['bottom_y']!=r['raw']['feet']['B']['bottom_y']

def test_independent_foot_penetration_cannot_drive_body_transform():
    im,p,f,e=geometry_fixture(5);r=pose_geometry.audit(im,p,f,e,'hash','plan')
    assert r['status']=='blocked_pose_or_shape' and 'B:projected_ground_penetration' in r['issues']
    assert r['fit']['scale']==1 and r['fit']['translation']==[0,0]

def test_hidden_heel_is_declared_unknown_and_never_a_pass():
    im,p,f,e=geometry_fixture();e['feet']['B'].update(heel=None,hidden=['heel'])
    r=pose_geometry.audit(im,p,f,e,'hash','plan')
    assert r['status']=='unverified_hidden' and r['unknown']==['B:heel'] and not r['issues']
    assert set(r['raw']['feet']['B']['target_error'])=={'toe'}
    whole=geometry_fixture()[3];whole['feet']['B']={'outline':[],'heel':None,'toe':None,'hidden':['heel','toe']}
    r=pose_geometry.audit(im,p,f,whole,'hash','plan')
    assert r['status']=='unverified_hidden' and r['unknown']==['B:heel','B:toe']

def test_hidden_points_are_declared_never_guessed_and_cannot_hide_contradictions():
    im,p,f,e=geometry_fixture()
    for change in (lambda e:e['feet']['B'].update(heel=None),lambda e:e['feet']['B'].update(hidden=['heel'])):
        bad=copy.deepcopy(e);change(bad)
        with pytest.raises(ValueError,match='never guess'):pose_geometry.audit(im,p,f,bad,'hash','plan')
    im,p,f,e=geometry_fixture(5);e['feet']['A'].update(heel=None,hidden=['heel'])
    r=pose_geometry.audit(im,p,f,e,'hash','plan')
    assert r['status']=='blocked_pose_or_shape' and 'B:projected_ground_penetration' in r['issues']

def coverage_plan(contacts):
    frames=[]
    for i,(a,b) in enumerate(contacts):
        joints={f'{leg}_{k}':[100.*i+(0 if k=='heel' else 40),900.] for leg in 'AB' for k in ('heel','toe')}
        frames.append({'id':f'f{i}','contacts':{'A':['heel','toe'] if a else [],'B':['heel','toe'] if b else []},'joints':joints,'gait':{}})
    return {'canvas':[1024,1024],'frames':frames}

def foot_report(plan,i,residual=0.,clearance=0.,shift=0.,hidden=(),body_error=0.):
    feet={}
    for leg in 'AB':
        joints=plan['frames'][i]['joints'];pts={k:[joints[f'{leg}_{k}'][0]+shift,joints[f'{leg}_{k}'][1]] for k in ('heel','toe') if k not in hidden}
        feet[leg]={'points':pts,'hidden':list(hidden),'clearance':clearance,'projected_contact_residual':{k:residual for k in pts}}
    issues=['body:shape_or_pose_mismatch'] if body_error>.012*1024 else []
    return {'issues':issues,'fit':{'scale':1.,'translation':[0.,0.],'max_body_error':body_error},'registered_prediction':{'feet':feet}}

def test_contact_coverage_judges_stance_intervals_not_single_hidden_heels():
    walk=coverage_plan([(1,0),(1,1),(0,1),(0,1),(1,1),(1,0)])
    none=pose_geometry.contact_coverage(walk,{f['id']:None for f in walk['frames']})
    assert none['status']=='unverified'
    a=next(r for r in none['intervals'] if r['kind']=='A-stance')
    assert a['frames']==['f4','f5','f0','f1'] # one planted foot across the loop seam
    # Heels hidden everywhere: toes alone still evidence each interval; never "pass by default".
    toes={f['id']:foot_report(walk,i,hidden=('heel',)) for i,f in enumerate(walk['frames'])}
    assert pose_geometry.contact_coverage(walk,toes)['status']=='pass'
    sparse={**{f['id']:None for f in walk['frames']},'f1':foot_report(walk,1)}
    assert pose_geometry.contact_coverage(walk,sparse)['status']=='unverified'
    lifted={**toes,'f5':foot_report(walk,5,residual=20.)}
    report=pose_geometry.contact_coverage(walk,lifted)
    assert report['status']=='fail' and 'f5' in next(r for r in report['intervals'] if r['kind']=='A-stance')['contradictions']
    slid={**toes,'f0':foot_report(walk,0,shift=25.,hidden=('heel',))}
    report=pose_geometry.contact_coverage(walk,slid)
    a=next(r for r in report['intervals'] if r['kind']=='A-stance')
    assert report['status']=='fail' and 'slip' in a['contradictions'] and a['max_slip_px']==25.

def test_contact_coverage_keeps_frames_whose_body_error_only_grazes_the_registration_limit():
    walk=coverage_plan([(1,0),(1,1),(0,1),(0,1),(1,1),(1,0)])
    # Measured on run02: 12.4/11.5/10.7px by annotator around the 12.29px (1.2%) limit.
    grazing={f['id']:foot_report(walk,i,body_error=12.4) for i,f in enumerate(walk['frames'])}
    assert all(r['issues'] for r in grazing.values())
    report=pose_geometry.contact_coverage(walk,grazing)
    assert report['status']=='pass' and report['body_tolerance_px']==.024*1024
    gross={f['id']:foot_report(walk,i,body_error=30.) for i,f in enumerate(walk['frames'])}
    report=pose_geometry.contact_coverage(walk,gross)
    assert report['status']=='unverified'
    assert all(row['reason'].startswith('body landmarks disagree') for r in report['intervals'] for row in r['evidence'])

def test_contact_coverage_needs_a_visible_gap_in_each_flight():
    run=coverage_plan([(1,0),(0,0),(0,1),(0,0)])
    clear={f['id']:foot_report(run,i,clearance=0. if any(f['contacts'].values()) else 30.) for i,f in enumerate(run['frames'])}
    assert pose_geometry.contact_coverage(run,clear)['status']=='pass'
    assert pose_geometry.contact_coverage(run,{**clear,'f3':foot_report(run,3,clearance=2.)})['status']=='fail'
    assert pose_geometry.contact_coverage(run,{**clear,'f3':foot_report(run,3,clearance=30.,hidden=('heel',))})['status']=='unverified'

def test_contract5_export_waits_for_sequence_review_and_contact_coverage(prepared):
    root,_=prepared;path=root/'prepared/walk-plan.json'
    run=root/'run-coverage';run.mkdir();manifest=gen_motion.prepare(path,run)
    def passed(p):
        d=json.loads(p.read_text());d['reviewer']='offline synthetic'
        for x in d['checks'].values():x.update(status='pass',observation='offline fixture, no artwork approval')
        p.write_text(json.dumps(d))
    passed(run/'input-review.json')
    frames=manifest['plan']['frames']
    for f in frames:passed(run/'poses'/('pose-'+f['id'])/'input-review.json')
    def runner(prompt,out,refs,report,**kw):
        i=int(out.parent.parent.name.rsplit('-',1)[1])
        im=Image.new('RGBA',(1024,1024));ImageDraw.Draw(im).rectangle((200+i,100,700,900),fill='tan');im.save(out)
        report.write_text('{"provider":"codex","offline":true}');return 0
    done=set()
    while len(done)<len(frames):
        for f in frames:
            if f['id'] in done or not set(f['anchors'])<=done:continue
            result=gen_motion.generate(run,manifest,f['id'],runner=runner);passed(Path(result['review']));done.add(f['id'])
    first=gen_motion.export_asset(run,manifest)
    assert first['status']=='awaiting_sequence_review' and first['contact_coverage']=='unverified'
    passed(run/'sequence-review.json')
    second=gen_motion.export_asset(run,manifest)
    assert second['status']=='contact_coverage_unverified' and not (run/'asset.json').exists()
