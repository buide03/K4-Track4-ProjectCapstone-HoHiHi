"""Monocular optical TTC. No metric distance or absolute speed is inferred."""

from dataclasses import dataclass, field
from math import isfinite

Point = tuple[float, float]
EPS = 1e-8


def cross(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def on_segment(p: Point, a: Point, b: Point) -> bool:
    return (abs(cross(a, b, p)) < EPS
            and min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS
            and min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS)


def intersects(a: Point, b: Point, c: Point, d: Point) -> bool:
    if any((on_segment(p, u, v) for p, u, v in
            ((a, c, d), (b, c, d), (c, a, b), (d, a, b)))):
        return True
    return cross(a, b, c) * cross(a, b, d) < 0 and cross(c, d, a) * cross(c, d, b) < 0


def edges(polygon):
    return zip(polygon, polygon[1:] + polygon[:1])


def validate_polygon(points, width, height):
    if not isinstance(points, list) or len(points) < 3:
        raise ValueError("Zone must contain at least 3 [x, y] vertices.")
    polygon = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("Each zone vertex must be [x, y].")
        try:
            x, y = map(float, point)
        except (ValueError, TypeError) as exc:
            raise ValueError("Zone coordinates must be numbers.") from exc
        if not (isfinite(x) and isfinite(y) and 0 <= x < width and 0 <= y < height):
            raise ValueError("Zone vertices must be finite and inside the video frame.")
        polygon.append((x, y))
    if len(set(polygon)) != len(polygon):
        raise ValueError("Zone vertices must be distinct; do not repeat the first vertex.")
    for i, point in enumerate(polygon):
        previous, following = polygon[i - 1], polygon[(i + 1) % len(polygon)]
        if on_segment(following, previous, point) or on_segment(previous, point, following):
            raise ValueError("Adjacent zone edges must not overlap.")
    area = sum(a[0] * b[1] - a[1] * b[0] for a, b in edges(polygon))
    if abs(area) < EPS:
        raise ValueError("Zone must have nonzero area.")
    segments = list(edges(polygon))
    for i, (a, b) in enumerate(segments):
        for j in range(i + 1, len(segments)):
            if j == i + 1 or (i == 0 and j == len(segments) - 1):
                continue
            if intersects(a, b, *segments[j]):
                raise ValueError("Zone must not self-intersect.")
    return polygon


def inside(p: Point, polygon) -> bool:
    result = False
    for a, b in edges(polygon):
        if on_segment(p, a, b):
            return True
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if p[0] < x:
                result = not result
    return result


@dataclass
class Track:
    history: list = field(default_factory=list)
    candidate: str = ""
    streak: int = 0
    status: str = "WARMUP"
    ttc: float | None = None
    width_px: float | None = None
    expansion_per_s: float | None = None
    fit_error: float | None = None
    in_path: bool = False
    reason: str = "collecting history"


class WarningEngine:
    def __init__(self, polygon, frame_size, history_seconds=1.0, warning_ttc=3.0,
                 critical_ttc=1.5, confirm_frames=3, min_expansion=.02):
        if not all(isfinite(v) and v > 0 for v in
                   (history_seconds, warning_ttc, critical_ttc, min_expansion)):
            raise ValueError("History, TTC thresholds and minimum expansion must be finite and positive.")
        if critical_ttc >= warning_ttc or confirm_frames < 1:
            raise ValueError("Require 0 < critical TTC < warning TTC and confirm_frames >= 1.")
        self.polygon = polygon
        self.frame_size = frame_size
        self.history_seconds = history_seconds
        self.warning_ttc = warning_ttc
        self.critical_ttc = critical_ttc
        self.confirm_frames = confirm_frames
        self.min_expansion = min_expansion
        self.tracks = {}
        self.last_timestamp = None

    @staticmethod
    def reset(track, status, reason):
        track.candidate = ""
        track.streak = 0
        track.status = status
        track.reason = reason
        track.ttc = None

    def update(self, observations: dict[int, tuple], timestamp: float):
        if not isfinite(timestamp) or (self.last_timestamp is not None and timestamp <= self.last_timestamp):
            raise ValueError("Video timestamps must be finite and strictly increasing.")
        self.last_timestamp = timestamp
        # Lost tracks immediately lose TTC/alarm history, including when an ID is reused.
        self.tracks = {key: value for key, value in self.tracks.items() if key in observations}
        for key, box in observations.items():
            if len(box) != 4 or not all(isfinite(v) for v in box):
                raise ValueError("Bounding boxes must have four finite coordinates.")
            x1, y1, x2, y2 = box
            width, height = x2 - x1, y2 - y1
            if width <= 0 or height <= 0:
                raise ValueError("Bounding boxes must have positive width and height.")
            track = self.tracks.setdefault(key, Track())
            track.ttc = track.fit_error = track.expansion_per_s = None
            track.width_px = width
            track.in_path = inside(((x1 + x2) / 2, y2), self.polygon)
            frame_width, frame_height = self.frame_size
            if x1 <= 1 or y1 <= 1 or x2 >= frame_width - 1 or y2 >= frame_height - 1:
                track.history.clear()
                self.reset(track, "UNRELIABLE" if track.in_path else "OUTSIDE_ZONE", "box clipped by image boundary")
                continue
            if track.history and max(width / track.history[-1][1], track.history[-1][1] / width) > 1.35:
                track.history.clear()
                track.history.append((timestamp, width, height))
                self.reset(track, "UNRELIABLE" if track.in_path else "OUTSIDE_ZONE", "abrupt scale change")
                continue
            track.history.append((timestamp, width, height))
            while len(track.history) > 3 and track.history[1][0] <= timestamp - self.history_seconds:
                track.history.pop(0)
            if not track.in_path:
                self.reset(track, "OUTSIDE_ZONE", "outside selected driving corridor")
                continue
            span = timestamp - track.history[0][0]
            if len(track.history) < 3 or span + EPS < self.history_seconds:
                self.reset(track, "WARMUP", "collecting history")
                continue
            aspects = [w / h for _, w, h in track.history]
            if max(aspects) / min(aspects) > 1.25:
                self.reset(track, "UNRELIABLE", "aspect ratio changed; possible turn or occlusion")
                continue
            # Pinhole model: 1/width = Z/(f*W). Under constant closing speed this
            # is linear in time. Fit inverse width instead of differentiating jittery boxes.
            times = [t - timestamp for t, _, _ in track.history]
            inverse_widths = [1 / w for _, w, _ in track.history]
            mean_t = sum(times) / len(times)
            mean_q = sum(inverse_widths) / len(inverse_widths)
            denominator = sum((t - mean_t) ** 2 for t in times)
            slope = sum((t - mean_t) * (q - mean_q)
                        for t, q in zip(times, inverse_widths)) / denominator
            current_q = mean_q - slope * mean_t
            track.fit_error = (sum((q - (current_q + slope * t)) ** 2
                                   for t, q in zip(times, inverse_widths)) / len(times)) ** .5 / mean_q
            if current_q <= 0 or track.fit_error > .04:
                self.reset(track, "UNRELIABLE", "unstable scale history")
                continue
            track.width_px = 1 / current_q
            track.expansion_per_s = -slope / current_q
            if track.expansion_per_s <= self.min_expansion:
                self.reset(track, "NOT_CLOSING", "no reliable positive expansion")
                continue
            track.ttc = -current_q / slope
            candidate = ("CRITICAL" if track.ttc <= self.critical_ttc else
                         "WARNING" if track.ttc <= self.warning_ttc else "CLEAR")
            track.streak = track.streak + 1 if candidate == track.candidate else 1
            track.candidate = candidate
            if candidate == "CLEAR" or track.streak >= self.confirm_frames:
                track.status = candidate
            elif track.status in ("WARNING", "CRITICAL"):
                track.status = "WARNING"  # Keep an existing alarm while confirming escalation.
            else:
                track.status = "PENDING"
            track.reason = "optical TTC estimate; constant closing speed assumption"
        return self.tracks
