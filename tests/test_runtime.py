import time

import pytest

from sensorfusion.contracts import RunConfig
from sensorfusion.runtime import (
    BusyError,
    ExperimentRuns,
    execute_run,
    require_certified_modalities,
)
from sensorfusion.storage import read_json, registered_path, trajectory


def test_complete_run_has_registered_evidence_and_reproducible_reference(tmp_path):
    status = execute_run(tmp_path, "fixture-check", RunConfig(duration_s=5))
    assert status["status"] == "completed"
    assert status["provenance"]["synthetic"]
    assert status["metrics"]["position_rmse_m"] < 0.2
    result = trajectory(tmp_path / "runs" / "fixture-check", 20)
    assert result["decimated"] and result["total_points"] == 101
    assert len(result["times"]) == 20
    manifest = read_json(tmp_path / "runs" / "fixture-check" / "artifacts.json")
    assert {row["id"] for row in manifest["artifacts"]} >= {
        "numerical.h5",
        "report.html",
        "trajectory.csv",
    }


def test_one_spawned_worker_and_cancellation(tmp_path):
    runs = ExperimentRuns(tmp_path)
    try:
        created = runs.submit(RunConfig(duration_s=300))
        with pytest.raises(BusyError):
            runs.submit(RunConfig())
        assert runs.cancel(created["run_id"])["requested"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            result = runs.get(created["run_id"])
            if result["status"] not in ("starting", "running", "finalizing"):
                break
            time.sleep(0.05)
        assert result["status"] == "cancelled"
        assert not (runs.directory(created["run_id"]) / "artifacts.json").exists()
    finally:
        runs.close()


def test_registered_identifiers_cannot_escape_registry(tmp_path):
    with pytest.raises(ValueError):
        registered_path(tmp_path, "../private")


def test_protected_final_session_cannot_run_as_an_experiment(tmp_path):
    with pytest.raises(ValueError, match="Protected final"):
        execute_run(
            tmp_path,
            "forbidden-final",
            RunConfig(dataset_id="roboloc-g", session_id="circle", method="M2"),
        )


def test_partial_calibration_cannot_pass_a_full_fusion_label():
    config = RunConfig(dataset_id="roboloc-g", session_id="zigzag1", method="M7")
    with pytest.raises(ValueError, match="radar"):
        require_certified_modalities({"certified_modalities": ["uwb", "ftm", "imu"]}, config)
    require_certified_modalities(
        {"certified_modalities": ["uwb"]}, config.model_copy(update={"method": "M2"})
    )
