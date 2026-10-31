# SPDX-License-Identifier: Apache-2.0
"""Orthographic analytic depth renderer for authored gait proxies (not sprites).

Ray/ellipsoid and ray/box intersection provide per-pixel occlusion. Colors and
draw order never determine physical leg identity. No source artwork is edited.
"""
from __future__ import annotations
import base64
import hashlib
import json
import math

from sprite_gen._deps import np
from PIL import Image, ImageDraw, ImageFont

COLORS={1:(239,126,46),2:(42,133,209),3:(194,137,67),4:(214,162,85),5:(39,133,143),6:(110,54,66),7:(100,72,48),8:(40,35,30)}


class Raster:
    def __init__(self,width=320,height=420,scale=155.,yaw=50.,elevation=0.,origin=None):
        self.width,self.height,self.scale=width,height,scale
        theta=math.radians(yaw)
        self.u=np.array([math.cos(theta),math.sin(theta),0.])
        elev=math.radians(elevation)
        self.v=np.array([-math.sin(theta)*math.sin(elev),math.cos(theta)*math.sin(elev),math.cos(elev)])
        self.d=np.cross(self.u,self.v)
        self.origin=np.array([width*.45,height-45.] if origin is None else origin,dtype=float)
        self.depth=np.full((height,width),-np.inf)
        self.ids=np.zeros((height,width),np.uint8)
        self.rgb=np.full((height,width,3),246,np.uint8)
        self.amodal={1:np.zeros((height,width),bool),2:np.zeros((height,width),bool)}
        self.leg_depth={1:np.full((height,width),-np.inf),2:np.full((height,width),-np.inf)}
        self.clipped=False

    def project(self,p):
        p=np.asarray(p)
        return np.array([self.origin[0]+self.scale*np.dot(p,self.u),self.origin[1]-self.scale*np.dot(p,self.v)])

    def shape(self,center,radii,part,rotation=None,box=False):
        center=np.asarray(center,float);radii=np.asarray(radii,float)
        rot=np.eye(3) if rotation is None else np.asarray(rotation)
        c=self.project(center)
        ext=np.array([np.sum(np.abs(self.u@rot)*radii),np.sum(np.abs(self.v@rot)*radii)])*self.scale
        lo=np.floor(c-ext-1).astype(int);hi=np.ceil(c+ext+2).astype(int)
        self.clipped |= bool(np.any(lo<0) or hi[0]>self.width or hi[1]>self.height)
        x0,y0=np.maximum(lo,[0,0]);x1,y1=np.minimum(hi,[self.width,self.height])
        if x0>=x1 or y0>=y1:return
        yy,xx=np.mgrid[y0:y1,x0:x1]
        world=((xx-self.origin[0])/self.scale)[...,None]*self.u+((self.origin[1]-yy)/self.scale)[...,None]*self.v
        o=(world-center)@rot;d=self.d@rot
        if box:
            lower=np.full(xx.shape,-np.inf);upper=np.full(xx.shape,np.inf);valid=np.ones(xx.shape,bool)
            for axis in range(3):
                if abs(d[axis])<1e-12:valid &= np.abs(o[...,axis])<=radii[axis]
                else:
                    t1=(-radii[axis]-o[...,axis])/d[axis];t2=(radii[axis]-o[...,axis])/d[axis]
                    lower=np.maximum(lower,np.minimum(t1,t2));upper=np.minimum(upper,np.maximum(t1,t2))
            hit=valid & (lower<=upper);depth=np.where(hit,upper,0.)
            local=o+depth[...,None]*d
            axis=np.argmax(np.abs(local/radii),axis=-1)
            normal=np.eye(3)[axis]*np.take_along_axis(np.sign(local),axis[...,None],axis=-1)
        else:
            od=o/radii;dd=d/radii
            a=float(dd@dd);b=np.sum(od*dd,axis=-1);cc=np.sum(od*od,axis=-1)-1
            disc=b*b-a*cc;hit=disc>=0
            depth=(-b+np.sqrt(np.maximum(0,disc)))/a
            local=o+depth[...,None]*d
            normal=local/(radii*radii)
        if part in self.amodal:
            self.amodal[part][y0:y1,x0:x1] |= hit
            ld=self.leg_depth[part][y0:y1,x0:x1]
            np.maximum(ld,np.where(hit,depth,-np.inf),out=ld)
        normal=normal@rot.T
        normal/=np.maximum(np.linalg.norm(normal,axis=-1,keepdims=True),1e-10)
        light=np.array([-.35,-.45,.82]);light/=np.linalg.norm(light)
        shade=.68+.32*np.maximum(0,normal@light)
        color=np.clip(shade[...,None]*np.array(COLORS[part]),0,255).astype(np.uint8)
        dest=self.depth[y0:y1,x0:x1];visible=hit&(depth>dest)
        dest[visible]=depth[visible]
        self.ids[y0:y1,x0:x1][visible]=part
        self.rgb[y0:y1,x0:x1][visible]=color[visible]

    def bone(self,a,b,radius,part):
        a,b=np.asarray(a),np.asarray(b);axis=b-a;length=np.linalg.norm(axis);axis/=length
        across=np.array([-axis[2],0.,axis[0]]);across/=np.linalg.norm(across)
        rot=np.column_stack([across,np.cross(axis,across),axis])
        self.shape((a+b)/2,[radius,radius,length/2+radius*.2],part,rot)
        for p in (a,b):self.shape(p,[radius]*3,part)


