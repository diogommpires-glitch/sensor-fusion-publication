"""Selection, immutable study registration and protected recorded-data evaluation."""

import itertools
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import numpy as np

from sensorfusion.contracts import FaultConfig, Method, RunConfig
from sensorfusion.evaluation import REFERENCE_MAX_GAP_S
from sensorfusion.storage import canonical_hash, read_json, registered_path, timestamp, write_json

PRIMARY_DEVELOPMENT = ["still", "zigzag1", "zigzag2"]
PRIMARY_VALIDATION = ["zigzag3", "zigzag4", "random1"]
PRIMARY_FINAL = ["circle", "random2"]
EXTERNAL_FINAL = ["default_1_random3_2"]


def default_protocol() -> dict:
    return {
        "schema_version": 1,
        "dataset_id": "roboloc-g",
        "dataset_version": "15989282",
        "calibration_sessions": PRIMARY_DEVELOPMENT,
        "validation_sessions": PRIMARY_VALIDATION,
        "final_sessions": PRIMARY_FINAL,
        "methods": [f"M{i}" for i in range(1, 9)],
        "primary_comparison": ["M7", "M2"],
        "primary_endpoint": "macro-average final-trajectory 3D position RMSE",
        "reference_max_gap_s": REFERENCE_MAX_GAP_S,
        "radar_primary": "AOP",
        "radar_substitutions": ["ISK0", "ISK1"],
        "fault_methods": ["M2", "M6", "M7"],
        "fault_seeds": list(range(5)),
        "fault_levels": {
            "dropout": [0.5, 2, 5],
            "range_bias": [0.25, 0.5, 1],
            "delay": [0.05, 0.1, 0.25],
            "imu_bias": [0.02, 0.05, 0.1],
            "radar_shift": [0.25, 0.5, 1],
        },
        "external": {
            "dataset_id": "miluv",
            "development": ["default_1_random3_0"],
            "final": EXTERNAL_FINAL,
            "methods": ["M1", "M2"],
            "claim": "Calibrated portability of the shared range subsystem only",
        },
    }


def scientific_source_hash() -> str:
    root = Path(__file__).parent
    return canonical_hash(
        {path.name: path.read_text(encoding="utf-8") for path in sorted(root.glob("*.py"))}
    )


def protected_session(config: RunConfig) -> bool:
    return (
        config.dataset_id == "roboloc-g"
        and config.session_id in PRIMARY_FINAL
        or config.dataset_id == "miluv"
        and config.session_id in EXTERNAL_FINAL
    )


def _calibration(workspace: Path, calibration_id: str, dataset_id: str) -> dict:
    calibration = read_json(
        registered_path(workspace / "calibrations", calibration_id).with_suffix(".json")
    )
    if calibration.get("dataset_id") != dataset_id:
        raise ValueError("calibration dataset does not match study dataset")
    return calibration


def _require_full_calibration(calibration: dict) -> None:
    modalities = set(calibration.get("certified_modalities", []))
    missing = {"uwb", "ftm", "imu", "radar"} - modalities
    if missing:
        raise ValueError(
            f"full recorded-data study lacks certified modalities: {', '.join(sorted(missing))}"
        )
    if calibration.get("fit_sessions") != PRIMARY_DEVELOPMENT:
        raise ValueError("primary calibration must use the exact predeclared development sessions")
    if not calibration.get("radars", {}).get("AOP", {}).get("certified", False):
        raise ValueError("primary AOP effective observation calibration is not certified")


def radar_settings(calibration: dict, radar_sensor: str) -> dict:
    selected = calibration["radars"][radar_sensor]["selected_clustering"]
    return {
        "radar_cluster_radius_m": float(selected["radius_m"]),
        "radar_min_samples": int(selected["min_samples"]),
    }


