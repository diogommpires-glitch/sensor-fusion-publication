"""Labelled observation-level interventions, separate from recorded measurements."""

from dataclasses import replace

import numpy as np

from sensorfusion.contracts import FaultConfig, Observation


def inject_fault(
    observations: list[Observation], config: FaultConfig
) -> tuple[list[Observation], dict]:
    if config.kind == "none":
        return list(observations), {"kind": "none", "injected": False, "affected": 0}
    if not observations:
        raise ValueError("cannot inject a fault into an empty observation stream")
    if config.level <= 0:
        raise ValueError(f"fault level must be positive, got {config.level}")
    first = min(o.arrival for o in observations)
    last = max(o.arrival for o in observations)
    duration = config.level if config.kind == "dropout" else config.duration_s
    eligible_start, eligible_end = first + 2, last - duration - 1
    if eligible_end < eligible_start:
        return list(observations), {
            "kind": config.kind,
            "injected": False,
            "skipped": True,
            "reason": "session too short for requested episode and safety margins",
            "affected": 0,
        }
    rng = np.random.default_rng(config.seed)
    start = (
        first + config.start_s
        if config.start_s is not None
        else float(rng.uniform(eligible_start, eligible_end))
    )
    if start < first or start + duration > last:
        raise ValueError("fault interval must lie entirely inside the recorded session")
    range_ids = sorted({o.sensor_id for o in observations if o.modality == "uwb"})
    selected = config.sensor_id or (range_ids[0] if range_ids else "")
    altered, affected = [], 0
    for observation in observations:
        if not start <= observation.t < start + duration:
            altered.append(observation)
            continue
        value = observation.value.copy()
        if config.kind == "dropout" and observation.modality in ("uwb", "ftm", "radar"):
            affected += 1
            continue
        if (
            config.kind == "range_bias"
            and observation.modality == "uwb"
            and observation.sensor_id == selected
        ):
            value[0] += config.level
        elif config.kind == "imu_bias" and observation.modality == "imu":
            value[config.axis] += config.level
        elif config.kind == "radar_shift" and observation.modality == "radar":
            if config.sensor_id is not None and observation.sensor_id != config.sensor_id:
                altered.append(observation)
                continue
            if value.ndim == 2:
                value[:, config.axis] += config.level
            else:
                value[config.axis] += config.level
        elif config.kind == "delay" and observation.modality in ("uwb", "ftm", "radar"):
            altered.append(replace(observation, arrival=observation.arrival + config.level))
            affected += 1
            continue
        else:
            altered.append(observation)
            continue
        altered.append(replace(observation, value=value))
        affected += 1
    return sorted(altered, key=lambda o: (o.arrival, o.t)), {
        "kind": config.kind,
        "injected": True,
        "start_s": start,
        "duration_s": duration,
        "level": config.level,
        "axis": config.axis,
        "seed": config.seed,
        "sensor_id": selected if config.kind == "range_bias" else config.sensor_id,
        "affected": affected,
        "notice": "Injected observation fault, not measured NLOS or RF behaviour.",
    }
