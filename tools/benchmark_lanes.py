"""Compare fixed ROI to image-based lanes on reproducible synthetic geometry.

This small regression fixture is NOT a public dataset accuracy measurement.
Run: python tools/benchmark_lanes.py --output outputs/benchmarks/synthetic
"""
import argparse
import csv
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from collision_warning.lanes import LaneEstimator
from collision_warning.lane_metrics import corridor_metrics, sample_boundary


def fixture(shift=0., curve=0., dashed=False, occluded=False, seed=0):
    h, w = 540, 960
    rng = np.random.default_rng(seed)
    frame = np.clip(rng.normal(65, 3, (h, w, 1)), 0, 255).astype(np.uint8)
    frame = np.repeat(frame, 3, axis=2)
    frame[:round(.53*h)] = (155, 120, 90)
    rows = np.linspace(.60, .94, 120)
    perspective = (rows - .55) / .39
    center = .5 + shift * perspective + curve * perspective**2
    half_width = .045 + .265 * perspective
    left = np.column_stack(((center-half_width)*(w-1), rows*(h-1)))
    right = np.column_stack(((center+half_width)*(w-1), rows*(h-1)))
    cv2.polylines(frame, [np.rint(left).astype(np.int32)], False, (0, 220, 245), 6)
    if dashed:
        for start in range(0, len(rows)-1, 24):
            cv2.polylines(frame, [np.rint(right[start:start+13]).astype(np.int32)], False, (235, 235, 235), 6)
    else:
        cv2.polylines(frame, [np.rint(right).astype(np.int32)], False, (235, 235, 235), 6)
    boxes = []
    if occluded:
        boxes = [[w*.45, h*.60, w*.60, h*.77]]
        cv2.rectangle(frame, (int(w*.45), int(h*.60)), (int(w*.60), int(h*.77)), (180,180,180), -1)
    return frame, left.tolist(), right.tolist(), boxes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='outputs/benchmarks/synthetic')
    args = parser.parse_args()
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    records, tiles = [], []
    configurations = [(shift, curve, dashed, occ) for shift, curve in [(0.,0.),(-.06,0.),(.06,0.),(0.,.06),(0.,-.06),(.02,.04)] for dashed, occ in [(False,False),(True,True)]]
    for number, (shift, curve, dashed, occluded) in enumerate(configurations):
        frame, left, right, boxes = fixture(shift, curve, dashed, occluded, seed=number)
        h,w = frame.shape[:2]
        reference = left + right[::-1]
        baseline = [[x*(w-1),y*(h-1)] for x,y in [[.45,.55],[.57,.55],[.85,.98],[.16,.98]]]
        begin = time.perf_counter(); lane = LaneEstimator().update(frame, boxes=boxes)
        milliseconds = (time.perf_counter()-begin)*1000
        rows = np.linspace(.62*(h-1), .92*(h-1), 40)
        for name, polygon, boundaries, runtime in [('fixed_roi',baseline,([baseline[0],baseline[3]],[baseline[1],baseline[2]]),0.),('auto_lane',lane.polygon,(lane.left,lane.right),milliseconds)]:
            errors, valid_points = [], 0
            for prediction, gt in zip(boundaries,(left,right)):
                pred = sample_boundary(prediction,rows); truth = sample_boundary(gt,rows)
                valid = (pred>=0)&(truth>=0); valid_points += int(valid.sum())
                errors.extend(np.abs(pred[valid]-truth[valid]).tolist())
            records.append(dict(case=number, method=name, shift=shift, curve=curve, dashed=dashed, occluded=occluded,
                                available=bool(polygon), corridor_iou=corridor_metrics(polygon,reference,w,h),
                                boundary_mae_px=float(np.mean(errors)) if errors else None,
                                sampled_row_coverage=valid_points/(2*len(rows)), runtime_ms=runtime))
        tile=frame.copy()
        cv2.polylines(tile,[np.rint(baseline).astype(np.int32)],True,(50,70,240),2)
        if lane.polygon:
            cv2.polylines(tile,[np.rint(lane.polygon).astype(np.int32)],True,(70,240,70),2)
        cv2.putText(tile,f'Case {number}: red=fixed, green=auto', (15,35),cv2.FONT_HERSHEY_SIMPLEX,.7,(255,255,255),2)
        tiles.append(cv2.resize(tile,(480,270)))
    summary = {'scope':'12 procedural daylight fixtures; geometry regression only; NOT TuSimple or real-world accuracy', 'seed':0, 'width':960,'height':540,'methods':{}}
    for method in ('fixed_roi','auto_lane'):
        group=[item for item in records if item['method']==method]
        valid=[item for item in group if item['boundary_mae_px'] is not None]
        summary['methods'][method]={'mean_corridor_iou_all_frames':float(np.mean([x['corridor_iou'] for x in group])),
                                   'boundary_mae_px_conditional':float(np.mean([x['boundary_mae_px'] for x in valid])) if valid else None,
                                   'frame_coverage':float(np.mean([x['available'] for x in group])),
                                   'sampled_row_coverage':float(np.mean([x['sampled_row_coverage'] for x in group])),
                                   'mean_lane_only_ms':float(np.mean([x['runtime_ms'] for x in group])) if method=='auto_lane' else None}
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    with (output/'frames.csv').open('w',newline='',encoding='utf-8') as target:
        writer=csv.DictWriter(target,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    cv2.imwrite(str(output/'comparison.jpg'),np.vstack([np.hstack(tiles[i:i+3]) for i in range(0,len(tiles),3)]))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
