"""Metre/second continuous-discrete models and covariance-safe updates."""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares

from sensorfusion.contracts import FloatArray, Observation


class GeometryError(ValueError):
    """The available observations do not identify a unique local 3D position."""


class NumericalError(ValueError):
    """A numerical scientific invariant failed; a run must not hide this failure."""


def validate_covariance(covariance: FloatArray, *, positive: bool = False) -> None:
    """Check symmetry and PSD without repairing negative eigenvalues."""
    if covariance.ndim != 2 or covariance.shape[0] != covariance.shape[1]:
        raise NumericalError(f"covariance must be square, got {covariance.shape}")
    if not np.isfinite(covariance).all():
        raise NumericalError("covariance must contain finite values")
    scale = max(float(np.max(np.abs(covariance))), 1.0)
    if not np.allclose(covariance, covariance.T, atol=1e-11 * scale, rtol=1e-10):
        raise NumericalError("covariance must be symmetric")
    smallest = float(np.linalg.eigvalsh(covariance)[0])
    if smallest < -1e-10 * scale or (positive and smallest <= 0):
        raise NumericalError(
            f"covariance must be {'positive definite' if positive else 'PSD'}, minimum eigenvalue {smallest}"
        )


def _noise_parameters(dt: float, *densities: float) -> None:
    if not np.isfinite(dt) or dt < 0:
        raise ValueError(f"dt must be finite and nonnegative, got {dt}")
    if any(not np.isfinite(value) or value < 0 for value in densities):
        raise ValueError(f"spectral densities must be finite and nonnegative, got {densities}")


def cv_discretisation(dt: float, q_a: float) -> tuple[FloatArray, FloatArray]:
    """Exact CV transition and Q for acceleration density q_a [m²/s³]."""
    _noise_parameters(dt, q_a)
    identity = np.eye(3)
    transition = np.eye(6)
    transition[:3, 3:] = dt * identity
    covariance = q_a * np.block(
        [[dt**3 / 3 * identity, dt**2 / 2 * identity], [dt**2 / 2 * identity, dt * identity]]
    )
    return transition, covariance


