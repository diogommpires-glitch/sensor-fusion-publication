import numpy as np
import pytest
from scipy.linalg import expm

from sensorfusion.contracts import Observation
from sensorfusion.models import (
    GeometryError,
    NumericalError,
    cv_discretisation,
    inertial_discretisation,
    joseph_update,
    range_observation,
    weighted_position,
)


def test_cv_matches_integrated_white_acceleration():
    transition, covariance = cv_discretisation(2.0, 3.0)
    assert transition[0, 3] == 2.0
    np.testing.assert_allclose(covariance[np.ix_([0, 3], [0, 3])], [[8, 6], [6, 6]])


def test_inertial_covariance_matches_independent_van_loan():
    dt, acceleration_noise, bias_diffusion = 0.37, 0.2, 0.03
    continuous = np.zeros((9, 9))
    continuous[:3, 3:6] = np.eye(3)
    continuous[3:6, 6:] = -np.eye(3)
    noise = np.diag([0.0] * 3 + [acceleration_noise] * 3 + [bias_diffusion] * 3)
    block = np.block([[continuous, noise], [np.zeros((9, 9)), -continuous.T]])
    exponential = expm(block * dt)
    expected_transition = exponential[:9, :9]
    expected_covariance = exponential[:9, 9:] @ expected_transition.T
    transition, covariance, control = inertial_discretisation(
        dt, acceleration_noise, bias_diffusion
    )
    np.testing.assert_allclose(transition, expected_transition, atol=1e-14)
    np.testing.assert_allclose(covariance, expected_covariance, atol=1e-14)
    np.testing.assert_allclose(control[:3], np.eye(3) * dt**2 / 2)
    assert covariance[0, 6] < 0


def test_true_3d_range_jacobian_matches_finite_difference():
    state = np.array([1.0, 2.0, 3.0, 0.1, 0.2, 0.3, 0.0, 0.0, 0.0])
    anchor = np.array([4.0, -1.0, 0.2])
    offset = np.array([0.3, -0.2, 0.5])
    predicted, jacobian = range_observation(state, anchor, offset)
    assert predicted[0] == pytest.approx(np.linalg.norm(state[:3] + offset - anchor))
    finite_difference = np.zeros(state.size)
    for axis in range(state.size):
        perturbation = np.zeros(state.size)
        perturbation[axis] = 1e-6
        plus, _ = range_observation(state + perturbation, anchor, offset)
        minus, _ = range_observation(state - perturbation, anchor, offset)
        finite_difference[axis] = (plus[0] - minus[0]) / 2e-6
    np.testing.assert_allclose(jacobian[0], finite_difference, atol=1e-8)


def test_joseph_update_matches_hand_computed_kalman_posterior():
    state, covariance, nis = joseph_update(
        np.array([0.0, 0.0]),
        np.array([[2.0, 1.0], [1.0, 3.0]]),
        np.array([3.0]),
        np.array([0.0]),
        np.array([[1.0, 0.0]]),
        np.array([[1.0]]),
    )
    np.testing.assert_allclose(state, [2.0, 1.0])
    np.testing.assert_allclose(covariance, [[2 / 3, 1 / 3], [1 / 3, 8 / 3]])
    assert nis == 3.0


def test_numerical_failure_is_explicit_without_clipping():
    with pytest.raises(NumericalError, match="covariance"):
        joseph_update(
            np.zeros(2),
            np.diag([-1.0, 1.0]),
            np.zeros(1),
            np.zeros(1),
            np.array([[1.0, 0.0]]),
            np.eye(1),
        )


def test_wls_solves_sensor_offsets_and_rejects_degenerate_geometry():
    anchors = np.array([[0, 0, 0], [4, 0, 0], [0, 4, 0], [0, 0, 4]], dtype=float)
    position = np.array([0.8, 1.1, 1.3])
    offset = np.array([0.2, -0.1, 0.3])
    observations = [
        Observation(
            0,
            0,
            "uwb",
            str(i),
            np.array([np.linalg.norm(position + offset - a)]),
            np.array([[0.01]]),
            anchor=a,
            offset=offset,
        )
        for i, a in enumerate(anchors)
    ]
    solution = weighted_position(observations)
    np.testing.assert_allclose(solution.position, position, atol=1e-8)
    assert np.linalg.eigvalsh(solution.covariance).min() > 0
    for observation in observations:
        assert observation.anchor is not None
        observation.anchor[2] = 0
    with pytest.raises(GeometryError, match="geometry"):
        weighted_position(observations)
