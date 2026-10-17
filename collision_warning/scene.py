"""Ego-lane scene logic on top of per-track optical TTC.

Answers, per frame and using only image geometry:
1. Where is each vehicle relative to the ego lane (in lane, drifting towards it, cutting in, adjacent)?
2. Which vehicle is the in-path target (closest vehicle in or entering the lane)?
3. Is the target braking hard (TTC falling faster than real time)?

Two phases per frame: ``observe`` (lane relations; tells the TTC engine which tracks are in
path) and ``assess`` (braking, target selection, alert levels). ``update`` runs both.
"""

from dataclasses import dataclass, field
import math
from math import isfinite, sqrt

from .core import edges

EPS = 1e-9
LEVELS = {"CLEAR": 0, "WARNING": 1, "CRITICAL": 2}
CANDIDATES = ("IN_LANE", "CUT_IN", "ENCROACHING")
RISK = {"DRIFTING": 1, "ENCROACHING": 2, "CUT_IN": 3}


def lane_span(polygon, y):
    """Left/right lane boundary x at image row y inside the polygon's rows; rows below use the bottom row."""
    if len(polygon) < 3:
        return None
    ys = [p[1] for p in polygon]
    top, bottom = min(ys), max(ys)
    if y < top:
        return None
    y = min(y, bottom - 1e-6 * (bottom - top))
    xs = [ax + (y - ay) * (bx - ax) / (by - ay)
          for (ax, ay), (bx, by) in edges(polygon) if (ay <= y < by) or (by <= y < ay)]
    return (min(xs), max(xs)) if len(xs) >= 2 else None


class LaneModel:
    """Lane polygon plus straight-line extension of its boundaries towards the vanishing point.

    Automatic lane detection only sees the near road; a lead vehicle further ahead must still be
    placed relative to the lane, so the boundaries are extrapolated above the polygon's top row.
    """

    def __init__(self, polygon):
        self.polygon = polygon
        self.valid = False
        if len(polygon) < 3:
            return
        ys = [p[1] for p in polygon]
        self.top, self.bottom = min(ys), max(ys)
        rows = [self.top + (self.bottom - self.top) * k / 8 for k in range(9)]
        samples = [(y, lane_span(polygon, y)) for y in rows]
        samples = [(y, span) for y, span in samples if span is not None]
        if len(samples) < 2 or self.bottom - self.top < 1:
            return
        self.left = self.fit([(y, span[0]) for y, span in samples])
        self.right = self.fit([(y, span[1]) for y, span in samples])
        width_slope = self.right[1] - self.left[1]
        self.bottom_width = (self.right[0] - self.left[0]) + width_slope * self.bottom
        if self.bottom_width <= 0:
            return
        # Rows where the extended lane would be narrower than 4% of its near width count as "far".
        self.vanish = (self.top - (self.bottom - self.top) if width_slope <= EPS
                       else -(self.right[0] - self.left[0]) / width_slope)
        self.far_row = self.vanish + .04 * (self.bottom - self.vanish)
        self.valid = True

    @staticmethod
    def fit(points):
        mean_y = sum(y for y, _ in points) / len(points)
        mean_x = sum(x for _, x in points) / len(points)
        denominator = sum((y - mean_y) ** 2 for y, _ in points)
        slope = sum((y - mean_y) * (x - mean_x) for y, x in points) / denominator if denominator > EPS else 0
        return mean_x - slope * mean_y, slope

    def span(self, y):
        if not self.valid:
            return None
        if y >= self.top:
            return lane_span(self.polygon, y)
        if y <= self.far_row:
            return None
        return self.left[0] + self.left[1] * y, self.right[0] + self.right[1] * y

    def proximity(self, y):
        """0 at the vanishing point, 1 at the bottom of the lane: a monotonic 'closeness' proxy."""
        return min(1.0, max(0.0, (y - self.vanish) / max(EPS, self.bottom - self.vanish)))


def lane_geometry(box, lane):
    """Penetration of the box's lane-facing edge into the lane, in lane widths."""
    if not isinstance(lane, LaneModel):
        lane = LaneModel(lane)
    x1, y1, x2, y2 = box
    span = lane.span(y2)
    if span is None:
        return None
    left, right = span
    width = max(EPS, right - left)
    center = (x1 + x2) / 2
    side = "left" if center < (left + right) / 2 else "right"
    # The inner edge stays valid when the outer side is clipped by the frame border.
    penetration = ((x2 - left) if side == "left" else (right - x1)) / width
    return {"inside": left <= center <= right, "side": side, "penetration": penetration,
            "proximity": lane.proximity(y2)}


