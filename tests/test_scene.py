import json
from pathlib import Path
import unittest

from collision_warning.core import WarningEngine
from collision_warning.scene import CANDIDATES
from collision_warning.scene import LaneModel, SceneAnalyzer, accel_ttc, lane_geometry, lane_span

# Perspective ego lane in a 960x720 frame: 100 px wide at y=300, 640 px at y=700.
LANE = [(430, 300), (530, 300), (800, 700), (160, 700)]
FPS = 15


def lane_width(y):
    return 100 + 1.35 * (y - 300)


def car(width, center):
    """Box whose bottom row follows the ground plane: a car covers ~75% of a lane width."""
    bottom = 300 + (width / .75 - 100) / 1.35
    return (center - width / 2, bottom - .55 * width, center + width / 2, bottom)


def clip(box, frame_width=960):
    x1, y1, x2, y2 = box
    return (max(1.5, x1), y1, min(frame_width - 1.5, x2), y2)


class Runner:
    def __init__(self, **options):
        self.engine = WarningEngine(LANE, (960, 720))
        self.scene = SceneAnalyzer(frame_width=960, **options)
        self.events = []

    def step(self, observations, t, lane=LANE):
        # Same order as the application: lane relations decide which tracks get TTC.
        relations = self.scene.observe(observations, lane, t)
        tracks = self.engine.update(observations, t, in_path={k: r in CANDIDATES for k, r in relations.items()})
        state = self.scene.assess(tracks, t)
        self.events.extend(state.events)
        return state


class GeometryTests(unittest.TestCase):
    def test_lane_span_follows_perspective_and_clamps_below_lane(self):
        self.assertEqual(lane_span(LANE, 200), None)
        left, right = lane_span(LANE, 500)
        self.assertAlmostEqual(right - left, lane_width(500))
        self.assertAlmostEqual(lane_span(LANE, 719)[0], 160, places=3)

    def test_penetration_uses_lane_facing_edge(self):
        left, right = lane_span(LANE, 500)
        outside = lane_geometry((left - 300, 400, left - 20, 500), LANE)
        self.assertEqual(outside["side"], "left")
        self.assertLess(outside["penetration"], 0)
        clipped = lane_geometry((right - 50, 400, 958, 500), LANE)
        self.assertEqual(clipped["side"], "right")
        self.assertAlmostEqual(clipped["penetration"], 50 / lane_width(500))
        self.assertTrue(lane_geometry(car(200, 480), LANE)["inside"])


class CutInTests(unittest.TestCase):
    def test_lead_tracked_then_cut_in_vehicle_becomes_target(self):
        run = Runner()
        lead = car(150, 480)
        targets, alerts = [], []
        for i in range(int(3.5 * FPS)):
            t = i / FPS
            progress = min(1, max(0, (t - 1.2) / 1.6))
            cutter = car(240, 160 + progress * (480 - 160))
            state = run.step({1: lead, 2: cutter}, t)
            targets.append(state.target_id)
            alerts.append(state.tracks[2].alert)
        self.assertEqual(targets[FPS], 1, "lead in lane is the target before the cut-in")
        self.assertEqual(state.tracks[2].relation, "IN_LANE")
        self.assertEqual(targets[-1], 2, "closer vehicle that cut in becomes the target")
        cut_in = next(e for e in run.events if e["type"] == "CUT_IN")
        switch = next(e for e in run.events if e["type"] == "TARGET_SWITCH" and e["to_id"] == 2)
        left = lane_span(LANE, cutter[3])[0]
        entered = 1.2 + 1.6 * (left - 160) / 320  # centre crosses the lane boundary
        self.assertLess(cut_in["time_s"], entered, "cut-in flagged before the vehicle centre enters the lane")
        self.assertLessEqual(switch["time_s"], entered)
        self.assertIn("WARNING", alerts)
        self.assertEqual(alerts[:FPS], ["CLEAR"] * FPS)

    def test_parallel_vehicle_clipped_at_frame_edge_swerves_in(self):
        run = Runner()
        relations, alerts = [], []
        for i in range(int(3 * FPS)):
            t = i / FPS
            progress = min(1, max(0, (t - 1.5) / .8))
            alongside = clip(car(420, 900 - progress * 340))
            state = run.step({1: car(150, 480), 7: alongside}, t)
            relations.append(state.tracks[7].relation)
            alerts.append(state.tracks[7].alert)
        before = relations[int(.5 * FPS):int(1.5 * FPS)]
        self.assertEqual(set(before), {"ADJACENT"}, "steady parallel vehicle is not a false alarm")
        self.assertEqual(set(alerts[:int(1.5 * FPS)]), {"CLEAR"})
        self.assertIn("CUT_IN", relations)
        self.assertIn("CRITICAL", alerts, "near cut-in escalates without needing TTC")
        self.assertEqual(state.target_id, 7)

    def test_parallel_vehicle_holding_lane_never_warns(self):
        run = Runner()
        for i in range(4 * FPS):
            state = run.step({3: clip(car(420, 900 + 4 * ((i % 6) - 3)))}, i / FPS)
            self.assertEqual(state.tracks[3].alert, "CLEAR")
            self.assertIsNone(state.target_id)

    def test_vehicle_emerging_at_frame_edge_is_not_cut_in(self):
        # Overtaking car appears at the right border: its inner edge moves left only because more of it
        # becomes visible, while it stays just outside the lane.
        run = Runner()
        right = lane_span(LANE, car(420, 0)[3])[1]
        for i in range(2 * FPS):
            t = i / FPS
            inner = 958 - min(1, t / .6) * (958 - (right + 5))
            state = run.step({5: (inner, 350, 958.5, car(420, 0)[3])}, t)
            self.assertNotEqual(state.tracks[5].relation, "CUT_IN")
            self.assertEqual(state.tracks[5].alert, "CLEAR")

    def test_vehicle_leaving_lane_is_not_cut_in(self):
        run = Runner()
        for i in range(3 * FPS):
            t = i / FPS
            state = run.step({4: car(240, 480 - min(1, t / 1.5) * 320)}, t)
            self.assertNotEqual(state.tracks[4].relation, "CUT_IN")
        self.assertEqual(state.tracks[4].relation, "ADJACENT")


