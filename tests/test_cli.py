import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from collision_warning.__main__ import parser, run, validate_args


class ArgumentTests(unittest.TestCase):
    def test_rejects_overwriting_source_and_bad_thresholds(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.mp4"
            source.write_bytes(b"fixture")
            for options in (["--output", str(source)], ["--critical-ttc", "4"],
                            ["--history", "nan"], ["--fps", "0"]):
                args = parser().parse_args(["--source", str(source), *options])
                with self.assertRaises(ValueError):
                    validate_args(args)


@unittest.skipUnless(importlib.util.find_spec("cv2") and importlib.util.find_spec("numpy"), "Requires OpenCV and NumPy")
class PipelineTests(unittest.TestCase):
    def test_video_export_and_failed_rerun_preserves_all_outputs(self):
        import cv2
        import numpy as np

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, zone, output = root / "input.mp4", root / "zone.json", root / "result.mp4"
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
            self.assertTrue(writer.isOpened())
            try:
                for _ in range(3):
                    writer.write(np.zeros((120, 160, 3), dtype=np.uint8))
            finally:
                writer.release()
            zone.write_text("[[10,10],[150,10],[150,110],[10,110]]")
            args = parser().parse_args(["--source", str(source), "--zone", str(zone), "--output", str(output)])
            model = SimpleNamespace(names={2: "car"}, track=Mock(return_value=[SimpleNamespace(boxes=None)]))
            with patch.dict(sys.modules, {"ultralytics": SimpleNamespace(YOLO=lambda _: model)}):
                with contextlib.redirect_stdout(io.StringIO()):
                    run(args)
                summary = json.loads(output.with_suffix(".summary.json").read_text())
                self.assertEqual(summary["frames"], 3)
                self.assertEqual(summary["events"], [])
                capture = cv2.VideoCapture(str(output))
                try:
                    self.assertEqual(sum(capture.read()[0] for _ in range(4)), 3)
                finally:
                    capture.release()
                paths = [output.with_suffix(suffix) for suffix in
                         (".mp4", ".csv", ".summary.json", ".zone.json", ".ttc.svg")]
                snapshots = {path: path.read_bytes() for path in paths}
                model.track.side_effect = [[SimpleNamespace(boxes=None)], RuntimeError("simulated inference failure")]
                with self.assertRaisesRegex(RuntimeError, "simulated inference failure"):
                    run(args)
                self.assertEqual({path: path.read_bytes() for path in paths}, snapshots)
                self.assertFalse(list(root.glob(".ttc-*")))


if __name__ == "__main__":
    unittest.main()
