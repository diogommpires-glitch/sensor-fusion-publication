"""Known-state consistency ensembles, separate from recorded-data evaluation."""

from typing import Any

import numpy as np
from scipy.linalg import expm
from scipy.stats import beta, chi2

from sensorfusion.models import (
    cv_discretisation,
    inertial_discretisation,
    joseph_update,
    validate_covariance,
)


def held_error_discretisation(
    dimension: int, dt: float, q_a: float, q_b: float
) -> tuple[np.ndarray, np.ndarray]:
    """Discretise [p,v,(b),e], with held acceleration error e [m/s²]."""
    if dimension not in (6, 9):
        raise ValueError(f"base dimension must be 6 or 9, got {dimension}")
    if not np.isfinite([dt, q_a, q_b]).all() or min(dt, q_a, q_b) < 0:
        raise ValueError("interval and spectral densities must be finite and nonnegative")
    size = dimension + 3
    dynamics = np.zeros((size, size))
    dynamics[:3, 3:6] = np.eye(3)
    dynamics[3:6, dimension:] = np.eye(3)
    density = np.zeros((size, size))
    density[3:6, 3:6] = q_a * np.eye(3)
    if dimension == 9:
        dynamics[3:6, 6:9] = -np.eye(3)
        density[6:9, 6:9] = q_b * np.eye(3)
    block = np.block([[dynamics, density], [np.zeros_like(dynamics), -dynamics.T]])
    exponential = expm(block * dt)
    transition = exponential[:size, :size]
    covariance = exponential[:size, size:] @ transition.T
    covariance = (covariance + covariance.T) / 2
    validate_covariance(covariance)
    return transition, covariance


def _base_model(dimension: int, dt: float, q_a: float, q_b: float) -> tuple:
    if dimension == 6:
        return cv_discretisation(dt, q_a)
    transition, covariance, _ = inertial_discretisation(dt, q_a, q_b)
    return transition, covariance


def _draws(ensemble_size: int, dimension: int, steps: int, family: int) -> tuple:
    initial, process, measurement, held = [], [], [], []
    for seed in range(ensemble_size):
        streams = np.random.SeedSequence([seed, dimension, family]).spawn(4)
        initial.append(np.random.default_rng(streams[0]).normal(size=dimension))
        process.append(np.random.default_rng(streams[1]).normal(size=(steps, dimension)))
        measurement.append(np.random.default_rng(streams[2]).normal(size=(steps, 3)))
        held.append(np.random.default_rng(streams[3]).normal(size=(steps, 3)))
    return tuple(np.asarray(values) for values in (initial, process, measurement, held))


