import json

import numpy as np
import pytest
from scipy.integrate import quad_vec
from scipy.linalg import expm

from scripts.consistency_v2 import export_consistency
from sensorfusion.consistency import _draws, _update, held_error_discretisation, run_consistency


@pytest.mark.parametrize("dimension", [6, 9])
def test_augmented_transition_and_covariance_match_independent_integral(dimension):
    dt, qa, qb = 0.37, 0.2, 0.003
    size = dimension + 3
    dynamics = np.zeros((size, size))
    dynamics[:3, 3:6] = np.eye(3)
    dynamics[3:6, dimension:] = np.eye(3)
    density = np.zeros((size, size))
    density[3:6, 3:6] = qa * np.eye(3)
    if dimension == 9:
        dynamics[3:6, 6:9] = -np.eye(3)
        density[6:9, 6:9] = qb * np.eye(3)
    expected_q, _ = quad_vec(
        lambda time: expm(dynamics * time) @ density @ expm(dynamics.T * time), 0, dt
    )
    transition, covariance = held_error_discretisation(dimension, dt, qa, qb)
    np.testing.assert_allclose(transition[:3, dimension:], dt**2 / 2 * np.eye(3), atol=1e-15)
    np.testing.assert_allclose(transition[3:6, dimension:], dt * np.eye(3), atol=1e-15)
    np.testing.assert_allclose(covariance, expected_q, rtol=1e-12, atol=1e-15)
    np.testing.assert_array_equal(covariance[dimension:, :], np.zeros((3, size)))


def test_held_error_keeps_cross_epoch_correlation_instead_of_reinjecting():
    transition, covariance = held_error_discretisation(6, 0.1, 0, 0)
    prior = np.diag([0.0] * 6 + [0.25] * 3)
    twice = transition @ (transition @ prior @ transition.T) @ transition.T
    expected = 0.25 * (0.2**2 / 2) ** 2
    assert twice[0, 0] == pytest.approx(expected)
    base_transition = transition[:6, :6]
    control = transition[:6, 6:]
    sample_covariance = control @ (0.25 * np.eye(3)) @ control.T
    independent = base_transition @ sample_covariance @ base_transition.T + sample_covariance
    assert independent[0, 0] == pytest.approx(0.0000625)
    assert twice[0, 0] > independent[0, 0]
    assert np.count_nonzero(covariance) == 0


def test_batched_update_matches_hand_calculated_linear_posterior_and_pre_update_nis():
    values = np.array([[1.0, 2.0, 3.0], [-1.0, 0.0, 1.0]])
    states, covariance, nis = _update(np.zeros((2, 6)), np.eye(6), values, 0.04)
    np.testing.assert_allclose(states[:, :3], 25 / 26 * values)
    np.testing.assert_array_equal(states[:, 3:], np.zeros((2, 3)))
    np.testing.assert_allclose(np.diag(covariance), [1 / 26] * 3 + [1.0] * 3)
    np.testing.assert_allclose(nis, np.array([14.0, 2.0]) / 1.04)


def test_seeded_endpoint_trials_and_actual_degrees_of_freedom():
    first = run_consistency(32, 10)
    second = run_consistency(32, 10)
    assert first == second
    assert first["synthetic"] and len(first["cases"]) == 8
    for case in first["cases"]:
        assert len(case["trials"]) == 32
        assert case["marginal_nees"]["degrees_of_freedom"] == case["base_dimension"]
        assert case["full_nees"]["degrees_of_freedom"] == case["filter_dimension"]
        assert case["pre_update_nis"]["degrees_of_freedom"] == 3
        assert all(row["nis"] >= 0 and np.isfinite(row["full_nees"]) for row in case["trials"])
    assert first["protocol"]["evaluation_epoch_s"] == 1


def test_extending_future_noise_draws_preserves_every_earlier_input_stream():
    short = _draws(3, 9, 10, 1)
    longer = _draws(3, 9, 20, 1)
    np.testing.assert_array_equal(short[0], longer[0])
    for prefix, extended in zip(short[1:], longer[1:], strict=True):
        np.testing.assert_array_equal(prefix, extended[:, :10])


def test_exclusive_export_preserves_existing_files_and_labels_simulation(tmp_path):
    destination = tmp_path / "new-study"
    export_consistency(destination, 8)
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["synthetic"] and manifest["status"] == "completed"
    before = {p.name: p.read_bytes() for p in destination.iterdir()}
    with pytest.raises(FileExistsError, match="already exists"):
        export_consistency(destination, 8)
    assert {p.name: p.read_bytes() for p in destination.iterdir()} == before


@pytest.mark.parametrize("ensemble,steps,dt", [(1, 50, 0.1), (2, 0, 0.1), (2, 50, np.nan)])
def test_ensemble_boundary_validation(ensemble, steps, dt):
    with pytest.raises(ValueError):
        run_consistency(ensemble, steps, dt)
