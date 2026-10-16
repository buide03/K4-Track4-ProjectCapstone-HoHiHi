"""Run all three TTC fixtures, check outputs, and save a validation report."""

import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def validate_result(output, scenario, expected_frames):
    import cv2

    summary = json.loads(output.with_suffix(".summary.json").read_text(encoding="utf-8"))
    with output.with_suffix(".csv").open(encoding="utf-8", newline="") as log:
        rows = list(csv.DictReader(log))
    ET.parse(output.with_suffix(".ttc.svg"))
    capture = cv2.VideoCapture(str(output))
    decoded = 0
    try:
        while capture.read()[0]:
            decoded += 1
    finally:
        capture.release()
    problems = []
    if decoded != expected_frames or summary["frames"] != expected_frames:
        problems.append(f"Expected {expected_frames} frames, decoded {decoded}, logged {summary['frames']}.")
    if not rows:
        problems.append("No tracked vehicle observations.")
    states = {event["status"] for event in summary["events"]}
    if scenario == "approaching" and not {"WARNING", "CRITICAL"}.issubset(states):
        problems.append("Approaching fixture did not produce both warning levels.")
    if scenario != "approaching" and summary["events"]:
        problems.append("Unexpected warning on stationary/receding fixture.")
    if scenario != "approaching" and any(row["ttc_s"] for row in rows):
        problems.append("Unexpected positive closing TTC on stationary/receding fixture.")
    return {"scenario": scenario, "passed": not problems, "problems": problems,
            "decoded_frames": decoded, "tracked_observations": len(rows),
            "events": summary["events"], "processing_fps": summary["processing_fps"],
            "video": str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--model", default="yolo11n.pt")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    fixtures = root / "outputs/ttc-sample"
    output_dir = root / "outputs/ttc-demo"
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "note": "Synthetic scale fixtures; not road validation or a safety certification.",
              "scenarios": []}
    try:
        subprocess.run([sys.executable, str(root / "tools/prepare_ttc_sample.py")], cwd=root, check=True)
        for scenario in ("approaching", "stationary", "receding"):
            output = output_dir / f"{scenario}.mp4"
            subprocess.run([sys.executable, "-m", "collision_warning", "--source", str(fixtures / f"{scenario}.mp4"),
                            "--zone", str(fixtures / "zone.json"), "--output", str(output),
                            "--device", args.device, "--model", args.model], cwd=root, check=True)
            truth = json.loads((fixtures / f"{scenario}.truth.json").read_text(encoding="utf-8"))
            result = validate_result(output, scenario, len(truth))
            report["scenarios"].append(result)
            print(f"{scenario}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)
        report["passed"] = all(item["passed"] for item in report["scenarios"])
    except (OSError, ValueError, ImportError, subprocess.CalledProcessError, ET.ParseError) as exc:
        report["error"] = str(exc)
        print(f"Demo failed: {exc}", file=sys.stderr)
    path = output_dir / "validation.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Validation report: {path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