def select_parameters(workspace: Path, calibration_id: str) -> dict:
    from sensorfusion.data import load_session
    from sensorfusion.engine import estimate
    from sensorfusion.evaluation import score
    from sensorfusion.models import NumericalError

    calibration = _calibration(workspace, calibration_id, "roboloc-g")
    _require_full_calibration(calibration)
    sessions = {
        name: load_session(workspace, "roboloc-g", name, calibration_id)
        for name in PRIMARY_VALIDATION
    }
    development = [
        load_session(workspace, "roboloc-g", name, calibration_id) for name in PRIMARY_DEVELOPMENT
    ]
    windows = []
    for width in (0.05, 0.1, 0.2, 0.5, 1.0):
        availability = []
        for session in development:
            config = RunConfig(
                dataset_id="roboloc-g",
                session_id=session.session_id,
                method="M1",
                calibration_id=calibration_id,
                wls_window_s=width,
            )
            result = estimate(session.observations, config)
            availability.append(float(np.mean(np.all(np.isfinite(result.states), axis=1))))
        windows.append({"window_s": width, "availability": float(np.mean(availability))})
    width = next((row["window_s"] for row in windows if row["availability"] >= 0.9), 1.0)
    selected: dict[str, Any] = {}
    candidates: dict[str, Any] = {}
    selection_evidence: dict[str, Any] = {}
    base_noise = float(calibration["imu"]["acceleration_noise_density_m2_s3"])
    if not np.isfinite(base_noise) or base_noise <= 0:
        raise ValueError(
            "certified IMU calibration needs a positive effective acceleration-noise density"
        )
    for method in (f"M{i}" for i in range(1, 9)):
        rows: list[dict[str, Any]] = []
        grid = [(0.1, 1.0, 1e-6)]
        if method in ("M2", "M3", "M8"):
            grid = [(q, 1.0, 1e-6) for q in (0.01, 0.1, 1, 10)]
        elif method in ("M4", "M5", "M6", "M7"):
            grid = [
                (0.1, multiplier, q_b)
                for multiplier, q_b in itertools.product((0.5, 1, 2), (0, 1e-6, 1e-4))
            ]
        for q, multiplier, q_b in grid:
            config = RunConfig(
                dataset_id="roboloc-g",
                method=cast(Method, method),
                calibration_id=calibration_id,
                q_cv=q,
                acceleration_noise=base_noise * multiplier,
                bias_diffusion=q_b,
                wls_window_s=width,
                **radar_settings(calibration, "AOP"),
            )
            scores = {}
            failures = {}
            for name, session in sessions.items():
                try:
                    result = estimate(
                        session.observations, config.model_copy(update={"session_id": name})
                    )
                    metrics = score(result, session.reference_times, session.reference_positions)[0]
                    scores[name] = metrics["position_rmse_m"]
                except NumericalError as error:
                    scores[name] = None
                    failures[name] = f"{type(error).__name__}: {error}"
            objective = (
                float(np.mean(list(scores.values())))
                if all(v is not None for v in scores.values())
                else None
            )
            rows.append(
                {
                    "config": config.model_dump(),
                    "objective": objective,
                    "per_session": scores,
                    "failures": failures,
                }
            )
        candidates[method] = rows
        eligible = [row for row in rows if row["objective"] is not None]
        if not eligible:
            if method != "M1":
                write_json(
                    workspace / "selection" / f"{calibration_id}.json",
                    {
                        "status": "blocked",
                        "failed_method": method,
                        "selected": selected,
                        "selection_evidence": selection_evidence,
                        "candidates": candidates,
                        "windows": windows,
                    },
                )
                raise ValueError(f"{method} has no complete validation result")
            # M1 has no fitted process parameter. Preserve its fixed configuration and
            # failed validation evidence so final WLS failures remain measurable.
            eligible = rows
        best = min(
            eligible,
            key=lambda row: (
                row["objective"],
                row["config"]["q_cv"],
                row["config"]["acceleration_noise"],
                row["config"]["bias_diffusion"],
            ),
        )
        selected[method] = best["config"]
        selection_evidence[method] = {
            "selection_basis": "fixed_baseline" if method == "M1" else "validation_macro_rmse",
            "validation_failed": best["objective"] is None,
            "objective": best["objective"],
            "failures": best["failures"],
        }
        write_json(
            workspace / "selection" / f"{calibration_id}.json",
            {
                "status": "in_progress",
                "selected": selected,
                "selection_evidence": selection_evidence,
                "candidates": candidates,
                "windows": windows,
            },
        )
    selection = {
        "status": "completed",
        "created_at": timestamp(),
        "dataset_id": "roboloc-g",
        "calibration_id": calibration_id,
        "calibration_hash": canonical_hash(calibration),
        "source_hash": scientific_source_hash(),
        "selected": selected,
        "selection_evidence": selection_evidence,
        "candidates": candidates,
        "windows": windows,
        "fit_sessions": PRIMARY_DEVELOPMENT,
        "selection_sessions": PRIMARY_VALIDATION,
        "final_accessed": False,
        "failure_policy": "Failed tunable candidates are ineligible; fixed M1 is retained with its failures.",
    }
    write_json(workspace / "selection" / f"{calibration_id}.json", selection)
    return selection


