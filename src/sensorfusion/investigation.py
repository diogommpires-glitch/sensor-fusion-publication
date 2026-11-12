"""Registered exploratory factorial contrasts, separate from protected v1 evidence."""

import csv
import hashlib
import html
import itertools
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

import h5py
import numpy as np
from scipy.interpolate import CubicSpline
from scipy.signal import savgol_filter

from sensorfusion.contracts import FactorConfig, RunConfig, Session
from sensorfusion.evaluation import REFERENCE_MAX_GAP_S, align_reference
from sensorfusion.sources import SOURCES
from sensorfusion.storage import (
    canonical_hash,
    read_json,
    registered_path,
    resolve_workspace_path,
    timestamp,
    write_json,
)
from sensorfusion.study import scientific_source_hash

V1_PROTOCOL_HASH = "9246fbe104ccf605a3f94e16363d9ee2222dbf2c4771f7b5c368d6e73c5f8f1c"
SESSIONS = ["still", "zigzag1", "zigzag2", "zigzag3", "zigzag4", "random1", "circle", "random2"]
DEVELOPMENT = ["still", "zigzag1", "zigzag2"]
FACTORS = ("propagation", "radar", "gating")


def condition_configs(baseline: dict) -> dict[str, RunConfig]:
    """Hold propagation parameters fixed while varying exactly three factors."""
    if canonical_hash(baseline) != V1_PROTOCOL_HASH:
        raise ValueError("investigation requires the pinned approved v1 primary protocol")
    conditions = {}
    for propagation, radar, gating in itertools.product(
        ("cv", "inertial"), (False, True), (False, True)
    ):
        factors = FactorConfig.model_validate(
            {"propagation": propagation, "radar": radar, "gating": gating}
        )
        method = "M3" if propagation == "cv" else "M6"
        values = {**baseline["selected"][method], "factors": factors.model_dump()}
        identifier = f"{propagation}-radar{int(radar)}-gate{int(gating)}"
        conditions[identifier] = RunConfig.model_validate(values)
    return conditions


def register_investigation(
    workspace: Path, investigation_id: str, baseline_protocol_path: Path
) -> dict:
    """Register before execution; none of these previously seen sessions is held out."""
    directory = registered_path(workspace / "investigations", investigation_id)
    if directory.exists():
        raise FileExistsError(f"investigation {investigation_id!r} already exists")
    baseline = read_json(baseline_protocol_path)
    conditions = condition_configs(baseline)
    calibration_id = baseline["calibration_id"]
    calibration = read_json(
        registered_path(workspace / "calibrations", calibration_id).with_suffix(".json")
    )
    if canonical_hash(calibration) != baseline["calibration_hash"]:
        raise ValueError("investigation calibration differs from the approved v1 calibration")
    protocol = {
        "schema_version": 2,
        "kind": "exploratory_investigation",
        "dataset_id": "roboloc-g",
        "dataset_version": baseline["dataset_version"],
        "baseline_protocol_hash": V1_PROTOCOL_HASH,
        "calibration_id": calibration_id,
        "calibration_hash": canonical_hash(calibration),
        "source_hash": scientific_source_hash(),
        "sessions": SESSIONS,
        "development_diagnostic_sessions": DEVELOPMENT,
        "conditions": {name: config.model_dump() for name, config in conditions.items()},
        "reference_max_gap_s": REFERENCE_MAX_GAP_S,
        "event_session": "circle",
        "event_window_s": [0, 20],
        "parameter_policy": "CV uses v1 M3; every inertial condition uses v1 M6. No new optimisation.",
        "contrast_policy": "Order k contrast = 2**k times mean(product of coded factor signs times response); average over remaining factors.",
        "interpretation": "Previously inspected recordings, exploratory mechanism evidence only. Still has no scoreable reference under the unchanged gap mask.",
    }
    directory.mkdir(parents=True)
    record: dict[str, Any] = {
        "id": investigation_id,
        "kind": "exploratory_investigation",
        "status": "registered",
        "created_at": timestamp(),
        "protocol": protocol,
        "protocol_hash": canonical_hash(protocol),
        "runs": [],
        "results": None,
    }
    write_json(directory / "protocol.json", protocol)
    write_json(directory / "investigation.json", record)
    return record


