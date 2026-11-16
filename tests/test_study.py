import pytest

from sensorfusion.contracts import RunConfig
from sensorfusion.runtime import execute_run
from sensorfusion.storage import canonical_hash, read_json, write_json
from sensorfusion.study import (
    _select_candidate,
    admit_study_config,
    run_study,
    scientific_source_hash,
    verify_study,
)


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


@pytest.mark.parametrize("score_field", ["objective", "rmse_m"])
def test_selection_retains_fixed_failure_and_excludes_failed_tunable_rows(score_field):
    fixed = {
        "config": RunConfig(method="M1").model_dump(),
        score_field: None,
        "failures": {"zigzag3": "NumericalError: authored failed validation result"},
    }
    assert _select_candidate([fixed], score_field, fixed_baseline=True) is fixed
    assert fixed["failures"] == {"zigzag3": "NumericalError: authored failed validation result"}
    failed = {"config": RunConfig(method="M2", q_cv=0.01).model_dump(), score_field: None}
    valid = {"config": RunConfig(method="M2", q_cv=0.1).model_dump(), score_field: 1.0}
    assert _select_candidate([failed, valid], score_field) is valid
    assert _select_candidate([failed], score_field) is None


@pytest.mark.parametrize("score_field", ["objective", "rmse_m"])
def test_selection_breaks_equal_score_ties_by_declared_process_noise(score_field):
    configurations = [
        RunConfig(q_cv=1.0, acceleration_noise=0.01, bias_diffusion=0),
        RunConfig(q_cv=0.1, acceleration_noise=0.2, bias_diffusion=0),
        RunConfig(q_cv=0.1, acceleration_noise=0.1, bias_diffusion=1e-6),
        RunConfig(q_cv=0.1, acceleration_noise=0.1, bias_diffusion=0),
    ]
    candidates = [{"config": config.model_dump(), score_field: 1.0} for config in configurations]
    assert _select_candidate(candidates, score_field) is candidates[-1]