def freeze_study(workspace: Path, study_id: str, calibration_id: str) -> dict:
    calibration = _calibration(workspace, calibration_id, "roboloc-g")
    _require_full_calibration(calibration)
    selection = read_json(workspace / "selection" / f"{calibration_id}.json")
    if selection.get("status") != "completed" or set(selection.get("selected", {})) != {
        f"M{i}" for i in range(1, 9)
    }:
        raise ValueError("complete eight-method validation selection is required before freezing")
    if selection["calibration_hash"] != canonical_hash(calibration):
        raise ValueError("calibration changed after validation selection")
    if selection["source_hash"] != scientific_source_hash():
        raise ValueError("scientific source changed after validation selection")
    directory = registered_path(workspace / "studies", study_id)
    if directory.exists():
        raise FileExistsError(
            f"study {study_id!r} already exists; frozen studies cannot be overwritten"
        )
    protocol = {
        **default_protocol(),
        "selected": selection["selected"],
        "calibration_id": calibration_id,
        "calibration_hash": canonical_hash(calibration),
        "source_hash": selection["source_hash"],
        "selection_hash": canonical_hash(selection),
        "selection_evidence": selection["selection_evidence"],
    }
    directory.mkdir(parents=True)
    study: dict[str, Any] = {
        "id": study_id,
        "status": "frozen",
        "created_at": timestamp(),
        "protocol": protocol,
        "protocol_hash": canonical_hash(protocol),
        "runs": [],
        "results": None,
        "review": {"human_review": "pending", "independent_reproduction": "pending"},
    }
    write_json(directory / "protocol.json", protocol)
    write_json(directory / "study.json", study)
    return study


def verify_study(workspace: Path, study_id: str) -> dict:
    directory = registered_path(workspace / "studies", study_id)
    study = read_json(directory / "study.json")
    protocol = read_json(directory / "protocol.json")
    if canonical_hash(protocol) != study["protocol_hash"]:
        raise ValueError("frozen protocol hash mismatch")
    if protocol["source_hash"] != scientific_source_hash():
        raise ValueError(
            "scientific source changed after freezing; preserve this study and register a new one"
        )
    if canonical_hash(study["protocol"]) != study["protocol_hash"]:
        raise ValueError("embedded frozen protocol does not match the registered protocol")
    calibration = _calibration(workspace, protocol["calibration_id"], protocol["dataset_id"])
    if protocol["calibration_hash"] != canonical_hash(calibration):
        raise ValueError("frozen calibration hash mismatch")
    return {**study, "protocol": protocol}


def admit_study_config(workspace: Path, study_id: str, config: RunConfig) -> dict:
    study = verify_study(workspace, study_id)
    protocol = study["protocol"]
    if (
        config.dataset_id != protocol["dataset_id"]
        or config.session_id not in protocol["final_sessions"]
    ):
        raise ValueError("frozen study does not permit this dataset or session")
    if config.method not in protocol["selected"]:
        raise ValueError("method is not registered in the frozen protocol")
    expected = RunConfig.model_validate(protocol["selected"][config.method]).model_copy(
        update={"session_id": config.session_id}
    )
    if config.radar_sensor != expected.radar_sensor:
        if config.method not in ("M6", "M7") or config.radar_sensor not in protocol.get(
            "radar_substitutions", []
        ):
            raise ValueError("radar substitution is not registered in the frozen protocol")
        calibration = _calibration(workspace, protocol["calibration_id"], protocol["dataset_id"])
        expected = expected.model_copy(
            update={
                "radar_sensor": config.radar_sensor,
                **radar_settings(calibration, config.radar_sensor),
            }
        )
    if config.fault.kind != "none":
        fault = config.fault
        if (
            config.method not in protocol.get("fault_methods", [])
            or fault.level not in protocol.get("fault_levels", {}).get(fault.kind, [])
            or fault.seed not in protocol.get("fault_seeds", [])
            or config.radar_sensor != protocol["radar_primary"]
            or fault.start_s is not None
            or fault.duration_s != 2
            or fault.axis != 0
            or fault.sensor_id is not None
        ):
            raise ValueError("fault configuration is not registered in the frozen protocol")
        expected = expected.model_copy(update={"fault": fault})
    if canonical_hash(expected.model_dump()) != canonical_hash(config.model_dump()):
        raise ValueError("configuration differs from the frozen selected parameters")
    return study


