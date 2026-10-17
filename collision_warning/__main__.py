"""Run with python -m collision_warning --source dashcam.mp4."""

import argparse
import csv
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

from .core import WarningEngine, validate_polygon
from .report import write_ttc_chart
from .scene import CANDIDATES, SceneAnalyzer

TRACKER = Path(__file__).resolve().parent / "trackers" / "bytetrack.yaml"
# Corridor straight ahead of a centred camera, used only when no lane has been confirmed recently.
DEFAULT_CORRIDOR = [[.45, .58], [.55, .58], [.82, .97], [.18, .97]]


class AnalysisCancelled(RuntimeError):
    """The caller cancelled analysis before publishing its outputs."""


def parser():
    p = argparse.ArgumentParser(description="Camera-only forward collision warning with estimated optical TTC")
    p.add_argument("--source", required=True, help="Local forward-facing dashcam video path")
    p.add_argument("--output", default="outputs/ttc.mp4")
    p.add_argument("--zone", help="JSON file containing pixel vertices [[x,y], ...]; otherwise select interactively")
    p.add_argument("--model", default="yolo11s.pt",
                   help="COCO vehicle detector weights (yolo11s: steadier tracks; yolo11n.pt: faster on CPU)")
    p.add_argument("--tracker", default=str(TRACKER), help="Ultralytics tracker YAML")
    p.add_argument("--device", default="auto", help="auto (first CUDA GPU if available, else cpu), cpu, 0, ...")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--conf", type=float, default=0.1)
    p.add_argument("--history", type=float, default=1.0)
    p.add_argument("--warning-ttc", type=float, default=3.0, help="Demo warning threshold, seconds")
    p.add_argument("--critical-ttc", type=float, default=1.5, help="Demo critical threshold, seconds")
    p.add_argument("--min-expansion", type=float, default=.02, help="Minimum fractional width growth per second")
    p.add_argument("--confirm-frames", type=int, default=3)
    p.add_argument("--fps", type=float, help="Override missing/incorrect source FPS")
    p.add_argument("--preview", action="store_true", help="Show video window; Q stops and finalizes output")
    p.add_argument("--max-seconds", type=float, help="Analyze only the first N seconds")
    p.add_argument("--lane-mode", choices=["manual", "auto"],
                   help="Ego lane: auto (lane markings, default) or manual (--zone or click); --zone implies manual")
    p.add_argument("--lane-fallback", choices=["memory", "none"], default="memory",
                   help="When markings are lost: reuse the last lane, then a default corridor (memory) or pause TTC (none)")
    p.add_argument("--lane-memory", type=float, default=2.0, help="Seconds to reuse the last confirmed lane")
    p.add_argument("--lane-width", type=int, default=960,
                   help="Automatic lane detection runs on frames downscaled to this width (speed vs. lane recall)")
    p.add_argument("--lost-grace", type=float, default=.3,
                   help="Seconds a briefly missed track keeps its TTC and lane history")
    p.add_argument("--entry-horizon", type=float, default=1.0, help="Flag a cut-in when lane entry is predicted within N seconds")
    p.add_argument("--near-ratio", type=float, default=.55,
                   help="Cut-in is critical when the vehicle bottom is this far down the lane (0=lane top, 1=bottom)")
    return p


def choose_zone(frame, cv2):
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise ValueError("No display available. Supply --zone zone.json to run without a GUI.")
    points = []
    name = "Zone: click vertices | Enter: confirm | R: reset | Q/Esc: cancel"
    cv2.namedWindow(name, cv2.WINDOW_NORMAL)

    def click(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append([x, y])

    cv2.setMouseCallback(name, click)
    try:
        while True:
            canvas = frame.copy()
            for point in points:
                cv2.circle(canvas, tuple(point), 5, (0, 220, 255), -1)
            if len(points) > 1:
                import numpy as np
                cv2.polylines(canvas, [np.array(points, dtype=np.int32)], len(points) > 2, (0, 220, 255), 2)
            cv2.imshow(name, canvas)
            key = cv2.waitKey(20) & 255
            if key in (27, ord("q")) or cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE) < 1:
                raise ValueError("Zone selection cancelled.")
            if key == ord("r"):
                points.clear()
            if key in (10, 13):
                try:
                    return validate_polygon(points, frame.shape[1], frame.shape[0])
                except ValueError as exc:
                    print(exc, file=sys.stderr)
    finally:
        cv2.destroyWindow(name)


