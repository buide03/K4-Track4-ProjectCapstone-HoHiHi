"""Geometry metrics and an independent Python 3 implementation of TuSimple rules.

Protocol reference: https://github.com/TuSimple/tusimple-benchmark/blob/master/evaluate/lane.py
Use the official evaluator for a leaderboard submission. Ego-only predictions do
not cover all annotated lanes; never compare an ego subset to full-set scores.
"""
import cv2
import numpy as np


def sample_boundary(points, rows):
    if len(points) < 2:
        return np.full(len(rows), -2., dtype=float)
    points = np.asarray(points, dtype=float)
    order = np.argsort(points[:, 1])
    x, y = points[order, 0], points[order, 1]
    return np.interp(rows, y, x, left=-2., right=-2.)


def corridor_metrics(prediction, reference, width, height):
    masks = []
    for polygon in (prediction, reference):
        mask = np.zeros((height, width), np.uint8)
        if len(polygon) >= 3:
            cv2.fillPoly(mask, [np.rint(polygon).astype(np.int32)], 1)
        masks.append(mask.astype(bool))
    union = np.count_nonzero(masks[0] | masks[1])
    return np.count_nonzero(masks[0] & masks[1]) / union if union else 1.


def tusimple_frame(predictions, truth, rows, runtime_ms):
    """Accuracy, FP, FN with the reference's 20px/85% and runtime rules.

    Matches the reference's per-GT best matching, including its >4 lane rule.
    This is NOT one-to-one matching or detection mAP.
    """
    if not rows or not truth or any(len(lane) != len(rows) for lane in [*predictions, *truth]):
        raise ValueError("Nonempty rows/truth and equal sample counts required")
    if runtime_ms > 200 or len(predictions) > len(truth) + 2:
        return 0., 0., 1.
    accuracies = []
    for lane in truth:
        gt = np.asarray(lane, dtype=float)
        valid = gt >= 0
        slope = float(np.polyfit(np.asarray(rows)[valid], gt[valid], 1)[0]) if valid.sum() > 1 else 0.
        threshold = 20 * np.sqrt(1 + slope * slope)
        expected = np.where(valid, gt, -100)
        agreements = [float(np.mean(np.abs(np.where(np.asarray(p) >= 0, p, -100) - expected) < threshold)) for p in predictions]
        accuracies.append(max(agreements, default=0.))
    matched = sum(value >= .85 for value in accuracies)
    missed = len(truth) - matched
    score = sum(accuracies)
    if len(truth) > 4:
        missed = max(0, missed - 1)
        score -= min(accuracies)
    denominator = min(4, len(truth))
    return score / denominator, (len(predictions) - matched) / len(predictions) if predictions else 0., missed / denominator
