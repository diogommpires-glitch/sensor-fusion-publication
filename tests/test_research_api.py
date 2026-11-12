from fastapi.testclient import TestClient

from sensorfusion.api import create_app
from sensorfusion.contracts import FactorConfig, RunConfig
from sensorfusion.runtime import ExperimentRuns, require_certified_modalities
from sensorfusion.storage import write_json


def test_research_catalogue_does_not_certify_unavailable_hardware(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/health").json()["version"] == "0.2.0"
        response = client.get("/api/research")
        assert response.status_code == 200
        catalogue = response.json()
        assert catalogue["investigations"] == []
        assert catalogue["historical_v1"]["revision"] == "72ad06d"
        assert not catalogue["hardware"]["physical_inventory_verified"]
        assert catalogue["hardware"]["blocking_reasons"]


def test_factor_condition_cannot_be_submitted_as_an_ordinary_run(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        factors = {"propagation": "cv", "radar": False, "gating": True}
        draft = client.post(
            "/api/config/resolve",
            json={
                "dataset_id": "roboloc-g",
                "session_id": "zigzag1",
                "method": "M3",
                "factors": factors,
            },
        )
        assert draft.status_code == 200 and draft.json()["config"]["factors"] == factors
        response = client.post(
            "/api/runs",
            json={
                "dataset_id": "roboloc-g",
                "session_id": "zigzag1",
                "method": "M3",
                "factors": {"propagation": "cv", "radar": False, "gating": True},
            },
        )
        assert response.status_code == 422
        assert "registered exploratory" in response.json()["detail"]
        assert client.get("/api/runs").json()["runs"] == []


def test_registered_research_reads_and_invalid_identifiers(tmp_path):
    saved = {"id": "registered", "kind": "exploratory_investigation", "status": "registered"}
    write_json(tmp_path / "investigations/registered/investigation.json", saved)
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/investigations/registered").json() == saved
        assert client.get("/api/research").json()["investigations"] == [saved]
        assert client.get("/api/investigations/bad$id").status_code == 400
        assert client.get("/api/investigations/missing").status_code == 404
        assert client.get("/api/runs/missing").status_code == 404
        assert client.get("/api/hardware/trials/bad$id").status_code == 400
        assert client.get("/api/hardware/trials/missing").status_code == 404


def test_factor_modalities_follow_the_declared_sensor_ablation(tmp_path):
    import pytest

    cv = RunConfig(
        dataset_id="roboloc-g",
        method="M3",
        factors=FactorConfig(propagation="cv", radar=False, gating=True),
    )
    require_certified_modalities({"certified_modalities": ["uwb", "ftm"]}, cv)
    inertial = RunConfig(
        dataset_id="roboloc-g",
        method="M6",
        factors=FactorConfig(propagation="inertial", radar=False, gating=False),
    )
    with pytest.raises(ValueError, match="imu"):
        require_certified_modalities({"certified_modalities": ["uwb", "ftm"]}, inertial)
    require_certified_modalities({"certified_modalities": ["uwb", "ftm", "imu"]}, inertial)
    controller = ExperimentRuns(tmp_path)
    try:
        with pytest.raises(ValueError, match="registered investigation"):
            controller.submit(cv)
        assert controller.list() == []
    finally:
        controller.close()