def fit_slope(samples):
    """Least-squares slope of (t, value) samples."""
    mean_t = sum(t for t, _ in samples) / len(samples)
    mean_v = sum(v for _, v in samples) / len(samples)
    denominator = sum((t - mean_t) ** 2 for t, _ in samples)
    if denominator < EPS:
        return None
    return sum((t - mean_t) * (v - mean_v) for t, v in samples) / denominator


def accel_ttc(history, now):
    """TTC under constant relative acceleration: first positive root of a quadratic in 1/width."""
    if len(history) < 8:
        return None
    ts = [t - now for t, _, _ in history]
    qs = [1 / w for _, w, _ in history]
    # Normal equations for q = a + b*t + c*t^2, solved by Cramer's rule.
    s = [sum(t ** k for t in ts) for k in range(5)]
    r = [sum(q * t ** k for t, q in zip(ts, qs)) for k in range(3)]
    m = [[s[0], s[1], s[2]], [s[1], s[2], s[3]], [s[2], s[3], s[4]]]

    def det(a):
        return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
                - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
                + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))

    d = det(m)
    if abs(d) < EPS * len(ts):
        return None
    a, b, c = (det([[r[i] if j == col else m[i][j] for j in range(3)] for i in range(3)]) / d for col in range(3))
    if a <= 0 or c >= 0:  # Closing must be accelerating (1/width curving down).
        return None
    root = (-b - sqrt(b * b - 4 * a * c)) / (2 * c)
    return root if isfinite(root) and root > 0 else None


@dataclass
class SceneTrack:
    lateral: list = field(default_factory=list)
    ttc_history: list = field(default_factory=list)
    relation: str = "UNKNOWN"
    age: int = 0
    last_seen: float = 0.0
    candidate: str = ""
    streak: int = 0
    clipped: bool = False
    penetration: float | None = None
    lateral_speed: float | None = None
    drift_speed: float | None = None
    time_to_entry: float | None = None
    proximity: float | None = None
    ttc_rate: float | None = None
    braking: bool = False
    brake_streak: int = 0
    effective_ttc: float | None = None
    alert: str = "CLEAR"
    reason: str = ""


@dataclass
class SceneState:
    tracks: dict
    target_id: int | None
    target_reason: str
    events: list