def verify_investigation(workspace: Path, investigation_id: str) -> dict:
    directory = registered_path(workspace / "investigations", investigation_id)
    record = read_json(directory / "investigation.json")
    protocol = read_json(directory / "protocol.json")
    if (
        canonical_hash(protocol) != record["protocol_hash"]
        or canonical_hash(record["protocol"]) != record["protocol_hash"]
    ):
        raise ValueError("registered investigation protocol hash mismatch")
    if protocol["source_hash"] != scientific_source_hash():
        raise ValueError(
            "source changed after investigation registration; create a new investigation"
        )
    calibration = read_json(
        registered_path(workspace / "calibrations", protocol["calibration_id"]).with_suffix(".json")
    )
    if canonical_hash(calibration) != protocol["calibration_hash"]:
        raise ValueError("investigation calibration hash mismatch")
    return {**record, "protocol": protocol}


def admit_investigation_config(workspace: Path, investigation_id: str, config: RunConfig) -> dict:
    record = verify_investigation(workspace, investigation_id)
    if config.session_id not in record["protocol"]["sessions"] or config.factors is None:
        raise ValueError("configuration is not an exploratory investigation condition")
    eligible = [
        RunConfig.model_validate(values).model_copy(update={"session_id": config.session_id})
        for values in record["protocol"]["conditions"].values()
    ]
    if canonical_hash(config.model_dump()) not in {
        canonical_hash(item.model_dump()) for item in eligible
    }:
        raise ValueError("configuration differs from the registered factorial condition")
    return record


def factorial_contrasts(rows: list[dict]) -> list[dict]:
    """Return complete-cell contrasts only, with the scale stated in the protocol."""
    if len(rows) != 8 or len({canonical_hash(row["factors"]) for row in rows}) != 8:
        raise ValueError("factorial contrasts require eight distinct conditions")
    signs = np.array(
        [
            [
                1 if row["factors"]["propagation"] == "inertial" else -1,
                1 if row["factors"]["radar"] else -1,
                1 if row["factors"]["gating"] else -1,
            ]
            for row in rows
        ]
    )
    complete = all(row.get("position_rmse_m") is not None for row in rows)
    values = np.array([row["position_rmse_m"] for row in rows], dtype=float) if complete else None
    contrasts = []
    for order in (1, 2, 3):
        for columns in itertools.combinations(range(3), order):
            value = (
                float(2**order * np.mean(np.prod(signs[:, columns], axis=1) * values))
                if values is not None
                else None
            )
            contrasts.append(
                {
                    "factors": ":".join(FACTORS[column] for column in columns),
                    "order": order,
                    "contrast_rmse_m": value,
                    "complete_cells": complete,
                }
            )
    return contrasts


def _residual_summary(values: list[float], times: list[float]) -> dict:
    residuals = np.array(values)
    correlation = None
    if len(residuals) >= 3 and np.std(residuals[:-1]) > 0 and np.std(residuals[1:]) > 0:
        correlation = float(np.corrcoef(residuals[:-1], residuals[1:])[0, 1])
    return {
        "samples": len(values),
        "mean": float(np.mean(residuals)) if len(values) else None,
        "standard_deviation": float(np.std(residuals, ddof=1)) if len(values) > 1 else None,
        "lag_one_event_correlation": correlation,
        "median_sample_interval_s": float(np.median(np.diff(times))) if len(times) > 1 else None,
    }


