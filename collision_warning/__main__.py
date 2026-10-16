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


def parser():
    p = argparse.ArgumentParser(description="Camera-only forward collision warning with estimated optical TTC")
    p.add_argument("--source", required=True, help="Local forward-facing dashcam video path")
    p.add_argument("--output", default="outputs/ttc.mp4")
    p.add_argument("--zone", help="JSON file containing pixel vertices [[x,y], ...]; otherwise select interactively")
    p.add_argument("--model", default="yolo11n.pt", help="COCO vehicle detector weights")
    p.add_argument("--device", default="cpu", help="cpu, 0, etc.")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--conf", type=float, default=0.1)
    p.add_argument("--history", type=float, default=1.0)
    p.add_argument("--warning-ttc", type=float, default=3.0, help="Demo warning threshold, seconds")
    p.add_argument("--critical-ttc", type=float, default=1.5, help="Demo critical threshold, seconds")
    p.add_argument("--min-expansion", type=float, default=.02, help="Minimum fractional width growth per second")
    p.add_argument("--confirm-frames", type=int, default=3)
    p.add_argument("--fps", type=float, help="Override missing/incorrect source FPS")
    p.add_argument("--preview", action="store_true", help="Show video window; Q stops and finalizes output")
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
          "OUTSIDE_ZONE": (130, 130, 130)}


def selected_target(tracks):
    candidates = [(key, track) for key, track in tracks.items()
                  if track.in_path and track.ttc is not None]
    return min(candidates, key=lambda pair: pair[1].ttc) if candidates else (None, None)


def render(frame, boxes, tracks, polygon, fps, cv2, np):
    contour = np.array(polygon, dtype=np.int32)
    overlay = frame.copy()
    cv2.fillPoly(overlay, [contour], (180, 90, 20))
    cv2.addWeighted(overlay, 0.20, frame, 0.80, 0, frame)
    cv2.polylines(frame, [contour], True, (255, 160, 50), 2)
    labels = []
    target_id, target = selected_target(tracks)
    for key, box, class_name in boxes:
        track = tracks.get(key)
        status = track.status if track else "UNTRACKED"
        color = COLORS.get(status, (180, 180, 180))
        x1, y1, x2, y2 = map(int, box)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        text = f"#{key if key is not None else '?'} {class_name} {status}"
        if track and track.ttc is not None:
            text += f" TTC~{track.ttc:.1f}s"
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
    active = {t.status for t in tracks.values()}
    severity = "CRITICAL" if "CRITICAL" in active else "WARNING" if "WARNING" in active else "CLEAR"
    label = severity if severity != "CLEAR" else "MONITORING"
    if target is not None:
        label += f" | #{target_id} estimated TTC {target.ttc:.1f}s"
    else:
        label += " | TTC unavailable"
    color = COLORS[severity]
    cv2.rectangle(frame, (0, 0), (frame.shape[1], 56), (25, 25, 25), -1)
    cv2.putText(frame, f"{label} | {fps:.1f} FPS", (10, 23), cv2.FONT_HERSHEY_SIMPLEX, .55, color, 2)
    cv2.putText(frame, "Optical TTC prototype | straight-road corridor | no metric distance or absolute speed", (10, 46),
                cv2.FONT_HERSHEY_SIMPLEX, .4, (220, 220, 220), 1)
    return frame


def validate_args(args):
    source, output = Path(args.source), Path(args.output)
    if not source.is_file():
        raise ValueError(f"Video does not exist: {source}")
    if output.suffix.lower() != ".mp4":
        raise ValueError("Output must have .mp4 extension.")
    # Every artifact must be distinct from the input and zone configuration.
    artifacts = [output, output.with_suffix(".csv"), output.with_suffix(".summary.json"),
                 output.with_suffix(".zone.json"), output.with_suffix(".ttc.svg")]
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
    return source, output, artifacts


def run(args):
    source, output, destinations = validate_args(args)
    import cv2
    import numpy as np
    from ultralytics import YOLO

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
        polygon = (validate_polygon(json.loads(Path(args.zone).read_text()), width, height)
                   if args.zone else choose_zone(frame, cv2))
        engine = WarningEngine(polygon, (width, height), args.history, args.warning_ttc,
                               args.critical_ttc, args.confirm_frames, args.min_expansion)
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
        previous = {}
        stopped = False
        with artifacts[1].open("w", newline="", encoding="utf-8") as csv_file:
            log = csv.writer(csv_file)
            log.writerow(["frame", "time_s", "track_id", "class_name", "x1", "y1", "x2", "y2",
                          "in_path", "width_px", "expansion_per_s", "ttc_s", "fit_error", "status", "reason", "selected_target"])
            while ok:
                started = time.perf_counter()
                results = model.track(frame, persist=True, tracker="bytetrack.yaml", classes=vehicle_classes,
                                      conf=args.conf, imgsz=args.imgsz, device=args.device, verbose=False)
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
                tracks = engine.update(observations, timestamp)
                target_id, target = selected_target(tracks)
                series.append((timestamp, (target_id, target.ttc) if target is not None else None))
                for key, track in tracks.items():
                    log.writerow([frames, round(timestamp, 6), key, names[key], *observations[key],
                                  track.in_path, track.width_px, track.expansion_per_s, track.ttc,
                                  track.fit_error, track.status, track.reason, key == target_id])
                    if track.status in ("WARNING", "CRITICAL") and previous.get(key) != track.status:
                        events.append({"frame": frames, "time_s": timestamp, "track_id": key,
                                       "status": track.status, "estimated_ttc_s": track.ttc})
                previous = {key: track.status for key, track in tracks.items()}
                elapsed = time.perf_counter() - started
                displayed_fps = frames / processing_seconds if processing_seconds else 1 / max(elapsed, 1e-9)
                render(frame, boxes, tracks, polygon, displayed_fps, cv2, np)
                writer.write(frame)
                processing_seconds += time.perf_counter() - started
                frames += 1
                if args.preview:
                    cv2.imshow("Forward collision warning (Q to stop)", frame)
                    if cv2.waitKey(1) & 255 in (ord("q"), 27):
                        stopped = True
                        break
                if frames % 100 == 0:
                    print(f"Processed {frames} frames", flush=True)
                ok, frame = capture.read()
        summary = {"source": str(source), "output": str(output), "frames": frames,
                   "model": args.model, "tracker": "bytetrack.yaml", "device": args.device,
                   "imgsz": args.imgsz, "confidence": args.conf,
                   "source_fps": fps, "video_duration_s": frames / fps,
                   "processing_fps": frames / max(processing_seconds, 1e-9),
                   "stopped_by_user": stopped, "history_s": args.history,
                   "warning_ttc_s": args.warning_ttc, "critical_ttc_s": args.critical_ttc,
                   "min_expansion_per_s": args.min_expansion,
                   "ttc_method": "inverse_width_linear_regression",
                   "confirm_frames": args.confirm_frames, "events": events,
                   "note": "Optical TTC is conditional on stable object size/orientation, constant closing speed and no camera zoom. No metric distance is measured. FPS excludes input decode/setup. Events are not ground-truth collisions."}
        artifacts[2].write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        write_ttc_chart(artifacts[4], series, args.warning_ttc, args.critical_ttc)
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
