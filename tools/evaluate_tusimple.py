"""Generate predictions and score all TuSimple GT lanes for fixed ROI and auto.

Use a locally obtained, labelled validation split. No download or training.
Ego-only models miss adjacent GT lanes: these are full-GT diagnostic scores.
Each sample is stateless (last annotated frame), identical protocol for both.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from collision_warning.lanes import LaneEstimator
from collision_warning.lane_metrics import sample_boundary, tusimple_frame


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--labels',type=Path,nargs='+',required=True)
    parser.add_argument('--output',type=Path,default=Path('outputs/benchmarks/tusimple'))
    args=parser.parse_args()
    samples=[json.loads(line) for path in args.labels for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not samples or len({x['raw_file'] for x in samples}) != len(samples):
        parser.error('Labels must contain nonempty unique raw_file entries')
    args.output.mkdir(parents=True,exist_ok=True)
    results={name:[] for name in ('fixed_roi','auto_lane')}
    for sample in samples:
        path=(args.root/sample['raw_file']).resolve()
        if not path.is_relative_to(args.root.resolve()):
            raise ValueError('Image path is outside dataset root')
        image=cv2.imread(str(path))
        if image is None:
            raise ValueError(f'Cannot read {path}; no partial split scoring')
        h,w=image.shape[:2]; rows=sample['h_samples']
        for method in results:
            begin=time.perf_counter()
            if method=='auto_lane':
                lane=LaneEstimator().update(image)
                boundaries=[lane.left,lane.right] if lane.polygon else []
            else:
                boundaries=[[[.45*(w-1),.55*(h-1)],[.16*(w-1),.98*(h-1)]],[[.57*(w-1),.55*(h-1)],[.85*(w-1),.98*(h-1)]]]
            predictions=[[int(round(x)) if 0<=x<w else -2 for x in sample_boundary(points,rows)] for points in boundaries]
            runtime=(time.perf_counter()-begin)*1000
            metrics=tusimple_frame(predictions,sample['lanes'],rows,runtime)
            results[method].append({'raw_file':sample['raw_file'],'lanes':predictions,'run_time':runtime,'metrics':metrics})
    report={'scope':'All GT lanes; ego-only predictions; no training; independent Python3 evaluator. NOT an official leaderboard submission.',
            'samples':len(samples),'labels':[{'path':str(x),'sha256':hashlib.sha256(x.read_bytes()).hexdigest()} for x in args.labels],
            'environment':{'python':platform.python_version(),'opencv':cv2.__version__,'numpy':np.__version__,'processor':platform.processor()},
            'protocol':'single annotated frame; no vehicle detector; lane-only wall time', 'methods':{}}
    for name, records in results.items():
        with (args.output/f'{name}.jsonl').open('w',encoding='utf-8') as target:
            for record in records:
                target.write(json.dumps({key:record[key] for key in ('raw_file','lanes','run_time')})+'\n')
        values=np.mean([x['metrics'] for x in records],axis=0)
        report['methods'][name]=dict(zip(('Accuracy','FP','FN'),map(float,values)))
        report['methods'][name]['mean_lane_only_ms']=float(np.mean([x['run_time'] for x in records]))
    (args.output/'summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
