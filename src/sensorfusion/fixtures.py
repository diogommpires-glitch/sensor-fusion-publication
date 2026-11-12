"""Analytical synthetic measurements for executable checks, never real-data claims."""

from typing import cast

import numpy as np

from sensorfusion.contracts import Modality, Observation, RunConfig, Session


def motion(times: np.ndarray, session_id: str = "helix") -> tuple[np.ndarray, ...]:
    t = np.asarray(times)
    w = 0.3
    p = np.column_stack(
        (2.5 + 0.7 * np.cos(w * t), 2.5 + 0.6 * np.sin(w * t), -0.9 + 0.25 * np.sin(0.5 * w * t))
    )
    v = np.column_stack(
        (-0.7 * w * np.sin(w * t), 0.6 * w * np.cos(w * t), 0.125 * w * np.cos(0.5 * w * t))
    )
    a = np.column_stack(
        (
            -0.7 * w**2 * np.cos(w * t),
            -0.6 * w**2 * np.sin(w * t),
            -0.0625 * w**2 * np.sin(0.5 * w * t),
        )
    )
    if session_id == "stationary":
        p[:] = [2.8, 2.4, -0.8]
        v[:] = 0
        a[:] = 0
    return p, v, a


def make_session(config: RunConfig) -> Session:
    if config.session_id not in ("helix", "stationary"):
        raise ValueError(f"unknown synthetic session {config.session_id!r}")
    rng = np.random.default_rng(config.seed)
    anchor_array = np.array(
        [[0, 0, 0], [5, 0, -0.2], [0, 5, -0.1], [5, 5, -2.5], [2.5, 0, -2], [0, 2.5, -2.8]],
        dtype=float,
    )
    anchors = {str(i): anchor for i, anchor in enumerate(anchor_array)}
    observations: list[Observation] = []
    offsets = {
        "uwb": np.array([-0.24019, 0.11768, -0.06812]),
        "ftm": np.array([-0.24802, -0.10431, -0.07511]),
    }
    for modality, hz, sigma in (("uwb", 10, 0.04), ("ftm", 5, 0.12)):
        times = np.arange(0, config.duration_s + 1e-8, 1 / hz)
        positions = motion(times, config.session_id)[0]
        for row, (t, p) in enumerate(zip(times, positions, strict=True)):
            for anchor_id, anchor in list(anchors.items())[: 6 if modality == "uwb" else 4]:
                distance = np.linalg.norm(p + offsets[modality] - anchor) + rng.normal(0, sigma)
                observations.append(
                    Observation(
                        float(t),
                        float(t),
                        cast(Modality, modality),
                        anchor_id,
                        np.array([distance]),
                        np.array([[sigma**2]]),
                        anchor,
                        offsets[modality],
                        row,
                    )
                )
    imu_times = np.arange(0, config.duration_s + 1e-8, 0.02)
    acceleration = motion(imu_times, config.session_id)[2]
    for row, (t, a) in enumerate(zip(imu_times, acceleration, strict=True)):
        observations.append(
            Observation(
                float(t),
                float(t),
                "imu",
                "IMU",
                a + rng.normal(0, 0.015, 3),
                np.eye(3) * 0.015**2,
                source_row=row,
            )
        )
    radar_frames = []
    radar_times = np.arange(0, config.duration_s + 1e-8, 0.2)
    for row, (t, p) in enumerate(
        zip(radar_times, motion(radar_times, config.session_id)[0], strict=True)
    ):
        for sensor in ("AOP", "ISK0", "ISK1"):
            centroid = p + rng.normal(0, 0.035, 3)
            points = centroid + rng.normal(0, 0.1, (24, 3))
            observations.append(
                Observation(
                    float(t),
                    float(t),
                    "radar",
                    sensor,
                    centroid,
                    np.eye(3) * 0.035**2,
                    source_row=row,
                )
            )
            radar_frames.append(
                {
                    "time": float(t),
                    "sensor_id": sensor,
                    "points": points.tolist(),
                    "centroid": centroid.tolist(),
                    "frame": "world",
                }
            )
    reference_times = np.arange(0, config.duration_s + 1e-8, 0.02)
    return Session(
        "fixture",
        config.session_id,
        sorted(observations, key=lambda o: (o.arrival, o.t)),
        reference_times,
        motion(reference_times, config.session_id)[0],
        anchors,
        radar_frames,
        {
            "synthetic": True,
            "study_eligible": False,
            "reference": "analytical trajectory",
            "calibration": "known synthetic geometry and noise",
            "seed": config.seed,
            "notice": "Synthetic validation fixture. These scores do not establish real-data accuracy.",
        },
    )
