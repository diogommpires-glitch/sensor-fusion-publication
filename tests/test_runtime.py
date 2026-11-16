import sys
import time
from multiprocessing.process import BaseProcess

import pytest

from sensorfusion.contracts import RunConfig
from sensorfusion.runtime import (
    BusyError,
    ExperimentRuns,
    execute_run,
    require_certified_modalities,
)
from sensorfusion.storage import read_json, registered_path, trajectory, write_json


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


def test_worker_start_failure_preserves_failed_record_and_allows_retry(tmp_path, monkeypatch):
    def unavailable(process):
        raise OSError("worker creation unavailable")

    runs = ExperimentRuns(tmp_path)
    try:
        with monkeypatch.context() as boundary:
            boundary.setattr(BaseProcess, "start", unavailable)
            with pytest.raises(OSError, match="worker creation unavailable"):
                runs.submit(RunConfig(duration_s=5))
        failed = runs.list()
        assert len(failed) == 1 and failed[0]["status"] == "failed"
        assert failed[0]["error"] == "worker creation unavailable"
        assert not runs.cancel(failed[0]["run_id"])["requested"]
        runs.close()
        created = runs.submit(RunConfig(duration_s=5))
        deadline = time.monotonic() + 20
        result = runs.get(created["run_id"])
        while (
            result["status"] in ("starting", "running", "finalizing")
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
            result = runs.get(created["run_id"])
        assert result["status"] == "completed"
    finally:
        runs.close()


@pytest.mark.skipif(
    sys.platform != "linux", reason="Linux epoch creation times track boot-time changes"
)
def test_live_linux_identity_survives_clock_adjustment_and_rejects_reuse(tmp_path, monkeypatch):
    import psutil
    from psutil import _pslinux

    from sensorfusion.runtime import process_identity

    process = psutil.Process()
    identity = process_identity(process.pid)
    status = {
        "status": "running",
        "owner_pid": process.pid,
        "owner_started": process.create_time(),
        "owner_identity": identity,
    }
    write_json(tmp_path / "runs/live/status.json", {**status, "run_id": "live"})
    write_json(
        tmp_path / "runs/reused/status.json",
        {**status, "run_id": "reused", "owner_identity": {**identity, "start_ticks": "-1"}},
    )
    shifted_boot = _pslinux.boot_time() + 60
    monkeypatch.setattr(_pslinux, "boot_time", lambda: shifted_boot)
    runs = ExperimentRuns(tmp_path)
    try:
        assert runs.get("live")["status"] == "running"
        assert runs.get("reused")["status"] == "interrupted"
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
