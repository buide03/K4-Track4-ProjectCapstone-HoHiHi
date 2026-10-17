"""Render synthetic multi-vehicle clips: cut-in, parallel swerve, hard braking, slow lane drift,
sudden close cut-in with braking, and a parallel vehicle holding its lane.

Vehicles follow a simple ground-plane perspective (closer = larger and lower). This
checks scene logic end to end with the real detector/tracker; it is not road validation.
"""

import json
from pathlib import Path
import shutil
from urllib.request import urlopen

import cv2
import numpy as np

FPS, WIDTH, HEIGHT = 15, 960, 720
LANE = [[430, 300], [530, 300], [800, 700], [160, 700]]


def bottom_for(width):
    # A car covers ~75% of the lane width at its own image row (lane: 100 px at y=300, +1.35 px/row).
    return 300 + (width / .75 - 100) / 1.35


def lane_left(y):
    return 430 - 270 * (y - 300) / 400


def gap(t):
    # Relative gap (arbitrary units): slow closing, then the lead brakes hard at t=1.5 s.
    return 30 - 2 * t - (3 * (t - 1.5) ** 2 if t > 1.5 else 0)


def ramp(t, start, duration):
    return min(1., max(0., (t - start) / duration))


def drift_centre(t):
    # Adjacent car (220 px) starts with its inner edge 0.2 lane widths outside the lane and drifts
    # 0.12 lane widths/s inward from t=1 s - a slow lane change without a sudden swerve.
    width = 100 + 1.35 * (bottom_for(220) - 300)
    return min(480., lane_left(bottom_for(220)) - .2 * width - 110 + max(0., t - 1) * .12 * width)


SCENARIOS = {
    "cut_in": (3.5, lambda t: [("lead", 150, 480), ("cutter", 240, 160 + 320 * ramp(t, 1.2, 1.6))]),
    "parallel_cut_in": (3.5, lambda t: [("lead", 150, 480), ("alongside", 420, 900 - 340 * ramp(t, 1.5, .8))]),
    "braking": (3.8, lambda t: [("lead", 120 * 30 / gap(t), 480)]),
    "lane_drift": (6.0, lambda t: [("lead", 150, 480), ("cutter", 220, drift_centre(t))]),
    # Close car swerves in from the left lane within 0.6 s, then brakes (its image grows).
    "sudden_cut_in": (3.2, lambda t: [("lead", 150, 480),
                                      ("cutter", 300 * (1 + .25 * max(0., t - 1.6) ** 2), 100 + 380 * ramp(t, 1.0, .6))]),
    "parallel_hold": (4.0, lambda t: [("lead", 150, 480), ("alongside", 420, 900 + 10 * np.sin(2 * np.pi * t / 1.5))]),
}


def background():
    frame = np.full((HEIGHT, WIDTH, 3), (120, 105, 95), dtype=np.uint8)
    frame[300:] = (62, 62, 62)
    for x_top, x_bottom in ((430, 160), (530, 800), (330, -380), (630, 1340)):
        for k in range(10):
            y0, y1 = 300 + 40 * k, 300 + 40 * k + 24
            p0 = (round(x_top + (x_bottom - x_top) * (y0 - 300) / 400), y0)
            p1 = (round(x_top + (x_bottom - x_top) * (y1 - 300) / 400), y1)
            cv2.line(frame, p0, p1, (235, 235, 235), max(2, round(2 + 8 * (y0 - 300) / 400)))
    return frame


def paste(frame, sprite, x1, y1):
    h, w = sprite.shape[:2]
    left, top = max(0, x1), max(0, y1)
    right, bottom = min(WIDTH, x1 + w), min(HEIGHT, y1 + h)
    if right > left and bottom > top:
        frame[top:bottom, left:right] = sprite[top - y1:bottom - y1, left - x1:right - x1]


def main():
    output = Path("outputs/scene-sample")
    output.mkdir(parents=True, exist_ok=True)
    photo = Path("outputs/ttc-sample/bus.jpg")
    if not photo.exists():
        photo.parent.mkdir(parents=True, exist_ok=True)
        url = "https://raw.githubusercontent.com/ultralytics/ultralytics/main/ultralytics/assets/bus.jpg"
        with urlopen(url, timeout=120) as response, photo.with_suffix(".download").open("wb") as target:
            shutil.copyfileobj(response, target)
        photo.with_suffix(".download").replace(photo)
    image = cv2.imread(str(photo))
    if image is None:
        raise RuntimeError("Cannot decode bus image.")
    ih, iw = image.shape[:2]
    crop = image[round(ih * .20):round(ih * .73), round(iw * .015):round(iw * .995)]
    sprites = {"lead": crop, "cutter": cv2.flip(crop, 1), "alongside": crop}
    aspect = crop.shape[0] / crop.shape[1]
    (output / "zone.json").write_text(json.dumps(LANE) + "\n")
    road = background()
    for name, (duration, layout) in SCENARIOS.items():
        path = output / f"{name}.mp4"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (WIDTH, HEIGHT))
        if not writer.isOpened():
            raise RuntimeError("Cannot create sample MP4.")
        truth = []
        try:
            for i in range(round(duration * FPS)):
                t = i / FPS
                frame = road.copy()
                vehicles = []
                # Draw far vehicles first so closer ones occlude them.
                for role, width, center in sorted(layout(t), key=lambda v: bottom_for(v[1])):
                    w, h = round(width), round(width * aspect)
                    x1, y2 = round(center - w / 2), round(bottom_for(width))
                    paste(frame, cv2.resize(sprites[role], (w, h)), x1, y2 - h)
                    vehicles.append({"role": role, "box": [x1, y2 - h, x1 + w, y2],
                                     "centre_in_lane": bool(lane_left(y2) <= center <= 2 * 480 - lane_left(y2))})
                cv2.putText(frame, f"SYNTHETIC {name.upper()} - NOT ROAD VALIDATION", (15, 95),
                            cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 1)
                writer.write(frame)
                record = {"frame": i, "time_s": t, "vehicles": vehicles}
                if name == "braking":
                    z, v, a = gap(t), 2 + (6 * (t - 1.5) if t > 1.5 else 0), 6 if t > 1.5 else 0
                    record["true_ttc_s"] = (z / v if a == 0 else (-v + (v * v + 2 * a * z) ** .5) / a)
                truth.append(record)
        finally:
            writer.release()
        (output / f"{name}.truth.json").write_text(json.dumps(truth, indent=1) + "\n")
    print(f"Prepared {len(SCENARIOS)} clips in {output}. Run e.g.:")
    print("python -m collision_warning --source outputs/scene-sample/cut_in.mp4 --zone outputs/scene-sample/zone.json")


if __name__ == "__main__":
    main()