def render_proxy(profile,state,*,width=320,height=420,origin=None,pixel_scale=1.):
    b=profile["body"];cam=profile["camera"]
    r=Raster(width=width,height=height,origin=origin,scale=cam["pixels_per_unit"]*pixel_scale,yaw=cam["yaw_degrees"],elevation=cam.get("elevation_degrees",0.))
    hip=state["hip_z"];q=state["phase"]
    r.shape([0,0,hip+b["torso_height"]],b["torso_radii"],3)
    r.shape([.1,0,hip+b["head_height"]],b["head_radii"],4)
    r.shape([b["muzzle_forward"],0,hip+b["muzzle_height"]],b["muzzle_radii"],7)
    for side in (-1,1):
        r.shape([-.03,side*.26,hip+b["head_height"]+.36],[.105,.075,.14],4)
        r.shape([.33,side*.31,hip+b["head_height"]+.06],[.045,.035,.022],8)
        r.shape([.17+side*.07*math.sin(2*math.pi*q),side*.49,hip+.57],[.23,.22,.25],5)
    r.shape([0,0,hip-.015],[b["torso_radii"][0]*.95,b["torso_radii"][1]*.96,.24],6)
    for leg,part in (("B",2),("A",1)):
        f=state["legs"][leg]
        local={k:np.asarray(f[k])-state["root"] for k in ("hip","knee","ankle","sole")}
        r.bone(local["hip"],local["knee"],b["thigh_radius"],part)
        r.bone(local["knee"],local["ankle"],b["shin_radius"],part)
        # A flat sole box, not an ellipse whose heel/toe would float above ground.
        r.shape(local["sole"]+[b["foot_length"]*.25,0,b["foot_height"]/2],
                [b["foot_length"]/2,b["foot_width"]/2,b["foot_height"]/2],part,box=True)
    return r


def occlusion_evidence(r):
    overlap=r.amodal[1]&r.amodal[2]
    visible_overlap=overlap&np.isin(r.ids,[1,2])
    nearest=np.where(r.leg_depth[1]>r.leg_depth[2],1,2)
    mismatch=int(np.count_nonzero(visible_overlap&(r.ids!=nearest)))
    return {"leg_overlap_pixels":int(overlap.sum()),"visible_leg_overlap_pixels":int(visible_overlap.sum()),
      "wrong_depth_winner_pixels":mismatch,
      "A_visible_pixels":int((r.ids==1).sum()),"B_visible_pixels":int((r.ids==2).sum()),
      "A_occluded_pixels":int((r.amodal[1]&(r.ids!=1)).sum()),
      "B_occluded_pixels":int((r.amodal[2]&(r.ids!=2)).sum()),"clipped":r.clipped}


def _font(size):
    # Portable Pillow font: no dependency on user-specific font paths.
    return ImageFont.load_default(size=size)


