import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import cv2

from collision_warning.lanes import LaneEstimator
from collision_warning.lane_metrics import corridor_metrics, sample_boundary, tusimple_frame
from tools.benchmark_lanes import fixture


class LaneTests(unittest.TestCase):
    def test_curved_and_dashed_lane_geometry(self):
        for curve in (-.06, 0, .06):
            frame, left, right, boxes = fixture(curve=curve, dashed=True, occluded=True)
            lane = LaneEstimator().update(frame, boxes=boxes)
            self.assertEqual(lane.status, 'detected')
            self.assertGreater(corridor_metrics(lane.polygon,left+right[::-1],960,540), .80)
            rows = np.linspace(.7*539,.9*539,20)
            for predicted, reference in ((lane.left,left),(lane.right,right)):
                self.assertLess(np.mean(np.abs(sample_boundary(predicted,rows)-sample_boundary(reference,rows))),5.)

    def test_blank_frame_no_fabricated_lane_and_hold_expiry(self):
        blank = np.zeros((540,960,3),np.uint8)
        estimator=LaneEstimator()
        self.assertEqual(estimator.update(blank,0).polygon,[])
        image,*_=fixture()
        detected=estimator.update(image,.1)
        held=estimator.update(blank,.2)
        self.assertEqual(held.status,'held')
        self.assertLess(held.confidence,detected.confidence)
        self.assertEqual(estimator.update(blank,.5).status,'unavailable')
        self.assertEqual(estimator.update(blank,.6).polygon,[])

    def test_large_jump_is_rejected_then_reacquired(self):
        estimator=LaneEstimator()
        frame,*_=fixture()
        original=estimator.detect(frame)
        shifted={**original,'left_coef':original['left_coef']+np.array([0,0,.2]),'right_coef':original['right_coef']+np.array([0,0,.2])}
        with patch.object(estimator,'detect',side_effect=[original,shifted,shifted,shifted]):
            self.assertEqual(estimator.update(frame,0).status,'detected')
            self.assertEqual(estimator.update(frame,.1).status,'held')
            self.assertEqual(estimator.update(frame,.5).status,'unavailable')
            self.assertEqual(estimator.update(frame,.6).status,'detected')

    def test_masked_vehicle_has_no_lane_evidence(self):
        frame,*_=fixture()
        mask=LaneEstimator.evidence(frame,[[0,300,960,520]])
        self.assertEqual(np.count_nonzero(mask[303:517]),0)


class MetricTests(unittest.TestCase):
    def test_tusimple_command_exports_all_samples_and_penalizes_adjacent_gt(self):
        from tools.evaluate_tusimple import main
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            image,left,right,_=fixture()
            cv2.imwrite(str(root/'lane.jpg'),image)
            cv2.imwrite(str(root/'blank.jpg'),np.zeros_like(image))
            rows=list(range(330,501,10))
            lanes=[[int(x) for x in sample_boundary(points,rows)] for points in (left,right)]
            lanes.append([900]*len(rows))
            labels=root/'labels.jsonl'
            labels.write_text('\n'.join(json.dumps({'raw_file':name,'lanes':lanes,'h_samples':rows}) for name in ('lane.jpg','blank.jpg')),encoding='utf-8')
            with patch('sys.argv',['evaluate','--root',str(root),'--labels',str(labels),'--output',str(root/'out')]), contextlib.redirect_stdout(io.StringIO()):
                main()
            report=json.loads((root/'out/summary.json').read_text())
            self.assertEqual(report['samples'],2)
            self.assertGreater(report['methods']['auto_lane']['FN'],0.)
            predictions=[json.loads(line) for line in (root/'out/auto_lane.jsonl').read_text().splitlines()]
            self.assertEqual([p['raw_file'] for p in predictions],['lane.jpg','blank.jpg'])
            self.assertEqual(predictions[1]['lanes'],[])

    def test_missing_corridor_is_penalized(self):
        square=[[2,2],[18,2],[18,18],[2,18]]
        self.assertEqual(corridor_metrics(square,square,20,20),1.)
        self.assertEqual(corridor_metrics([],square,20,20),0.)
        self.assertEqual(sample_boundary([[10,10],[20,20]],[0,15,30]).tolist(),[-2,15,-2])

    def test_tusimple_known_perfect_missing_and_extra(self):
        truth=[[100]*10,[300]*10];rows=list(range(10))
        self.assertEqual(tusimple_frame(truth,truth,rows,10),(1.,0.,0.))
        self.assertEqual(tusimple_frame([],truth,rows,10),(0.,0.,1.))
        self.assertEqual(tusimple_frame([truth[0]],truth,rows,10),(.5,0.,.5))
        self.assertEqual(tusimple_frame(truth+[[500]*10],truth,rows,10),(1.,1/3,0.))
        self.assertEqual(tusimple_frame(truth,truth,rows,201),(0.,0.,1.))

    def test_tusimple_threshold_is_strict_and_requires_85_percent(self):
        rows=list(range(20));truth=[[100]*20]
        self.assertEqual(tusimple_frame([[120]*20],truth,rows,1),(0.,1.,1.))
        self.assertEqual(tusimple_frame([[100]*17+[150]*3],truth,rows,1),(.85,0.,0.))
        self.assertEqual(tusimple_frame([[100]*16+[150]*4],truth,rows,1),(.8,1.,1.))

    def test_tusimple_five_lane_rule(self):
        rows=list(range(10));truth=[[100*i]*10 for i in range(1,6)]
        self.assertEqual(tusimple_frame(truth[:4],truth,rows,1),(1.,0.,0.))


if __name__=='__main__':
    unittest.main()
