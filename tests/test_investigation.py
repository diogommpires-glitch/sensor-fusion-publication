import hashlib
import itertools
from pathlib import Path

import h5py
import numpy as np
import pytest
from pydantic import ValidationError

from sensorfusion.contracts import FactorConfig, FaultConfig, Observation, RunConfig, Session
from sensorfusion.engine import estimate
from sensorfusion.investigation import (
    V1_PROTOCOL_HASH,
    admit_investigation_config,
    condition_configs,
    development_diagnostics,
    factorial_contrasts,
    register_investigation,
    run_investigation,
    summarise_investigation,
    verify_investigation,
)
from sensorfusion.storage import canonical_hash, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "evidence" / "primary-protocol.json"


def registered(tmp_path):
    baseline = read_json(BASELINE)
    calibration = read_json(
        ROOT / "protocols" / "snapshots" / "calibrations" / f"{baseline['calibration_id']}.json"
    )
    write_json(tmp_path / "calibrations" / f"{baseline['calibration_id']}.json", calibration)
    return register_investigation(tmp_path, "mechanisms", BASELINE)


def observations():
    anchors = np.array([[0, 0, 0], [4, 0, 0], [0, 4, 0], [0, 0, 4]], dtype=float)
    position = np.array([1, 1, 1])
    rows: list[Observation] = []
    for epoch in (0.0, 0.5, 1.0):
        rows.extend(
            Observation(
                epoch,
                epoch,
                "uwb",
                str(index),
                np.array([np.linalg.norm(position - anchor)]),
                np.eye(1) * 0.01,
                anchor=anchor,
            )
            for index, anchor in enumerate(anchors)
        )
        rows.extend(
            [
                Observation(
                    epoch,
                    epoch,
                    "ftm",
                    "ftm",
                    np.array([np.linalg.norm(position)]),
                    np.eye(1) * 10,
                    anchor=np.zeros(3),
                ),
                Observation(
                    epoch, epoch, "imu", "imu", np.array([0.2, 0.1, -0.1]), np.eye(3) * 0.01
                ),
                Observation(epoch, epoch, "radar", "AOP", position + [3, 0, 0], np.eye(3) * 0.1),
            ]
        )
    return rows


@pytest.mark.parametrize(
    "method,propagation,radar,gating",
    [
        ("M3", "cv", False, False),
        ("M5", "inertial", False, False),
        ("M6", "inertial", True, False),
        ("M7", "inertial", True, True),
        ("M8", "cv", True, True),
    ],
)
def test_factor_controls_match_existing_engine_numerically(method, propagation, radar, gating):
    legacy = estimate(observations(), RunConfig(dataset_id="roboloc-g", method=method))
    factors = FactorConfig(propagation=propagation, radar=radar, gating=gating)
    config = RunConfig(
        dataset_id="roboloc-g", method="M3" if propagation == "cv" else "M6", factors=factors
    )
    controlled = estimate(observations(), config)
    np.testing.assert_array_equal(legacy.times, controlled.times)
    np.testing.assert_array_equal(legacy.states, controlled.states)
    np.testing.assert_array_equal(legacy.covariances, controlled.covariances)
    assert legacy.innovations == controlled.innovations
    assert "factors" not in legacy.metadata
    assert controlled.metadata["factors"] == factors.model_dump()


def test_all_factorial_conditions_are_causal_and_have_the_declared_state_dimension():
    for config in condition_configs(read_json(BASELINE)).values():
        assert config.factors is not None
        prefix = [row for row in observations() if row.arrival <= 0.5]
        before = estimate(prefix, config)
        after = estimate(observations(), config)
        np.testing.assert_array_equal(before.states, after.states[: len(before.times)])
        np.testing.assert_array_equal(before.covariances, after.covariances[: len(before.times)])
        assert before.states.shape[1] == (6 if config.factors.propagation == "cv" else 9)
        assert all(row["modality"] != "radar" for row in before.innovations) == (
            not config.factors.radar
        )


def test_factor_boundary_rejects_mislabelled_or_coerced_conditions():
    with pytest.raises(ValidationError):
        FactorConfig.model_validate({"propagation": "cv", "radar": "false", "gating": False})
    with pytest.raises(ValidationError, match="RoboLoc-G"):
        RunConfig(factors=FactorConfig(propagation="cv", radar=False, gating=False))
    with pytest.raises(ValidationError, match="unmodified"):
        RunConfig(
            dataset_id="roboloc-g",
            method="M3",
            factors=FactorConfig(propagation="cv", radar=False, gating=False),
            fault=FaultConfig(kind="delay", level=0.1),
        )