COLORS = {"WARMUP": (180, 180, 180), "PENDING": (180, 180, 180),
          "CLEAR": (60, 210, 60), "WARNING": (0, 170, 255), "CRITICAL": (40, 40, 240),
          "NOT_CLOSING": (60, 210, 60), "UNRELIABLE": (180, 180, 180),
          "OUTSIDE_ZONE": (130, 130, 130), "LANE_UNAVAILABLE": (180, 180, 180)}


def render(frame, boxes, tracks, polygon, fps, cv2, np, lane=None, scene=None):
    if len(polygon) >= 3:
        contour = np.array(polygon, dtype=np.int32)
        overlay = frame.copy()
        cv2.fillPoly(overlay, [contour], (180, 90, 20))
        cv2.addWeighted(overlay, 0.20, frame, 0.80, 0, frame)
        # Blue: lane seen in this frame; amber: held/remembered lane; grey: default corridor.
        color = ({"held": (0, 180, 255), "memory": (0, 180, 255), "fallback": (170, 170, 170)}
                 .get(lane["status"], (255, 160, 50)) if lane else (255, 160, 50))
        cv2.polylines(frame, [contour], True, color, 2)
    labels = []
    target_id = scene.target_id
    for key, box, class_name in boxes:
        track = tracks.get(key)
        state = scene.tracks.get(key)
        if state is None:
            status, color = "UNTRACKED", (180, 180, 180)
        elif state.alert != "CLEAR":
            status, color = f"{state.relation} {state.alert}", COLORS[state.alert]
        else:
            status = state.relation
            color = COLORS["CLEAR"] if state.relation in CANDIDATES else COLORS["OUTSIDE_ZONE"]
        x1, y1, x2, y2 = map(int, box)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3 if key == target_id and key is not None else 2)
        text = f"#{key if key is not None else '?'} {class_name} {status}"
        if state and state.effective_ttc is not None:
            text += f" TTC~{state.effective_ttc:.1f}s"
        elif track and track.status in ("WARMUP", "UNRELIABLE", "NOT_CLOSING") and state and state.relation in CANDIDATES:
            text += f" ({track.status.lower()})"
        if state and state.braking:
            text += " BRAKING"
        if key is not None and key == target_id:
            text = "* " + text
        labels.append((text, x1, y1, color))
        if track:
            foot = (int((x1 + x2) / 2), y2)
            cv2.circle(frame, foot, 4, color, -1)
    occupied = []
    for text, x, y, color in labels:
        (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, .45, 1)
        x = max(0, min(x, frame.shape[1] - tw - 6))
        y = max(75, y - 6)
        while y > 75 and any(x < right and x + tw + 6 > left and
                             y - th - 4 < bottom and y + baseline > top
                             for left, top, right, bottom in occupied):
            y = max(75, y - th - baseline - 6)
        rectangle = (x, y - th - 4, x + tw + 6, y + baseline)
        occupied.append(rectangle)
        cv2.rectangle(frame, rectangle[:2], rectangle[2:], (25, 25, 25), -1)
        cv2.putText(frame, text, (x + 3, y - 2), cv2.FONT_HERSHEY_SIMPLEX, .45, color, 1)
    active = {s.alert for s in scene.tracks.values()}
    severity = "CRITICAL" if "CRITICAL" in active else "WARNING" if "WARNING" in active else "CLEAR"
    label = severity if severity != "CLEAR" else "MONITORING"
    if lane and lane["status"] == "unavailable":
        label = "LANE UNAVAILABLE"
    target = scene.tracks.get(target_id)
    if target is not None:
        label += f" | target #{target_id} {target.reason}"
        label += f" TTC {target.effective_ttc:.1f}s" if target.effective_ttc is not None else " TTC n/a"
    else:
        label += " | no in-path vehicle"
    color = COLORS[severity]
    cv2.rectangle(frame, (0, 0), (frame.shape[1], 56), (25, 25, 25), -1)
    cv2.putText(frame, f"{label} | {fps:.1f} FPS", (10, 23), cv2.FONT_HERSHEY_SIMPLEX, .55, color, 2)
    lane_label = f"auto lane: {lane['status']} ({lane['confidence']:.0%})" if lane else "manual road region"
    cv2.putText(frame, f"Optical TTC | {lane_label} | no metric distance", (10, 46),
                cv2.FONT_HERSHEY_SIMPLEX, .4, (220, 220, 220), 1)
    return frame