def development_diagnostics(workspace: Path, sessions: list[Session], calibration: dict) -> dict:
    """References enter this evaluator-only path, never the estimator."""
    if sorted(session.session_id for session in sessions) != sorted(DEVELOPMENT):
        raise ValueError("residual diagnostics require exactly the three development sessions")
    radio, imu_rows = [], []
    for session in sessions:
        ranges = [row for row in session.observations if row.modality in ("uwb", "ftm")]
        times = np.array([row.t for row in ranges])
        reference = align_reference(times, session.reference_times, session.reference_positions)
        if session.session_id == "still" and np.allclose(
            session.reference_positions, session.reference_positions[0]
        ):
            reference[:] = session.reference_positions[0]
        for sensor in sorted({row.sensor_id for row in ranges}):
            residuals, epochs = [], []
            for index, row in enumerate(ranges):
                if row.sensor_id == sensor and np.isfinite(reference[index]).all():
                    assert row.anchor is not None
                    residuals.append(
                        float(
                            row.value[0]
                            - np.linalg.norm(reference[index] + row.offset - row.anchor)
                        )
                    )
                    epochs.append(row.t)
            radio.append(
                {
                    "session": session.session_id,
                    "sensor_id": sensor,
                    "unit": "m",
                    **_residual_summary(residuals, epochs),
                }
            )
        if session.session_id == "still":
            continue
        grid = np.arange(session.reference_times[0], session.reference_times[-1], 0.1)
        regular = np.column_stack(
            [
                np.interp(grid, session.reference_times, session.reference_positions[:, axis])
                for axis in range(3)
            ]
        )
        derivative = savgol_filter(
            regular, window_length=11, polyorder=3, deriv=2, delta=0.1, axis=0
        )
        spline = CubicSpline(grid, derivative, extrapolate=False)
        controls = [row for row in session.observations if row.modality == "imu"]
        imu_epochs = np.array([row.t for row in controls])
        eligible = (imu_epochs >= grid[0] + 0.6) & (imu_epochs <= grid[-1] - 0.6)
        for left, right in zip(
            session.reference_times[:-1], session.reference_times[1:], strict=True
        ):
            if right - left > 0.5:
                eligible &= ~((imu_epochs >= left - 0.6) & (imu_epochs <= right + 0.6))
        values = np.array([row.value for row in controls])[eligible] - spline(imu_epochs[eligible])
        for axis, name in enumerate("xyz"):
            imu_rows.append(
                {
                    "session": session.session_id,
                    "axis": name,
                    "unit": "m/s²",
                    **_residual_summary(values[:, axis].tolist(), imu_epochs[eligible].tolist()),
                }
            )
    annotation = calibration["radar_annotation_fit"]
    annotation_path = resolve_workspace_path(workspace, annotation["path"])
    if hashlib.sha256(annotation_path.read_bytes()).hexdigest() != annotation["sha256"]:
        raise ValueError("radar annotation checksum differs from approved calibration")
    labels = read_json(annotation_path)
    visibility = []
    for sensor, data in labels["radars"].items():
        for role in ("fit", "check"):
            selected = [scan for scan in data["scans"] if scan["role"] == role]
            visibility.append(
                {
                    "sensor_id": sensor,
                    "role": role,
                    "total_scans": len(selected),
                    "labels": dict(Counter(scan["label"] for scan in selected)),
                }
            )
    return {
        "radio_residuals": radio,
        "imu_residuals": imu_rows,
        "radar_development_visibility": visibility,
        "scope": "Development diagnostics only. IMU reference uses the frozen 10Hz/11-point cubic second-derivative protocol and 0.6s margins; stationary reference is used only here. Correlations are event-lag diagnostics, not white-noise certification. Annotation labels are engineering-reviewed machine labels, not new human annotations.",
    }


def run_investigation(workspace: Path, investigation_id: str) -> dict:
    """Caller holds the workspace numerical-worker lock, as for study execution."""
    from sensorfusion.data import load_session
    from sensorfusion.runtime import execute_run

    record = verify_investigation(workspace, investigation_id)
    if record["status"] not in ("registered", "running"):
        raise ValueError("completed investigations cannot be rerun under the same identity")
    directory = registered_path(workspace / "investigations", investigation_id)
    protocol = record["protocol"]
    record["status"] = "running"
    write_json(directory / "investigation.json", record)
    sessions = {}
    completed = {row["key"] for row in record["runs"]}
    for previous in record["runs"]:
        if previous["status"] in ("starting", "running", "finalizing"):
            previous.update(status="interrupted", error="previous investigation process stopped")
    for session_id, condition in itertools.product(protocol["sessions"], protocol["conditions"]):
        key = f"{session_id}-{condition}"
        if key in completed:
            continue
        config = RunConfig.model_validate(protocol["conditions"][condition]).model_copy(
            update={"session_id": session_id}
        )
        row = {
            "key": key,
            "session": session_id,
            "condition": condition,
            "run_id": f"v2-{uuid4().hex}",
            "status": "starting",
            "metrics": None,
            "error": None,
            "config": config.model_dump(),
        }
        record["runs"].append(row)
        write_json(directory / "investigation.json", record)
        try:
            if session_id not in sessions:
                sessions[session_id] = load_session(
                    workspace, "roboloc-g", session_id, protocol["calibration_id"]
                )
            result = execute_run(
                workspace,
                row["run_id"],
                config,
                investigation_id=investigation_id,
                session=sessions[session_id],
            )
            row.update(
                status=result["status"], metrics=result.get("metrics"), error=result.get("error")
            )
        except Exception as error:
            # The job boundary records a failed condition; it never imputes an estimate.
            row.update(status="failed", error=f"{type(error).__name__}: {error}")
        write_json(directory / "investigation.json", record)
    calibration = read_json(
        registered_path(workspace / "calibrations", protocol["calibration_id"]).with_suffix(".json")
    )
    from sensorfusion.data import DataBlockedError

    try:
        development = [
            sessions.get(name)
            or load_session(workspace, "roboloc-g", name, protocol["calibration_id"])
            for name in DEVELOPMENT
        ]
        diagnostics = development_diagnostics(workspace, development, calibration)
        diagnostics["status"] = "completed"
    except (DataBlockedError, FileNotFoundError) as error:
        diagnostics = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
    write_json(directory / "development-diagnostics.json", diagnostics)
    record["results"] = summarise_investigation(workspace, record)
    record["status"] = (
        "completed"
        if all(row["status"] == "completed" for row in record["runs"])
        and diagnostics["status"] == "completed"
        else "completed_with_failures"
    )
    record["completed_at"] = timestamp()
    write_json(directory / "investigation.json", record)
    return record