def test_registration_pins_baseline_calibration_conditions_and_exploratory_scope(tmp_path):
    record = registered(tmp_path)
    assert record["kind"] == "exploratory_investigation"
    assert record["protocol"]["baseline_protocol_hash"] == V1_PROTOCOL_HASH
    assert len(record["protocol"]["conditions"]) == 8
    assert len(record["protocol"]["sessions"]) == 8
    for values in record["protocol"]["conditions"].values():
        assert values["wls_window_s"] == 0.5
        assert values["radar_sensor"] == "AOP"
        if values["factors"]["propagation"] == "cv":
            assert values["q_cv"] == 0.01
        else:
            assert values["acceleration_noise"] == pytest.approx(0.07247761828407538)
            assert values["bias_diffusion"] == 1e-4
        config = RunConfig.model_validate(values).model_copy(update={"session_id": "circle"})
        admit_investigation_config(tmp_path, "mechanisms", config)
        with pytest.raises(ValueError, match="differs"):
            admit_investigation_config(
                tmp_path, "mechanisms", config.model_copy(update={"q_cv": 7})
            )
    with pytest.raises(FileExistsError):
        register_investigation(tmp_path, "mechanisms", BASELINE)
    with pytest.raises(ValueError, match="pinned"):
        condition_configs({**read_json(BASELINE), "reference_max_gap_s": 1})


def test_embedded_protocol_replacement_is_rejected(tmp_path):
    record = registered(tmp_path)
    record["protocol"]["sessions"] = ["circle"]
    write_json(tmp_path / "investigations/mechanisms/investigation.json", record)
    with pytest.raises(ValueError, match="hash"):
        verify_investigation(tmp_path, "mechanisms")


def test_factorial_contrast_scaling_and_failure_preservation():
    rows = []
    for a, b, c in itertools.product((-1, 1), repeat=3):
        rows.append(
            {
                "factors": {
                    "propagation": "inertial" if a == 1 else "cv",
                    "radar": b == 1,
                    "gating": c == 1,
                },
                "position_rmse_m": 100 + 2 * a + 3 * b + 4 * c + 5 * a * b + 7 * a * b * c,
            }
        )
    contrasts = {row["factors"]: row["contrast_rmse_m"] for row in factorial_contrasts(rows)}
    assert contrasts == {
        "propagation": 4,
        "radar": 6,
        "gating": 8,
        "propagation:radar": 20,
        "propagation:gating": 0,
        "radar:gating": 0,
        "propagation:radar:gating": 56,
    }
    rows[0]["position_rmse_m"] = None
    assert all(row["contrast_rmse_m"] is None for row in factorial_contrasts(rows))
    with pytest.raises(ValueError, match="distinct"):
        factorial_contrasts([rows[0]] * 8)


def test_registration_calibration_hash_is_checked_before_directory_creation(tmp_path):
    baseline = read_json(BASELINE)
    write_json(tmp_path / "calibrations" / f"{baseline['calibration_id']}.json", {})
    with pytest.raises(ValueError, match="calibration"):
        register_investigation(tmp_path, "rejected", BASELINE)
    assert not (tmp_path / "investigations/rejected").exists()
    assert canonical_hash(baseline) == V1_PROTOCOL_HASH


def test_missing_sources_preserve_all64_failed_jobs_and_no_favourable_macro(tmp_path):
    registered(tmp_path)
    outcome = run_investigation(tmp_path, "mechanisms")
    assert outcome["status"] == "completed_with_failures"
    assert len(outcome["runs"]) == 64
    assert all(row["status"] == "failed" and row["error"] for row in outcome["runs"])
    assert all(row["contrast_rmse_m"] is None for row in outcome["results"]["macro_contrasts"])
    diagnostics = read_json(tmp_path / "investigations/mechanisms/development-diagnostics.json")
    assert diagnostics["status"] == "failed"
    export = tmp_path / outcome["results"]["report_directory"]
    manifest = read_json(export / "artifacts.json")
    assert all(
        hashlib.sha256((export / item["name"]).read_bytes()).hexdigest() == item["sha256"]
        for item in manifest["artifacts"]
    )
    with pytest.raises(ValueError, match="cannot be rerun"):
        run_investigation(tmp_path, "mechanisms")


