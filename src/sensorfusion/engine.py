"""Causal eight-method estimators that have no reference-data argument."""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import numpy as np
from scipy.stats import chi2

from sensorfusion.contracts import Estimate, FloatArray, Observation, RunConfig
from sensorfusion.models import (
    GeometryError,
    WLSResult,
    cv_discretisation,
    inertial_discretisation,
    joseph_update,
    range_observation,
    validate_covariance,
    weighted_position,
)
from sensorfusion.radar import associate_centroid, cluster_frame

_INERTIAL = {"M4", "M5", "M6", "M7"}
_FTM = {"M3", "M5", "M6", "M7", "M8"}
_RADAR = {"M6", "M7", "M8"}
_GATED = {"M7", "M8"}


class CancelledError(RuntimeError):
    """Cooperative cancellation leaves a run incomplete."""


def _validate_observation(row: Observation) -> None:
    dimension = 1 if row.modality in ("uwb", "ftm") else 3
    if row.modality not in ("uwb", "ftm", "imu", "radar"):
        raise ValueError(f"unsupported modality {row.modality!r}")
    point_cloud = row.modality == "radar" and row.value.ndim == 2 and row.value.shape[1] == 3
    if (row.value.shape != (dimension,) and not point_cloud) or row.covariance.shape != (
        dimension,
        dimension,
    ):
        raise ValueError(
            f"{row.modality} value/covariance shape must be ({dimension},)/({dimension},{dimension})"
        )
    if not np.isfinite(row.value).all():
        raise ValueError(f"{row.sensor_id} observation value must be finite")
    if not np.isfinite(row.t) or not np.isfinite(row.arrival) or row.arrival < row.t - 1e-9:
        raise ValueError(
            f"acquisition/arrival must be finite with arrival>=acquisition, got {row.t}, {row.arrival}"
        )
    if row.frame != "world":
        raise ValueError(f"estimator requires world-frame observations, got {row.frame!r}")
    if row.offset.shape != (3,) or not np.isfinite(row.offset).all():
        raise ValueError("sensor mounting offset must have finite shape (3,)")
    if row.modality in ("uwb", "ftm") and (
        row.anchor is None or row.anchor.shape != (3,) or not np.isfinite(row.anchor).all()
    ):
        raise ValueError("range anchor must have finite shape (3,)")
    validate_covariance(row.covariance, positive=True)


def _selected(row: Observation, config: RunConfig) -> bool:
    if config.factors is not None:
        if row.modality in ("uwb", "ftm"):
            return True
        if row.modality == "imu":
            return config.factors.propagation == "inertial"
        return config.factors.radar and row.sensor_id == "AOP"
    if row.modality == "uwb":
        return True
    if row.modality == "ftm":
        return config.method in _FTM
    if row.modality == "imu":
        return config.method in _INERTIAL
    return config.method in _RADAR and row.sensor_id == config.radar_sensor


def _window(latest: dict[str, Observation], time: float, width: float) -> list[Observation]:
    return [row for row in latest.values() if -1e-9 <= time - row.t <= width + 1e-9]


def _predict(
    state: FloatArray,
    covariance: FloatArray,
    dt: float,
    acceleration: FloatArray,
    config: RunConfig,
) -> tuple[FloatArray, FloatArray]:
    inertial = (
        config.factors.propagation == "inertial"
        if config.factors is not None
        else config.method in _INERTIAL
    )
    if inertial:
        transition, process_covariance, control = inertial_discretisation(
            dt, config.acceleration_noise, config.bias_diffusion
        )
        predicted_state = transition @ state + control @ acceleration
    else:
        transition, process_covariance = cv_discretisation(dt, config.q_cv)
        predicted_state = transition @ state
    predicted_covariance = transition @ covariance @ transition.T + process_covariance
    predicted_covariance = (predicted_covariance + predicted_covariance.T) / 2
    validate_covariance(predicted_covariance)
    return predicted_state, predicted_covariance


def _measurement(state: FloatArray, row: Observation) -> tuple[FloatArray, FloatArray]:
    if row.modality == "radar":
        jacobian = np.zeros((3, state.size))
        jacobian[:, :3] = np.eye(3)
        return state[:3], jacobian
    assert row.anchor is not None
    return range_observation(state, row.anchor, row.offset)