def inertial_discretisation(
    dt: float, q_a: float, q_b: float
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Exact [p,v,b] model; q_a [m²/s³], bias diffusion q_b [m²/s⁵]."""
    _noise_parameters(dt, q_a, q_b)
    identity = np.eye(3)
    transition = np.eye(9)
    transition[:3, 3:6] = dt * identity
    transition[:3, 6:] = -(dt**2) / 2 * identity
    transition[3:6, 6:] = -dt * identity
    control = np.zeros((9, 3))
    control[:3] = dt**2 / 2 * identity
    control[3:6] = dt * identity
    covariance = np.block(
        [
            [
                (q_a * dt**3 / 3 + q_b * dt**5 / 20) * identity,
                (q_a * dt**2 / 2 + q_b * dt**4 / 8) * identity,
                -q_b * dt**3 / 6 * identity,
            ],
            [
                (q_a * dt**2 / 2 + q_b * dt**4 / 8) * identity,
                (q_a * dt + q_b * dt**3 / 3) * identity,
                -q_b * dt**2 / 2 * identity,
            ],
            [-q_b * dt**3 / 6 * identity, -q_b * dt**2 / 2 * identity, q_b * dt * identity],
        ]
    )
    return transition, covariance, control


def range_observation(
    state: FloatArray, anchor: FloatArray, offset: FloatArray
) -> tuple[FloatArray, FloatArray]:
    """Slant range to a sensor with its fixed world-frame mounting offset."""
    displacement = state[:3] + offset - anchor
    distance = float(np.linalg.norm(displacement))
    if distance <= 1e-12:
        raise GeometryError("range Jacobian is undefined at an anchor")
    jacobian = np.zeros((1, state.size))
    jacobian[0, :3] = displacement / distance
    return np.array([distance]), jacobian


def joseph_update(
    state: FloatArray,
    covariance: FloatArray,
    value: FloatArray,
    predicted: FloatArray,
    jacobian: FloatArray,
    measurement_covariance: FloatArray,
) -> tuple[FloatArray, FloatArray, float]:
    """Kalman update using solves and Joseph covariance; no eigenvalue clipping."""
    validate_covariance(covariance)
    validate_covariance(measurement_covariance, positive=True)
    residual = value - predicted
    innovation_covariance = jacobian @ covariance @ jacobian.T + measurement_covariance
    validate_covariance(innovation_covariance, positive=True)
    gain = np.linalg.solve(innovation_covariance, jacobian @ covariance).T
    nis = float(residual @ np.linalg.solve(innovation_covariance, residual))
    posterior_state = state + gain @ residual
    attenuation = np.eye(state.size) - gain @ jacobian
    posterior_covariance = (
        attenuation @ covariance @ attenuation.T + gain @ measurement_covariance @ gain.T
    )
    posterior_covariance = (posterior_covariance + posterior_covariance.T) / 2
    validate_covariance(posterior_covariance)
    if not np.isfinite(posterior_state).all() or not np.isfinite(nis):
        raise NumericalError("posterior state and NIS must be finite")
    return posterior_state, posterior_covariance, nis


@dataclass(frozen=True)
class WLSResult:
    position: FloatArray
    covariance: FloatArray
    sensor_ids: tuple[str, ...]
    times: FloatArray


def weighted_position(observations: list[Observation]) -> WLSResult:
    """Independent-range WLS, requiring four distinct sensors and 3D geometry."""
    if len(observations) < 4 or len({row.sensor_id for row in observations}) != len(observations):
        raise GeometryError("WLS geometry needs at least four distinct range sensors")
    effective_anchors = []
    for row in observations:
        if row.anchor is None:
            raise ValueError("WLS range observations must specify anchors")
        effective_anchors.append(row.anchor - row.offset)
    anchors = np.stack(effective_anchors)
    ranges = np.asarray([row.value[0] for row in observations])
    variance = np.asarray([row.covariance[0, 0] for row in observations])
    if not np.isfinite(anchors).all() or not np.isfinite(ranges).all():
        raise ValueError("WLS anchors and calibrated ranges must be finite")
    if not np.isfinite(variance).all() or np.any(variance <= 0):
        raise NumericalError("range covariance must have positive finite variance")
    geometry = anchors[1:] - anchors[0]
    singular_values = np.linalg.svd(geometry, compute_uv=False)
    if singular_values[-1] <= singular_values[0] * 1e-6:
        raise GeometryError("WLS geometry does not span three dimensions")
    target = (
        np.sum(anchors[1:] ** 2, axis=1)
        - np.sum(anchors[0] ** 2)
        - ranges[1:] ** 2
        + ranges[0] ** 2
    )
    initial = np.linalg.lstsq(2 * geometry, target, rcond=None)[0]
    standard_deviation = np.sqrt(variance)

    def residual(position: FloatArray) -> FloatArray:
        return (np.linalg.norm(position - anchors, axis=1) - ranges) / standard_deviation

    solution = least_squares(residual, initial, jac="3-point", ftol=1e-12, xtol=1e-12, gtol=1e-12)
    if not solution.success or not np.isfinite(solution.x).all():
        raise NumericalError(f"range WLS failed: {solution.message}")
    information = solution.jac.T @ solution.jac
    if np.linalg.matrix_rank(information, tol=np.linalg.norm(information, 2) * 1e-10) < 3:
        raise GeometryError("WLS local range geometry has rank below three")
    covariance = np.linalg.solve(information, np.eye(3))
    covariance = (covariance + covariance.T) / 2
    validate_covariance(covariance, positive=True)
    return WLSResult(
        solution.x,
        covariance,
        tuple(row.sensor_id for row in observations),
        np.asarray([row.t for row in observations]),
    )
