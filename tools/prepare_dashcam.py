"""Download official Udacity dashcam fixtures atomically with source attribution."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--short-only", action="store_true", help="Download only the 1.52-second clip")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "data/sources.json").read_text(encoding="utf-8"))
    for item in manifest["sources"]:
        if item["kind"] != "dashcam" or "url" not in item or (args.short_only and item["id"] != "udacity-test"):
            continue
        path = ROOT / item["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            partial = path.with_suffix(".download")
            try:
                print(f"Downloading {item['id']}...", flush=True)
                with urlopen(item["url"], timeout=60) as response, partial.open("wb") as target:
                    shutil.copyfileobj(response, target)
                import cv2
                cap = cv2.VideoCapture(str(partial))
                ok, _ = cap.read()
                cap.release()
                if not ok:
                    raise RuntimeError("Downloaded file is not a decodable video.")
                partial.replace(path)
            finally:
                partial.unlink(missing_ok=True)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.with_suffix(".source.json").write_text(json.dumps({
            "source": item["source_url"], "download": item["url"],
            "license": item["license"], "sha256": digest}, indent=2) + "\n", encoding="utf-8")
        print(f"Ready: {path}", flush=True)
    original = ROOT / "data/videos/udacity-highway.mp4"
    excerpt = ROOT / "data/videos/udacity-traffic.webm"
    if original.exists() and not excerpt.exists() and not args.short_only:
        import cv2
        capture = cv2.VideoCapture(str(original))
        writer = None
        temporary = excerpt.with_name("udacity-traffic.partial.webm")
        try:
            fps = capture.get(cv2.CAP_PROP_FPS)
            capture.set(cv2.CAP_PROP_POS_MSEC, 28000)
            writer = cv2.VideoWriter(str(temporary), cv2.VideoWriter_fourcc(*"VP80"), 15, (960, 540))
            if not writer.isOpened():
                raise RuntimeError("Cannot encode the browser-ready dashcam excerpt.")
            next_output, index, written = 0, 0, 0
            while written < 180:
                ok, frame = capture.read()
                if not ok:
                    break
                if index >= round(next_output * fps / 15):
                    writer.write(cv2.resize(frame, (960, 540)))
                    written += 1
                    next_output += 1
                index += 1
            if written != 180:
                raise RuntimeError("Dashcam excerpt must contain 180 frames.")
            writer.release()
            writer = None
            temporary.replace(excerpt)
            excerpt.with_suffix(".source.json").write_text(json.dumps({
                "source": "https://github.com/udacity/CarND-Vehicle-Detection",
                "original_start_s": 28, "original_end_s": 40, "fps": 15,
                "note": "Actual road video excerpt; resampled at original playback speed, not synthetic.",
                "sha256": hashlib.sha256(excerpt.read_bytes()).hexdigest()}, indent=2) + "\n", encoding="utf-8")
            print(f"Ready: {excerpt}", flush=True)
        finally:
            capture.release()
            if writer is not None:
                writer.release()
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