def run_study(workspace: Path, study_id: str, *, faults: bool = False) -> dict:
    from sensorfusion.runtime import execute_run

    directory = registered_path(workspace / "studies", study_id)
    study = verify_study(workspace, study_id)
    if study["status"] not in ("frozen", "running", "nominal_completed"):
        raise ValueError(f"study status {study['status']} cannot start another final evaluation")
    protocol = study["protocol"]
    study["status"] = "running"
    write_json(directory / "study.json", study)
    results = list(study["runs"])
    completed = {row["key"] for row in results}
    session_cache = {}
    jobs = []
    for session_id, method in itertools.product(protocol["final_sessions"], protocol["methods"]):
        config = RunConfig.model_validate(protocol["selected"][method]).model_copy(
            update={"session_id": session_id}
        )
        jobs.append((f"{session_id}-{method}-original", config))
    calibration = _calibration(workspace, protocol["calibration_id"], protocol["dataset_id"])
    for session_id, radar, method in itertools.product(
        protocol["final_sessions"], protocol.get("radar_substitutions", []), ("M6", "M7")
    ):
        config = RunConfig.model_validate(protocol["selected"][method]).model_copy(
            update={
                "session_id": session_id,
                "radar_sensor": radar,
                **radar_settings(calibration, radar),
            }
        )
        jobs.append((f"{session_id}-{method}-{radar}", config))
    if faults:
        for session_id, method, kind, seed in itertools.product(
            protocol["final_sessions"],
            protocol.get("fault_methods", []),
            protocol.get("fault_levels", {}),
            protocol.get("fault_seeds", []),
        ):
            for level in protocol["fault_levels"][kind]:
                fault = FaultConfig(kind=kind, seed=seed, level=level)
                config = RunConfig.model_validate(protocol["selected"][method]).model_copy(
                    update={"session_id": session_id, "fault": fault}
                )
                jobs.append((f"{session_id}-{method}-{kind}-{level}-{seed}", config))
    for row in results:
        if row["status"] in ("starting", "running", "finalizing"):
            status_path = workspace / "runs" / row["run_id"] / "status.json"
            if status_path.exists():
                status = read_json(status_path)
                row.update(
                    status=status["status"],
                    metrics=status.get("metrics"),
                    error=status.get("error"),
                )
            if row["status"] in ("starting", "running", "finalizing"):
                row.update(
                    status="interrupted", error="previous study process stopped before finalisation"
                )
    for key, config in jobs:
        if key in completed:
            continue
        run_id = f"{study_id[:40]}-{uuid4().hex}"
        row = {
            "key": key,
            "run_id": run_id,
            "status": "starting",
            "config": config.model_dump(),
            "metrics": None,
            "error": None,
        }
        results.append(row)
        study["runs"] = results
        write_json(directory / "study.json", study)
        try:
            if config.session_id not in session_cache:
                from sensorfusion.data import load_session

                session_cache[config.session_id] = load_session(
                    workspace, config.dataset_id, config.session_id, config.calibration_id
                )
            status = execute_run(
                workspace,
                run_id,
                config,
                study_id=study_id,
                session=session_cache[config.session_id],
            )
            row.update(
                status=status["status"], metrics=status.get("metrics"), error=status.get("error")
            )
        except Exception as error:
            # The job boundary preserves each failed attempt and continues the declared matrix.
            row.update(status="failed", error=f"{type(error).__name__}: {error}")
        study["runs"] = results
        write_json(directory / "study.json", study)
    primary: dict[str, float | None] = {}
    comparison = protocol["primary_comparison"]
    for method in comparison:
        rows = [
            row
            for row in results
            if row["key"] in {f"{name}-{method}-original" for name in protocol["final_sessions"]}
        ]
        if len(rows) != len(protocol["final_sessions"]) or any(
            row["status"] != "completed" or row["metrics"]["position_rmse_m"] is None
            for row in rows
        ):
            primary[method] = None
        else:
            primary[method] = float(np.mean([row["metrics"]["position_rmse_m"] for row in rows]))
    first, second = primary[comparison[0]], primary[comparison[1]]
    study["results"] = {
        "primary_macro_rmse_m": primary,
        "primary_difference_m": first - second
        if first is not None and second is not None
        else None,
        "faults_executed": faults,
        "interpretation": "Negative or failed results are preserved. Two trajectories are not independent sites.",
    }
    if any(row["status"] != "completed" for row in results):
        study["status"] = "completed_with_failures"
    elif not faults and protocol.get("fault_methods"):
        study["status"] = "nominal_completed"
    else:
        study["status"] = "completed"
    study["completed_at"] = timestamp()
    write_json(directory / "study.json", study)
    return study