class BrakingTests(unittest.TestCase):
    @staticmethod
    def gap(t):
        # Slow closing, then the lead brakes hard at t=1.5 s (relative acceleration).
        return 30 - 2 * t - (3 * (t - 1.5) ** 2 if t > 1.5 else 0)

    def test_hard_braking_is_flagged_and_warns_earlier(self):
        run = Runner()
        plain = Runner()
        first = {}
        for i in range(int(3.8 * FPS)):
            t = i / FPS
            width = 120 * 30 / self.gap(t)
            box = car(width, 480)
            state = run.step({1: box}, t)
            plain_track = plain.engine.update({1: box}, t)[1]
            first.setdefault(state.tracks[1].alert, t)
            first.setdefault("plain_" + plain_track.status, t)
        self.assertEqual(state.tracks[1].relation, "IN_LANE")
        braking = next(e for e in run.events if e["type"] == "BRAKING")
        self.assertGreater(braking["time_s"], 1.5)
        self.assertLess(braking["time_s"], 2.6)
        self.assertIn("WARNING", first)
        self.assertLessEqual(first["WARNING"], first.get("plain_WARNING", 99))
        self.assertIn("CRITICAL", first)

    def test_constant_closing_speed_is_not_braking(self):
        run = Runner()
        for i in range(49):
            t = i / 10
            state = run.step({1: car(160 * 6 / (6 - t), 480)}, t)
            self.assertFalse(state.tracks[1].braking)

    def test_accel_ttc_matches_constant_acceleration(self):
        history = []
        for i in range(16):
            t = i / FPS
            gap = 20 - 4 * t - 2 * t * t
            history.append((t, 1000 / gap, 1))
        now = history[-1][0]
        # Remaining gap 20-4t-2t^2 at t=1: 14; closing speed 8, accel 4 -> 14 = 8s + 2s^2.
        self.assertAlmostEqual(accel_ttc(history, now), (-8 + (64 + 112) ** .5) / 4, places=6)

    def test_invalid_configuration(self):
        for options in ({"near_ratio": 0}, {"brake_rate": -.5}, {"entry_horizon": float("nan")}):
            with self.assertRaises(ValueError):
                SceneAnalyzer(**options)


# Auto-lane polygons only cover the near road (here rows 431-683), like LaneEstimator output.
NEAR_LANE = [(339.25, 431.4), (168.9, 683.05), (790.16, 683.05), (619.78, 431.4)]


class AutoLaneTests(unittest.TestCase):
    def test_lane_extends_towards_vanishing_point(self):
        lane = LaneModel(NEAR_LANE)
        left, right = lane.span(362)  # lead vehicle bottom row, above the polygon
        self.assertLess(left, 480)
        self.assertGreater(right, 480)
        self.assertLess(right - left, lane.span(431.4)[1] - lane.span(431.4)[0])
        self.assertIsNone(lane.span(lane.vanish + 1))
        self.assertTrue(lane_geometry(car(150, 480), NEAR_LANE)["inside"])

    def test_far_lead_on_near_lane_gets_ttc_and_braking(self):
        run = Runner()
        for i in range(int(3.8 * FPS)):
            t = i / FPS
            state = run.step({1: car(120 * 30 / BrakingTests.gap(t), 480)}, t, NEAR_LANE)
        self.assertEqual(state.target_id, 1)
        self.assertTrue(any(e["type"] == "BRAKING" for e in run.events))
        self.assertEqual(state.tracks[1].alert, "CRITICAL")


