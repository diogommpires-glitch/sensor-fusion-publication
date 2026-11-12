"""Matched-model Monte Carlo checks with known states, separate from recorded data."""

import json
from pathlib import Path

import numpy as np
from scipy.stats import beta, chi2

from sensorfusion.models import cv_discretisation, inertial_discretisation, joseph_update
from sensorfusion.storage import timestamp, write_json

records = []
for dimension in (6, 9):
    if dimension == 6:
        transition, process_covariance = cv_discretisation(0.1, 0.02)
    else:
        transition, process_covariance, _ = inertial_discretisation(0.1, 0.02, 1e-4)
    observation = np.zeros((3, dimension))
    observation[:, :3] = np.eye(3)
    measurement_covariance = np.eye(3) * 0.04
    process_root = np.linalg.cholesky(process_covariance)
    nees, nis, coverage = [], [], []
    for seed in range(100):
        rng = np.random.default_rng(seed)
        truth = rng.normal(size=dimension)
        state = np.zeros(dimension)
        covariance = np.eye(dimension)
        for _ in range(50):
            truth = transition @ truth + process_root @ rng.normal(size=dimension)
            state = transition @ state
            covariance = transition @ covariance @ transition.T + process_covariance
            value = observation @ truth + 0.2 * rng.normal(size=3)
            state, covariance, last_nis = joseph_update(
                state, covariance, value, observation @ state, observation, measurement_covariance
            )
        difference = truth - state
        nees.append(float(difference @ np.linalg.solve(covariance, difference)))
        nis.append(last_nis)
        coverage.append(nees[-1] <= chi2.ppf(0.95, dimension))
    nees_bounds = chi2.ppf([0.005, 0.995], 100 * dimension) / 100
    nis_bounds = chi2.ppf([0.005, 0.995], 300) / 100
    successes = sum(coverage)
    records.append(
        {
            "state_dimension": dimension,
            "ensemble_size": 100,
            "seeds": [0, 99],
            "steps": 50,
            "dt_s": 0.1,
            "nees_mean": float(np.mean(nees)),
            "nees_99_interval": nees_bounds.tolist(),
            "nis_mean": float(np.mean(nis)),
            "nis_99_interval": nis_bounds.tolist(),
            "nominal_95_coverage": successes / 100,
            "coverage_binomial_95_interval": [
                float(beta.ppf(0.025, successes, 101 - successes)) if successes else 0,
                float(beta.ppf(0.975, successes + 1, 100 - successes)) if successes < 100 else 1,
            ],
            "consistency_check_passed": bool(
                nees_bounds[0] <= np.mean(nees) <= nees_bounds[1]
                and nis_bounds[0] <= np.mean(nis) <= nis_bounds[1]
            ),
        }
    )
result = {
    "created_at": timestamp(),
    "records": records,
    "passed": all(row["consistency_check_passed"] for row in records),
    "scope": "Independent final-time ensembles with a matched linear Gaussian model and known full states. Not a real-data uncertainty certificate.",
}
write_json(Path("evidence/consistency.json"), result)
print(json.dumps(result, indent=2))
raise SystemExit(not result["passed"])