def study_catalog(workspace: Path) -> dict:
    studies = [read_json(path) for path in (workspace / "studies").glob("*/study.json")]
    blockers = []
    if not studies:
        blockers.append("No frozen recorded-data study has been registered and executed.")
    blockers.extend(
        ["External human review is pending.", "Independent human reproduction is pending."]
    )
    return {"studies": studies, "protocol": default_protocol(), "blockers": blockers}


def freeze_external_study(workspace: Path, study_id: str, calibration_id: str) -> dict:
    from sensorfusion.data import load_session
    from sensorfusion.engine import estimate
    from sensorfusion.evaluation import score

    directory = registered_path(workspace / "studies", study_id)
    if directory.exists():
        raise FileExistsError(
            "external study already exists; frozen evidence cannot be overwritten"
        )
    calibration = _calibration(workspace, calibration_id, "miluv")
    if "uwb" not in calibration.get("certified_modalities", []):
        raise ValueError("external study requires certified UWB observations")
    if calibration.get("fit_sessions") != ["default_1_random3_0"]:
        raise ValueError("external calibration must use only the selected development recording")
    session = load_session(workspace, "miluv", "default_1_random3_0", calibration_id)
    windows = []
    candidates: list[dict[str, Any]] = []
    for width in (0.05, 0.1, 0.2, 0.5, 1.0):
        config = RunConfig(
            dataset_id="miluv",
            session_id=session.session_id,
            method="M1",
            calibration_id=calibration_id,
            wls_window_s=width,
        )
        result = estimate(session.observations, config)
        availability = float(np.mean(np.all(np.isfinite(result.states), axis=1)))
        windows.append({"window_s": width, "availability": availability})
    width = next((row["window_s"] for row in windows if row["availability"] >= 0.9), 1.0)
    baseline = RunConfig(
        dataset_id="miluv",
        session_id=session.session_id,
        method="M1",
        calibration_id=calibration_id,
        wls_window_s=width,
    )
    for q in (0.01, 0.1, 1, 10):
        config = baseline.model_copy(update={"method": "M2", "q_cv": q})
        result = estimate(session.observations, config)
        metric = score(result, session.reference_times, session.reference_positions)[0]
        candidates.append({"config": config.model_dump(), "rmse_m": metric["position_rmse_m"]})
    eligible = [row for row in candidates if row["rmse_m"] is not None]
    if not eligible:
        raise ValueError("external development filtering has no scoreable estimates")
    best = min(eligible, key=lambda row: (row["rmse_m"], row["config"]["q_cv"]))
    protocol = {
        "schema_version": 1,
        "dataset_id": "miluv",
        "dataset_version": "28386041.v1",
        "calibration_sessions": ["default_1_random3_0"],
        "final_sessions": EXTERNAL_FINAL,
        "methods": ["M1", "M2"],
        "primary_comparison": ["M2", "M1"],
        "reference_max_gap_s": REFERENCE_MAX_GAP_S,
        "radar_substitutions": [],
        "selected": {"M1": baseline.model_dump(), "M2": best["config"]},
        "calibration_id": calibration_id,
        "calibration_hash": canonical_hash(calibration),
        "source_hash": scientific_source_hash(),
        "selection": {
            "windows": windows,
            "q_candidates": candidates,
            "development_only": True,
            "final_accessed": False,
        },
        "claim": "Development-calibrated portability of the range subsystem; not four-modality transfer.",
    }
    directory.mkdir(parents=True)
    study: dict[str, Any] = {
        "id": study_id,
        "status": "frozen",
        "created_at": timestamp(),
        "protocol": protocol,
        "protocol_hash": canonical_hash(protocol),
        "runs": [],
        "results": None,
        "review": {"human_review": "pending", "independent_reproduction": "pending"},
    }
    write_json(directory / "protocol.json", protocol)
    write_json(directory / "study.json", study)
    return study
