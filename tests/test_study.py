import pytest

from sensorfusion.contracts import RunConfig
from sensorfusion.runtime import execute_run
from sensorfusion.storage import canonical_hash, read_json, write_json
from sensorfusion.study import admit_study_config, run_study, scientific_source_hash, verify_study


def registered_study(root):
    calibration = {
        "dataset_id": "roboloc-g",
        "id": "test-calibration",
        "certified_modalities": ["uwb", "ftm", "imu", "radar"],
        "radars": {
            "ISK0": {"certified": True, "selected_clustering": {"radius_m": 0.4, "min_samples": 3}}
        },
    }
    write_json(root / "calibrations" / "test-calibration.json", calibration)
    selected = {
        method: RunConfig.model_validate(
            {"dataset_id": "roboloc-g", "method": method, "calibration_id": "test-calibration"}
        ).model_dump()
        for method in ("M2", "M7")
    }
    protocol = {
        "dataset_id": "roboloc-g",
        "calibration_id": "test-calibration",
        "calibration_hash": canonical_hash(calibration),
        "source_hash": scientific_source_hash(),
        "selected": selected,
        "final_sessions": ["circle", "random2"],
        "methods": ["M2", "M7"],
        "primary_comparison": ["M7", "M2"],
        "radar_primary": "AOP",
        "radar_substitutions": [],
        "fault_methods": ["M7"],
        "fault_levels": {"delay": [0.1]},
        "fault_seeds": [0],
    }
    study = {
        "id": "registered",
        "status": "frozen",
        "protocol": protocol,
        "protocol_hash": canonical_hash(protocol),
        "runs": [],
    }
    write_json(root / "studies" / "registered" / "protocol.json", protocol)
    write_json(root / "studies" / "registered" / "study.json", study)
    return study


def test_mutating_only_embedded_protocol_is_rejected(tmp_path):
    study = registered_study(tmp_path)
    study["protocol"]["selected"]["M2"]["q_cv"] = 99
    write_json(tmp_path / "studies" / "registered" / "study.json", study)
    with pytest.raises(ValueError, match="embedded"):
        verify_study(tmp_path, "registered")


def test_frozen_parameters_cannot_be_overridden_before_loading_final_data(tmp_path):
    registered_study(tmp_path)
    config = RunConfig(
        dataset_id="roboloc-g",
        session_id="circle",
        method="M2",
        calibration_id="test-calibration",
        q_cv=99,
    )
    with pytest.raises(ValueError, match="differs"):
        execute_run(tmp_path, "forbidden-drift", config, study_id="registered")
    assert not (tmp_path / "runs" / "forbidden-drift").exists()
    admit_study_config(tmp_path, "registered", config.model_copy(update={"q_cv": 0.1}))


def test_failed_matrix_preserves_every_attempt_and_null_headline(tmp_path):
    registered_study(tmp_path)
    result = run_study(tmp_path, "registered")
    assert result["status"] == "completed_with_failures"
    assert len(result["runs"]) == 4
    assert all(row["status"] == "failed" and row["error"] for row in result["runs"])
    assert result["results"]["primary_macro_rmse_m"] == {"M7": None, "M2": None}
    before = read_json(tmp_path / "studies" / "registered" / "study.json")
    with pytest.raises(ValueError, match="cannot start"):
        run_study(tmp_path, "registered")
    assert read_json(tmp_path / "studies" / "registered" / "study.json") == before


def test_selection_preserves_fixed_wls_failure_and_excludes_failed_tunable_candidate(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    import numpy as np

    from sensorfusion.models import NumericalError
    from sensorfusion.study import select_parameters

    calibration = {
        "dataset_id": "roboloc-g",
        "certified_modalities": ["uwb", "ftm", "imu", "radar"],
        "fit_sessions": ["still", "zigzag1", "zigzag2"],
        "imu": {"acceleration_noise_density_m2_s3": 0.1},
        "radars": {
            "AOP": {"certified": True, "selected_clustering": {"radius_m": 0.2, "min_samples": 3}}
        },
    }
    write_json(tmp_path / "calibrations" / "test.json", calibration)
    monkeypatch.setattr(
        "sensorfusion.data.load_session",
        lambda workspace, dataset, session, calibration: SimpleNamespace(
            session_id=session, observations=[], reference_times=[], reference_positions=[]
        ),
    )

    def fake_estimate(observations, config):
        if config.session_id == "zigzag3" and (
            config.method == "M1" or (config.method == "M2" and config.q_cv == 0.01)
        ):
            raise NumericalError("known WLS nonconvergence")
        return SimpleNamespace(states=np.zeros((2, 6)))

    monkeypatch.setattr("sensorfusion.engine.estimate", fake_estimate)
    monkeypatch.setattr(
        "sensorfusion.evaluation.score", lambda *args: ({"position_rmse_m": 1.0}, None, None)
    )
    selection = select_parameters(tmp_path, "test")
    assert selection["status"] == "completed" and not selection["final_accessed"]
    fixed = selection["candidates"]["M1"][0]
    assert fixed["objective"] is None
    assert "nonconvergence" in fixed["failures"]["zigzag3"]
    assert selection["selection_evidence"]["M1"]["selection_basis"] == "fixed_baseline"
    assert selection["selection_evidence"]["M1"]["validation_failed"]
    assert selection["selected"]["M2"]["q_cv"] == 0.1
    assert selection["candidates"]["M2"][0]["objective"] is None