def _checked_run(workspace: Path, row: dict) -> tuple[Path, dict]:
    directory = registered_path(workspace / "runs", row["run_id"])
    manifest = read_json(directory / "artifacts.json")["artifacts"]
    for name in ("metrics.json", "config.json", "innovations.json", "numerical.h5"):
        registered = [item for item in manifest if item["name"] == name]
        if (
            len(registered) != 1
            or hashlib.sha256((directory / name).read_bytes()).hexdigest()
            != registered[0]["sha256"]
        ):
            raise ValueError(f"investigation run {row['run_id']} has an invalid {name} artefact")
    if canonical_hash(read_json(directory / "metrics.json")) != canonical_hash(
        row["metrics"]
    ) or canonical_hash(read_json(directory / "config.json")) != canonical_hash(row["config"]):
        raise ValueError(
            "investigation ledger differs from the registered run metrics/configuration"
        )
    return directory, read_json(directory / "innovations.json")


def summarise_investigation(workspace: Path, record: dict) -> dict:
    """Export full-trace diagnostics and a predeclared event window without retuning."""
    registry = registered_path(workspace / "investigations", record["id"])
    directory = registry / "reports" / uuid4().hex
    directory.mkdir(parents=True)
    table: list[dict] = []
    events: list[dict] = []
    traces = {}
    for row in record["runs"]:
        metrics = row.get("metrics") or {}
        entry = {
            "run_id": row["run_id"],
            "session": row["session"],
            "condition": row["condition"],
            "status": row["status"],
            "error": row.get("error"),
            "factors": row["config"]["factors"],
            **{
                key: metrics.get(key)
                for key in (
                    "position_rmse_m",
                    "availability",
                    "reference_coverage",
                    "position_coverage_95",
                    "accepted_updates",
                    "rejected_updates",
                )
            },
        }
        table.append(entry)
        if row["status"] != "completed":
            continue
        run_directory, saved = _checked_run(workspace, row)
        accepted = sorted(item["arrival"] for item in saved["innovations"] if item["accepted"])
        with h5py.File(run_directory / "numerical.h5") as numerical:
            times = numerical["times"][:]
            accepted = (
                [epoch for epoch in accepted if times[0] <= epoch <= times[-1]]
                if len(times)
                else []
            )
            entry["largest_accepted_positioning_gap_s"] = (
                float(np.max(np.diff([times[0], *accepted, times[-1]]))) if len(times) else None
            )
            entry["association_reasons"] = dict(
                Counter(item["reason"] for item in saved["metadata"].get("radar_associations", []))
            )
            if row["session"] == "circle":
                states, errors = numerical["states"][:], numerical["position_errors"][:]
                traces[row["condition"]] = (times, errors)
                events.extend(
                    {
                        "condition": row["condition"],
                        "time_s": times[index],
                        "error_m": errors[index],
                        **{f"state_{i}": value for i, value in enumerate(states[index])},
                    }
                    for index in np.flatnonzero((times >= 0) & (times <= 20))
                )
        events.extend(
            {
                "condition": row["condition"],
                "time_s": item["time"],
                "modality": item["modality"],
                "accepted": item["accepted"],
                "reason": item["reason"],
                "nis": item["nis"],
            }
            for item in saved["innovations"]
            if row["session"] == "circle" and 0 <= item["time"] <= 20
        )
    effects: list[dict] = []
    for session_id in record["protocol"]["sessions"]:
        selected = [row for row in table if row["session"] == session_id]
        effects.extend(
            {"session": session_id, **contrast} for contrast in factorial_contrasts(selected)
        )
    for filename, rows in (
        ("results.csv", table),
        ("effects.csv", effects),
        ("circle-events.csv", events),
    ):
        with (directory / filename).open("x", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=list(dict.fromkeys(key for row in rows for key in row))
            )
            writer.writeheader()
            writer.writerows(rows)
    _plot_traces(directory, traces)
    macro_effects = []
    for contrast in effects[:7]:
        moving = [
            row
            for row in effects
            if row["session"] != "still" and row["factors"] == contrast["factors"]
        ]
        complete = len(moving) == 7 and all(row["complete_cells"] for row in moving)
        macro_effects.append(
            {
                "factors": contrast["factors"],
                "order": contrast["order"],
                "contrast_rmse_m": float(np.mean([row["contrast_rmse_m"] for row in moving]))
                if complete
                else None,
                "complete_cells": complete,
            }
        )
    summary = {
        "kind": "exploratory_investigation",
        "report_directory": directory.relative_to(workspace).as_posix(),
        "runs": table,
        "contrasts": effects,
        "macro_contrasts": macro_effects,
        "macro_scope": "Equal weighting of all seven moving recordings; still excluded solely because its endpoint-only reference is unsupported by the unchanged gap rule. No failed condition is dropped.",
        "interpretation": {
            "observed": "Read full trajectories, event rows and update rejection diagnostics.",
            "experimentally_supported": "Registered factorial contrasts measure changes under the specified factors with fixed propagation parameters.",
            "unresolved": "Mechanism evidence on previously inspected recordings does not establish causal properties of a new environment or prove correct sensor calibration.",
        },
    }
    write_json(directory / "summary.json", summary)
    write_json(
        directory / "development-diagnostics.json",
        read_json(registry / "development-diagnostics.json"),
    )
    write_json(directory / "protocol.json", record["protocol"])
    body = [
        "<!doctype html><html lang='en'><meta charset='utf-8'><title>Exploratory factorial investigation</title><h1>Exploratory factorial investigation</h1><p>Previously inspected RoboLoc-G recordings. This is not a new held-out result.</p>"
    ]
    source = SOURCES["roboloc-g"]
    body.append(
        f"<p>{html.escape(source['attribution'])}. <a href='{source['url']}'>RoboLoc-G v2</a>, <a href='{source['licence_url']}'>CC BY 4.0</a>.</p>"
    )
    body.extend(
        f"<p><a href='{filename}'>{filename}</a></p>"
        for filename in (
            "results.csv",
            "effects.csv",
            "circle-events.csv",
            "development-diagnostics.json",
            "summary.json",
            "protocol.json",
        )
    )
    body.append(
        "<img src='circle-error.png' alt='Full circle trace and predeclared0to20second window'><pre>"
        + html.escape(str(summary["interpretation"]))
        + "</pre></html>"
    )
    (directory / "report.html").write_text("\n".join(body), encoding="utf-8")
    write_json(
        directory / "artifacts.json",
        {
            "artifacts": [
                {
                    "name": path.name,
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for path in sorted(directory.iterdir())
                if path.is_file()
            ]
        },
    )
    write_json(registry / "summary.json", summary)
    return summary


def _plot_traces(directory: Path, traces: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    figure, axes = plt.subplots(2, 1, figsize=(11, 7), layout="constrained")
    for condition, (times, errors) in traces.items():
        axes[0].plot(times, errors, label=condition)
        selected = (times >= 0) & (times <= 20)
        axes[1].plot(times[selected], errors[selected], label=condition)
    for axis, title in zip(
        axes, ("Entire recording", "Predeclared 0–20 s diagnostic window"), strict=True
    ):
        axis.set(xlabel="Time / s", ylabel="3D position error / m", title=title)
    if traces:
        axes[0].legend(fontsize=8)
    figure.savefig(directory / "circle-error.png", dpi=150)
    plt.close(figure)