def frame_board(profile,motion,state,r,index,count):
    board=Image.new("RGB",(960,540),"#edf1f5");d=ImageDraw.Draw(board)
    d.text((16,10),f"MOCHA PROXY | {motion.upper()} {index+1:02}/{count} | phase {state['phase']:.3f} | support {state['support']}",font=_font(20),fill="#172b42")
    for col,title in enumerate(("3D volume / visible surfaces","X-ray joints / both physical legs","Depth-tested part IDs")):
        d.text((col*320+8,44),title,font=_font(16),fill="#172b42")
        if col==0:panel=Image.fromarray(r.rgb)
        elif col==1:panel=Image.new("RGB",(320,420),"#f6f6f6")
        else:
            colors=np.array([(246,246,246),COLORS[1],COLORS[2],(170,174,181),(170,174,181),(170,174,181),(170,174,181),(170,174,181),(170,174,181)],np.uint8)
            panel=Image.fromarray(colors[r.ids])
        pd=ImageDraw.Draw(panel)
        floor=r.origin[1]
        elevated=bool(profile['camera'].get('elevation_degrees',0))
        if elevated:
            for lane in (-profile['body']['hip_half_width'],profile['body']['hip_half_width']):
                a,b=r.project([-3,lane,0]),r.project([3,lane,0]);pd.line((*a,*b),fill='#5d6877',width=2)
        else:pd.line((0,floor,320,floor),fill="#5d6877",width=2)
        # Fixed-world ticks scroll with camera, revealing foot locking in playback.
        root=state["root"][0]
        tick=profile["motions"][motion]["period"]*profile["motions"][motion]["speed"]/4
        for k in range(math.floor((root-4)/tick),math.ceil((root+4)/tick)):
            x,y=r.project([k*tick-root,0,0])
            if not elevated:y=floor
            pd.line((x,y,x,y+9),fill="#9da9b9")
        if col==1:
            for leg,part in (("B",2),("A",1)):
                f=state["legs"][leg];pts={key:tuple(r.project(np.asarray(f[key])-state["root"])) for key in ("hip","knee","ankle","heel","toe")}
                pd.line([pts[k] for k in ("hip","knee","ankle")],fill=COLORS[part],width=6)
                pd.line([pts["heel"],pts["toe"]],fill=COLORS[part],width=5)
                for p in pts.values():pd.ellipse((p[0]-4,p[1]-4,p[0]+4,p[1]+4),fill=COLORS[part])
                if f["stance"]:
                    for key in ("heel","toe"):
                        x,y=pts[key];pd.rectangle((x-6,y-6,x+6,y+6),outline="#1c9a6a",width=2)
                pd.text((pts["knee"][0]+8,pts["knee"][1]),leg,font=_font(18),fill=COLORS[part])
        board.paste(panel,(col*320,70))
    d.text((15,496),"A orange / B blue: fixed identities | green: locked contact | camera follows body; ground ticks are world-fixed",font=_font(15),fill="#172b42")
    d.text((15,519),"AUTHORED KINEMATIC GUIDE ONLY - no AI generation, no artwork cutouts, not a finished character animation",font=_font(14),fill="#7b3650")
    return board


