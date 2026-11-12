import numpy as np

from sensorfusion.contracts import Estimate
from sensorfusion.evaluation import align_reference, score


def test_reference_gaps_and_missing_estimates_are_reported_separately():
    times = np.array([0.0, 0.05, 0.1, 0.15, 0.2])
    truth_t = np.array([0.0, 0.05, 0.1, 0.2])
    truth = np.zeros((4, 3))
    state = np.zeros((5, 6))
    state[1] = np.nan
    state[:, 0] += 1
    estimate = Estimate(times, state, np.tile(np.eye(6), (5, 1, 1)), [])
    metrics, _, _ = score(estimate, truth_t, truth)
    assert metrics["position_rmse_m"] == 1
    assert metrics["availability"] == 0.8


def test_reference_interpolation_does_not_bridge_large_gaps():
    aligned = align_reference(np.array([0.05, 0.5]), np.array([0, 0.1, 1]), np.zeros((3, 3)))
    assert np.isfinite(aligned[0]).all()
    assert np.isnan(aligned[1]).all()


def test_published_ten_hz_reference_allows_measured_clock_jitter_not_dropouts():
    times = np.array([0.0, 0.1001072, 0.2002144, 0.4004288])
    positions = np.column_stack((times, np.zeros((4, 2))))
    aligned = align_reference(np.array([0.05, 0.15, 0.3]), times, positions)
    np.testing.assert_allclose(aligned[:2, 0], [0.05, 0.15])
    assert np.isnan(aligned[2]).all()
