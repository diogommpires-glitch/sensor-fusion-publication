import threading
import time

import numpy as np
import pytest
from filelock import FileLock
from scipy.stats import chi2

from sensorfusion.contracts import RunConfig
from sensorfusion.reporting import generate_report
from sensorfusion.runtime import BusyError, ExperimentRuns, execute_run
from sensorfusion.storage import canonical_hash, read_json, trajectory, write_json


def test_shared_cli_lock_rejects_admission_and_orphans_reconcile(tmp_path):
    write_json(
        tmp_path / "runs" / "orphan" / "status.json",
        {"status": "running", "run_id": "orphan", "created_at": "2026-10-29"},
    )
    runs = ExperimentRuns(tmp_path)
    assert runs.get("orphan")["status"] == "interrupted"
    with FileLock(str(tmp_path / "numerical-worker.lock")), pytest.raises(BusyError):
        runs.submit(RunConfig(duration_s=5))
    assert len(runs.list()) == 1


def test_report_export_preserves_completed_artifacts_and_confidence_geometry(tmp_path):
    execute_run(tmp_path, "immutable", RunConfig(duration_s=5))
    directory = tmp_path / "runs" / "immutable"
    original = canonical_hash(read_json(directory / "artifacts.json"))
    with pytest.raises(ValueError, match="immutable"):
        generate_report(directory)
    export = generate_report(directory, tmp_path / "new-export")
    assert export.is_file()
    assert canonical_hash(read_json(directory / "artifacts.json")) == original
    result = trajectory(directory, 20)
    axes = np.array(result["confidence_ellipsoid_axes"][0])
    covariance = np.array(result["position_covariances"][0])
    np.testing.assert_allclose(axes @ axes.T, chi2.ppf(0.95, 3) * covariance, atol=1e-12)
    ellipse = np.array(result["confidence_ellipse_xy_axes"][0])
    np.testing.assert_allclose(
        ellipse @ ellipse.T, chi2.ppf(0.95, 2) * covariance[:2, :2], atol=1e-12
    )


def test_shared_radar_key_binds_content_not_only_frame_count(tmp_path):
    execute_run(tmp_path, "five", RunConfig(duration_s=5))
    execute_run(tmp_path, "slightly-longer", RunConfig(duration_s=5.03))
    first = read_json(tmp_path / "runs" / "five" / "radar.json")["frames"]
    second = read_json(tmp_path / "runs" / "slightly-longer" / "radar.json")["frames"]
    assert len(first) == len(second)
    assert first[0]["time"] == second[0]["time"]
    assert first[0]["centroid"] != second[0]["centroid"]


def test_polling_does_not_interrupt_a_live_direct_cli_job(tmp_path):
    cancellation = threading.Event()
    thread = threading.Thread(
        target=execute_run, args=(tmp_path, "live-cli", RunConfig(duration_s=300), cancellation)
    )
    thread.start()
    try:
        path = tmp_path / "runs" / "live-cli" / "status.json"
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        status = ExperimentRuns(tmp_path).get("live-cli")
        assert status["status"] != "interrupted"
        assert status["owner_pid"] > 0
    finally:
        cancellation.set()
        thread.join(timeout=20)
    assert not thread.is_alive()
