"""Development-only observation calibration, with explicit identifiability checks."""

from dataclasses import dataclass

import numpy as np

from sensorfusion.contracts import FloatArray
from sensorfusion.models import validate_covariance


def _development(split: str) -> None:
    if split != "development":
        raise ValueError(f"calibration fits require development data, got split={split!r}")


def _vectors(values: FloatArray, name: str) -> FloatArray:
    vectors = np.asarray(values, dtype=float)
    if vectors.ndim != 2 or vectors.shape[1] != 3 or vectors.shape[0] < 2:
        raise ValueError(f"{name} must have shape (N,3) with N>=2, got {vectors.shape}")
    if not np.isfinite(vectors).all():
        raise ValueError(f"{name} must be finite")
    return vectors


def _rotation(source: FloatArray, target: FloatArray) -> tuple[FloatArray, int]:
    singular_values = np.linalg.svd(source.T @ target, compute_uv=False)
    rank = int(np.count_nonzero(singular_values > max(singular_values[0] * 1e-6, 1e-12)))
    if rank < 2:
        raise ValueError("proper rotation needs two adequately excited noncollinear directions")
    left, _, right_transposed = np.linalg.svd(source.T @ target)
    handedness = np.eye(3)
    handedness[2, 2] = np.linalg.det(right_transposed.T @ left.T)
    rotation = right_transposed.T @ handedness @ left.T
    return rotation, rank


def _residual_covariance(residuals: FloatArray) -> FloatArray:
    covariance = np.asarray(np.cov(residuals, rowvar=False, ddof=1))
    validate_covariance(covariance)
    return covariance


@dataclass(frozen=True)
class RigidCalibration:
    rotation: FloatArray
    translation: FloatArray
    covariance: FloatArray
    rank: int

    def apply(self, points: FloatArray) -> FloatArray:
        return np.asarray(points) @ self.rotation.T + self.translation


def fit_rigid_transform(source: FloatArray, target: FloatArray, *, split: str) -> RigidCalibration:
    """Proper Kabsch fit; planar rank-two motion suffices, rank one does not."""
    _development(split)
    source = _vectors(source, "source")
    target = _vectors(target, "target")
    if source.shape != target.shape:
        raise ValueError(f"source and target shapes differ: {source.shape}, {target.shape}")
    source_mean, target_mean = source.mean(axis=0), target.mean(axis=0)
    rotation, rank = _rotation(source - source_mean, target - target_mean)
    translation = target_mean - rotation @ source_mean
    residual = target - (source @ rotation.T + translation)
    return RigidCalibration(rotation, translation, _residual_covariance(residual), rank)


@dataclass(frozen=True)
class RangeCalibration:
    bias: float
    variance: float
    count: int


def fit_range_bias(
    measured: FloatArray, true_ranges: FloatArray, *, split: str
) -> RangeCalibration:
    """Fit measured-minus-geometric offset [m] and residual sample variance [m²]."""
    _development(split)
    measured, true_ranges = np.asarray(measured), np.asarray(true_ranges)
    if measured.ndim != 1 or measured.shape != true_ranges.shape or measured.size < 2:
        raise ValueError("range pairs must be matching one-dimensional arrays with N>=2")
    if not np.isfinite(measured).all() or not np.isfinite(true_ranges).all():
        raise ValueError("range calibration pairs must be finite")
    if np.any(measured <= 0) or np.any(true_ranges <= 0):
        raise ValueError("range calibration pairs must be positive metres")
    residual = measured - true_ranges
    return RangeCalibration(float(residual.mean()), float(residual.var(ddof=1)), measured.size)


@dataclass(frozen=True)
class IMUCalibration:
    stationary_mean: FloatArray
    rotation: FloatArray
    covariance: FloatArray

    def apply(self, samples: FloatArray) -> FloatArray:
        return (np.asarray(samples) - self.stationary_mean) @ self.rotation.T


def fit_imu_calibration(
    stationary_acceleration: FloatArray,
    moving_acceleration: FloatArray,
    reference_acceleration: FloatArray,
    *,
    split: str,
) -> IMUCalibration:
    """Remove combined stationary contribution and fit a zero-translation rotation."""
    _development(split)
    stationary = _vectors(stationary_acceleration, "stationary acceleration")
    moving = _vectors(moving_acceleration, "moving acceleration")
    reference = _vectors(reference_acceleration, "reference acceleration")
    if moving.shape != reference.shape:
        raise ValueError("moving and reference accelerations must have matching shapes")
    stationary_mean = stationary.mean(axis=0)
    corrected = moving - stationary_mean
    rotation, _ = _rotation(corrected, reference)
    residual = reference - corrected @ rotation.T
    return IMUCalibration(stationary_mean, rotation, _residual_covariance(residual))


def fit_radar_calibration(
    centroids: FloatArray, reference_positions: FloatArray, *, split: str
) -> RigidCalibration:
    """Effective centroid-to-origin fit, not an identified physical radar extrinsic."""
    return fit_rigid_transform(centroids, reference_positions, split=split)