def resolve_device(device, load=False):
    """'auto' picks the first CUDA GPU when PyTorch was built with CUDA, otherwise the CPU.

    Uses the torch already imported by Ultralytics; ``load=True`` imports it (slow, for the web server)."""
    if device != "auto":
        return device
    torch = sys.modules.get("torch")
    if torch is None and load:
        try:
            import torch
        except ImportError:
            torch = None
    return "0" if torch is not None and torch.cuda.is_available() else "cpu"


def device_label(device):
    torch = sys.modules.get("torch")
    if device == "cpu" or torch is None:
        return "CPU"
    return f"GPU {torch.cuda.get_device_name(int(str(device).split(',')[0]))}"


def estimate_lane(estimator, frame, timestamp, boxes, cv2, max_width=960):
    """Lane detection on a frame downscaled to max_width (its thresholds are relative to image size);
    coordinates are mapped back to the full frame. On 720p, 960 px halves the cost and keeps most
    detections; 640 px is ~3x faster but loses more frames to lane memory."""
    scale = min(1.0, max_width / frame.shape[1])
    if scale < 1:
        frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        boxes = [[v * scale for v in box] for box in boxes]
    lane = estimator.update(frame, timestamp, boxes).as_dict()
    if scale < 1:
        for key in ("polygon", "left", "right"):
            lane[key] = [[round(x / scale, 2), round(y / scale, 2)] for x, y in lane[key]]
    return lane


def validate_args(args):
    source, output = Path(args.source), Path(args.output)
    if not source.is_file():
        raise ValueError(f"Video does not exist: {source}")
    if output.suffix.lower() != ".mp4":
        raise ValueError("Output must have .mp4 extension.")
    # Every artifact must be distinct from the input and zone configuration.
    artifacts = [output, output.with_suffix(".csv"), output.with_suffix(".summary.json"),
                 output.with_suffix(".zone.json"), output.with_suffix(".ttc.svg"), output.with_suffix(".lanes.json")]
    protected = {source.resolve()}
    if args.zone:
        protected.add(Path(args.zone).resolve())
    if any(p.resolve() in protected for p in artifacts):
        raise ValueError("Output artifacts must not overwrite input video or zone file.")
    if args.fps is not None and (not math.isfinite(args.fps) or args.fps <= 0):
        raise ValueError("--fps must be finite and positive.")
    if not 0 < args.conf <= 1 or args.imgsz < 32:
        raise ValueError("Confidence must be in (0, 1]; image size must be >= 32.")
    if not all(math.isfinite(value) and value > 0 for value in
               (args.history, args.warning_ttc, args.critical_ttc, args.min_expansion)):
        raise ValueError("History, TTC thresholds and minimum expansion must be finite and positive.")
    if args.critical_ttc >= args.warning_ttc or args.confirm_frames < 1:
        raise ValueError("Require 0 < critical TTC < warning TTC and confirm-frames >= 1.")
    if args.max_seconds is not None and (not math.isfinite(args.max_seconds) or args.max_seconds <= 0):
        raise ValueError("--max-seconds must be finite and positive.")
    if not (math.isfinite(args.entry_horizon) and args.entry_horizon > 0 and 0 < args.near_ratio <= 1):
        raise ValueError("--entry-horizon must be positive and --near-ratio in (0, 1].")
    if args.lane_width < 160:
        raise ValueError("--lane-width must be at least 160 pixels.")
    if not all(math.isfinite(v) and v >= 0 for v in (args.lane_memory, args.lost_grace)):
        raise ValueError("--lane-memory and --lost-grace must be finite and non-negative.")
    if args.lane_mode is None:
        args.lane_mode = "manual" if args.zone else "auto"
    return source, output, artifacts


