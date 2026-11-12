import numpy as np
import pytest

from sensorfusion.calibration import (
    fit_imu_calibration,
    fit_radar_calibration,
    fit_range_bias,
    fit_rigid_transform,
)


def test_proper_rotation_accepts_planar_excitation_and_rejects_rank_one():
    source = np.array([[0, 0, 0], [1, 0, 0], [0, 2, 0], [-1, -1, 0]], dtype=float)
    rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], dtype=float)
    target = source @ rotation.T + [2, 3, 4]
    calibration = fit_rigid_transform(source, target, split="development")
    np.testing.assert_allclose(calibration.rotation, rotation, atol=1e-14)
    np.testing.assert_allclose(calibration.apply(source), target, atol=1e-14)
    assert np.linalg.det(calibration.rotation) == pytest.approx(1.0)
    assert calibration.rank == 2
    with pytest.raises(ValueError, match="noncollinear"):
        fit_rigid_transform(source[:2], target[:2], split="development")


def test_calibration_rejects_protected_split_and_estimates_range_bias():
    measured = np.array([2.1, 3.2, 4.3])
    truth = np.array([2.0, 3.0, 4.0])
    result = fit_range_bias(measured, truth, split="development")
    assert result.bias == pytest.approx(0.2)
    assert result.variance == pytest.approx(0.01)
    with pytest.raises(ValueError, match="development"):
        fit_range_bias(measured, truth, split="final")


def test_imu_combined_stationary_offset_and_motion_rotation():
    stationary = np.tile([0.1, -0.2, 9.7], (8, 1))
    motion = np.array([[1, 0, 0], [0, 2, 0], [-1, -1, 0], [1, -2, 0]], dtype=float)
    rotation = np.array([[0, 0, 1], [1, 0, 0], [0, 1, 0]], dtype=float)
    reference = motion @ rotation.T
    fit = fit_imu_calibration(stationary, motion + stationary[0], reference, split="development")
    np.testing.assert_allclose(fit.apply(motion + stationary[0]), reference, atol=1e-14)
    np.testing.assert_allclose(fit.stationary_mean, stationary[0])


def test_radar_covariance_retains_full_correlated_residuals():
    random = np.random.default_rng(3)
    centroids = random.normal(size=(100, 3))
    noise = random.normal(size=(100, 3)) @ np.array([[0.1, 0.1, 0], [0, 0.2, 0.1], [0, 0, 0.3]])
    calibration = fit_radar_calibration(centroids, centroids + noise, split="development")
    assert calibration.covariance.shape == (3, 3)
    assert np.linalg.eigvalsh(calibration.covariance).min() > 0
    assert abs(calibration.covariance[0, 1]) > 0.001