def deliver(profile,states,out):
    evidence={};sections=[];inventory=[]
    for motion,seq in states.items():
        folder=out/motion;folder.mkdir();boards=[];records=[];duration=profile["motions"][motion]["period"]*1000
        # Integer millisecond durations sum to the exact requested period.
        durations=[round((i+1)*duration/len(seq))-round(i*duration/len(seq)) for i in range(len(seq))]
        for i,state in enumerate(seq):
            raster=render_proxy(profile,state);record=occlusion_evidence(raster);records.append(record)
            if record["clipped"] or record["wrong_depth_winner_pixels"]:
                raise ValueError("Clipped proxy or inconsistent depth ordering; preserve diagnostic directory")
            frame_board(profile,motion,state,raster,i,len(seq)).save(folder/f"frame-{i:02}.png")
            boards.append(Image.open(folder/f"frame-{i:02}.png").copy())
            Image.fromarray(raster.ids).save(folder/f"part-id-{i:02}.png")
            # Front-facing camera-space depth in authored units; background NaN.
            np.save(folder/f"depth-{i:02}.npy",np.where(np.isfinite(raster.depth),raster.depth,np.nan).astype(np.float32))
            for leg,part in (("A",1),("B",2)):
                Image.fromarray((raster.amodal[part]*255).astype(np.uint8)).save(folder/f"amodal-{leg}-{i:02}.png")
        for suffix,mult in (("",1),("-slow",3)):
            boards[0].save(out/f"{motion}{suffix}.webp",save_all=True,append_images=boards[1:],duration=[d*mult for d in durations],loop=0,lossless=True)
            boards[0].save(out/f"{motion}{suffix}.gif",save_all=True,append_images=boards[1:],duration=[round(d*mult/10)*10 for d in durations],loop=0,optimize=False,disposal=2)
        sheet=Image.new("RGB",(960,270*len(seq)),"white")
        for i,board in enumerate(boards):sheet.paste(board.resize((480,270)),((i%2)*480,(i//2)*270))
        # Two columns, no blank lower half.
        sheet.crop((0,0,960,math.ceil(len(seq)/2)*270)).save(out/f"{motion}-contact-sheet.png")
        folder.joinpath("poses.json").write_text(json.dumps({"kind":"authored-gait-poses","coordinates":"world XYZ; X forward, Y lateral, Z up","durations_ms":durations,"frames":seq},indent=2), encoding="utf-8")
        evidence[motion]={"frames":len(seq),"duration_ms":sum(durations),"per_frame":records,
                         "total_leg_overlap_pixels":sum(r["leg_overlap_pixels"] for r in records),
                         "wrong_depth_winner_pixels":sum(r["wrong_depth_winner_pixels"] for r in records),
                         "method":"analytic ray depth intersections; per-pixel nearest surface; X-ray is diagnostic only"}
        encoded=[base64.b64encode((folder/f"frame-{i:02}.png").read_bytes()).decode() for i in range(len(seq))]
        sections.append({"motion":motion,"period":duration,"images":encoded,"states":[s["support"] for s in seq]})
    payload=json.dumps(sections)
    page='''<!doctype html><html lang="ko"><meta charset="utf-8"><title>모카 보행 제어 가이드</title><style>body{font:16px system-ui;margin:24px;background:#edf1f5;color:#172b42}section{background:white;padding:16px;margin:20px 0;border-radius:12px;max-width:1100px}img{width:100%}input{width:60%}button,select{padding:8px;margin:8px}strong{color:#a1304d}</style><h1>모카 체형의 보행 제어 가이드</h1><p>연속 위상 + 2-Bone IK + 월드 좌표 발 고정. 주황 A / 파랑 B는 동일한 물리적 다리입니다. 가운데는 가려진 관절도 보이는 X-ray, 오른쪽은 깊이 검사 후 보이는 부위만 표시합니다.</p><p><strong>직접 설계한 대리 체형이며 원화의 3D 복원이나 생성된 최종 모카가 아닙니다. AI 호출 0회. 접지·교대·가려짐 검증용입니다.</strong></p><div id="root"></div><script>const data=PAYLOAD;for(const s of data){const el=document.createElement('section');el.innerHTML=`<h2>${s.motion} — ${s.images.length} frames</h2><img alt="${s.motion} guide"><div><button>일시정지</button><select><option value="1">정상 속도</option><option value="0.333">느리게</option></select><input type="range" min="0" max="${s.images.length-1}" value="0" step="1"><span></span></div>`;document.querySelector('#root').append(el);let running=true,phase=0,last=performance.now();const img=el.querySelector('img'),slider=el.querySelector('input'),label=el.querySelector('span'),button=el.querySelector('button'),speed=el.querySelector('select');function draw(i){img.src='data:image/png;base64,'+s.images[i];slider.value=i;label.textContent=` ${i+1}/${s.images.length} 지지: ${s.states[i]}`;}button.onclick=()=>{running=!running;button.textContent=running?'일시정지':'재생'};slider.oninput=()=>{running=false;button.textContent='재생';phase=+slider.value/s.images.length;draw(+slider.value)};function tick(t){if(running){phase=(phase+(t-last)*+speed.value/s.period)%1;draw(Math.floor(phase*s.images.length))}last=t;requestAnimationFrame(tick)}draw(0);requestAnimationFrame(tick);}</script></html>'''.replace('PAYLOAD',payload)
    (out/"preview.html").write_text(page,encoding="utf-8")
    for p in sorted(out.rglob("*")):
        if p.is_file():inventory.append({"file":str(p.relative_to(out)),"sha256":hashlib.sha256(p.read_bytes()).hexdigest()})
    (out/"file-hashes.json").write_text(json.dumps(inventory,indent=2), encoding="utf-8")
    return evidence