def run(args, progress=None, cancelled=None):
    source, output, destinations = validate_args(args)
    import cv2
    import numpy as np
    from ultralytics import YOLO
    args.device = resolve_device(args.device)

    capture = cv2.VideoCapture(str(source))
    writer = None
    staging = None
    try:
        ok, frame = capture.read()
        if not ok:
            raise ValueError("Cannot decode first video frame.")
        height, width = frame.shape[:2]
        if width % 2 or height % 2:
            raise ValueError("MP4 output requires even frame width and height; resize input first.")
        fps = args.fps if args.fps is not None else capture.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("Source FPS unavailable; provide --fps.")
        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_limit = math.ceil(args.max_seconds * fps) if args.max_seconds is not None else None
        if frame_limit is not None:
            total_frames = min(total_frames, frame_limit) if total_frames > 0 else frame_limit
        lane_estimator = None
        if args.lane_mode == "auto":
            from .lanes import LaneEstimator
            lane_estimator = LaneEstimator()
            polygon = []
        else:
            polygon = (validate_polygon(json.loads(Path(args.zone).read_text()), width, height)
                       if args.zone else choose_zone(frame, cv2))
        engine = WarningEngine(polygon, (width, height), args.history, args.warning_ttc,
                               args.critical_ttc, args.confirm_frames, args.min_expansion, args.lost_grace)
        scene = SceneAnalyzer(args.warning_ttc, args.critical_ttc, args.confirm_frames,
                              entry_horizon=args.entry_horizon, near_ratio=args.near_ratio, frame_width=width,
                              lost_grace=args.lost_grace)
        corridor = [[round(x * (width - 1), 2), round(y * (height - 1), 2)] for x, y in DEFAULT_CORRIDOR]
        last_lane = None
        scene_events = []
        model = YOLO(args.model)
        vehicle_classes = [key for key, value in model.names.items()
                           if value in {"car", "motorcycle", "bus", "truck"}]
        if not vehicle_classes:
            raise ValueError("Model must contain car, motorcycle, bus or truck classes.")
        output.parent.mkdir(parents=True, exist_ok=True)
        # Publish only completed results; a failed rerun keeps previous outputs intact.
        staging = tempfile.TemporaryDirectory(prefix=".ttc-", dir=output.parent)
        artifacts = [Path(staging.name) / path.name for path in destinations]
        writer = cv2.VideoWriter(str(artifacts[0]), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
        if not writer.isOpened():
            raise ValueError("Cannot create MP4 output; check codec support and output path.")
        artifacts[3].write_text(json.dumps(polygon, indent=2) + "\n", encoding="utf-8")
        frames = 0
        processing_seconds = 0.0
        events = []
        series = []
        lane_records = []
        previous = {}
        stopped = False
        with artifacts[1].open("w", newline="", encoding="utf-8") as csv_file:
            log = csv.writer(csv_file)
            log.writerow(["frame", "time_s", "track_id", "class_name", "x1", "y1", "x2", "y2",
                          "in_path", "width_px", "expansion_per_s", "ttc_s", "fit_error", "status", "reason", "selected_target",
                          "lane_relation", "lane_penetration", "lateral_speed", "time_to_entry_s", "braking",
                          "effective_ttc_s", "alert", "alert_reason"])
            while ok:
                if cancelled is not None and cancelled():
                    raise AnalysisCancelled("Analysis cancelled.")
                started = time.perf_counter()
                # Class-agnostic NMS: one vehicle must not appear as both "bus" and "truck".
                results = model.track(frame, persist=True, tracker=args.tracker, classes=vehicle_classes,
                                      agnostic_nms=True, conf=args.conf, imgsz=args.imgsz, device=args.device, verbose=False)
                observations, boxes, names = {}, [], {}
                detected = results[0].boxes
                if detected is not None:
                    coords = detected.xyxy.cpu().tolist()
                    ids = detected.id.int().cpu().tolist() if detected.id is not None else [None] * len(coords)
                    classes = detected.cls.int().cpu().tolist()
                    for key, box, class_id in zip(ids, coords, classes):
                        class_name = model.names[class_id]
                        boxes.append((key, box, class_name))
                        if key is not None:
                            observations[key] = tuple(box)
                            names[key] = class_name
                timestamp = frames / fps
                lane = None
                lane_available = True
                if lane_estimator is not None:
                    lane = estimate_lane(lane_estimator, frame, timestamp, [box for _, box, _ in boxes], cv2,
                                         args.lane_width)
                    if lane["status"] in {"detected", "held"} and lane["confidence"] >= .25:
                        last_lane = (timestamp, lane["polygon"])
                    elif args.lane_fallback == "memory" and last_lane and timestamp - last_lane[0] <= args.lane_memory:
                        # Markings are often hidden exactly when a vehicle cuts in; keep the last confirmed lane.
                        lane = {**lane, "status": "memory", "polygon": last_lane[1],
                                "reason": "last confirmed lane reused while markings are hidden"}
                    elif args.lane_fallback == "memory":
                        lane = {**lane, "status": "fallback", "polygon": corridor,
                                "reason": "no lane markings; default corridor ahead of the camera"}
                    else:
                        lane = {**lane, "status": "unavailable", "polygon": [], "reason": "lane confidence below gating threshold"}
                    polygon = lane["polygon"]
                    engine.polygon = polygon
                    lane_available = lane["status"] != "unavailable"
                    lane_records.append({"frame": frames, "time_s": timestamp, **lane})
                # Scene first: lane relations (with the lane extended towards the horizon and cut-ins)
                # decide which tracks get TTC; then braking, target and alerts use that TTC.
                relations = scene.observe(observations, polygon if lane_available else [], timestamp)
                tracks = engine.update(observations, timestamp, lane_available=lane_available,
                                       in_path={key: relation in CANDIDATES for key, relation in relations.items()})
                state = scene.assess(tracks, timestamp)
                scene_events.extend({"frame": frames, **event} for event in state.events)
                target_id = state.target_id
                target = state.tracks.get(target_id)
                series.append((timestamp, (target_id, target.effective_ttc)
                               if target is not None and target.effective_ttc is not None else None))
                for key, track in tracks.items():
                    info = state.tracks[key]
                    log.writerow([frames, round(timestamp, 6), key, names[key], *observations[key],
                                  track.in_path, track.width_px, track.expansion_per_s, track.ttc,
                                  track.fit_error, track.status, track.reason, key == target_id,
                                  info.relation, info.penetration, info.lateral_speed, info.time_to_entry,
                                  info.braking, info.effective_ttc, info.alert, info.reason])
                    if info.alert in ("WARNING", "CRITICAL") and previous.get(key) != info.alert:
                        events.append({"frame": frames, "time_s": timestamp, "track_id": key,
                                       "status": info.alert, "estimated_ttc_s": info.effective_ttc,
                                       "lane_relation": info.relation, "reason": info.reason})
                previous = {key: info.alert for key, info in state.tracks.items()}
                elapsed = time.perf_counter() - started
                displayed_fps = frames / processing_seconds if processing_seconds else 1 / max(elapsed, 1e-9)
                render(frame, boxes, tracks, polygon, displayed_fps, cv2, np, lane, state)
                writer.write(frame)
                processing_seconds += time.perf_counter() - started
                frames += 1
                if progress is not None:
                    progress({"frames": frames, "total_frames": total_frames, "time_s": timestamp,
                              "processing_fps": frames / max(processing_seconds, 1e-9),
                              "tracks": [{"id": key, "class_name": names[key], "box": observations[key],
                                          "ttc_s": state.tracks[key].effective_ttc, "status": track.status,
                                          "reason": track.reason, "in_path": track.in_path,
                                          "lane_relation": state.tracks[key].relation, "alert": state.tracks[key].alert,
                                          "alert_reason": state.tracks[key].reason, "braking": state.tracks[key].braking}
                                         for key, track in tracks.items()],
                              "target_id": target_id, "target_reason": state.target_reason,
                              "ttc_s": target.effective_ttc if target else None,
                              "scene_events": list(scene_events),
                              "events": list(events), "lane": lane or {"status": "manual", "confidence": None, "polygon": polygon}}, frame)
                if args.preview:
                    cv2.imshow("Forward collision warning (Q to stop)", frame)
                    if cv2.waitKey(1) & 255 in (ord("q"), 27):
                        stopped = True
                        break
                if frames % 100 == 0:
                    print(f"Processed {frames} frames", flush=True)
                if frame_limit is not None and frames >= frame_limit:
                    break
                ok, frame = capture.read()
        summary = {"source": str(source), "output": str(output), "frames": frames,
                   "model": args.model, "device": args.device, "device_name": device_label(args.device),
                   "imgsz": args.imgsz, "confidence": args.conf,
                   "source_fps": fps, "video_duration_s": frames / fps,
                   "processing_fps": frames / max(processing_seconds, 1e-9),
                   "stopped_by_user": stopped, "history_s": args.history,
                   "max_seconds": args.max_seconds,
                   "lane_mode": args.lane_mode, "lane_fallback": args.lane_fallback,
                   "lane_memory_frames": sum(record["status"] == "memory" for record in lane_records),
                   "lane_fallback_frames": sum(record["status"] == "fallback" for record in lane_records),
                   "tracker": Path(args.tracker).name, "lost_grace_s": args.lost_grace, "lane_width": args.lane_width,
                   "lane_available_frames": sum(record["status"] == "detected" or (record["status"] == "held" and record["confidence"] >= .25) for record in lane_records) if lane_estimator else frames,
                   "warning_ttc_s": args.warning_ttc, "critical_ttc_s": args.critical_ttc,
                   "min_expansion_per_s": args.min_expansion,
                   "ttc_method": "inverse_width_linear_regression",
                   "confirm_frames": args.confirm_frames, "events": events,
                   "entry_horizon_s": args.entry_horizon, "near_ratio": args.near_ratio,
                   "target_policy": "closest vehicle in, cutting into or encroaching on the ego lane",
                   "scene_events": scene_events,
                   "note": "Optical TTC is conditional on stable object size/orientation and no camera zoom; a braking target switches to a constant-acceleration fit. Cut-in uses lateral image motion relative to the lane. No metric distance is measured. FPS excludes input decode/setup. Events are not ground-truth collisions."}
        artifacts[2].write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        write_ttc_chart(artifacts[4], series, args.warning_ttc, args.critical_ttc)
        artifacts[5].write_text(json.dumps({"lane_mode": args.lane_mode, "frames": lane_records}, indent=2) + "\n", encoding="utf-8")
        writer.release()
        writer = None
        for artifact, destination in zip(artifacts, destinations):
            artifact.replace(destination)
        print(json.dumps(summary, indent=2))
    except cv2.error as exc:
        raise RuntimeError(f"OpenCV could not process the video or open a window: {exc}. Use --zone without --preview on a headless machine.") from exc
    finally:
        capture.release()
        if writer is not None:
            writer.release()
        if staging is not None:
            staging.cleanup()
        if args.preview or not args.zone:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass  # A cleanup error must not hide the original GUI/display failure.


def main():
    args = parser().parse_args()
    try:
        run(args)
    except (ValueError, OSError, ImportError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled. Previously completed outputs are preserved.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
