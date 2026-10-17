import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

AVAILABLE = all(importlib.util.find_spec(name) for name in ("fastapi", "httpx", "cv2", "numpy", "multipart"))


@unittest.skipUnless(AVAILABLE, "Requires dashboard dependencies")
class DashboardTests(unittest.TestCase):
    def setUp(self):
        import cv2
        import numpy as np
        from fastapi.testclient import TestClient
        from collision_warning.web import create_app

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        self.video = self.root / "data/input.mp4"
        writer = cv2.VideoWriter(str(self.video), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
        self.assertTrue(writer.isOpened())
        for _ in range(4):
            writer.write(np.zeros((120, 160, 3), dtype=np.uint8))
        writer.release()
        manifest = {"sources": [{"id": "fixture", "path": "data/input.mp4", "title": "Fixture", "kind": "dashcam", "zone": [[.1,.1],[.9,.1],[.9,.9],[.1,.9]]}]}
        (self.root / "data/sources.json").write_text(json.dumps(manifest))
        self.app = create_app(self.root)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.app.state.dashboard.pool.shutdown(wait=True)
        self.temp.cleanup()

    def test_assets_preview_and_video_byte_ranges(self):
        asset = self.client.get("/api/assets").json()[0]
        self.assertTrue(asset["available"])
        self.assertNotIn("path", asset)
        self.assertEqual(asset["frames"], 4)
        self.assertEqual(self.client.get(asset["frame_url"]).headers["content-type"], "image/jpeg")
        partial = self.client.get(asset["video_url"], headers={"Range": "bytes=0-19"})
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(len(partial.content), 20)
        self.assertEqual(self.client.get("/api/assets/unknown/video").status_code, 404)

    def test_validation_single_job_and_cancel(self):
        payload = {"asset_id": "fixture", "zone": [[10,10],[150,10],[150,110],[10,110]]}
        with patch.object(self.app.state.dashboard.pool, "submit"):
            bad = dict(payload, critical_ttc=4)
            self.assertEqual(self.client.post("/api/jobs", json=bad).status_code, 422)
            bad = dict(payload, zone=[[10,10],[999,10],[150,110]])
            self.assertEqual(self.client.post("/api/jobs", json=bad).status_code, 422)
            response = self.client.post("/api/jobs", json=payload)
            self.assertEqual(response.status_code, 202)
            job_id = response.json()["id"]
            self.assertEqual(self.client.post("/api/jobs", json=payload).status_code, 409)
            self.assertEqual(self.client.get(f"/api/jobs/{job_id}/files/mp4").status_code, 404)
            self.assertEqual(self.client.post(f"/api/jobs/{job_id}/cancel").json()["status"], "cancelling")
            self.assertTrue(self.app.state.dashboard.cancel_events[job_id].is_set())

    def test_auto_preview_and_start_without_manual_polygon(self):
        self.assertEqual(self.client.get("/api/assets").json()[0]["default_lane_mode"], "auto")
        preview = self.client.get("/api/assets/fixture/lane")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["status"], "unavailable")
        self.assertEqual(preview.json()["polygon"], [])
        with patch.object(self.app.state.dashboard.pool, "submit"):
            response = self.client.post("/api/jobs", json={"asset_id": "fixture", "lane_mode": "auto"})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["config"]["lane_mode"], "auto")

    def test_new_ui_and_evaluation_do_not_claim_unmeasured_accuracy(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Mỗi giây đều quan trọng.", response.text)
        self.assertIn('id="laneMode"', response.text)
        self.assertIn('id="benchmarkRows"', response.text)
        self.assertEqual(self.client.get("/app.js").status_code, 200)
        self.assertEqual(self.client.get("/style.css").status_code, 200)
        report = self.client.get("/api/evaluation").json()
        self.assertIsNone(report["synthetic"])
        self.assertIsNone(report["public_score"])
        self.assertFalse(report["comparison_available"])
        self.assertEqual(self.client.get("/api/evaluation/files/unknown").status_code, 404)
        directory = self.root / "outputs/benchmarks/synthetic"
        directory.mkdir(parents=True)
        (directory / "summary.json").write_text('{"methods": {}}', encoding="utf-8")
        self.assertEqual(self.client.get("/api/evaluation").json()["synthetic"], {"methods": {}})
        self.assertEqual(self.client.get("/api/evaluation/files/summary").status_code, 200)

    def test_job_preserves_scene_warning_without_ttc_and_playback_snapshot(self):
        import cv2
        from collision_warning.web import AnalysisRequest

        store = self.app.state.dashboard
        request = AnalysisRequest(asset_id="fixture", zone=[[10,10],[150,10],[150,110],[10,110]])
        with patch.object(store.pool, "submit"):
            job = store.start(request)
        track = {"id": 7, "class_name": "car", "box": [70,50,100,100], "ttc_s": None,
                 "status": "OUTSIDE_ZONE", "in_path": False, "alert": "WARNING",
                 "lane_relation": "CUT_IN", "alert_reason": "vehicle cutting in"}

        def fake_run(args, progress, cancelled):
            output = Path(args.output)
            output.write_bytes(self.video.read_bytes())
            output.with_suffix(".summary.json").write_text('{"frames":4}', encoding="utf-8")
            capture = cv2.VideoCapture(str(self.video))
            ok, frame = capture.read()
            capture.release()
            self.assertTrue(ok)
            progress({"frames": 1, "time_s": 0, "total_frames": 4, "processing_fps": 10,
                      "tracks": [track], "ttc_s": None, "target_id": 7, "target_reason": "CUT_IN",
                      "events": [{"time_s":0, "track_id":7, "status":"WARNING", "estimated_ttc_s":None}],
                      "lane": {"status":"manual"}}, frame)

        with patch("collision_warning.web.run", side_effect=fake_run):
            store.analyze(job["id"], self.video, request.zone)
        result = store.job(job["id"])
        self.assertEqual(result["status"], "completed", result["error"])
        sample = result["series"][0]
        self.assertEqual(sample["status"], "WARNING")
        self.assertEqual(sample["target_reason"], "CUT_IN")
        self.assertEqual(sample["tracks"][0]["id"], 7)
        self.assertIsNone(sample["ttc_s"])

    def test_upload_and_cross_origin_writes(self):
        invalid = self.client.post("/api/uploads", files={"file": ("fake.mp4", b"not video", "video/mp4")})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(list((self.root / "data/uploads").iterdir()), [])
        uploaded = self.client.post("/api/uploads", files={"file": ("../test.mp4", self.video.read_bytes(), "video/mp4")})
        self.assertEqual(uploaded.status_code, 200)
        self.assertEqual(uploaded.json()["title"], "test.mp4")
        self.assertTrue(uploaded.json()["available"])
        blocked = self.client.post("/api/jobs", json={}, headers={"Origin": "https://unrelated.example"})
        self.assertEqual(blocked.status_code, 403)


if __name__ == "__main__":
    unittest.main()
