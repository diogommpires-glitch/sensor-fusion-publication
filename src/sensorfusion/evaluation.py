"""Position-only reference comparisons with explicit gaps and availability."""

import numpy as np
from scipy.stats import chi2

from sensorfusion.contracts import Estimate

REFERENCE_MAX_GAP_S = 0.105


def align_reference(
    times: np.ndarray,
    reference_times: np.ndarray,
    reference_positions: np.ndarray,
    max_gap_s: float = REFERENCE_MAX_GAP_S,
) -> np.ndarray:
    aligned = np.full((len(times), 3), np.nan)
    if len(reference_times) < 2:
        return aligned
    if np.any(np.diff(reference_times) <= 0):
        raise ValueError("reference timestamps must be strictly increasing")
    indices = np.searchsorted(reference_times, times, side="right")
    indices = np.clip(indices, 1, len(reference_times) - 1)
    valid = (
        (times >= reference_times[0])
        & (times <= reference_times[-1])
        & ((reference_times[indices] - reference_times[indices - 1]) <= max_gap_s)
    )
    for axis in range(3):
        aligned[valid, axis] = np.interp(
            times[valid], reference_times, reference_positions[:, axis]
        )
    return aligned


def score(
    estimate: Estimate,
    reference_times: np.ndarray,
    reference_positions: np.ndarray,
) -> tuple[dict, np.ndarray, np.ndarray]:
    reference = align_reference(estimate.times, reference_times, reference_positions)
    reference_valid = np.all(np.isfinite(reference), axis=1)
    estimate_valid = np.all(np.isfinite(estimate.states[:, :3]), axis=1)
    valid = reference_valid & estimate_valid
    errors = np.full(len(estimate.times), np.nan)
    errors[valid] = np.linalg.norm(estimate.states[valid, :3] - reference[valid], axis=1)
    coverage = []
    for i in np.flatnonzero(valid):
        covariance = estimate.covariances[i, :3, :3]
        if not np.all(np.isfinite(covariance)):
            continue
        try:
            difference = estimate.states[i, :3] - reference[i]
            normalized = float(difference @ np.linalg.solve(covariance, difference))
        except np.linalg.LinAlgError:
            continue
        coverage.append(normalized <= chi2.ppf(0.95, 3))
    errors_valid = errors[valid]
    accepted = sum(bool(item.get("accepted")) for item in estimate.innovations)
    metrics = {
        "position_rmse_m": float(np.sqrt(np.mean(errors_valid**2))) if len(errors_valid) else None,
        "median_error_m": float(np.median(errors_valid)) if len(errors_valid) else None,
        "p95_error_m": float(np.quantile(errors_valid, 0.95)) if len(errors_valid) else None,
        "availability": float(np.sum(valid) / np.sum(reference_valid))
        if reference_valid.any()
        else 0.0,
        "reference_coverage": float(np.mean(reference_valid)) if len(reference_valid) else 0.0,
        "position_coverage_95": float(np.mean(coverage)) if coverage else None,
        "accepted_updates": accepted,
        "rejected_updates": len(estimate.innovations) - accepted,
        "scored_samples": int(np.sum(valid)),
        "total_samples": len(estimate.times),
        "notes": [
            "Position-only reference; no full-state NEES.",
            "Nominal ellipsoid coverage is a diagnostic, not an independent-sample consistency test.",
        ],
    }
    return metrics, reference, errors
