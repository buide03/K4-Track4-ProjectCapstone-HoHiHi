import unittest

from collision_warning.core import WarningEngine, inside, validate_polygon

ZONE = [(400, 100), (900, 100), (900, 680), (400, 680)]


def box(width, center=640, height=None, bottom=600):
    height = width * .65 if height is None else height
    return (center - width / 2, bottom - height, center + width / 2, bottom)


class WarningTests(unittest.TestCase):
    def engine(self, **kwargs):
        return WarningEngine(ZONE, (1280, 720), **kwargs)

    def test_geometry_and_validation(self):
        self.assertTrue(inside((400, 150), ZONE))
        self.assertFalse(inside((300, 150), ZONE))
        self.assertEqual(len(validate_polygon([list(p) for p in ZONE], 1280, 720)), 4)
        for points in ([], [[0, 0], [1, 1], [2, 2]],
                       [[0, 0], [20, 30], [0, 20], [30, 0]],
                       [[0, 0], [None, 0], [1, 2]],
                       [[0, 0], [5000, 0], [1, 2]]):
            with self.assertRaises(ValueError):
                validate_polygon(points, 1280, 720)

    def test_constant_closing_speed_known_ttc(self):
        engine = self.engine()
        statuses = []
        for i in range(49):
            t = i / 10
            track = engine.update({1: box(160 * 6 / (6 - t))}, t)[1]
            statuses.append(track.status)
            if track.ttc is not None:
                self.assertAlmostEqual(track.ttc, 6 - t, places=6)
        self.assertEqual(statuses[0], "WARMUP")
        self.assertIn("CLEAR", statuses)
        self.assertIn("WARNING", statuses)
        self.assertEqual(statuses[-1], "CRITICAL")

    def test_stationary_and_receding_do_not_warn(self):
        for width_at in (lambda t: 160, lambda t: 160 * 6 / (6 + t)):
            engine = self.engine()
            for i in range(31):
                track = engine.update({1: box(width_at(i / 10))}, i / 10)[1]
            self.assertEqual(track.status, "NOT_CLOSING")
            self.assertIsNone(track.ttc)

    def test_low_growth_and_small_jitter_do_not_warn(self):
        engine = self.engine()
        for i in range(31):
            track = engine.update({1: box(160 + (1 if i % 2 else -1))}, i / 10)[1]
        self.assertEqual(track.status, "NOT_CLOSING")
        self.assertIsNone(track.ttc)

    def test_approaching_outside_lane_does_not_warn(self):
        engine = self.engine()
        for i in range(31):
            track = engine.update({1: box(60 * 4 / (4 - i / 10), center=200)}, i / 10)[1]
        self.assertEqual(track.status, "OUTSIDE_ZONE")
        self.assertIsNone(track.ttc)

    def test_missing_id_resets_history(self):
        engine = self.engine()
        for i in range(21):
            track = engine.update({8: box(80 * 4 / (4 - i / 10))}, i / 10)[8]
        self.assertEqual(track.status, "WARNING")
        self.assertEqual(engine.update({}, 2.1), {})
        track = engine.update({8: box(160)}, 2.2)[8]
        self.assertEqual(track.status, "WARMUP")
        self.assertIsNone(track.ttc)

    def test_lane_loss_suppresses_alarm_and_requires_new_history(self):
        engine = self.engine()
        for i in range(21):
            track = engine.update({8: box(80 * 4 / (4 - i / 10))}, i / 10)[8]
        self.assertEqual(track.status, "WARNING")
        track = engine.update({8: box(165)}, 2.1, lane_available=False)[8]
        self.assertEqual(track.status, "LANE_UNAVAILABLE")
        self.assertIsNone(track.ttc)
        self.assertEqual(track.history, [])
        track = engine.update({8: box(170)}, 2.2)[8]
        self.assertEqual(track.status, "WARMUP")
        self.assertIsNone(track.ttc)

    def test_clipped_and_abrupt_scale_reset(self):
        for invalid in ((0, 200, 1280, 600), box(400)):
            engine = self.engine()
            for i in range(11):
                engine.update({1: box(160)}, i / 10)
            track = engine.update({1: invalid}, 1.1)[1]
            self.assertEqual(track.status, "UNRELIABLE")
            self.assertIsNone(track.ttc)
            self.assertLessEqual(len(track.history), 1)

    def test_changing_aspect_ratio_is_unreliable(self):
        engine = self.engine()
        for i in range(11):
            track = engine.update({1: box(160 + 6 * i, height=100)}, i / 10)[1]
        self.assertEqual(track.status, "UNRELIABLE")
        self.assertIsNone(track.ttc)

    def test_noisy_inverse_scale_is_unreliable(self):
        engine = self.engine()
        for i in range(11):
            track = engine.update({1: box(160 + (16 if i % 2 else -16))}, i / 10)[1]
        self.assertEqual(track.status, "UNRELIABLE")
        self.assertIsNone(track.ttc)

    def test_three_consecutive_frames_required(self):
        engine = self.engine()
        for i in range(13):
            track = engine.update({1: box(80 * 3 / (3 - i / 10))}, i / 10)[1]
            if i in (10, 11):
                self.assertEqual(track.status, "PENDING")
        self.assertEqual(track.status, "WARNING")

    def test_escalation_preserves_confirmed_warning(self):
        engine = self.engine()
        warned = False
        for i in range(49):
            t = i / 10
            track = engine.update({1: box(160 * 6 / (6 - t))}, t)[1]
            if track.status == "WARNING":
                warned = True
            if warned:
                self.assertIn(track.status, ("WARNING", "CRITICAL"))

    def test_video_time_independent_of_fps(self):
        for fps in (1, 10, 30):
            engine = self.engine(confirm_frames=1)
            for i in range(2 * fps + 1):
                t = i / fps
                track = engine.update({1: box(160 * 10 / (10 - t))}, t)[1]
            self.assertAlmostEqual(track.ttc, 8)

    def test_history_bounded_and_invalid_configuration(self):
        engine = self.engine()
        for i in range(1000):
            track = engine.update({1: box(160)}, i / 30)[1]
        self.assertLessEqual(len(track.history), 32)
        for options in ({"history_seconds": float("nan")}, {"critical_ttc": 4},
                        {"confirm_frames": 0}, {"min_expansion": 0}):
            with self.assertRaises(ValueError):
                self.engine(**options)
        with self.assertRaises(ValueError):
            engine.update({}, 0)


if __name__ == "__main__":
    unittest.main()
