"""Render controlled vehicle-scale clips with known TTC; not road validation."""

import json
from pathlib import Path
import shutil
from urllib.request import urlopen

import cv2
import numpy as np


def main():
    output = Path("outputs/ttc-sample")
    output.mkdir(parents=True, exist_ok=True)
    photo = output / "bus.jpg"
    if not photo.exists():
        url = "https://raw.githubusercontent.com/ultralytics/ultralytics/main/ultralytics/assets/bus.jpg"
        temporary = photo.with_suffix(".download")
        with urlopen(url, timeout=120) as response, temporary.open("wb") as target:
            shutil.copyfileobj(response, target)
        temporary.replace(photo)
    image = cv2.imread(str(photo))
    if image is None:
        raise RuntimeError("Cannot decode bus image.")
    # Fixed crop for the official 810x1080 fixture. YOLO must still detect it;
    # no reference boxes or TTC ground truth are passed to the application.
    ih, iw = image.shape[:2]
    crop = image[round(ih * .20):round(ih * .73), round(iw * .015):round(iw * .995)]
    fps, frames, width, height = 15, 75, 960, 720
    zone = [[260, 160], [700, 160], [900, 700], [60, 700]]
    (output / "zone.json").write_text(json.dumps(zone, indent=2) + "\n")
    for scenario in ("approaching", "stationary", "receding"):
        writer = cv2.VideoWriter(str(output / f"{scenario}.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
        truth = []
        if not writer.isOpened():
            raise RuntimeError("Cannot create sample MP4.")
        try:
            for i in range(frames):
                t = i / fps
                object_width = 110 * 6 / (6 - t) if scenario == "approaching" else (
                    200 if scenario == "stationary" else 200 * 6 / (6 + t))
                object_width = round(object_width)
                object_height = round(object_width * crop.shape[0] / crop.shape[1])
                frame = np.full((height, width, 3), (55, 55, 55), dtype=np.uint8)
                cv2.fillPoly(frame, [np.array(zone, dtype=np.int32)], (75, 75, 75))
                x, y = (width - object_width) // 2, 660 - object_height
                frame[y:y + object_height, x:x + object_width] = cv2.resize(crop, (object_width, object_height))
                cv2.putText(frame, "SYNTHETIC SCALE TEST - NOT ROAD VALIDATION", (15, 95),
                            cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 1)
                writer.write(frame)
                truth.append({"frame": i, "time_s": t, "rendered_width_px": object_width,
                              "ideal_optical_ttc_s": 6 - t if scenario == "approaching" else None})
        finally:
            writer.release()
        (output / f"{scenario}.truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    print("Prepared 3 synthetic scale clips. Run:")
    print("python -m collision_warning --source outputs/ttc-sample/approaching.mp4 --zone outputs/ttc-sample/zone.json")


if __name__ == "__main__":
    main()