class LaneJitterTests(unittest.TestCase):
    def test_recorded_lane_jitter_does_not_create_cut_ins(self):
        # Real dashcam regression: the automatic lane jumps between frames; a distant car on the other
        # carriageway and a car a lane away were reported as cutting in before lane smoothing and guards.
        data = json.loads((Path(__file__).parent / "data" / "highway_lane_jitter.json").read_text())
        by_track = {}
        for item in data["frames"]:
            by_track.setdefault(item["track"], []).append(item)
        for track, items in by_track.items():
            scene = SceneAnalyzer(frame_width=1280)
            for item in items:
                state = scene.update({1: tuple(item["box"])}, {}, item["lane"], item["frame"] / 25)
                self.assertNotIn(state.tracks[1].relation, ("CUT_IN", "DRIFTING"), f"track {track}")
                self.assertEqual(state.tracks[1].alert, "CLEAR")


class IntentAndSwerveTests(unittest.TestCase):
    def test_slow_drift_towards_lane_is_flagged_before_entry(self):
        run = Runner()
        relations, alerts = [], []
        bottom = car(220, 0)[3]
        width = lane_width(bottom)
        start = lane_span(LANE, bottom)[0] - .2 * width - 110  # inner edge 0.2 lane widths outside
        for i in range(12 * FPS):
            t = i / FPS
            # Adjacent car drifts slowly (0.12 lane widths/s) into the ego lane from t=1 s, then settles.
            centre = min(480, start + max(0, t - 1) * .12 * width)
            state = run.step({1: car(150, 480), 2: car(220, centre)}, t)
            relations.append(state.tracks[2].relation)
            alerts.append(state.tracks[2].alert)
        first_drift = relations.index("DRIFTING")
        self.assertNotIn("CUT_IN", relations[:first_drift], "intent is recognised before the cut-in")
        self.assertEqual(alerts[first_drift], "WARNING")
        self.assertEqual(set(alerts[:FPS]), {"CLEAR"})
        self.assertIn("CUT_IN", relations[first_drift:])
        self.assertEqual(state.tracks[2].relation, "IN_LANE")

    def test_sudden_close_cut_in_that_brakes_is_critical(self):
        run = Runner()
        alerts = []
        for i in range(int(3.2 * FPS)):
            t = i / FPS
            # Close car swerves in (0.6 s) then brakes: its image width grows.
            width = 300 * (1 + .25 * max(0, t - 1.6) ** 2)
            state = run.step({1: car(150, 480), 2: car(width, 480 - 380 * (1 - min(1, max(0, (t - 1) / .6))))}, t)
            alerts.append(state.tracks[2].alert)
        self.assertEqual(state.target_id, 2)
        cut = next(e for e in run.events if e["type"] == "CUT_IN")
        self.assertLess(cut["time_s"], 1.6)
        self.assertIn("CRITICAL", alerts)
        self.assertIsNotNone(state.tracks[2].effective_ttc, "cut-in vehicle in path gets TTC")


class LostTrackTests(unittest.TestCase):
    def test_brief_detection_gap_keeps_history(self):
        engine = WarningEngine(LANE, (960, 720), lost_grace=.3)
        scene = SceneAnalyzer(frame_width=960, lost_grace=.3)
        for i in range(31):
            t = i / 10
            if i in (20, 21):  # detector misses the lead for two frames
                scene.update({}, engine.update({}, t), LANE, t)
                continue
            obs = {1: car(160 * 6 / (6 - t), 480)}
            state = scene.update(obs, engine.update(obs, t), LANE, t)
            if i == 22:
                self.assertIsNotNone(state.tracks[1].effective_ttc, "TTC continues right after the gap")
                self.assertEqual(state.target_id, 1)
        switches = [e for e in scene.events if e["type"] == "TARGET_SWITCH"]
        self.assertEqual(switches, [])

    def test_in_path_override_and_default_grace(self):
        engine = WarningEngine(LANE, (960, 720))
        self.assertFalse(engine.update({1: car(150, 100)}, 0)[1].in_path)
        self.assertTrue(engine.update({1: car(150, 100)}, .1, in_path={1: True})[1].in_path)
        self.assertEqual(engine.update({}, .2), {})
        self.assertNotIn(1, engine.tracks)


if __name__ == "__main__":
    unittest.main()