def _update(
    states: np.ndarray, covariance: np.ndarray, values: np.ndarray, variance: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    size = covariance.shape[0]
    observation = np.zeros((3, size))
    observation[:, :3] = np.eye(3)
    measurement_covariance = variance * np.eye(3)
    innovations = values - states[:, :3]
    innovation_covariance = covariance[:3, :3] + measurement_covariance
    gain = np.linalg.solve(innovation_covariance, observation @ covariance).T
    nis = np.einsum("ij,ji->i", innovations, np.linalg.solve(innovation_covariance, innovations.T))
    # The shared covariance is deterministic for a linear model; use the engine's
    # Joseph update once per epoch, rather than repeat it for every independent trial.
    _, posterior_covariance, _ = joseph_update(
        np.zeros(size),
        covariance,
        np.zeros(3),
        np.zeros(3),
        observation,
        measurement_covariance,
    )
    return states + innovations @ gain.T, posterior_covariance, nis


def _summary(values: np.ndarray, degrees: int) -> dict[str, Any]:
    count = len(values)
    interval = chi2.ppf([0.005, 0.995], count * degrees) / count
    mean = float(np.mean(values))
    successes = int(np.sum(values <= chi2.ppf(0.95, degrees)))
    coverage_interval = [
        float(beta.ppf(0.025, successes, count - successes + 1)) if successes else 0.0,
        float(beta.ppf(0.975, successes + 1, count - successes)) if successes < count else 1.0,
    ]
    return {
        "degrees_of_freedom": degrees,
        "mean": mean,
        "mean_99_interval": interval.tolist(),
        "inside_mean_99_interval": bool(interval[0] <= mean <= interval[1]),
        "nominal_95_coverage": successes / count,
        "coverage_binomial_95_interval": coverage_interval,
    }


def _endpoint(
    case_id: str,
    truth: np.ndarray,
    states: np.ndarray,
    covariance: np.ndarray,
    nis: np.ndarray,
    base_dimension: int,
) -> dict[str, Any]:
    difference = truth - states
    base_difference = difference[:, :base_dimension]
    marginal_covariance = covariance[:base_dimension, :base_dimension]
    nees = np.einsum(
        "ij,ji->i", base_difference, np.linalg.solve(marginal_covariance, base_difference.T)
    )
    full_nees = np.einsum("ij,ji->i", difference, np.linalg.solve(covariance, difference.T))
    nees_summary = _summary(nees, base_dimension)
    nis_summary = _summary(nis, 3)
    return {
        "case_id": case_id,
        "base_dimension": base_dimension,
        "filter_dimension": states.shape[1],
        "marginal_nees": nees_summary,
        "full_nees": _summary(full_nees, states.shape[1]),
        "pre_update_nis": nis_summary,
        "consistency_99_compatible": bool(
            nees_summary["inside_mean_99_interval"] and nis_summary["inside_mean_99_interval"]
        ),
        "trials": [
            {"seed": seed, "marginal_nees": float(n), "full_nees": float(f), "nis": float(z)}
            for seed, (n, f, z) in enumerate(zip(nees, full_nees, nis, strict=True))
        ],
    }


def _gaussian_cases(dimension: int, ensemble_size: int, steps: int, dt: float) -> list[dict]:
    transition, process_covariance = _base_model(dimension, dt, 0.02, 1e-4)
    process_root = np.linalg.cholesky(process_covariance)
    initial, process, measurement, _ = _draws(ensemble_size, dimension, steps, 0)
    records = []
    for scale, label in ((1.0, "matched"), (0.1, "underestimated_R")):
        truth = initial.copy()
        states = np.zeros_like(truth)
        covariance = np.eye(dimension)
        for epoch in range(steps):
            truth = truth @ transition.T + process[:, epoch] @ process_root.T
            states = states @ transition.T
            covariance = transition @ covariance @ transition.T + process_covariance
            values = truth[:, :3] + 0.2 * measurement[:, epoch]
            states, covariance, nis = _update(states, covariance, values, 0.04 * scale)
        records.append(_endpoint(f"{dimension}-{label}", truth, states, covariance, nis, dimension))
    return records


def _held_cases(dimension: int, ensemble_size: int, steps: int, dt: float) -> list[dict]:
    hold_steps, sigma_error = 5, 0.5
    transition, process_covariance = held_error_discretisation(dimension, dt, 0.02, 1e-4)
    process_root = np.linalg.cholesky(process_covariance[:dimension, :dimension])
    initial, process, measurement, held = _draws(ensemble_size, dimension, steps, 1)
    truth = np.column_stack([initial, sigma_error * held[:, 0]])
    augmented = np.zeros_like(truth)
    augmented_covariance = np.diag([1.0] * dimension + [sigma_error**2] * 3)
    approximation = np.zeros_like(initial)
    approximation_covariance = np.eye(dimension)
    effective_density = 0.02 + sigma_error**2 * hold_steps * dt
    white_transition, white_covariance = _base_model(dimension, dt, effective_density, 1e-4)
    reset = np.diag([1.0] * dimension + [0.0] * 3)
    reset_covariance = np.diag([0.0] * dimension + [sigma_error**2] * 3)
    for epoch in range(steps):
        if epoch and epoch % hold_steps == 0:
            truth[:, dimension:] = sigma_error * held[:, epoch]
            augmented[:, dimension:] = 0
            augmented_covariance = reset @ augmented_covariance @ reset.T + reset_covariance
        truth = truth @ transition.T
        truth[:, :dimension] += process[:, epoch] @ process_root.T
        values = truth[:, :3] + 0.2 * measurement[:, epoch]
        augmented = augmented @ transition.T
        augmented_covariance = transition @ augmented_covariance @ transition.T + process_covariance
        augmented, augmented_covariance, augmented_nis = _update(
            augmented, augmented_covariance, values, 0.04
        )
        approximation = approximation @ white_transition.T
        approximation_covariance = (
            white_transition @ approximation_covariance @ white_transition.T + white_covariance
        )
        approximation, approximation_covariance, approximation_nis = _update(
            approximation, approximation_covariance, values, 0.04
        )
    return [
        _endpoint(
            f"{dimension}-held_white_approximation",
            truth[:, :dimension],
            approximation,
            approximation_covariance,
            approximation_nis,
            dimension,
        ),
        _endpoint(
            f"{dimension}-held_augmented",
            truth,
            augmented,
            augmented_covariance,
            augmented_nis,
            dimension,
        ),
    ]


def run_consistency(ensemble_size: int = 1000, steps: int = 50, dt: float = 0.1) -> dict:
    """Evaluate independent final-time trials; never certify real-data uncertainty."""
    if type(ensemble_size) is not int or ensemble_size < 2:
        raise ValueError(f"ensemble size must be an integer >=2, got {ensemble_size}")
    if type(steps) is not int or steps < 1:
        raise ValueError(f"steps must be a positive integer, got {steps}")
    if not np.isfinite(dt) or dt <= 0:
        raise ValueError(f"dt must be finite and positive, got {dt}")
    records = []
    for dimension in (6, 9):
        records.extend(_gaussian_cases(dimension, ensemble_size, steps, dt))
        records.extend(_held_cases(dimension, ensemble_size, steps, dt))
    return {
        "schema_version": 2,
        "synthetic": True,
        "scope": "Known-state simulation only. No recorded-data or hardware consistency claim.",
        "protocol": {
            "ensemble_size": ensemble_size,
            "seeds": [0, ensemble_size - 1],
            "seed_construction": "SeedSequence([seed, base_dimension, family]).spawn(4): independent initial, process, measurement and held-error streams; shared draws within paired cases",
            "steps": steps,
            "dt_s": dt,
            "evaluation_epoch_s": steps * dt,
            "q_a_m2_s3": 0.02,
            "q_b_m2_s5": 1e-4,
            "true_measurement_variance_m2": 0.04,
            "underestimated_measurement_variance_m2": 0.004,
            "held_error_std_m_s2": 0.5,
            "hold_steps": 5,
            "held_white_approximation_q_a_m2_s3": 0.02 + 0.25 * 5 * dt,
            "ensemble_test": "Two-sided 99% chi-square mean bounds, evaluated once at the final epoch; no gating.",
            "coverage_interval": "Exact two-sided95% Clopper-Pearson binomial interval across independent trials.",
        },
        "cases": records,
        "interpretation": "Compatibility is not proof of the model. Failed or undetected mismatch cases remain reported without retuning.",
    }
