# SPDX-License-Identifier: Apache-2.0
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import numpy as np
from sprite_gen.scene.gait import audit, ik_two_bone, sample, run
from sprite_gen.scene.gait_render import Raster, occlusion_evidence, render_proxy

class GaitTests(unittest.TestCase):
    def setUp(self):
        self.profile=json.loads((Path(__file__).resolve().parents[2]/'examples/gait-mocha.json').read_text())

    def test_dense_contact_cycle_and_lengths(self):
        for motion in ('walk','run'):
            with self.subTest(motion=motion):
                report=audit(self.profile,motion)
                self.assertEqual(report['status'],'pass',report)
                for key in ('max_stance_world_drift','max_bone_length_error','max_loop_error','max_half_cycle_exchange_error'):
                    self.assertLess(report[key],1e-10)
                self.assertGreater(report['max_swing_clearance'],.1)

    def test_stance_is_locked_in_world_not_screen(self):
        a=sample(self.profile,'walk',.02);b=sample(self.profile,'walk',.2)
        self.assertEqual(a['legs']['A']['sole'],b['legs']['A']['sole'])
        self.assertGreater(b['root'][0],a['root'][0])
        self.assertLess(b['legs']['A']['sole'][0]-b['root'][0],a['legs']['A']['sole'][0]-a['root'][0])

    def test_periodic_for_negative_time_and_cycle_boundaries(self):
        for time in (-2.7,-.0001,0,.0001,1.28,3.7):
            with self.subTest(time=time):
                a=sample(self.profile,'walk',time);b=sample(self.profile,'walk',time+1.28)
                for leg in ('A','B'):
                    for part in ('hip','knee','ankle','heel','toe'):
                        np.testing.assert_allclose(np.array(a['legs'][leg][part])-a['root'],np.array(b['legs'][leg][part])-b['root'],atol=1e-10)

    def test_ik_rejects_impossible_targets_without_clamping(self):
        for target in ([4,0,0],[0,0,1],[0,1,0]):
            with self.subTest(target=target),self.assertRaises(ValueError):ik_two_bone([0,0,1],target,.4,.4)

    def test_knee_uses_forward_pole(self):
        knee=ik_two_bone([0,0,1],[0,0,.3],.4,.4)
        self.assertGreater(knee[0],0)
        self.assertAlmostEqual(math.dist(knee,[0,0,1]),.4)
        self.assertAlmostEqual(math.dist(knee,[0,0,.3]),.4)

    def test_bad_profiles_fail(self):
        for mutation in ('run_no_flight','walk_flight','overlap_feet','unreachable','nonfinite'):
            self.setUp();p=self.profile
            if mutation=='run_no_flight':p['motions']['run']['duty']=.6
            if mutation=='walk_flight':p['motions']['walk']['duty']=.4
            if mutation=='overlap_feet':p['body']['foot_width']=.6
            if mutation=='unreachable':p['motions']['walk']['hip_height']=2
            if mutation=='nonfinite':p['body']['upper_leg']=float('nan')
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):audit(p,'walk')

    def test_depth_is_geometric_and_draw_order_independent(self):
        images=[]
        for order in ((1,2),(2,1)):
            r=Raster(yaw=50)
            for part in order:
                center=np.array([0,0,1])+r.d*(.3 if part==2 else -.3)
                r.shape(center,[.3,.3,.3],part)
            self.assertTrue(np.all(r.ids[r.ids>0]==2))
            self.assertEqual(occlusion_evidence(r)['wrong_depth_winner_pixels'],0)
            images.append(r.ids)
        np.testing.assert_array_equal(*images)

    def test_box_has_flat_ground_sole(self):
        r=Raster(yaw=50);r.shape([0,0,.1],[.4,.2,.1],1,box=True)
        ys,xs=np.where(r.ids==1)
        self.assertEqual(ys.max(),r.origin[1])

    def test_actual_proxy_renders_occlusion_without_clipping(self):
        for motion in ('walk','run'):
            total=0
            for phase in (0,.125,.25,.375,.5,.625,.75,.875):
                r=render_proxy(self.profile,sample(self.profile,motion,phase*self.profile['motions'][motion]['period']))
                e=occlusion_evidence(r)
                self.assertFalse(e['clipped'])
                self.assertEqual(e['wrong_depth_winner_pixels'],0)
                self.assertGreater(e['A_visible_pixels'],0);self.assertGreater(e['B_visible_pixels'],0)
                total+=e['leg_overlap_pixels']
            self.assertGreater(total,0,'Test must exercise overlapping projected legs')

    def test_refuses_overwriting_accepted_output(self):
        with TemporaryDirectory() as folder:
            d=Path(folder);source=d/'profile.json';source.write_text(json.dumps(self.profile));output=d/'out';output.mkdir()
            with self.assertRaisesRegex(ValueError,'new directory'):run(profile=source,out_dir=output,motion='walk')


    def test_subpixel_flight_is_not_a_renderable_run(self):
        from sprite_gen.scene.gait import screen_audit
        p=self.profile;p['camera'].update(pixels_per_unit=163.125,elevation_degrees=16)
        p['motions']['run'].update(duty=.4,clearance=.22,flight_lift=0)
        r=screen_audit(p,'run',[(i+.5)/16 for i in range(49)])
        self.assertEqual(r['status'],'fail')
        self.assertLess(r['max_simultaneous_clearance_px'],1)
        self.assertEqual(r['resolved_flight_count'],0)
        self.assertEqual(audit(p,'run')['status'],'fail')

    def test_wider_flight_is_resolved_at_actual_sampling(self):
        from sprite_gen.scene.gait import screen_audit
        p=self.profile;p['camera'].update(pixels_per_unit=163.125,elevation_degrees=16)
        p['motions']['run'].update(duty=.25,clearance=.22,flight_lift=0)
        for times in (None,[(i+.5)/16 for i in range(49)]):
            r=screen_audit(p,'run',times)
            self.assertEqual(r['status'],'pass',r)
            self.assertGreaterEqual(r['resolved_flight_count'],2)

    def test_flight_lift_makes_a_jog_duty_readable_without_breaking_kinematics(self):
        from sprite_gen.scene.gait import screen_audit
        p=self.profile;p['camera'].update(pixels_per_unit=163.125,elevation_degrees=16)
        p['motions']['run'].update(duty=.4,clearance=.22,flight_lift=.15)
        r=screen_audit(p,'run',[(i+.5)/16 for i in range(49)])
        self.assertEqual(r['status'],'pass',r)
        self.assertGreater(r['max_simultaneous_clearance_px'],16)
        report=audit(p,'run')
        self.assertEqual(report['status'],'pass',report['checks'])
        # Lift exists only while BOTH feet are airborne; stance stays on the ground.
        for i in range(400):
            s=sample(p,'run',i*p['motions']['run']['period']/400)
            for f in s['legs'].values():
                if f['stance']:self.assertEqual(f['sole'][2],0.)

    def test_flight_lift_is_run_only_and_bounded(self):
        for motion,value in (('walk',.1),('run',-.1),('run',1.),('run',float('nan'))):
            self.setUp();self.profile['motions'][motion]['flight_lift']=value
            with self.subTest(motion=motion,value=value),self.assertRaises(ValueError):audit(self.profile,'run')

    def test_sampling_and_final_scale_are_not_ignored(self):
        from sprite_gen.scene.gait import screen_audit
        p=self.profile
        self.assertEqual(screen_audit(p,'run',[0,.42])['status'],'fail')
        p['camera']['pixels_per_unit']=21
        self.assertEqual(screen_audit(p,'run')['status'],'fail')
        for times in ([],[float('nan')],[0,0]):
            with self.assertRaises(ValueError):screen_audit(p,'run',times)

if __name__=='__main__':unittest.main()
