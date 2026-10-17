"""Daylight ego-lane baseline: color evidence, robust curves and temporal filtering.

This is image-based classical CV, not a pretrained lane neural network. It returns
no corridor when two plausible boundaries cannot be supported by image evidence.
"""

from dataclasses import dataclass, field
import math

import cv2
import numpy as np


@dataclass
class LaneResult:
    status: str = "unavailable"
    confidence: float = 0.0
    polygon: list = field(default_factory=list)
    left: list = field(default_factory=list)
    right: list = field(default_factory=list)
    reason: str = "Two lane boundaries not found"

    def as_dict(self):
        return {"status": self.status, "confidence": round(float(self.confidence), 4),
                "polygon": self.polygon, "left": self.left, "right": self.right, "reason": self.reason}


class LaneEstimator:
    def __init__(self, hold_seconds=.3):
        self.hold_seconds = hold_seconds
        self.previous = None
        self.previous_time = None
        self.last_seen = None

    @staticmethod
    def evidence(frame, boxes=()):
        height, width = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hls = cv2.cvtColor(frame, cv2.COLOR_BGR2HLS)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        local = cv2.GaussianBlur(gray, (0, 0), max(5, width / 60))
        contrast = gray.astype(np.int16) - local.astype(np.int16)
        yellow = cv2.inRange(hsv, (12, 65, 80), (40, 255, 255))
        white = ((hls[:, :, 1] >= 165) & (hls[:, :, 2] <= 115) & (contrast >= 9)).astype(np.uint8) * 255
        mask = cv2.bitwise_or(yellow, white)
        mask[:round(.53 * height)] = 0
        mask[round(.95 * height):] = 0
        mask[:, :round(.02 * width)] = 0
        mask[:, round(.98 * width):] = 0
        for box in boxes:
            x1, y1, x2, y2 = map(int, box)
            mask[max(0, y1 - 3):min(height, y2 + 3), max(0, x1 - 3):min(width, x2 + 3)] = 0
        return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    @staticmethod
    def seeds(mask):
        h, w = mask.shape
        segments = cv2.HoughLinesP(mask, 1, np.pi / 180, threshold=max(12, int(h * .025)),
                                   minLineLength=max(12, int(h * .035)), maxLineGap=int(h * .045))
        candidates = {"left": [], "right": []}
        if segments is None:
            return {}
        for x1, y1, x2, y2 in np.asarray(segments).reshape(-1, 4):
            if abs(y2 - y1) < .015 * h or max(y1, y2) < .68 * h:
                continue
            slope = (x2 - x1) / (y2 - y1)
            if not .12 < abs(slope) < 2.8:
                continue
            bottom_x = (x1 + slope * (.92 * h - y1)) / w
            side = "left" if slope < 0 else "right"
            if side == "left" and .02 < bottom_x < .49:
                candidates[side].append((bottom_x, math.hypot(x2 - x1, y2 - y1), slope * h / w))
            elif side == "right" and .51 < bottom_x < .98:
                candidates[side].append((bottom_x, math.hypot(x2 - x1, y2 - y1), slope * h / w))
        # Choose the nearest supported boundary on either side of the camera.
        result = {}
        for side, items in candidates.items():
            groups = []
            for value, length, slope in sorted(items):
                if not groups or value - groups[-1][-1][0] > .055:
                    groups.append([])
                groups[-1].append((value, length, slope))
            supported = [group for group in groups if sum(length for _, length, _ in group) >= h * .08]
            if supported:
                centers = [(sum(x * length for x, length, _ in group) / sum(length for _, length, _ in group),
                            sum(s * length for _, length, s in group) / sum(length for _, length, _ in group)) for group in supported]
                result[side] = max(centers) if side == "left" else min(centers)
        return result

    @staticmethod
    def trace(mask, seed, side):
        h, w = mask.shape
        xs, ys = [], []
        seed_x, seed_slope = seed
        current = seed_x * w
        step = max(6, round(h * .022))
        missing = 0
        for bottom in range(round(h * .94), round(h * .58), -step):
            top = bottom - step
            row_y = (top + bottom) / (2 * h)
            guide = (seed_x + seed_slope * (row_y - .92)) * w
            if len(xs) >= 5:
                prediction = np.polyval(np.polyfit(np.array(ys) / h, np.array(xs) / w, 2 if len(xs) >= 9 else 1), row_y) * w
                current = float(np.clip(prediction, guide - .12 * w, guide + .12 * w))
            else:
                current = guide
            half = w * (.045 + .012 * min(missing, 3))
            left, right = max(0, int(current - half)), min(w, int(current + half))
            if left >= right:
                break
            counts = np.sum(mask[max(0, top):bottom, left:right] > 0, axis=0).astype(float)
            counts = np.convolve(counts, np.ones(5) / 5, mode="same")
            if counts.size == 0 or counts.max() < max(1.2, step * .10):
                missing += 1
                continue
            peak = np.argmax(counts)
            positions = np.arange(len(counts))
            near = np.abs(positions - peak) < max(4, int(w * .008))
            weight = counts * near
            current = left + np.sum(positions * weight) / max(1e-9, weight.sum())
            xs.append(float(current)); ys.append(float((top + bottom) / 2)); missing = 0
        if len(xs) < 5 or max(ys) - min(ys) < h * .15:
            return None
        y, x = np.array(ys) / h, np.array(xs) / w
        # Iteratively remove outliers; road paint width and speckle are not lane geometry.
        keep = np.ones(len(x), dtype=bool)
        for _ in range(3):
            coef = np.polyfit(y[keep], x[keep], 2)
            residual = np.abs(x - np.polyval(coef, y))
            keep = residual < max(.008, float(np.median(residual) * 3))
            if keep.sum() < 5:
                return None
        residual = float(np.sqrt(np.mean((x[keep] - np.polyval(coef, y[keep])) ** 2)))
        if residual > .018:
            return None
        return coef, (float(y[keep].min()), float(y[keep].max())), float(keep.mean()), len(xs)

    def detect(self, frame, boxes=()):
        h, w = frame.shape[:2]
        mask = self.evidence(frame, boxes)
        seeds = self.seeds(mask)
        if "left" not in seeds or "right" not in seeds:
            return None
        left, right = self.trace(mask, seeds["left"], "left"), self.trace(mask, seeds["right"], "right")
        if left is None or right is None:
            return None
        low = max(.60, left[1][0], right[1][0])
        high = min(.95, min(left[1][1], right[1][1]) + .14)
        if high - low < .14:
            return None
        # Bridge a foreground dash gap, but never extrapolate past the road/hood limit.
        y = np.linspace(low, high, 24)
        lx, rx = np.polyval(left[0], y), np.polyval(right[0], y)
        widths = rx - lx
        valid_top = np.flatnonzero(widths >= .03)
        if not len(valid_top):
            return None
        first = int(valid_top[0])
        y, lx, rx, widths = y[first:], lx[first:], rx[first:], widths[first:]
        low = float(y[0])
        if len(y) < 12 or np.any(widths < .025) or np.any(widths > .90) or widths[-1] < .18:
            return None
        if not (lx[-1] < .5 < rx[-1]) or widths[-1] < widths[0] * 1.15:
            return None
        if np.any(lx < -.03) or np.any(rx > 1.03):
            return None
        confidence = min(1., min(left[2], right[2]) * min(1., min(left[3], right[3]) / 12))
        if confidence < .55:
            return None
        return {"left_coef": left[0], "right_coef": right[0], "low": low, "high": float(y[-1]), "confidence": confidence}

    def update(self, frame, timestamp=0., boxes=()):
        if self.previous_time is not None and timestamp < self.previous_time:
            raise ValueError("Lane timestamps must not decrease.")
        h, w = frame.shape[:2]
        current = self.detect(frame, boxes)
        status = "detected"
        if current is not None:
            if self.previous is not None:
                y = np.linspace(max(current["low"], self.previous["low"]), min(current["high"], self.previous["high"]), 8)
                jump = max(np.max(np.abs(np.polyval(current[key], y) - np.polyval(self.previous[key], y))) for key in ("left_coef", "right_coef"))
                if jump > .12:
                    current = None
                else:
                    dt = max(.001, timestamp - self.previous_time)
                    alpha = 1 - math.exp(-dt / .12)
                    for key in ("left_coef", "right_coef"):
                        current[key] = alpha * current[key] + (1 - alpha) * self.previous[key]
            if current is not None:
                self.previous = current
                self.last_seen = timestamp
        if current is None:
            if self.previous is None or timestamp - self.last_seen > self.hold_seconds:
                self.previous = None
                self.previous_time = timestamp
                return LaneResult()
            current = dict(self.previous)
            current["confidence"] *= max(0., 1 - (timestamp - self.last_seen) / self.hold_seconds)
            status = "held"
        self.previous_time = timestamp
        y = np.linspace(current["low"], current["high"], 24)
        left = [[round(float(np.clip(x, 0, 1)) * (w - 1), 2), round(float(v) * (h - 1), 2)] for x, v in zip(np.polyval(current["left_coef"], y), y)]
        right = [[round(float(np.clip(x, 0, 1)) * (w - 1), 2), round(float(v) * (h - 1), 2)] for x, v in zip(np.polyval(current["right_coef"], y), y)]
        return LaneResult(status, current["confidence"], left + right[::-1], left, right,
                          "Image-supported lane boundaries" if status == "detected" else "Brief temporal hold; confidence decays")