def _innovation(row: Observation, reason: str) -> dict[str, Any]:
    dimension = 1 if row.modality in ("uwb", "ftm") else 3
    point_cloud = row.modality == "radar" and row.value.ndim == 2
    return {
        "time": row.t,
        "arrival": row.arrival,
        "modality": row.modality,
        "sensor_id": row.sensor_id,
        "source_row": row.source_row,
        "dimension": dimension,
        "value": [None] * 3 if point_cloud else row.value.tolist(),
        "predicted": [None] * dimension,
        "residual": [None] * dimension,
        "nis": None,
        "threshold": None,
        "accepted": False,
        "reason": reason,
    }


def _initial_state(solution: WLSResult, dimension: int) -> tuple[FloatArray, FloatArray]:
    state = np.zeros(dimension)
    state[:3] = solution.position
    covariance = np.eye(dimension)
    covariance[:3, :3] = solution.covariance
    if dimension == 9:
        covariance[6:, 6:] = np.eye(3) * 0.1
    return state, covariance


def estimate(
    observations: list[Observation],
    config: RunConfig,
    *,
    progress: Callable[[float, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Estimate:
    """Process arrival order and emit causal predictions on a first-arrival grid."""
    inertial = (
        config.factors.propagation == "inertial"
        if config.factors is not None
        else config.method in _INERTIAL
    )
    gated = config.factors.gating if config.factors is not None else config.method in _GATED
    dimension = 3 if config.method == "M1" else (9 if inertial else 6)
    metadata: dict[str, Any] = {
        "method": config.method,
        "initialisation": "uwb_wls",
        "initialised_at": None,
        "automatic_resets": 0,
        "unavailable_outputs": 0,
        "processing_order": "arrival",
        "grid_origin": "first_arrival",
        "state_dimension": dimension,
        "initial_velocity_variance_m2_s2": 1.0,
        "initial_bias_variance_m2_s4": 0.1 if dimension == 9 else None,
        "imu_before_first_sample": "zero_control_with_process_noise",
        "imu_noise_model": "resolved_continuous_density; sample covariance retained at input boundary",
        "wls_observation_ages_s": [],
        "radar_associations": [],
        "imu_controls": [],
        "radar_clustering": {
            "radius_m": config.radar_cluster_radius_m,
            "min_samples": config.radar_min_samples,
        },
    }
    if config.factors is not None:
        metadata["factors"] = config.factors.model_dump()
    if cancelled is not None and cancelled():
        raise CancelledError("estimation cancelled")
    if not observations:
        return Estimate(
            np.empty(0), np.empty((0, dimension)), np.empty((0, dimension, dimension)), [], metadata
        )
    for index, row in enumerate(observations):
        if index % 1000 == 0 and cancelled is not None and cancelled():
            raise CancelledError("estimation cancelled")
        _validate_observation(row)
    ordered = sorted(observations, key=lambda row: row.arrival)
    start, end = ordered[0].arrival, ordered[-1].arrival
    count = int(np.floor((end - start) * config.output_hz + 1e-8)) + 1
    times = start + np.arange(count) / config.output_hz
    states = np.full((count, dimension), np.nan)
    covariances = np.full((count, dimension, dimension), np.nan)
    innovations: list[dict[str, Any]] = []
    latest: dict[str, Observation] = {}
    state: FloatArray | None = None
    covariance: FloatArray | None = None
    epoch = start
    watermark = -np.inf
    acceleration = np.zeros(3)
    event_index = 0
    wls_cache: tuple[tuple[str, float], ...] | None = None
    wls_solution: WLSResult | None = None

    for output_index, time in enumerate(np.append(times, end)):
        if cancelled is not None and cancelled():
            raise CancelledError("estimation cancelled")
        # The final extra epoch processes diagnostics after the last regular grid point.
        while event_index < len(ordered) and ordered[event_index].arrival <= time + 1e-9:
            row = ordered[event_index]
            event_index += 1
            if cancelled is not None and cancelled():
                raise CancelledError("estimation cancelled")
            if not _selected(row, config):
                continue
            diagnostic = _innovation(row, "initialisation_geometry")
            if row.t < watermark - 1e-9:
                diagnostic["reason"] = "stale"
                if row.modality == "imu":
                    metadata["imu_controls"].append(
                        {"time": row.t, "arrival": row.arrival, "applied": False, "reason": "stale"}
                    )
                else:
                    innovations.append(diagnostic)
                continue
            watermark = max(watermark, row.t)
            if state is not None and config.method != "M1":
                assert covariance is not None
                state, covariance = _predict(
                    state, covariance, max(0.0, row.t - epoch), acceleration, config
                )
                epoch = row.t
            if row.modality == "imu":
                acceleration = row.value.copy()
                metadata["imu_controls"].append(
                    {
                        "time": row.t,
                        "arrival": row.arrival,
                        "applied": True,
                        "reason": "held_control",
                    }
                )
                continue
            if row.modality == "radar" and row.value.ndim == 2:
                clusters = cluster_frame(
                    row.value,
                    radius=config.radar_cluster_radius_m,
                    min_samples=config.radar_min_samples,
                )
                association_record: dict[str, Any] = {
                    "time": row.t,
                    "arrival": row.arrival,
                    "sensor_id": row.sensor_id,
                    "cluster_count": len(clusters),
                    "point_count": row.value.shape[0],
                    "clusters": [cluster.centroid.tolist() for cluster in clusters],
                    "candidate_count": 0,
                    "centroid": None,
                    "nis": None,
                }
                if state is None:
                    association_record["reason"] = "uninitialised"
                    diagnostic["reason"] = "radar_uninitialised"
                    metadata["radar_associations"].append(association_record)
                    innovations.append(diagnostic)
                    continue
                assert covariance is not None
                association = associate_centroid(
                    clusters,
                    state[:3],
                    covariance[:3, :3],
                    row.covariance,
                    gate_probability=config.gate_probability,
                )
                association_record.update(
                    reason=association.reason,
                    candidate_count=association.candidate_count,
                    centroid=None
                    if association.centroid is None
                    else association.centroid.tolist(),
                    nis=association.nis,
                )
                metadata["radar_associations"].append(association_record)
                if not association.accepted:
                    diagnostic.update(
                        reason=f"radar_{association.reason}", predicted=state[:3].tolist()
                    )
                    innovations.append(diagnostic)
                    continue
                assert association.centroid is not None
                row = replace(row, value=association.centroid)
                diagnostic["value"] = row.value.tolist()
            if row.modality == "uwb":
                latest[row.sensor_id] = row
            if state is None or config.method == "M1":
                available = _window(latest, row.t, config.wls_window_s)
                try:
                    solution = weighted_position(available)
                except GeometryError:
                    innovations.append(diagnostic)
                    continue
                diagnostic["accepted"] = True
                diagnostic["reason"] = "initialisation" if state is None else "wls_input"
                if state is None:
                    metadata["initialised_at"] = row.t
                    metadata["initialisation_sensor_ids"] = list(solution.sensor_ids)
                    metadata["initialisation_ages_s"] = (row.t - solution.times).tolist()
                state, covariance = _initial_state(solution, dimension)
                epoch = row.t
                innovations.append(diagnostic)
                continue
            assert covariance is not None
            try:
                predicted, jacobian = _measurement(state, row)
            except GeometryError:
                diagnostic["reason"] = "undefined_range"
                innovations.append(diagnostic)
                continue
            residual = row.value - predicted
            innovation_covariance = jacobian @ covariance @ jacobian.T + row.covariance
            validate_covariance(innovation_covariance, positive=True)
            nis = float(residual @ np.linalg.solve(innovation_covariance, residual))
            threshold = float(chi2.ppf(config.gate_probability, row.value.size))
            diagnostic.update(
                predicted=predicted.tolist(),
                residual=residual.tolist(),
                nis=nis,
                threshold=threshold,
                accepted=True,
                reason="accepted",
            )
            if gated and nis > threshold:
                diagnostic.update(accepted=False, reason="gate")
            else:
                state, covariance, _ = joseph_update(
                    state, covariance, row.value, predicted, jacobian, row.covariance
                )
            innovations.append(diagnostic)

        if output_index == count:
            break
        if config.method == "M1":
            available = _window(latest, time, config.wls_window_s)
            key = tuple((row.sensor_id, row.t) for row in available)
            if key != wls_cache:
                try:
                    wls_solution = weighted_position(available)
                except GeometryError:
                    wls_solution = None
                wls_cache = key
            if wls_solution is not None:
                states[output_index] = wls_solution.position
                covariances[output_index] = wls_solution.covariance
                metadata["wls_observation_ages_s"].append((time - wls_solution.times).tolist())
            else:
                metadata["wls_observation_ages_s"].append([])
        elif state is not None:
            assert covariance is not None
            states[output_index], covariances[output_index] = _predict(
                state, covariance, max(0.0, time - epoch), acceleration, config
            )
        if not np.isfinite(states[output_index]).all():
            metadata["unavailable_outputs"] += 1
        if progress is not None and (output_index % 20 == 0 or output_index == count - 1):
            progress(
                (output_index + 1) / count, f"Estimated {output_index + 1}/{count} causal outputs"
            )
    metadata["processed_observations"] = event_index
    metadata["pending_after_last_grid"] = len(ordered) - event_index
    return Estimate(times, states, covariances, innovations, metadata)
