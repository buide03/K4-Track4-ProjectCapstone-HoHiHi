"""Local dashboard: python -m collision_warning.web --open-browser."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
from contextlib import asynccontextmanager
import json
import math
import mimetypes
from pathlib import Path
import threading
import time
import uuid
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from .__main__ import AnalysisCancelled, device_label, parser as analysis_parser, resolve_device, run
from .core import validate_polygon

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = {"queued", "running", "encoding", "cancelling"}


# Filled in by main() once torch is loaded, so importing this module stays cheap (and test-safe).
DEVICE = "CPU"


def detector_weights(root):
    """YOLO11s gives steadier dashcam tracks; fall back to the bundled YOLO11n when it is absent."""
    small = Path(root) / "yolo11s.pt"
    return small if small.exists() else Path(root) / "yolo11n.pt"


class AnalysisRequest(BaseModel):
    asset_id: str
    zone: list[list[float]] = Field(default_factory=list)
    lane_mode: Literal["manual", "auto"] = "manual"
    warning_ttc: float = Field(default=3, gt=0, le=10, allow_inf_nan=False)
    critical_ttc: float = Field(default=1.5, gt=0, le=10, allow_inf_nan=False)
    max_seconds: float = Field(default=12, ge=1, le=60, allow_inf_nan=False)
    confidence: float = Field(default=.25, ge=.1, le=.9, allow_inf_nan=False)

    @model_validator(mode="after")
    def thresholds(self):
        if self.critical_ttc >= self.warning_ttc:
            raise ValueError("Ngưỡng khẩn cấp phải nhỏ hơn ngưỡng cảnh báo.")
        return self


def inspect_video(path):
    import cv2
    capture = cv2.VideoCapture(str(path))
    try:
        ok, frame = capture.read()
        fps = capture.get(cv2.CAP_PROP_FPS)
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if not ok or not math.isfinite(fps) or fps <= 0 or frames <= 0:
            raise ValueError("Không đọc được video hoặc thông tin FPS/số frame.")
        height, width = frame.shape[:2]
        if width % 2 or height % 2:
            raise ValueError("Video cần chiều rộng và cao là số chẵn.")
        if width > 3840 or height > 2160:
            raise ValueError("Vui lòng dùng video tối đa 3840×2160.")
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise ValueError("Không tạo được ảnh xem trước.")
        return {"width": width, "height": height, "fps": fps, "frames": frames,
                "duration_s": frames / fps}, encoded.tobytes()
    finally:
        capture.release()


class Dashboard:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.assets = {}
        self.jobs = {}
        self.cancel_events = {}
        self.frames = {}
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ttc")
        self.result_dir = self.root / "outputs/web"
        self.result_dir.mkdir(parents=True, exist_ok=True)
        manifest = self.root / "data/sources.json"
        if manifest.exists():
            for item in json.loads(manifest.read_text(encoding="utf-8"))["sources"]:
                self.assets[item["id"]] = dict(item)
        for receipt in (self.root / "data/uploads").glob("*.json"):
            try:
                asset = json.loads(receipt.read_text(encoding="utf-8"))
                self.assets[asset["id"]] = asset
            except (OSError, ValueError, KeyError):
                continue
        for receipt in self.result_dir.glob("*/job.json"):
            try:
                job = json.loads(receipt.read_text(encoding="utf-8"))
                if job["status"] in ACTIVE:
                    job.update(status="failed", error="Phiên xử lý trước đã bị gián đoạn. Hãy chạy lại.")
                self.jobs[job["id"]] = job
            except (OSError, ValueError, KeyError):
                continue

    def asset(self, asset_id):
        if asset_id not in self.assets:
            raise HTTPException(404, "Không tìm thấy video.")
        item = self.assets[asset_id]
        path = (self.root / item["path"]).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise HTTPException(404, "Video chưa được tải. Chạy tools/prepare_dashcam.py hoặc tải video của bạn lên.")
        return item, path

    def describe(self, asset_id):
        item = dict(self.assets[asset_id])
        item.pop("path", None)
        item["default_lane_mode"] = "manual" if item["kind"] == "synthetic" else "auto"
        try:
            _, path = self.asset(asset_id)
            metadata, _ = inspect_video(path)
            item.update(metadata, available=True, video_url=f"/api/assets/{asset_id}/video",
                        frame_url=f"/api/assets/{asset_id}/frame")
        except (HTTPException, ValueError, OSError):
            item["available"] = False
        return item

    def job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise HTTPException(404, "Không tìm thấy phiên phân tích.")
            return copy.deepcopy(self.jobs[job_id])

    def persist(self, job_id):
        job = self.job(job_id)
        directory = self.result_dir / job_id
        directory.mkdir(exist_ok=True)
        path = directory / "job.json"
        temporary = directory / "job.json.tmp"
        temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def start(self, request):
        asset, source = self.asset(request.asset_id)
        metadata, _ = inspect_video(source)
        polygon = []
        if request.lane_mode == "manual":
            try:
                polygon = validate_polygon(request.zone, metadata["width"], metadata["height"])
            except (ValueError, TypeError) as exc:
                raise HTTPException(422, str(exc)) from exc
        with self.lock:
            if any(job["status"] in ACTIVE for job in self.jobs.values()):
                raise HTTPException(409, "Đang có một phiên phân tích. Hãy chờ hoặc dừng phiên hiện tại.")
            job_id = uuid.uuid4().hex
            self.cancel_events[job_id] = threading.Event()
            self.jobs[job_id] = {"id": job_id, "asset_id": request.asset_id, "title": asset["title"],
                                 "kind": asset["kind"], "status": "queued", "created_at": time.time(),
                                 "config": request.model_dump(), "frames": 0, "total_frames": min(metadata["frames"], math.ceil(request.max_seconds * metadata["fps"])),
                                 "time_s": 0, "ttc_s": None, "tracks": [], "events": [], "series": [],
                                 "processing_fps": 0, "error": None}
        self.persist(job_id)
        self.pool.submit(self.analyze, job_id, source, polygon)
        return self.job(job_id)

    def analyze(self, job_id, source, polygon):
        import cv2
        directory = self.result_dir / job_id
        zone_path = directory / "zone.json"
        zone_path.write_text(json.dumps(polygon), encoding="utf-8")
        config = self.job(job_id)["config"]
        output = directory / "result.mp4"
        args = analysis_parser().parse_args(["--source", str(source), "--zone", str(zone_path),
                                            "--output", str(output), "--model", str(detector_weights(self.root)),
                                            "--max-seconds", str(config["max_seconds"]), "--conf", str(config["confidence"]),
                                            "--lane-mode", config.get("lane_mode", "manual"),
                                            "--warning-ttc", str(config["warning_ttc"]), "--critical-ttc", str(config["critical_ttc"])])
        cancel = self.cancel_events[job_id]

        def progress(payload, frame):
            image = None
            if payload["frames"] % 3 == 0 or payload["frames"] == 1:
                preview = frame
                if frame.shape[1] > 960:
                    preview = cv2.resize(frame, (960, round(frame.shape[0] * 960 / frame.shape[1])))
                ok, encoded = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 78])
                if ok:
                    image = encoded.tobytes()
            with self.lock:
                job = self.jobs[job_id]
                job.update(payload)
                if job["status"] != "cancelling":
                    job["status"] = "running"
                statuses = {track.get("alert", track["status"]) for track in payload["tracks"]}
                status = "LANE_UNAVAILABLE" if payload.get("lane", {}).get("status") == "unavailable" else "CRITICAL" if "CRITICAL" in statuses else "WARNING" if "WARNING" in statuses else "MONITORING"
                job["series"].append({"time_s": payload["time_s"], "ttc_s": payload["ttc_s"],
                                      "target_id": payload["target_id"], "target_reason": payload.get("target_reason"), "status": status,
                                      "tracks_count": len(payload["tracks"]), "tracks": payload["tracks"],
                                      "lane": payload.get("lane")})
                if image:
                    self.frames[job_id] = image

        try:
            with self.lock:
                self.jobs[job_id]["status"] = "running"
            run(args, progress=progress, cancelled=cancel.is_set)
            if cancel.is_set():
                raise AnalysisCancelled()
            with self.lock:
                self.jobs[job_id]["status"] = "encoding"
            # VP8/WebM is playable in browsers; preserve the original MP4 download too.
            cap = cv2.VideoCapture(str(output))
            writer = None
            try:
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("Không đọc được video kết quả.")
                h, w = frame.shape[:2]
                writer = cv2.VideoWriter(str(directory / "result.webm"), cv2.VideoWriter_fourcc(*"VP80"), cap.get(cv2.CAP_PROP_FPS), (w, h))
                if not writer.isOpened():
                    raise RuntimeError("Không tạo được WebM để phát trên trình duyệt.")
                while ok:
                    if cancel.is_set():
                        raise AnalysisCancelled()
                    writer.write(frame)
                    ok, frame = cap.read()
            finally:
                cap.release()
                if writer is not None:
                    writer.release()
            summary = json.loads(output.with_suffix(".summary.json").read_text(encoding="utf-8"))
            with self.lock:
                self.jobs[job_id].update(status="completed", summary=summary, completed_at=time.time(),
                                        video_url=f"/api/jobs/{job_id}/files/video", chart_url=f"/api/jobs/{job_id}/files/chart")
        except AnalysisCancelled:
            with self.lock:
                self.jobs[job_id].update(status="cancelled", error="Đã dừng theo yêu cầu.")
        except Exception as exc:
            with self.lock:
                self.jobs[job_id].update(status="failed", error=str(exc))
        finally:
            self.persist(job_id)


def create_app(root=ROOT):
    store = Dashboard(root)

    @asynccontextmanager
    async def lifespan(app):
        yield
        with store.lock:
            for event in store.cancel_events.values():
                event.set()
        store.pool.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="TTC Studio", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.dashboard = store

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method in {"POST", "PUT", "DELETE"} and origin and origin != str(request.base_url).rstrip("/"):
            return Response("Cross-origin writes are not allowed", status_code=403)
        return await call_next(request)

    @app.get("/api/health")
    def health():
        with store.lock:
            active = next((key for key, job in store.jobs.items() if job["status"] in ACTIVE), None)
        return {"ok": True, "active_job_id": active, "device": DEVICE,
                "model": f"{detector_weights(store.root).stem.upper()} + ByteTrack"}

    @app.get("/api/assets")
    def assets():
        return [store.describe(key) for key in store.assets]

    @app.get("/api/evaluation")
    def evaluation():
        directory = store.root / "outputs/benchmarks/synthetic"
        summary = directory / "summary.json"
        try:
            result = json.loads(summary.read_text(encoding="utf-8")) if summary.is_file() else None
        except (OSError, ValueError):
            result = None
        return {"synthetic": result, "comparison_available": (directory / "comparison.jpg").is_file(),
                "public_benchmark": "TuSimple", "public_score": None}

    @app.get("/api/evaluation/files/{kind}")
    def evaluation_file(kind: str):
        mapping = {"guide": (store.root / "benchmarks/README.md", "text/markdown; charset=utf-8"),
                   "summary": (store.root / "outputs/benchmarks/synthetic/summary.json", "application/json"),
                   "comparison": (store.root / "outputs/benchmarks/synthetic/comparison.jpg", "image/jpeg")}
        if kind not in mapping:
            raise HTTPException(404, "Không tìm thấy tài liệu đánh giá.")
        path, media_type = mapping[kind]
        if not path.is_file():
            raise HTTPException(404, "Chưa có kết quả. Chạy tools/benchmark_lanes.py trên máy này.")
        return FileResponse(path, media_type=media_type, filename=path.name)

    @app.get("/api/assets/{asset_id}/video")
    def asset_video(asset_id: str):
        _, path = store.asset(asset_id)
        return FileResponse(path, media_type=mimetypes.guess_type(path)[0] or "video/mp4")

    @app.get("/api/assets/{asset_id}/frame")
    def asset_frame(asset_id: str):
        _, path = store.asset(asset_id)
        _, jpeg = inspect_video(path)
        return Response(jpeg, media_type="image/jpeg")

    @app.get("/api/assets/{asset_id}/lane")
    def asset_lane(asset_id: str):
        import cv2
        from .lanes import LaneEstimator
        _, path = store.asset(asset_id)
        capture = cv2.VideoCapture(str(path))
        try:
            ok, frame = capture.read()
            if not ok:
                raise HTTPException(422, "Không đọc được frame để xác định làn.")
            return LaneEstimator().update(frame).as_dict()
        finally:
            capture.release()

    @app.post("/api/uploads")
    async def upload(file: UploadFile):
        if Path(file.filename or "").suffix.lower() not in {".mp4", ".mov", ".avi", ".webm", ".mkv"}:
            raise HTTPException(422, "Chọn video MP4, MOV, AVI, WebM hoặc MKV.")
        asset_id = "upload-" + uuid.uuid4().hex
        directory = store.root / "data/uploads"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (asset_id + Path(file.filename).suffix.lower())
        size = 0
        try:
            with path.open("wb") as target:
                while block := await file.read(1024 * 1024):
                    size += len(block)
                    if size > 200 * 1024 * 1024:
                        raise HTTPException(413, "Video vượt quá giới hạn 200 MB.")
                    target.write(block)
            try:
                inspect_video(path)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            item = {"id": asset_id, "title": Path(file.filename).name[:120], "kind": "upload",
                    "description": "Video của bạn · lưu trên máy này", "path": str(path.relative_to(store.root)),
                    "attribution": "Video do người dùng cung cấp", "source_url": None,
                    "zone": [[.45, .55], [.57, .55], [.85, .98], [.16, .98]]}
            store.assets[asset_id] = item
            path.with_suffix(".json").write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")
            return store.describe(asset_id)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.get("/api/jobs")
    def jobs():
        with store.lock:
            return sorted([dict(value) for value in store.jobs.values()], key=lambda job: job["created_at"], reverse=True)[:30]

    @app.post("/api/jobs", status_code=202)
    def start_job(request: AnalysisRequest):
        return store.start(request)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return store.job(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        job = store.job(job_id)
        if job["status"] not in ACTIVE:
            return job
        with store.lock:
            store.cancel_events[job_id].set()
            store.jobs[job_id]["status"] = "cancelling"
        return store.job(job_id)

    @app.get("/api/jobs/{job_id}/frame")
    def job_frame(job_id: str):
        store.job(job_id)
        with store.lock:
            frame = store.frames.get(job_id)
        if not frame:
            raise HTTPException(404, "Chưa có frame xem trước.")
        return Response(frame, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/jobs/{job_id}/files/{kind}")
    def job_file(job_id: str, kind: str):
        job = store.job(job_id)
        mapping = {"video": ("result.webm", "video/webm"), "mp4": ("result.mp4", "video/mp4"),
                   "csv": ("result.csv", "text/csv"), "summary": ("result.summary.json", "application/json"),
                   "zone": ("result.zone.json", "application/json"), "chart": ("result.ttc.svg", "image/svg+xml"),
                   "lanes": ("result.lanes.json", "application/json")}
        if job["status"] != "completed" or kind not in mapping:
            raise HTTPException(404, "Kết quả chưa sẵn sàng.")
        name, media_type = mapping[kind]
        path = store.result_dir / job_id / name
        if not path.is_file():
            raise HTTPException(404, "Không tìm thấy file kết quả.")
        return FileResponse(path, media_type=media_type, filename=name if kind in {"mp4", "csv", "summary", "zone", "lanes"} else None)

    app.mount("/", StaticFiles(directory=ROOT / "ui", html=True), name="dashboard")
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    import uvicorn
    global DEVICE
    DEVICE = device_label(resolve_device("auto", load=True))
    if args.open_browser:
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
