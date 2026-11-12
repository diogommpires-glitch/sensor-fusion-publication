from fastapi.testclient import TestClient

from sensorfusion.api import create_app


def test_default_config_and_catalog_are_authoritative(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.get("/api/catalog")
        assert response.status_code == 200
        data = response.json()
        assert data["default_config"]["method"] == "M7"
        assert data["datasets"][0]["synthetic"]
        assert len(data["methods"]) == 8
        primary = next(item for item in data["datasets"] if item["id"] == "roboloc-g")
        assert {row["id"] for row in primary["sessions"]} == {
            "still",
            "zigzag1",
            "zigzag2",
            "zigzag3",
            "zigzag4",
            "random1",
            "circle",
            "random2",
        }
        assert (
            next(row for row in primary["sessions"] if row["id"] == "circle")["status"]
            == "protected"
        )
        assert client.post("/api/config/resolve", json={"q_cv": -1}).status_code == 422


def test_untrusted_origin_and_paths_are_rejected(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert (
            client.post(
                "/api/runs", json={}, headers={"Origin": "https://unrelated.example"}
            ).status_code
            == 403
        )
        assert client.get("/api/runs/%2e%2e%2fprivate").status_code == 404
        assert (
            client.post(
                "/api/runs",
                json={"dataset_id": "roboloc-g", "session_id": "circle", "method": "M2"},
            ).status_code
            == 422
        )


def test_no_study_is_passed_without_execution(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        result = client.get("/api/studies").json()
        assert result["studies"] == []
        assert result["blockers"]
