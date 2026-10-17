"""Run the four multi-vehicle scene fixtures and check the expected behaviour.

cut_in           lead tracked, then a closer vehicle cuts in -> CUT_IN, target switches before lane entry
parallel_cut_in  vehicle alongside (clipped at frame edge) swerves in -> CRITICAL cut-in
braking          lead brakes hard -> BRAKING, warning earlier than constant-speed TTC alone
lane_drift       adjacent car drifts slowly into the lane -> DRIFTING warning well before it enters
sudden_cut_in    close car swerves in and brakes -> CRITICAL cut-in, TTC on the new target
parallel_hold    vehicle alongside holds its lane -> no alert

The ego lane is found automatically from the painted markings (use --lane manual for the drawn zone).
"""

import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys


def centre_entry_time(truth, role):
    for record in truth:
        for vehicle in record["vehicles"]:
            if vehicle["role"] == role and vehicle["centre_in_lane"]:
                return record["time_s"]
    return None


def check(scenario, summary, rows, truth):
    events, alerts = summary["scene_events"], summary["events"]
    problems, facts = [], {}
    cut_ins = [e for e in events if e["type"] == "CUT_IN"]
    if scenario in ("cut_in", "parallel_cut_in", "sudden_cut_in"):
        role = "alongside" if scenario == "parallel_cut_in" else "cutter"
        entry = centre_entry_time(truth, role)
        facts["centre_enters_lane_s"] = entry
        if not cut_ins:
            problems.append("No CUT_IN event.")
        else:
            facts["cut_in_flagged_s"] = cut_ins[0]["time_s"]
            facts["lead_time_s"] = round(entry - cut_ins[0]["time_s"], 3)
            if cut_ins[0]["time_s"] > entry:
                problems.append("Cut-in flagged after the vehicle centre entered the lane.")
            switch = [e for e in events if e["type"] == "TARGET_SWITCH" and e["to_id"] == cut_ins[0]["track_id"]]
            if not switch:
                problems.append("Target did not switch to the cutting-in vehicle.")
        wanted = "WARNING" if scenario == "cut_in" else "CRITICAL"
        if not any(a["status"] == wanted and a["lane_relation"] == "CUT_IN" for a in alerts):
            problems.append(f"Expected a {wanted} alert for the cut-in.")
        if scenario == "parallel_cut_in":
            before = [r for r in rows if float(r["time_s"]) < 1.5 and r["alert"] != "CLEAR"]
            if before:
                problems.append("False alert while the vehicle was only driving alongside.")
        if scenario == "sudden_cut_in" and cut_ins:
            cutter = str(cut_ins[0]["track_id"])
            ttc = [r for r in rows if r["track_id"] == cutter and r["effective_ttc_s"] and float(r["time_s"]) > 1.6]
            facts["cutter_ttc_frames"] = len(ttc)
            if not ttc:
                problems.append("No TTC for the vehicle that cut in and braked.")
    elif scenario == "lane_drift":
        drift = [e for e in events if e["type"] == "DRIFTING"]
        entry = centre_entry_time(truth, "cutter")
        facts.update(drift_flagged_s=drift[0]["time_s"] if drift else None,
                     cut_in_flagged_s=cut_ins[0]["time_s"] if cut_ins else None, centre_enters_lane_s=entry)
        if not drift:
            problems.append("No DRIFTING (lane-change intent) event.")
        elif entry is not None:
            facts["intent_lead_time_s"] = round(entry - drift[0]["time_s"], 3)
            if entry - drift[0]["time_s"] < 1.0:
                problems.append("Lane-change intent flagged less than 1 s before lane entry.")
        if not any(a["status"] == "WARNING" and a["lane_relation"] == "DRIFTING" for a in alerts):
            problems.append("Drifting vehicle did not raise a WARNING.")
        if any(r["alert"] != "CLEAR" for r in rows if float(r["time_s"]) < 1.0):
            problems.append("Alert before the drift started.")
    elif scenario == "braking":
        braking = [e for e in events if e["type"] == "BRAKING"]
        def first(column, values):
            return next((float(r["time_s"]) for r in rows if r[column] in values), None)

        # First alert at any level, compared with the constant-speed TTC alone (the "status" column).
        facts.update(braking_flagged_s=braking[0]["time_s"] if braking else None,
                     first_alert_s=first("alert", ("WARNING", "CRITICAL")),
                     constant_speed_first_alert_s=first("status", ("WARNING", "CRITICAL")),
                     critical_s=first("alert", ("CRITICAL",)), constant_speed_critical_s=first("status", ("CRITICAL",)))
        if not braking:
            problems.append("No BRAKING event.")
        if facts["first_alert_s"] is None or facts["critical_s"] is None:
            problems.append("Braking did not reach WARNING and CRITICAL.")
        elif (facts["constant_speed_first_alert_s"] is not None
              and facts["first_alert_s"] > facts["constant_speed_first_alert_s"]):
            problems.append("Braking-aware alert was not earlier than constant-speed TTC.")
    elif scenario == "parallel_hold":
        if alerts or cut_ins:
            problems.append("Vehicle holding the adjacent lane raised an alert.")
    return {"scenario": scenario, "passed": not problems, "problems": problems, **facts,
            "lane_fallback_frames": summary.get("lane_fallback_frames"),
            "processing_fps": summary["processing_fps"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--model", default="yolo11s.pt")
    parser.add_argument("--lane", choices=["auto", "manual"], default="auto")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    fixtures, output_dir = root / "outputs/scene-sample", root / "outputs/scene-demo"
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "lane": args.lane, "model": args.model,
              "note": "Synthetic multi-vehicle fixtures; not road validation.", "scenarios": []}
    try:
        subprocess.run([sys.executable, str(root / "tools/prepare_scene_sample.py")], cwd=root, check=True,
                       stdout=subprocess.DEVNULL)
        lane = ["--zone", str(fixtures / "zone.json")] if args.lane == "manual" else ["--lane-mode", "auto"]
        for scenario in ("cut_in", "parallel_cut_in", "braking", "lane_drift", "sudden_cut_in", "parallel_hold"):
            output = output_dir / f"{scenario}.mp4"
            subprocess.run([sys.executable, "-m", "collision_warning", "--source", str(fixtures / f"{scenario}.mp4"),
                            *lane, "--output", str(output),
                            "--device", args.device, "--model", args.model], cwd=root, check=True,
                           stdout=subprocess.DEVNULL)
            summary = json.loads(output.with_suffix(".summary.json").read_text(encoding="utf-8"))
            with output.with_suffix(".csv").open(encoding="utf-8", newline="") as log:
                rows = list(csv.DictReader(log))
            truth = json.loads((fixtures / f"{scenario}.truth.json").read_text(encoding="utf-8"))
            result = check(scenario, summary, rows, truth)
            report["scenarios"].append(result)
            details = ", ".join(f"{k}={v}" for k, v in result.items()
                                if k not in ("scenario", "passed", "problems", "processing_fps"))
            print(f"{scenario}: {'PASS' if result['passed'] else 'FAIL'} {details} {' '.join(result['problems'])}",
                  flush=True)
        report["passed"] = all(item["passed"] for item in report["scenarios"])
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        report["error"] = str(exc)
        print(f"Scene demo failed: {exc}", file=sys.stderr)
    path = output_dir / "validation.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Validation report: {path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
