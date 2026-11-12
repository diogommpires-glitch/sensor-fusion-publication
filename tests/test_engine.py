import numpy as np
import pytest

from sensorfusion.contracts import Observation, RunConfig
from sensorfusion.engine import CancelledError, estimate

ANCHORS = np.array([[0, 0, 0], [4, 0, 0], [0, 4, 0], [0, 0, 4]], dtype=float)
POSITION = np.array([1.0, 1.0, 1.0])


def ranges(time, position=POSITION):
    return [
        Observation(
            time,
            time,
            "uwb",
            str(i),
            np.array([np.linalg.norm(position - anchor)]),
            np.array([[0.01]]),
            anchor=anchor.copy(),
        )
        for i, anchor in enumerate(ANCHORS)
    ]


def test_all_methods_share_reference_free_initialisation_and_full_covariances():
    observations = ranges(0) + ranges(0.5) + ranges(1.0)
    for method in ("M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8"):
        result = estimate(observations, RunConfig(method=method, wls_window_s=1.0))
        np.testing.assert_allclose(result.states[:, :3], np.tile(POSITION, (21, 1)), atol=1e-8)
        assert result.covariances.shape == (21, result.states.shape[1], result.states.shape[1])
        assert np.linalg.eigvalsh(result.covariances).min() >= -1e-12
        assert result.metadata["initialisation"] == "uwb_wls"


def test_causal_prefix_and_delayed_arrival_are_not_acquisition_sorted():
    prefix = ranges(0) + ranges(0.3) + ranges(0.6)
    delayed = Observation(
        0.2, 0.8, "uwb", "late", np.array([20.0]), np.array([[0.01]]), anchor=ANCHORS[0]
    )
    configuration = RunConfig(method="M2")
    before = estimate(prefix, configuration)
    after = estimate(prefix + [delayed] + ranges(1.0), configuration)
    np.testing.assert_array_equal(after.times[: len(before.times)], before.times)
    np.testing.assert_allclose(after.states[: len(before.times)], before.states, atol=0, rtol=0)
    rejected = [row for row in after.innovations if row["sensor_id"] == "late"]
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "stale"
    assert not rejected[0]["accepted"]


def test_inertial_prediction_holds_previous_available_acceleration():
    observations = ranges(0) + [
        Observation(0, 0, "imu", "imu", np.array([1.0, 0, 0]), np.eye(3) * 0.01),
        Observation(0.5, 0.5, "imu", "imu", np.zeros(3), np.eye(3) * 0.01),
        Observation(1, 1, "imu", "imu", np.zeros(3), np.eye(3) * 0.01),
    ]
    result = estimate(observations, RunConfig(method="M4"))
    np.testing.assert_allclose(result.states[-1, :6], [1.375, 1, 1, 0.5, 0, 0], atol=1e-8)


def test_gate_uses_actual_dimension_and_retains_rejected_innovation():
    observations = ranges(0) + [
        Observation(0.2, 0.2, "radar", "AOP", POSITION + [10, 0, 0], np.eye(3) * 0.01),
        Observation(0.4, 0.4, "radar", "ISK0", POSITION + [30, 0, 0], np.eye(3) * 0.01),
        Observation(0.6, 0.6, "radar", "AOP", POSITION.copy(), np.eye(3) * 0.01),
    ]
    gated = estimate(observations, RunConfig(method="M7"))
    ungated = estimate(observations, RunConfig(method="M6"))
    radar = [row for row in gated.innovations if row["modality"] == "radar"]
    assert len(radar) == 2
    assert radar[0]["dimension"] == 3
    assert radar[0]["reason"] == "gate"
    assert radar[0]["nis"] > radar[0]["threshold"]
    assert radar[1]["accepted"]
    assert np.linalg.norm(gated.states[4, :3] - POSITION) < 1e-8
    assert np.linalg.norm(ungated.states[4, :3] - POSITION) > 1.0
    for key in ("time", "arrival", "sensor_id", "predicted", "value", "residual"):
        assert key in radar[0]


def test_geometry_failure_and_m1_expiry_remain_visible():
    observations = ranges(0)[:3] + ranges(0.5) + ranges(1)[:1]
    result = estimate(observations, RunConfig(method="M1", wls_window_s=0.2))
    assert np.isnan(result.states[0]).all()
    assert np.isfinite(result.states[10]).all()
    assert np.isnan(result.states[-1]).all()
    assert result.metadata["unavailable_outputs"] > 0
    assert any(row["reason"] == "initialisation_geometry" for row in result.innovations)


def test_valid_measurements_can_be_locked_out_without_automatic_reset():
    observations = ranges(0)
    for time in (0.2, 0.4, 0.6):
        observations += ranges(time, POSITION + [20, 20, 20])
    result = estimate(observations, RunConfig(method="M8", q_cv=0.0))
    assert all(row["reason"] == "gate" for row in result.innovations[4:])
    np.testing.assert_allclose(result.states[-1, :3], POSITION, atol=1e-8)
    assert result.metadata["automatic_resets"] == 0


def test_raw_radar_clouds_use_causal_prediction_and_one_update_per_scan():
    target = POSITION + np.array([[0, 0, 0], [0.01, -0.01, 0], [-0.01, 0.01, 0]])
    cloud = np.vstack([target, target + [8, 0, 0], [[20, 20, 20]]])
    observations = ranges(0) + [
        Observation(0.2, 0.2, "radar", "AOP", cloud, np.eye(3) * 0.01),
        Observation(0.4, 0.4, "radar", "AOP", np.empty((0, 3)), np.eye(3) * 0.01),
        Observation(0.6, 0.6, "radar", "AOP", cloud + [8, 0, 0], np.eye(3) * 0.01),
    ]
    result = estimate(observations, RunConfig(method="M7"))
    associations = result.metadata["radar_associations"]
    assert [row["reason"] for row in associations] == ["associated", "no_clusters", "outside_gate"]
    assert associations[0]["cluster_count"] == 2
    radar = [row for row in result.innovations if row["modality"] == "radar"]
    assert len(radar) == 3
    assert sum(row["accepted"] for row in radar) == 1
    np.testing.assert_allclose(result.states[-1, :3], POSITION, atol=1e-8)


def test_cancellation_and_invalid_observation_boundary():
    with pytest.raises(CancelledError):
        estimate(ranges(0), RunConfig(), cancelled=lambda: True)
    invalid = Observation(0, 0, "radar", "AOP", np.zeros(2), np.eye(2))
    with pytest.raises(ValueError, match="shape"):
        estimate([invalid], RunConfig())


def test_negative_bias_corrected_range_is_not_censored():
    corrected = Observation(
        0.2, 0.2, "ftm", "ftm", np.array([-0.5]), np.array([[4.0]]), anchor=ANCHORS[0]
    )
    result = estimate(ranges(0) + [corrected], RunConfig(method="M3"))
    diagnostic = result.innovations[-1]
    assert diagnostic["value"] == [-0.5]
    assert diagnostic["accepted"]
    assert diagnostic["residual"][0] < -0.5
    assert np.isfinite(result.states).all()