class SceneAnalyzer:
    def __init__(self, warning_ttc=3.0, critical_ttc=1.5, confirm_frames=3, lateral_history=.4,
                 entry_horizon=1.0, min_lateral_speed=.15, intrusion=.15, encroach=.3,
                 near_ratio=.55, brake_rate=-1.3, brake_window=.6, frame_width=None,
                 drift_window=1.0, drift_speed=.08, intent_horizon=3.0, drift_alert_ratio=.35,
                 lost_grace=0.0, lane_smoothing=.3, predict_reach=.5, predict_proximity=.15,
                 max_lateral_speed=3.0):
        if not all(isfinite(v) and v > 0 for v in (lateral_history, entry_horizon, min_lateral_speed, intrusion,
                                                    encroach, brake_window, drift_window, drift_speed,
                                                    intent_horizon)):
            raise ValueError("Scene parameters must be finite and positive.")
        if not 0 < near_ratio <= 1 or not 0 < drift_alert_ratio <= 1 or brake_rate >= -1 or confirm_frames < 1:
            raise ValueError("Require 0 < near/drift ratios <= 1, brake rate < -1 and confirm_frames >= 1.")
        if not isfinite(lost_grace) or lost_grace < 0:
            raise ValueError("Lost-track grace must be finite and non-negative.")
        self.warning_ttc, self.critical_ttc = warning_ttc, critical_ttc
        self.confirm_frames = confirm_frames
        self.lateral_history, self.entry_horizon = lateral_history, entry_horizon
        self.min_lateral_speed, self.intrusion, self.encroach = min_lateral_speed, intrusion, encroach
        self.near_ratio, self.brake_rate, self.brake_window = near_ratio, brake_rate, brake_window
        self.frame_width = frame_width
        self.drift_window, self.drift_speed = drift_window, drift_speed
        self.intent_horizon, self.drift_alert_ratio = intent_horizon, drift_alert_ratio
        self.lost_grace = lost_grace
        # Prediction guards: only vehicles near the lane edge and not near the horizon can be predicted to enter;
        # faster apparent lateral motion than this (lane widths/s) is lane jitter, not a vehicle.
        self.lane_smoothing, self.predict_reach = lane_smoothing, predict_reach
        self.predict_proximity, self.max_lateral_speed = predict_proximity, max_lateral_speed
        self.lane = None
        self.lane_time = None
        self.tracks = {}
        self.observed = []
        self.events = []
        self.target_id = None

    def ttc_level(self, ttc):
        if ttc is None:
            return "CLEAR"
        return "CRITICAL" if ttc <= self.critical_ttc else "WARNING" if ttc <= self.warning_ttc else "CLEAR"

    def classify(self, state, geometry):
        if geometry is None:
            return "FAR"
        p, v, d = state.penetration, state.lateral_speed, state.drift_speed
        moving_in = v is not None and v >= self.min_lateral_speed
        # Slow lane changes count too once the vehicle really intrudes.
        inward = moving_in or (d is not None and d >= self.drift_speed and (v is None or v > 0))
        predictable = (not state.clipped and p >= -self.predict_reach
                       and state.proximity is not None and state.proximity >= self.predict_proximity)
        if geometry["inside"]:
            # A fast swerve can put the centre in the lane within a frame; it stays a cut-in until it settles.
            return "CUT_IN" if inward and state.relation not in ("IN_LANE", "UNKNOWN") else "IN_LANE"
        # Already intruding: any sustained inward motion is a cut-in, not only a fast swerve.
        if p >= self.intrusion and (inward or (v is not None and v >= self.drift_speed)):
            return "CUT_IN"
        # A box clipped by the frame edge grows inward as the vehicle emerges (e.g. overtaking),
        # so its lateral motion only counts once it really intrudes; no predicted entry.
        if (moving_in and predictable and state.time_to_entry is not None
                and state.time_to_entry <= self.entry_horizon):
            return "CUT_IN"
        if p >= self.encroach:
            return "ENCROACHING"
        # Lane-change intent: slow but sustained drift towards the lane, entry predicted within a few seconds.
        if (predictable and d is not None and d >= self.drift_speed
                and (self.intrusion - p) / d <= self.intent_horizon):
            return "DRIFTING"
        return "ADJACENT"

    def smooth(self, polygon, timestamp):
        """Temporal EMA of the lane polygon: automatic lanes jitter frame to frame, and that jitter
        would otherwise look like lateral motion of every vehicle."""
        polygon = [tuple(map(float, p)) for p in polygon]
        if (not polygon or self.lane is None or len(self.lane) != len(polygon)
                or self.lane_smoothing <= 0 or self.lane_time is None):
            self.lane = polygon or None
        else:
            alpha = 1 - math.exp(-max(0., timestamp - self.lane_time) / self.lane_smoothing)
            self.lane = [(px + alpha * (x - px), py + alpha * (y - py))
                         for (px, py), (x, y) in zip(self.lane, polygon)]
        self.lane_time = timestamp
        return self.lane or []

    def observe(self, observations, polygon, timestamp):
        """Update lane relations. Returns {track_id: relation} for the observed tracks."""
        self.events = []
        lane = polygon if isinstance(polygon, LaneModel) else LaneModel(self.smooth(polygon, timestamp))
        # Briefly missed tracks keep their history; anything older is dropped.
        self.tracks = {key: value for key, value in self.tracks.items()
                       if key in observations or timestamp - value.last_seen <= self.lost_grace}
        self.observed = list(observations)
        margin = max(4, .02 * self.frame_width) if self.frame_width else 0
        for key, box in observations.items():
            state = self.tracks.setdefault(key, SceneTrack())
            state.age += 1
            state.last_seen = timestamp
            # Detector boxes stop a few pixels short of the border, so use a 2% margin.
            state.clipped = self.frame_width is not None and (box[0] <= margin or box[2] >= self.frame_width - margin)
            geometry = lane_geometry(box, lane)
            state.lateral_speed = state.drift_speed = state.time_to_entry = None
            if geometry is None:
                state.lateral.clear()
                state.penetration = state.proximity = None
            else:
                state.penetration, state.proximity = geometry["penetration"], geometry["proximity"]
                state.lateral.append((timestamp, state.penetration))
                window = max(self.lateral_history, self.drift_window)
                while len(state.lateral) > 2 and state.lateral[1][0] <= timestamp - window:
                    state.lateral.pop(0)
                recent = [s for s in state.lateral if s[0] >= timestamp - self.lateral_history - EPS]
                if len(recent) >= 3 and timestamp - recent[0][0] >= .1:
                    state.lateral_speed = fit_slope(recent)
                    if state.lateral_speed is not None and abs(state.lateral_speed) > self.max_lateral_speed:
                        state.lateral_speed = None
                    if state.lateral_speed and state.lateral_speed > EPS and state.penetration < self.intrusion:
                        state.time_to_entry = (self.intrusion - state.penetration) / state.lateral_speed
                if len(state.lateral) >= 5 and timestamp - state.lateral[0][0] >= .6 * self.drift_window:
                    state.drift_speed = fit_slope(state.lateral)
                    if state.drift_speed is not None and abs(state.drift_speed) > self.max_lateral_speed:
                        state.drift_speed = None
            raw = self.classify(state, geometry)
            state.streak = state.streak + 1 if raw == state.candidate else 1
            state.candidate = raw
            # Escalating to a hazardous relation is confirmed one frame faster than relaxing from it.
            needed = (max(1, self.confirm_frames - 1)
                      if RISK.get(raw, 0) > RISK.get(state.relation, 0) else self.confirm_frames)
            if state.relation == "UNKNOWN" or state.streak >= needed:
                if raw in ("CUT_IN", "DRIFTING") and state.relation != raw:
                    self.events.append({"type": raw, "time_s": timestamp, "track_id": key,
                                        "time_to_entry_s": state.time_to_entry, "proximity": state.proximity})
                state.relation = raw
        return {key: self.tracks[key].relation for key in observations}

    def assess(self, ttc_tracks, timestamp):
        """Braking, target selection and alert levels, given this frame's TTC tracks."""
        events = self.events
        current = {key: self.tracks[key] for key in self.observed}
        for key, state in current.items():
            # Braking: with constant closing speed TTC falls 1 s per second; faster means the gap closes harder.
            ttc_track = ttc_tracks.get(key)
            ttc = ttc_track.ttc if ttc_track is not None else None
            if ttc is None:
                state.ttc_history.clear()
            else:
                state.ttc_history.append((timestamp, ttc))
                while len(state.ttc_history) > 2 and state.ttc_history[1][0] <= timestamp - self.brake_window:
                    state.ttc_history.pop(0)
            state.ttc_rate = (fit_slope(state.ttc_history)
                              if len(state.ttc_history) >= 4 and timestamp - state.ttc_history[0][0] >= .3 else None)
            # Ignore far targets: TTC from tiny expansion is noisy and irrelevant for an alert.
            hard = (state.ttc_rate is not None and state.ttc_rate <= self.brake_rate
                    and ttc is not None and ttc <= 2 * self.warning_ttc)
            state.brake_streak = state.brake_streak + 1 if hard else 0
            was_braking = state.braking
            state.braking = state.brake_streak >= self.confirm_frames or (was_braking and ttc is not None and hard)
            if state.braking and not was_braking:
                events.append({"type": "BRAKING", "time_s": timestamp, "track_id": key,
                               "ttc_s": ttc, "ttc_rate": state.ttc_rate})
            state.effective_ttc = ttc
            if state.braking and ttc_track is not None:
                accelerated = accel_ttc(ttc_track.history, timestamp)
                if accelerated is not None and ttc is not None:
                    state.effective_ttc = min(ttc, accelerated)

        # In-path target: the closest vehicle (lowest box bottom) that is in, entering or blocking the lane.
        # A track must persist a few frames first so one-frame ghost detections cannot steal the target.
        candidates = [(state.proximity, key) for key, state in current.items()
                      if state.relation in CANDIDATES and state.proximity is not None
                      and (state.age >= self.confirm_frames or key == self.target_id)]
        target = max(candidates)[1] if candidates else None
        if target is None and self.target_id in self.tracks and self.target_id not in current:
            target = self.target_id  # Keep a briefly missed target instead of announcing a switch.
        reason = self.tracks[target].relation if target is not None else "no vehicle in ego lane"
        if target != self.target_id:
            events.append({"type": "TARGET_SWITCH", "time_s": timestamp, "from_id": self.target_id,
                           "to_id": target, "reason": reason})
            self.target_id = target

        for key, state in current.items():
            ttc_track = ttc_tracks.get(key)
            level, why = "CLEAR", state.relation.lower()
            if state.relation in CANDIDATES:
                confirmed = ttc_track.status if ttc_track is not None and ttc_track.status in LEVELS else "CLEAR"
                level = max(confirmed, self.ttc_level(state.effective_ttc) if state.braking else "CLEAR",
                            key=LEVELS.get)
                if state.braking:
                    why = "target braking hard"
                    if state.effective_ttc is not None and state.effective_ttc <= 2 * self.warning_ttc:
                        level = max(level, "WARNING", key=LEVELS.get)
                if state.relation == "CUT_IN":
                    near = state.proximity is not None and state.proximity >= self.near_ratio
                    level = max(level, "CRITICAL" if near else "WARNING", key=LEVELS.get)
                    why = "close vehicle cutting in" if near else "vehicle cutting in"
                elif state.relation == "ENCROACHING" and state.proximity is not None and state.proximity >= self.near_ratio:
                    level = max(level, "WARNING", key=LEVELS.get)
                    why = "close vehicle encroaching on lane"
                elif level != "CLEAR" and not state.braking:
                    why = "optical TTC"
            elif state.relation == "DRIFTING" and state.proximity is not None and state.proximity >= self.drift_alert_ratio:
                level, why = "WARNING", "vehicle drifting toward lane"
            state.alert, state.reason = level, why
        return SceneState(current, self.target_id, reason, events)

    def update(self, observations, ttc_tracks, polygon, timestamp):
        self.observe(observations, polygon, timestamp)
        return self.assess(ttc_tracks, timestamp)
