import hashlib

import pytest
from fastapi.testclient import TestClient

from sensorfusion.api import create_app
from sensorfusion.contracts import RunConfig
from sensorfusion.runtime import execute_run
from sensorfusion.storage import write_json


@pytest.fixture(scope="module")
def completed_workspace(tmp_path_factory):
    root = tmp_path_factory.mktemp("window-api")
    execute_run(root, "fixture", RunConfig(duration_s=5))
    return root


def test_windowed_reads_bound_rows_and_preserve_saved_metrics(completed_workspace):
    directory = completed_workspace / "runs/fixture"
    before = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in ("metrics.json", "numerical.h5", "artifacts.json")
    }
    with TestClient(create_app(completed_workspace)) as client:
        trajectory = client.get(
            "/api/runs/fixture/trajectory?start_s=1&end_s=2&max_points=10"
        ).json()
        assert trajectory["times"][0] == 1 and trajectory["times"][-1] == 2
        assert len(trajectory["times"]) == trajectory["returned_points"] == 10
        assert trajectory["window_points"] == 21 and trajectory["total_points"] == 101
        assert trajectory["decimated"]
        full = client.get("/api/runs/fixture/diagnostics?max_points=20000").json()
        eligible = [row for row in full["innovations"] if 1 <= row["t"] <= 2]
        diagnostics = client.get(
            "/api/runs/fixture/diagnostics?start_s=1&end_s=2&max_points=10"
        ).json()
        assert all(1 <= row["t"] <= 2 for row in diagnostics["innovations"])
        assert diagnostics["window_points"] == len(eligible)
        assert diagnostics["total_points"] == len(full["innovations"])
        assert 0 < diagnostics["returned_points"] <= 10
        frames = client.get("/api/runs/fixture/radar-frames?start_s=1&end_s=2&max_frames=3")
        assert frames.status_code == 200
        data = frames.json()
        assert data["window_points"] > 3 and data["decimated"]
        assert len(data["frames"]) == data["returned_points"] == 3
        for frame in data["frames"]:
            assert 1 <= frame["time"] <= 2 and frame["sensor_id"] == "AOP"
            nearest = client.get(
                "/api/runs/fixture/radar-frame", params={"time": frame["time"]}
            ).json()
            assert frame["centroid"] == nearest["centroid"]
            assert frame["points"] == nearest["points"]
        for route, key in (
            ("trajectory", "times"),
            ("diagnostics", "innovations"),
            ("radar-frames", "frames"),
        ):
            empty = client.get(f"/api/runs/fixture/{route}?start_s=6&end_s=7").json()
            assert empty[key] == [] and empty["window_points"] == 0
            assert not empty["decimated"]
    assert before == {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in before
    }


@pytest.mark.parametrize("route", ["trajectory", "diagnostics", "radar-frames"])
def test_time_windows_validate_bounds(completed_workspace, route):
    with TestClient(create_app(completed_workspace)) as client:
        url = f"/api/runs/fixture/{route}"
        assert client.get(url, params={"start_s": 2, "end_s": 1}).status_code == 400
        assert client.get(url, params={"start_s": "nan"}).status_code == 422
        assert client.get(url, params={"end_s": "inf"}).status_code == 422


def test_radar_window_supports_legacy_json_frames(tmp_path):
    directory = tmp_path / "runs/legacy"
    write_json(directory / "status.json", {"status": "completed", "run_id": "legacy"})
    write_json(directory / "config.json", {"radar_sensor": "AOP"})
    frames = [
        {
            "t": time,
            "sensor_id": sensor,
            "points": [[time, 0, 0]],
            "centroid": None,
            "frame": "world",
        }
        for time, sensor in ((0, "AOP"), (1, "AOP"), (1.5, "ISK0"), (2, "AOP"))
    ]
    write_json(directory / "radar.json", {"frames": frames})
    with TestClient(create_app(tmp_path)) as client:
        response = client.get("/api/runs/legacy/radar-frames?start_s=1&end_s=2&max_frames=1")
        assert response.status_code == 200
        data = response.json()
        assert data["total_points"] == 3 and data["window_points"] == 2
        assert data["returned_points"] == 1 and data["decimated"]
        assert data["frames"][0]["time"] == 1
