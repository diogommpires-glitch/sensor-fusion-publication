"""Compare saved radio rejections with evaluation-only reference geometry."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from sensorfusion.contracts import Session
from sensorfusion.data import load_session
from sensorfusion.evaluation import align_reference
from sensorfusion.storage import read_json, registered_path


def reference_residuals(session: Session, innovations: list[dict]) -> list[dict]:
    observations = {
        (row.sensor_id, row.source_row, row.t): row
        for row in session.observations
        if row.modality == "uwb"
    }
    selected = [
        row
        for row in innovations
        if row["modality"] == "uwb" and row["reason"] in ("gate", "accepted")
    ]
    reference = align_reference(
        np.array([row["time"] for row in selected]),
        session.reference_times,
        session.reference_positions,
    )
    rows = []
    for diagnostic, position in zip(selected, reference, strict=True):
        if not np.isfinite(position).all():
            continue
        observation = observations[
            (diagnostic["sensor_id"], diagnostic["source_row"], diagnostic["time"])
        ]
        if observation.anchor is None:
            raise ValueError("UWB geometry requires a world anchor")
        if diagnostic["value"][0] != observation.value[0]:
            raise ValueError("saved innovation differs from its calibrated source measurement")
        geometric_range = float(np.linalg.norm(position + observation.offset - observation.anchor))
        rows.append(
            {
                "time_s": diagnostic["time"],
                "sensor_id": observation.sensor_id,
                "outcome": diagnostic["reason"],
                "reference_geometry_residual_m": float(observation.value[0] - geometric_range),
                "filter_innovation_m": diagnostic["residual"][0],
            }
        )
    return rows


def export(workspace: Path, investigation_id: str, destination: Path) -> dict:
    if destination.exists():
        raise FileExistsError(f"diagnostic output exists: {destination}")
    investigation = read_json(
        registered_path(workspace / "investigations", investigation_id) / "investigation.json"
    )
    if investigation["status"] != "completed":
        raise ValueError("geometry analysis requires the completed exploratory investigation")
    session = load_session(
        workspace, "roboloc-g", "circle", investigation["protocol"]["calibration_id"]
    )
    results = []
    for run in investigation["runs"]:
        if run["session"] != "circle":
            continue
        directory = registered_path(workspace / "runs", run["run_id"])
        content = (directory / "innovations.json").read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        manifest = read_json(directory / "artifacts.json")["artifacts"]
        if not any(
            row["name"] == "innovations.json" and row["sha256"] == digest for row in manifest
        ):
            raise ValueError("innovation checksum differs from registered evidence")
        rows = reference_residuals(session, json.loads(content)["innovations"])
        groups = []
        for window, end in (("whole_recording", float("inf")), ("declared_event_0_20_s", 20)):
            for outcome in ("accepted", "gate"):
                eligible = [r for r in rows if 0 <= r["time_s"] <= end and r["outcome"] == outcome]
                groups.append(
                    {
                        "window": window,
                        "outcome": outcome,
                        "samples": len(eligible),
                        "median_absolute_geometry_residual_m": float(
                            np.median([abs(r["reference_geometry_residual_m"]) for r in eligible])
                        )
                        if eligible
                        else None,
                        "median_absolute_filter_innovation_m": float(
                            np.median([abs(r["filter_innovation_m"]) for r in eligible])
                        )
                        if eligible
                        else None,
                    }
                )
        results.append(
            {
                "condition": run["condition"],
                "run_id": run["run_id"],
                "innovations_sha256": digest,
                "candidate_uwb_innovations": sum(
                    row["modality"] == "uwb" and row["reason"] in ("accepted", "gate")
                    for row in json.loads(content)["innovations"]
                ),
                "reference_supported_innovations": len(rows),
                "groups": groups,
                "samples": rows,
            }
        )
    result = {
        "kind": "post_freeze_exploratory_reference_geometry_diagnostic",
        "protocol_hash": investigation["protocol_hash"],
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "Reference geometry is evaluator-only. These residuals do not prove exact radio truth, independent errors, physical NLOS or a calibrated probability model. No estimator rerun or parameter change.",
        "conditions": results,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2, allow_nan=False)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    parser.add_argument("--investigation", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = export(args.workspace, args.investigation, args.output)
    print(f"Saved evaluator-only diagnostics for {len(result['conditions'])} circle conditions.")