def test_development_residuals_reconstruct_known_acceleration_and_visibility_denominators(tmp_path):
    epochs = np.arange(0, 3.01, 0.1)
    sessions = []
    for name in ("still", "zigzag1", "zigzag2"):
        stationary = name == "still"
        positions = np.column_stack(
            [np.ones(len(epochs)) + (0 if stationary else epochs**2 / 2), np.ones((len(epochs), 2))]
        )
        rows = [
            Observation(
                float(epoch),
                float(epoch),
                "uwb",
                "anchor",
                np.array([np.linalg.norm(position) + 0.01 * epoch]),
                np.eye(1),
                anchor=np.zeros(3),
            )
            for epoch, position in zip(epochs, positions, strict=True)
        ]
        rows.extend(
            Observation(
                float(epoch),
                float(epoch),
                "imu",
                "imu",
                np.array([0 if stationary else 1, 0, 0]),
                np.eye(3),
            )
            for epoch in epochs
        )
        sessions.append(Session("fixture", name, rows, epochs, positions, {}))
    annotation_file = tmp_path / "data/annotations/fixture.json"
    write_json(
        annotation_file,
        {
            "radars": {
                "AOP": {
                    "scans": [
                        {"role": "fit", "label": "target"},
                        {"role": "check", "label": "no-supported-target"},
                    ]
                }
            }
        },
    )
    calibration = {
        "radar_annotation_fit": {
            "path": "data/annotations/fixture.json",
            "sha256": hashlib.sha256(annotation_file.read_bytes()).hexdigest(),
        }
    }
    diagnostics = development_diagnostics(tmp_path, sessions, calibration)
    assert all(row["mean"] == pytest.approx(0.015) for row in diagnostics["radio_residuals"])
    assert all(abs(row["mean"]) < 1e-10 for row in diagnostics["imu_residuals"])
    assert all(row["total_scans"] == 1 for row in diagnostics["radar_development_visibility"])
    with pytest.raises(ValueError, match="development"):
        development_diagnostics(tmp_path, sessions[:2], calibration)
    annotation_file.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        development_diagnostics(tmp_path, sessions, calibration)


def test_summary_checks_consumed_artifacts_and_retains_previous_exports(tmp_path):
    record = registered(tmp_path)
    for session in record["protocol"]["sessions"]:
        for index, (condition, values) in enumerate(record["protocol"]["conditions"].items()):
            config = (
                RunConfig.model_validate(values)
                .model_copy(update={"session_id": session})
                .model_dump()
            )
            run_id = f"{session}-{index}"
            metrics = {"position_rmse_m": 1.0, "availability": 1.0, "reference_coverage": 1.0}
            row = {
                "session": session,
                "condition": condition,
                "run_id": run_id,
                "status": "completed" if session == "circle" else "failed",
                "error": None if session == "circle" else "fixture failure",
                "config": config,
                "metrics": metrics if session == "circle" else None,
            }
            record["runs"].append(row)
            if session != "circle":
                continue
            directory = tmp_path / "runs" / run_id
            write_json(directory / "config.json", config)
            write_json(directory / "metrics.json", metrics)
            write_json(
                directory / "innovations.json",
                {
                    "metadata": {"radar_associations": []},
                    "innovations": [
                        {
                            "time": 0.5,
                            "arrival": 0.5,
                            "accepted": True,
                            "modality": "uwb",
                            "reason": "accepted",
                            "nis": 1,
                        }
                    ],
                },
            )
            with h5py.File(directory / "numerical.h5", "w") as h5:
                h5["times"] = np.arange(41) * 0.05
                h5["states"] = np.zeros((41, 9))
                h5["position_errors"] = np.ones(41)
            write_json(
                directory / "artifacts.json",
                {
                    "artifacts": [
                        {
                            "name": name,
                            "sha256": hashlib.sha256((directory / name).read_bytes()).hexdigest(),
                        }
                        for name in (
                            "config.json",
                            "metrics.json",
                            "innovations.json",
                            "numerical.h5",
                        )
                    ]
                },
            )
    registry = tmp_path / "investigations/mechanisms"
    write_json(
        registry / "development-diagnostics.json",
        {"status": "completed", "synthetic_fixture": True},
    )
    summary = summarise_investigation(tmp_path, record)
    assert all(
        effect["contrast_rmse_m"] == 0
        for effect in summary["contrasts"]
        if effect["session"] == "circle"
    )
    assert all(effect["contrast_rmse_m"] is None for effect in summary["macro_contrasts"])
    export = tmp_path / summary["report_directory"]
    original_report = (export / "report.html").read_bytes()
    with (tmp_path / "runs/circle-0/numerical.h5").open("ab") as tampered:
        tampered.write(b"x")
    with pytest.raises(ValueError, match="invalid numerical"):
        summarise_investigation(tmp_path, record)
    assert (export / "report.html").read_bytes() == original_report
    assert read_json(registry / "summary.json")["report_directory"] == summary["report_directory"]
