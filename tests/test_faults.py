from dataclasses import replace

import numpy as np
import pytest

from sensorfusion.contracts import FaultConfig, Observation
from sensorfusion.faults import inject_fault


def test_radar_shift_moves_every_point_in_one_axis_without_mutating_source():
    cloud = np.array([[1.0, 1, 1], [1.02, 1, 1], [0.98, 1, 1]])
    radar = Observation(3, 3, "radar", "AOP", cloud, np.eye(3))
    endpoints = [replace(radar, t=0, arrival=0), radar, replace(radar, t=9, arrival=9)]
    shifted, metadata = inject_fault(
        endpoints, FaultConfig(kind="radar_shift", level=0.5, start_s=3, duration_s=1, axis=0)
    )
    np.testing.assert_allclose(shifted[1].value - cloud, np.tile([0.5, 0, 0], (3, 1)))
    np.testing.assert_array_equal(radar.value, cloud)
    assert metadata["affected"] == 1


def test_delay_changes_arrival_order_without_changing_acquisition_time():
    base = Observation(0, 0, "uwb", "a", np.array([3.0]), np.eye(1), np.ones(3))
    source = [replace(base, t=t, arrival=t) for t in range(10)]
    altered, metadata = inject_fault(
        source, FaultConfig(kind="delay", level=0.25, start_s=3, duration_s=1)
    )
    row = next(row for row in altered if row.t == 3)
    assert row.arrival == 3.25
    assert source[3].arrival == 3
    assert metadata["injected"]


def test_episode_that_does_not_fit_is_reported_skipped():
    row = Observation(0, 0, "uwb", "a", np.array([3.0]), np.eye(1), np.ones(3))
    _, metadata = inject_fault([row], FaultConfig(kind="dropout", level=5))
    assert metadata["skipped"]
    assert not metadata["injected"]


@pytest.mark.parametrize("kind,modality", [("range_bias", "uwb"), ("radar_shift", "radar")])
def test_unknown_explicit_fault_sensor_is_rejected(kind, modality):
    dimension = 1 if modality == "uwb" else 3
    row = Observation(0, 0, modality, "known", np.ones(dimension), np.eye(dimension))
    observations = [row, replace(row, t=9, arrival=9)]
    with pytest.raises(ValueError, match="unknown.*sensor"):
        inject_fault(
            observations,
            FaultConfig(kind=kind, level=0.5, sensor_id="absent", start_s=3, duration_s=1),
        )


def test_empty_fault_episode_preserves_observations_and_reports_no_injection():
    row = Observation(0, 0, "uwb", "a", np.array([3.0]), np.eye(1), np.ones(3))
    observations = [row, replace(row, t=9, arrival=9)]
    unchanged, metadata = inject_fault(
        observations,
        FaultConfig(kind="range_bias", level=0.5, sensor_id="a", start_s=3, duration_s=1),
    )
    assert all(left is right for left, right in zip(unchanged, observations, strict=True))
    assert metadata["affected"] == 0
    assert not metadata["injected"]
    assert metadata["reason"] == "no eligible observations in the requested episode"
