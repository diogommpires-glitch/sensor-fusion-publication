"""Version-pinned archive adapters; references remain separate from observations."""

import csv
import hashlib
import io
import itertools
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.signal import savgol_filter
from scipy.spatial.transform import Rotation

from sensorfusion.calibration import fit_imu_calibration, fit_radar_calibration, fit_range_bias
from sensorfusion.contracts import FloatArray, Observation, Session
from sensorfusion.radar import cluster_frame
from sensorfusion.sources import SOURCES, source, verify_archive
from sensorfusion.storage import resolve_workspace_path

PRIMARY_SPLITS = {
    "still": "development",
    "zigzag1": "development",
    "zigzag2": "development",
    "zigzag3": "validation",
    "zigzag4": "validation",
    "random1": "validation",
    "circle": "final",
    "random2": "final",
}
MILUV_SPLITS = {"default_1_random3_0": "development", "default_1_random3_2": "final"}
# Zenodo v2 infrastructure table: centimetres converted once at this boundary.
PRIMARY_ANCHORS = {
    "UWB_685A": [1.2092, -0.7295, -0.848],
    "UWB_6867": [3.0824, -0.7136, -2.133],
    "UWB_685B": [-0.3897, 3.3065, -0.125],
    "UWB_687C": [0.4095, 5.6300, -0.125],
    "UWB_686F": [5.5520, 2.0336, -0.125],
    "FTM_87C6": [1.9610, -0.3252, -0.120],
    "FTM_B9DC": [-0.3922, 2.3432, -0.120],
    "FTM_DBF0": [3.3696, 5.6266, -0.120],
    "FTM_74E8": [5.5514, 3.5184, -0.120],
}
TAG_OFFSETS = {"uwb": [-0.24019, 0.11768, -0.06812], "ftm": [-0.24802, -0.10431, -0.07511]}
FTM_ALIASES = {
    "7c:df:a1:05:87:c7": "FTM_87C6",
    "7c:df:a1:06:b9:dd": "FTM_B9DC",
    "7c:df:a1:0e:db:f1": "FTM_DBF0",
    "7c:df:a1:0f:74:e9": "FTM_74E8",
}
# Official MILUV configuration pinned at 399ad46f4e6e88c3c98b40aae33fe8e2c6719cff.
MILUV_ANCHORS = {
    "default_1_random3_0": [
        [3.273827392578125, 3.46404736328125, 1.8093309326171875],
        [3.186386962890625, 0.27394485473632812, 1.5884853515625],
        [2.850500244140625, -2.923056884765625, 1.89742041015625],
        [-2.497634521484375, -3.5018203125, 1.7730911865234375],
        [-2.95793310546875, 0.6128419189453125, 1.65714208984375],
        [-2.734676513671875, 3.65854248046875, 1.890254638671875],
    ],
    "default_1_random3_2": [
        [3.28161572265625, 0.8334449462890625, 1.2502938232421875],
        [3.0746474609375, -0.40140902709960937, 1.9075947265625],
        [-3.143182861328125, -2.721824951171875, 1.3383465576171875],
        [-2.332289794921875, -3.3927958984375, 1.845730712890625],
        [-1.79465478515625, 4.198333984375, 1.8233143310546875],
        [-0.29467779541015625, 4.1459013671875, 1.2259888916015625],
    ],
}
MILUV_LEVER_ARM = np.array([0.13189, -0.17245, -0.05249])
MILUV_ALLOWLIST = ("timestamp", "from_id", "to_id", "range_raw")
_VERIFIED_ARCHIVES: dict[tuple[str, int, int], dict[str, Any]] = {}


class DataBlockedError(ValueError):
    """A scientific prerequisite is missing or has failed its check."""


def _rows(archive: Path, member: str, required: tuple[str, ...]) -> list[dict[str, str]]:
    with ZipFile(archive) as zipped, zipped.open(member) as stream:
        reader = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig", newline=""))
        missing = set(required) - set(reader.fieldnames or [])
        if missing:
            raise DataBlockedError(
                f"{archive.name}:{member} lacks required columns {sorted(missing)}"
            )
        return list(reader)


def _archive(workspace: Path, dataset_id: str, filename: str) -> Path:
    return workspace / "data" / "raw" / dataset_id / filename


def _verify_sources(workspace: Path, dataset_id: str) -> list[dict[str, Any]]:
    manifests = []
    for entry in source(dataset_id)["files"]:
        path = _archive(workspace, dataset_id, entry["name"])
        if not path.exists():
            raise DataBlockedError(f"missing pinned source archive {path.name}")
        stat = path.stat()
        key = str(path.resolve()), stat.st_size, stat.st_mtime_ns
        if key not in _VERIFIED_ARCHIVES:
            _VERIFIED_ARCHIVES[key] = verify_archive(path, entry)
        manifests.append({"name": entry["name"], **_VERIFIED_ARCHIVES[key]})
    return manifests


def _primary_rows(
    workspace: Path, session_id: str, filename: str, required: tuple[str, ...]
) -> list[dict[str, str]]:
    return _rows(
        _archive(workspace, "roboloc-g", "SENSORS_MEASUREMENTS_CSV.zip"),
        f"SENSORS_MEASUREMENTS_CSV/UWB_FTM_IMU/{session_id}/{filename}",
        required,
    )


def _reference(workspace: Path, dataset_id: str, session_id: str) -> tuple[FloatArray, FloatArray]:
    times, positions, _ = _reference_with_provenance(workspace, dataset_id, session_id)
    return times, positions


def _normalise_reference(
    times: FloatArray, positions: FloatArray, source_id: str
) -> tuple[FloatArray, FloatArray, dict[str, Any]]:
    """Collapse identical duplicates and quarantine every member of a conflicting timestamp."""
    if (
        times.ndim != 1
        or positions.shape != (len(times), 3)
        or not np.isfinite(times).all()
        or not np.isfinite(positions).all()
    ):
        raise DataBlockedError(
            f"{source_id} reference must contain finite times and positions with shape (N,3)"
        )
    if np.any(np.diff(times) < 0):
        raise DataBlockedError(
            f"{source_id} reference timestamps must not decrease; source order is preserved"
        )
    keep = np.ones(len(times), dtype=bool)
    identical_groups: list[dict[str, Any]] = []
    conflicting_groups: list[dict[str, Any]] = []
    starts = np.r_[0, np.flatnonzero(np.diff(times) != 0) + 1]
    ends = np.r_[starts[1:], len(times)]
    for start, end in zip(starts, ends, strict=True):
        if end - start <= 1:
            continue
        group = {
            "time_s": float(times[start]),
            "source_rows": list(range(int(start) + 2, int(end) + 2)),
            "positions_m": positions[start:end].tolist(),
        }
        if np.all(positions[start:end] == positions[start]):
            keep[start + 1 : end] = False
            identical_groups.append(group)
        else:
            keep[start:end] = False
            conflicting_groups.append(group)
    if np.sum(keep) < 2:
        raise DataBlockedError(
            f"{source_id} reference needs at least two supported distinct timestamps after normalisation"
        )
    provenance = {
        "policy": "reference-normalisation-v1",
        "source_id": source_id,
        "rule": "preserve source order; identical duplicate positions collapse to first; conflicting timestamp groups excluded in full; no averaging or extrapolation",
        "source_rows_include_header": True,
        "input_rows": len(times),
        "output_rows": int(np.sum(keep)),
        "identical_duplicate_rows_removed": sum(
            len(group["source_rows"]) - 1 for group in identical_groups
        ),
        "conflicting_rows_removed": sum(len(group["source_rows"]) for group in conflicting_groups),
        "identical_groups": identical_groups,
        "conflicting_groups": conflicting_groups,
    }
    return times[keep], positions[keep], provenance


def _reference_with_provenance(
    workspace: Path, dataset_id: str, session_id: str
) -> tuple[FloatArray, FloatArray, dict[str, Any]]:
    if dataset_id == "roboloc-g":
        rows = _rows(
            _archive(workspace, dataset_id, "GROUND_TRUTH.zip"),
            f"GROUND_TRUTH/{session_id}_ground_truth_gantry.csv",
            ("time", "Grua.X", "Grua.Y", "Grua.Z"),
        )
        times = np.array([float(row["time"]) for row in rows])
        positions = np.array(
            [[float(row[f"Grua.{axis}"]) / 1000 for axis in "XYZ"] for row in rows]
        )
    else:
        rows = _rows(
            _archive(workspace, dataset_id, f"{session_id}.zip"),
            f"{session_id}/ifo001/mocap.csv",
            (
                "timestamp",
                "pose.position.x",
                "pose.position.y",
                "pose.position.z",
                "pose.orientation.x",
                "pose.orientation.y",
                "pose.orientation.z",
                "pose.orientation.w",
            ),
        )
        times = np.array([float(row["timestamp"]) for row in rows])
        body = np.array([[float(row[f"pose.position.{axis}"]) for axis in "xyz"] for row in rows])
        quaternions = np.array(
            [[float(row[f"pose.orientation.{axis}"]) for axis in "xyzw"] for row in rows]
        )
        if not np.allclose(np.linalg.norm(quaternions, axis=1), 1, atol=1e-3):
            raise DataBlockedError("MILUV mocap quaternion norm must be within 0.001 of one")
        positions = body + Rotation.from_quat(quaternions).apply(MILUV_LEVER_ARM)
    return _normalise_reference(times, positions, f"{dataset_id}/{session_id}")


def _transactions(rows: list[dict[str, str]]) -> list[tuple[int, dict[str, str]]]:
    """FTM dist_est repeats per exchange frame; preserve one estimate per transaction."""
    selected: dict[tuple[str, str], tuple[int, dict[str, str]]] = {}
    for index, row in enumerate(rows, 2):
        key = row["timestamp"], row["anchor_id"]
        if key in selected and row["dist_est"] != selected[key][1]["dist_est"]:
            raise DataBlockedError(f"FTM transaction {key} has contradictory dist_est values")
        selected.setdefault(key, (index, row))
    return list(selected.values())


def _primary_ranges(workspace: Path, session_id: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for modality, filename, field, scale in [
        ("uwb", "gtec-uwb-ranging-pozyx.csv", "range", 0.001),
        ("ftm", "gtec-ftm.csv", "dist_est", 1.0),
    ]:
        rows = _primary_rows(workspace, session_id, filename, ("timestamp", "anchor_id", field))
        selected = _transactions(rows) if modality == "ftm" else list(enumerate(rows, 2))
        for index, row in selected:
            if modality == "uwb":
                if row.get("tag_id") != "0":
                    raise DataBlockedError(
                        f"unexpected primary UWB tag {row.get('tag_id')!r}, expected 0"
                    )
                anchor_id = f"UWB_{int(row['anchor_id']):04X}"
            else:
                if row["anchor_id"] not in FTM_ALIASES:
                    raise DataBlockedError(f"unregistered FTM MAC {row['anchor_id']!r}")
                anchor_id = FTM_ALIASES[row["anchor_id"]]
            if anchor_id not in PRIMARY_ANCHORS:
                raise DataBlockedError(f"unregistered primary anchor {anchor_id}")
            value, timestamp = float(row[field]) * scale, int(row["timestamp"]) / 1e9
            if not np.isfinite(value) or value <= 0 or not np.isfinite(timestamp):
                continue
            observations.append(
                {
                    "t": timestamp,
                    "arrival": timestamp,
                    "value": value,
                    "modality": modality,
                    "sensor_id": anchor_id,
                    "source_row": index,
                    "rss": row.get("rss"),
                    "raw_anchor_id": row["anchor_id"],
                }
            )
    return observations


def read_miluv_ranges(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Read only four raw fields, excluding all embedded truth and derived bias."""
    observations = []
    for index, source_row in enumerate(rows, 2):
        row = {field: source_row[field] for field in MILUV_ALLOWLIST}
        first, second = int(row["from_id"]), int(row["to_id"])
        if first == 10 and second in range(6):
            anchor_id = second
        elif second == 10 and first in range(6):
            anchor_id = first
        else:
            continue
        value, timestamp = float(row["range_raw"]), float(row["timestamp"])
        if not np.isfinite(value) or value <= 0 or not np.isfinite(timestamp):
            continue
        observations.append(
            {
                "t": timestamp,
                "arrival": timestamp,
                "value": value,
                "modality": "uwb",
                "sensor_id": f"UWB_{anchor_id}",
                "source_row": index,
            }
        )
    return observations


def _miluv_ranges(workspace: Path, session_id: str) -> list[dict[str, Any]]:
    return read_miluv_ranges(
        _rows(
            _archive(workspace, "miluv", f"{session_id}.zip"),
            f"{session_id}/ifo001/uwb_range.csv",
            MILUV_ALLOWLIST,
        )
    )


def _imu_rows(
    workspace: Path, session_id: str
) -> tuple[list[dict[str, str]], FloatArray, FloatArray]:
    rows = _primary_rows(
        workspace,
        session_id,
        "gtec-uwb-imu-pozyx_0.csv",
        (
            "timestamp",
            "header.stamp.sec",
            "header.stamp.nanosec",
            "linear_acceleration.x",
            "linear_acceleration.y",
            "linear_acceleration.z",
        ),
    )
    times = np.array(
        [int(row["header.stamp.sec"]) + int(row["header.stamp.nanosec"]) / 1e9 for row in rows]
    )
    acceleration = np.array(
        [[float(row[f"linear_acceleration.{axis}"]) for axis in "xyz"] for row in rows]
    )
    if not np.isfinite(acceleration).all() or np.any(np.diff(times) <= 0):
        raise DataBlockedError(f"{session_id} IMU must be finite and strictly increasing")
    return rows, times, acceleration


def _radar_frames(workspace: Path, session_id: str, origin: float) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for sensor in ["AOP", "ISK0", "ISK1"]:
        member = f"SENSORS_MEASUREMENTS_CSV/{sensor}/{session_id}/radar_scan.csv"
        rows = _rows(
            _archive(workspace, "roboloc-g", "SENSORS_MEASUREMENTS_CSV.zip"),
            member,
            (
                "timestamp",
                "header.stamp.sec",
                "header.stamp.nanosec",
                "array_index",
                "returns.range",
                "returns.azimuth",
                "returns.elevation",
                "returns.doppler_velocity",
            ),
        )
        grouped: dict[tuple[str, str], list[tuple[int, dict[str, str]]]] = {}
        for index, row in enumerate(rows, 2):
            grouped.setdefault((row["header.stamp.sec"], row["header.stamp.nanosec"]), []).append(
                (index, row)
            )
        for (sec, ns), detections in grouped.items():
            ranges = np.array([float(row["returns.range"]) for _, row in detections])
            azimuth = np.array([float(row["returns.azimuth"]) for _, row in detections])
            elevation = np.array([float(row["returns.elevation"]) for _, row in detections])
            points = np.column_stack(
                (
                    ranges * np.sin(azimuth) * np.cos(elevation),
                    ranges * np.cos(azimuth) * np.cos(elevation),
                    ranges * np.sin(elevation),
                )
            )
            if not np.isfinite(points).all() or np.any(ranges < 0):
                raise DataBlockedError(f"invalid polar return in {member}, scan {sec}:{ns}")
            frames.append(
                {
                    "sensor_id": sensor,
                    "scan_id": f"{sec}:{ns}",
                    "t": int(sec) + int(ns) / 1e9 - origin,
                    "arrival": max(int(row["timestamp"]) for _, row in detections) / 1e9 - origin,
                    "points": points.tolist(),
                    "frame": f"{sensor}-native",
                    "source_rows": [index for index, _ in detections],
                    "doppler": [float(row["returns.doppler_velocity"]) for _, row in detections],
                }
            )
    return sorted(frames, key=lambda frame: (frame["arrival"], frame["sensor_id"]))


def catalog(workspace: Path) -> dict[str, Any]:
    inventory: dict[str, Any] = {}
    for dataset_id, definition in SOURCES.items():
        missing = [
            entry["name"]
            for entry in definition["files"]
            if not _archive(workspace, dataset_id, entry["name"]).exists()
        ]
        audit_path = workspace / "data" / "audits" / f"{dataset_id}.json"
        report = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}
        reasons = [f"missing pinned source archives: {', '.join(missing)}"] if missing else []
        reasons += report.get(
            "blocking_reasons", ["actual schema audit and development calibration required"]
        )
        inventory[dataset_id] = {
            "id": dataset_id,
            "name": definition["title"],
            "version": definition["version"],
            "source": definition["url"],
            "licence": definition["licence"],
            "status": "blocked" if missing else report.get("status", "downloaded"),
            "reasons": reasons,
            "sessions": PRIMARY_SPLITS if dataset_id == "roboloc-g" else MILUV_SPLITS,
            "certification": report.get("certification", {}),
        }
    return inventory


def _calibration(workspace: Path, dataset_id: str, calibration_id: str | None) -> dict[str, Any]:
    if calibration_id is None:
        raise DataBlockedError(f"{dataset_id} requires an explicit development calibration_id")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", calibration_id):
        raise ValueError(f"invalid registered calibration id {calibration_id!r}")
    path = workspace / "calibrations" / f"{calibration_id}.json"
    if not path.exists():
        raise DataBlockedError(f"calibration {calibration_id!r} does not exist")
    calibration: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if calibration["dataset_id"] != dataset_id or calibration["split"] != "development":
        raise DataBlockedError(
            f"calibration {calibration_id} must match dataset and development split"
        )
    return calibration


def load_session(
    workspace: Path, dataset_id: str, session_id: str, calibration_id: str | None = None
) -> Session:
    source(dataset_id)
    splits = PRIMARY_SPLITS if dataset_id == "roboloc-g" else MILUV_SPLITS
    if session_id not in splits:
        raise ValueError(f"unknown {dataset_id} session {session_id!r}")
    calibration = _calibration(workspace, dataset_id, calibration_id)
    manifests = _verify_sources(workspace, dataset_id)
    if calibration.get("source_hashes") != manifests:
        raise DataBlockedError("calibration source hashes must match the pinned verified archives")
    raw = (
        _primary_ranges(workspace, session_id)
        if dataset_id == "roboloc-g"
        else _miluv_ranges(workspace, session_id)
    )
    origin = min(row["t"] for row in raw)
    anchors = (
        {key: np.array(value) for key, value in PRIMARY_ANCHORS.items()}
        if dataset_id == "roboloc-g"
        else {
            f"UWB_{index}": np.array(value) for index, value in enumerate(MILUV_ANCHORS[session_id])
        }
    )
    observations = []
    for row in raw:
        sensor = row["sensor_id"]
        if sensor not in calibration["ranges"]:
            raise DataBlockedError(f"calibration lacks radio {sensor}")
        fitted = calibration["ranges"][sensor]
        variance = float(fitted["variance_m2"])
        if not np.isfinite(variance) or variance <= 0:
            raise DataBlockedError(f"{sensor} empirical variance must be positive, got {variance}")
        value = row["value"] - float(fitted["bias_m"])
        observations.append(
            Observation(
                t=row["t"] - origin,
                arrival=row["arrival"] - origin,
                modality=row["modality"],
                sensor_id=sensor,
                value=np.array([value]),
                covariance=np.array([[variance]]),
                anchor=anchors[sensor],
                offset=np.array(TAG_OFFSETS[row["modality"]])
                if dataset_id == "roboloc-g"
                else np.zeros(3),
                source_row=row["source_row"],
            )
        )
    radar_frames: list[dict[str, Any]] = []
    if dataset_id == "roboloc-g":
        if calibration.get("imu", {}).get("status") == "certified":
            rows, times, acceleration = _imu_rows(workspace, session_id)
            imu = calibration["imu"]
            values = (acceleration - np.array(imu["stationary_mean"])) @ np.array(imu["rotation"]).T
            for index, (row, timestamp, value) in enumerate(
                zip(rows, times, values, strict=True), 2
            ):
                observations.append(
                    Observation(
                        t=float(timestamp - origin),
                        arrival=int(row["timestamp"]) / 1e9 - origin,
                        modality="imu",
                        sensor_id="Pozyx-IMU",
                        value=value,
                        covariance=np.array(imu["covariance"]),
                        source_row=index,
                    )
                )
        radar_frames = _radar_frames(workspace, session_id, origin)
        for frame in radar_frames:
            fitted_radar = calibration.get("radars", {}).get(frame["sensor_id"], {})
            if fitted_radar.get("certified"):
                points = np.array(frame["points"]) @ np.array(
                    fitted_radar["rotation"]
                ).T + np.array(fitted_radar["translation"])
                frame["native_points"] = frame["points"]
                frame["points"] = points.tolist()
                frame["frame"] = "world"
                observations.append(
                    Observation(
                        t=frame["t"],
                        arrival=frame["arrival"],
                        modality="radar",
                        sensor_id=frame["sensor_id"],
                        value=points,
                        covariance=np.array(fitted_radar["covariance"]),
                        source_row=frame["source_rows"][0],
                    )
                )
    reference_times, reference_positions, reference_provenance = _reference_with_provenance(
        workspace, dataset_id, session_id
    )
    return Session(
        dataset_id=dataset_id,
        session_id=session_id,
        observations=sorted(observations, key=lambda obs: (obs.arrival, obs.source_row)),
        reference_times=reference_times - origin,
        reference_positions=reference_positions,
        anchors=anchors,
        radar_frames=radar_frames,
        metadata={
            "source_version": source(dataset_id)["version"],
            "split": splits[session_id],
            "calibration_id": calibration_id,
            "reference_normalisation": reference_provenance,
            "calibration_hash": hashlib.sha256(
                (workspace / "calibrations" / f"{calibration_id}.json").read_bytes()
            ).hexdigest(),
            "certified_modalities": calibration["certified_modalities"],
            "radars": calibration.get("radars", {}),
            "time_origin_s": origin,
            "target": "gantry origin" if dataset_id == "roboloc-g" else "ifo001 physical tag 10",
            "imu_status": calibration.get("imu", {}).get("status", "not-applicable"),
            "radar_status": "calibrated world clouds; causal predicted-state association required"
            if "radar" in calibration["certified_modalities"]
            else "raw scans require frozen calibration and causal association",
            "radio_clock": "archive timestamp used as arrival and proxy acquisition; hardware latency unknown",
        },
    )


def _stream_summary(rows: list[dict[str, str]], *, time_scale: float) -> dict[str, Any]:
    timestamps = np.array([float(row["timestamp"]) * time_scale for row in rows])
    unique = np.unique(timestamps)
    gaps = np.diff(unique)
    return {
        "rows": len(rows),
        "columns": list(rows[0]) if rows else [],
        "start_s": float(unique[0]) if len(unique) else None,
        "end_s": float(unique[-1]) if len(unique) else None,
        "unique_timestamps": len(unique),
        "repeated_timestamp_rows": len(rows) - len(unique),
        "backwards_timestamps": int(np.sum(np.diff(timestamps) < 0)),
        "median_rate_hz": float(1 / np.median(gaps)) if len(gaps) else None,
        "maximum_gap_s": float(np.max(gaps)) if len(gaps) else None,
    }


def audit(workspace: Path, dataset_id: str) -> dict[str, Any]:
    """Inspect actual archive rows and record unresolved physical prerequisites."""
    definition = source(dataset_id)
    report: dict[str, Any] = {
        "dataset_id": dataset_id,
        "version": definition["version"],
        "audited_at": datetime.now(UTC).isoformat(),
        "source": definition,
        "archives": [],
        "streams": {},
        "blocking_reasons": [],
        "certification": {
            "archives": False,
            "actual_schema": False,
            "units": False,
            "clocks": False,
            "full_study": False,
        },
    }
    for entry in definition["files"]:
        path = _archive(workspace, dataset_id, entry["name"])
        if not path.exists():
            report["blocking_reasons"].append(f"missing pinned archive {entry['name']}")
        else:
            report["archives"].append({"name": entry["name"], **verify_archive(path, entry)})
    if report["blocking_reasons"]:
        report["status"] = "blocked"
    else:
        report["certification"]["archives"] = True
        if dataset_id == "roboloc-g":
            for session in PRIMARY_SPLITS:
                for modality, filename in [
                    ("uwb", "gtec-uwb-ranging-pozyx.csv"),
                    ("ftm", "gtec-ftm.csv"),
                    ("imu", "gtec-uwb-imu-pozyx_0.csv"),
                ]:
                    rows = _primary_rows(workspace, session, filename, ("timestamp",))
                    summary = _stream_summary(rows, time_scale=1e-9)
                    if modality in ("uwb", "ftm"):
                        summary["anchor_ids"] = dict(Counter(row["anchor_id"] for row in rows))
                        field = "range" if modality == "uwb" else "dist_est"
                        values = np.array([float(row[field]) for row in rows])
                        summary["nonpositive_or_nonfinite"] = int(
                            np.sum(~np.isfinite(values) | (values <= 0))
                        )
                        summary["raw_range_min_max"] = [float(values.min()), float(values.max())]
                        if modality == "ftm":
                            summary["transactions"] = len(_transactions(rows))
                    else:
                        _, _, acceleration = _imu_rows(workspace, session)
                        quaternions = np.array(
                            [[float(row[f"orientation.{axis}"]) for axis in "xyzw"] for row in rows]
                        )
                        gyro = np.array(
                            [
                                [float(row[f"angular_velocity.{axis}"]) for axis in "xyz"]
                                for row in rows
                            ]
                        )
                        summary.update(
                            acceleration_mean=acceleration.mean(0).tolist(),
                            acceleration_std=acceleration.std(0).tolist(),
                            gyro_max_norm_rad_s=float(np.max(np.linalg.norm(gyro, axis=1))),
                            quaternion_norm_min_max=[
                                float(np.linalg.norm(quaternions, axis=1).min()),
                                float(np.linalg.norm(quaternions, axis=1).max()),
                            ],
                        )
                    report["streams"][f"{session}/{modality}"] = summary
                frames = _radar_frames(workspace, session, 0)
                for sensor in ["AOP", "ISK0", "ISK1"]:
                    selected = [frame for frame in frames if frame["sensor_id"] == sensor]
                    report["streams"][f"{session}/{sensor}"] = {
                        "scans": len(selected),
                        "returns": sum(len(frame["points"]) for frame in selected),
                        "frame": f"{sensor}-native",
                        "scan_id": "header.stamp.sec:header.stamp.nanosec",
                    }
            report["unit_rulings"] = {
                "timestamp": "sensor CSV integer Unix nanoseconds; GT time floating Unix seconds",
                "uwb": "raw range millimetres, divide by 1000; metadata metres claim contradicted by payload and Pozyx native units",
                "reference": "gantry coordinates millimetres, divide by 1000; command G21 and still 2500,2500,-800 corroborate",
                "anchors": "published infrastructure centimetres, divide by 100",
                "offsets": "published mounting offsets millimetres, divide by 1000",
                "ftm": "dist_est metres; repeated rows per packet frame are one transaction",
                "radar": "metres and radians; native x=r*sin(az)*cos(el), y=r*cos(az)*cos(el), z=r*sin(el), checked against cloud",
                "imu": "m/s^2 and rad/s; near-zero still acceleration indicates gravity removed upstream, physical bias/gravity not separately identified",
            }
            report["blocking_reasons"] = [
                "development radio covariance/bias and FTM alias calibration required",
                "IMU fixed-frame excitation, timing and rotation calibration required",
                "each radar requires 30 reviewed development annotations and effective calibration",
                "radio acquisition time is unavailable; CSV timestamp is the declared proxy",
                "still sensor recording is 122.7s but reference supports only 11.24s; no reference extrapolation",
            ]
        else:
            for session in MILUV_SPLITS:
                rows = _rows(
                    _archive(workspace, dataset_id, f"{session}.zip"),
                    f"{session}/ifo001/uwb_range.csv",
                    MILUV_ALLOWLIST,
                )
                summary = _stream_summary(rows, time_scale=1)
                selected = read_miluv_ranges(rows)
                summary.update(
                    allowed_columns=list(MILUV_ALLOWLIST),
                    selected_tag=10,
                    selected_rows=len(selected),
                    ignored_columns=sorted(set(rows[0]) - set(MILUV_ALLOWLIST)),
                    anchor_counts=dict(Counter(row["sensor_id"] for row in selected)),
                )
                report["streams"][session] = summary
            report["unit_rulings"] = {
                "timestamp": "relative seconds",
                "range_raw": "metres",
                "reference": "mocap body position + R(quaternion)*published tag10 lever arm; evaluation only",
            }
            report["blocking_reasons"] = [
                "external development-only radio bias/covariance calibration required"
            ]
        sessions = PRIMARY_SPLITS if dataset_id == "roboloc-g" else MILUV_SPLITS
        for session in sessions:
            _, _, normalisation = _reference_with_provenance(workspace, dataset_id, session)
            report["streams"][f"{session}/reference"] = normalisation
        report["certification"].update(actual_schema=True, units=True, clocks=True)
        report["status"] = "blocked"
    calibrations = []
    calibration_directory = workspace / "calibrations"
    if calibration_directory.exists():
        for path in calibration_directory.glob("*.json"):
            calibration = json.loads(path.read_text(encoding="utf-8"))
            if (
                calibration.get("dataset_id") == dataset_id
                and "certified_modalities" in calibration
            ):
                calibrations.append(calibration)
    if calibrations and report["certification"]["archives"]:
        current = max(calibrations, key=lambda entry: entry["created_at"])
        report["calibration_id"] = current["id"]
        report["certified_modalities"] = current["certified_modalities"]
        report["blocking_reasons"] = current.get("blockers", [])
        if current["status"] == "certified":
            report["certification"]["full_study"] = True
            report["status"] = "certified"
    report["certified"] = report["certification"]["full_study"]
    report["blockers"] = report["blocking_reasons"]
    directory = workspace / "data" / "audits"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{dataset_id}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def calibrate_dataset(workspace: Path, dataset_id: str) -> dict[str, Any]:
    """Fit radio and constrained IMU models using declared development sessions only."""
    source_hashes = _verify_sources(workspace, dataset_id)
    sessions = [
        session
        for session, split in (
            PRIMARY_SPLITS if dataset_id == "roboloc-g" else MILUV_SPLITS
        ).items()
        if split == "development"
    ]
    residuals: dict[str, list[tuple[float, float]]] = {}
    ftm_assignment: dict[str, list[tuple[float, FloatArray]]] = {}
    stationary: list[FloatArray] = []
    moving: list[FloatArray] = []
    reference_accelerations: list[FloatArray] = []
    for session in sessions:
        times, positions = _reference(workspace, dataset_id, session)
        raw = (
            _primary_ranges(workspace, session)
            if dataset_id == "roboloc-g"
            else _miluv_ranges(workspace, session)
        )
        anchors = (
            PRIMARY_ANCHORS
            if dataset_id == "roboloc-g"
            else {f"UWB_{index}": value for index, value in enumerate(MILUV_ANCHORS[session])}
        )
        for row in raw:
            if not times[0] <= row["t"] <= times[-1]:
                continue
            index = int(np.clip(np.searchsorted(times, row["t"]), 1, len(times) - 1))
            if times[index] - times[index - 1] > (12 if session == "still" else 0.5):
                continue
            position = np.array(
                [np.interp(row["t"], times, positions[:, axis]) for axis in range(3)]
            )
            if dataset_id == "roboloc-g":
                position += np.array(TAG_OFFSETS[row["modality"]])
            truth_range = float(np.linalg.norm(position - anchors[row["sensor_id"]]))
            residuals.setdefault(row["sensor_id"], []).append((row["value"], truth_range))
            if dataset_id == "roboloc-g" and row["modality"] == "ftm" and session != "still":
                ftm_assignment.setdefault(row["raw_anchor_id"], []).append((row["value"], position))
        if dataset_id == "roboloc-g":
            _, imu_times, acceleration = _imu_rows(workspace, session)
            if session == "still":
                stationary.extend(acceleration[(imu_times >= times[0]) & (imu_times <= times[-1])])
            else:
                # Fixed 10Hz resampling and 1.1s cubic Savitzky-Golay derivative, chosen before results.
                grid = np.arange(times[0], times[-1], 0.1)
                regular = np.column_stack(
                    [np.interp(grid, times, positions[:, axis]) for axis in range(3)]
                )
                derivative = savgol_filter(
                    regular, window_length=11, polyorder=3, deriv=2, delta=0.1, axis=0
                )
                spline = CubicSpline(grid, derivative, extrapolate=False)
                eligible = (imu_times >= grid[0] + 0.6) & (imu_times <= grid[-1] - 0.6)
                for left, right in zip(times[:-1], times[1:], strict=True):
                    if right - left > 0.5:
                        eligible &= ~((imu_times >= left - 0.6) & (imu_times <= right + 0.6))
                moving.extend(acceleration[eligible])
                reference_accelerations.extend(spline(imu_times[eligible]))
    calibration: dict[str, Any] = {
        "dataset_id": dataset_id,
        "split": "development",
        "sessions": sessions,
        "fit_sessions": sessions,
        "source_hashes": source_hashes,
        "created_at": datetime.now(UTC).isoformat(),
        "ranges": {},
        "radars": {
            sensor: {
                "certified": False,
                "reason": "30 reviewed development annotations remain required",
            }
            for sensor in ["AOP", "ISK0", "ISK1"]
        }
        if dataset_id == "roboloc-g"
        else {},
        "certified_modalities": ["uwb", "ftm"] if dataset_id == "roboloc-g" else ["uwb"],
        "status": "blocked" if dataset_id == "roboloc-g" else "certified",
        "blockers": [
            "each radar requires 30 reviewed development annotations and effective calibration"
        ]
        if dataset_id == "roboloc-g"
        else [],
    }
    for sensor, pairs in residuals.items():
        measured, truth = np.array(pairs).T
        fitted = fit_range_bias(measured, truth, split="development")
        calibration["ranges"][sensor] = {
            "bias_m": fitted.bias,
            "variance_m2": fitted.variance,
            "count": fitted.count,
            "lag_one_residual_correlation": float(
                np.corrcoef((measured - truth)[:-1], (measured - truth)[1:])[0, 1]
            ),
        }
    if dataset_id == "roboloc-g":
        costs = []
        macs = sorted(ftm_assignment)
        for permutation in itertools.permutations(FTM_ALIASES.values()):
            cost = 0.0
            for mac, anchor_id in zip(macs, permutation, strict=True):
                delta = np.array(
                    [
                        value - np.linalg.norm(point - PRIMARY_ANCHORS[anchor_id])
                        for value, point in ftm_assignment[mac]
                    ]
                )
                cost += float(np.mean((delta - delta.mean()) ** 2))
            costs.append((cost, dict(zip(macs, permutation, strict=True))))
        costs.sort(key=lambda candidate: candidate[0])
        if costs[0][1] != FTM_ALIASES or costs[1][0] <= costs[0][0]:
            raise DataBlockedError(
                "published FTM device aliases are not the unique development geometry assignment"
            )
        calibration["ftm_alias_evidence"] = {
            "aliases": FTM_ALIASES,
            "method": "all 24 geometry assignments with per-anchor mean removed; zigzag1/2 only",
            "best_cost_m2": costs[0][0],
            "second_cost_m2": costs[1][0],
            "limitation": "empirical assignment plus matching suffix pattern, not device-firmware proof",
        }
        fitted_imu = fit_imu_calibration(
            np.array(stationary),
            np.array(moving),
            np.array(reference_accelerations),
            split="development",
        )
        calibrated = fitted_imu.apply(np.array(moving))
        residual = calibrated - np.array(reference_accelerations)
        calibration["imu"] = {
            "status": "certified",
            "stationary_mean": fitted_imu.stationary_mean.tolist(),
            "rotation": fitted_imu.rotation.tolist(),
            "covariance": fitted_imu.covariance.tolist(),
            "stationary_count": len(stationary),
            "moving_count": len(moving),
            "residual_rmse_m_s2": float(np.sqrt(np.mean(np.sum(residual**2, axis=1)))),
            "timing_offset_s": 0.0,
            "timing_status": "original NTP-synchronised timestamps retained; no additional shift identified or applied",
            "differentiation": "10Hz interpolation; Savitzky-Golay 11 samples, cubic, derivative2; gaps>0.5s and edges±0.6s excluded",
            "reason": "constrained fixed-mount model with empirical residual covariance; high error is retained, not an accuracy claim",
        }
        diagnostics = {}
        sampling_intervals = []
        for session in sessions:
            rows, imu_times, _ = _imu_rows(workspace, session)
            sampling_intervals.extend(np.diff(imu_times).tolist())
            quaternions = np.array(
                [[float(row[f"orientation.{axis}"]) for axis in "xyzw"] for row in rows]
            )
            rotations = Rotation.from_quat(quaternions)
            excursions = (rotations[0].inv() * rotations).magnitude()
            gyro = np.array(
                [[float(row[f"angular_velocity.{axis}"]) for axis in "xyz"] for row in rows]
            )
            diagnostics[session] = {
                "maximum_quaternion_excursion_deg": float(np.rad2deg(excursions.max())),
                "gyro_p95_rad_s": float(np.quantile(np.linalg.norm(gyro, axis=1), 0.95)),
                "quaternion_norm_min_max": [
                    float(np.linalg.norm(quaternions, axis=1).min()),
                    float(np.linalg.norm(quaternions, axis=1).max()),
                ],
                "arrival_minus_header_median_s": float(
                    np.median(np.array([int(row["timestamp"]) / 1e9 for row in rows]) - imu_times)
                ),
                "arrival_minus_header_max_s": float(
                    np.max(np.array([int(row["timestamp"]) / 1e9 for row in rows]) - imu_times)
                ),
            }
        calibration["imu"]["fixed_mount_diagnostics"] = diagnostics
        calibration["imu"]["fixed_mount_basis"] = (
            "published translating gantry mount; quaternion excursion <=1.17 degrees on development; gyro includes vibration/spikes"
        )
        sample_interval = float(np.median(sampling_intervals))
        calibration["imu"]["median_sampling_interval_s"] = sample_interval
        calibration["imu"]["acceleration_noise_density_m2_s3"] = float(
            np.trace(fitted_imu.covariance) / 3 * sample_interval
        )
        calibration["imu"]["acceleration_noise_density_matrix_m2_s3"] = (
            fitted_imu.covariance * sample_interval
        ).tolist()
        calibration["imu"]["noise_semantics"] = (
            "effective white acceleration density = empirical acceleration residual covariance * median sample interval; includes differentiation/model error; no claim of temporal whiteness"
        )
        calibration["imu"]["residual_lag_one_correlations"] = [
            float(np.corrcoef(residual[:-1, axis], residual[1:, axis])[0, 1]) for axis in range(3)
        ]
        calibration["imu"]["gravity_ruling"] = {
            "stationary_mean_norm_m_s2": float(np.linalg.norm(fitted_imu.stationary_mean)),
            "interpretation": "near-zero published linear_acceleration: upstream gravity removal is inferred; remove remaining stationary mean without subtracting 9.80665 again; gravity and physical bias are not separately identifiable",
        }
        calibration["certified_modalities"].append("imu")
        calibration["radar_annotation_manifest"] = prepare_radar_annotations(workspace)
        radar_result = fit_radar_annotations(workspace, calibration["radar_annotation_manifest"])
        calibration["radars"] = radar_result["radars"]
        calibration["radar_annotation_fit"] = radar_result["manifest"]
        calibration["blockers"] = [
            f"{sensor}: {entry['reason']}"
            for sensor, entry in radar_result["radars"].items()
            if not entry["certified"]
        ]
        if any(entry["certified"] for entry in calibration["radars"].values()):
            calibration["certified_modalities"].append("radar")
        if not calibration["blockers"]:
            calibration["status"] = "certified"
    return _save_calibration(workspace, calibration)


def _save_calibration(workspace: Path, calibration: dict[str, Any]) -> dict[str, Any]:
    identity = {
        key: value
        for key, value in calibration.items()
        if key not in ("created_at", "id", "calibration_id")
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:12]
    calibration_id = f"{calibration['dataset_id']}-development-{digest}"
    calibration["id"] = calibration_id
    calibration["calibration_id"] = calibration_id
    directory = workspace / "calibrations"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{calibration_id}.json"
    if path.exists():
        existing: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return existing
    with path.open("x", encoding="utf-8") as stream:
        json.dump(calibration, stream, indent=2)
    return calibration


def accept_radar_review(workspace: Path, calibration_id: str, review_path: Path) -> dict[str, Any]:
    """Admit a hash-bound agent scientific review into a new immutable calibration."""
    calibration = _calibration(workspace, "roboloc-g", calibration_id)
    calibration_path = workspace / "calibrations" / f"{calibration_id}.json"
    review_bytes = review_path.read_bytes()
    review = json.loads(review_bytes)
    if review.get("kind") != "agent-scientific-review" or review.get("verdict") != "approved":
        raise DataBlockedError(
            "radar admission requires kind=agent-scientific-review and verdict=approved"
        )
    if not review.get("reviewer") or not review.get("reviewed_at") or not review.get("checks"):
        raise DataBlockedError("radar review requires reviewer, reviewed_at and nonempty checks")
    if (
        review.get("calibration_sha256")
        != hashlib.sha256(calibration_path.read_bytes()).hexdigest()
    ):
        raise DataBlockedError(
            "radar review calibration_sha256 differs from the inspected calibration"
        )
    annotation = calibration["radar_annotation_fit"]
    try:
        manifest_path = resolve_workspace_path(workspace, annotation["path"])
    except ValueError as error:
        raise DataBlockedError(
            "registered radar manifest must remain inside the workspace or its data tree"
        ) from error
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if review.get("manifest_sha256") != manifest_hash or annotation["sha256"] != manifest_hash:
        raise DataBlockedError(
            "radar review manifest_sha256 differs from the fitted annotation manifest"
        )
    verdicts = review.get("radars")
    if not isinstance(verdicts, dict) or not verdicts or set(verdicts) - {"AOP", "ISK0", "ISK1"}:
        raise DataBlockedError("radar review must name approved registered radars")
    for sensor, verdict in verdicts.items():
        if verdict != "approved" or not calibration["radars"][sensor].get(
            "engineering_gates_passed"
        ):
            raise DataBlockedError(
                f"{sensor} admission requires an approved review and passed engineering gates"
            )
    record = {
        "kind": "agent-scientific-review",
        "sha256": hashlib.sha256(review_bytes).hexdigest(),
        "reviewer": review["reviewer"],
        "reviewed_at": review["reviewed_at"],
        "checks": review["checks"],
        "manifest_sha256": manifest_hash,
        "calibration_sha256": review["calibration_sha256"],
    }
    for sensor in verdicts:
        calibration["radars"][sensor].update(
            certified=True,
            reason="engineering gates and independent agent scientific annotation review passed",
            review=record,
        )
    calibration["parent_calibration_id"] = calibration_id
    calibration["created_at"] = datetime.now(UTC).isoformat()
    calibration["radar_annotation_fit"]["review_status"] = "agent-reviewed"
    calibration["radar_review"] = record
    calibration["external_human_review"] = "pending"
    calibration["blockers"] = [
        f"{sensor}: {entry['reason']}"
        for sensor, entry in calibration["radars"].items()
        if not entry["certified"]
    ]
    calibration["status"] = "blocked" if calibration["blockers"] else "certified"
    if "radar" not in calibration["certified_modalities"]:
        calibration["certified_modalities"].append("radar")
    return _save_calibration(workspace, calibration)


def prepare_radar_annotations(workspace: Path) -> dict[str, Any]:
    """Select 30 scans per radar and enumerate candidates without claiming reviewed labels."""
    selected_by_sensor: dict[str, list[dict[str, Any]]] = {
        sensor: [] for sensor in ["AOP", "ISK0", "ISK1"]
    }
    for session_id, split in PRIMARY_SPLITS.items():
        if split != "development":
            continue
        times, positions = _reference(workspace, "roboloc-g", session_id)
        for frame in _radar_frames(workspace, session_id, 0):
            if times[0] <= frame["t"] <= times[-1]:
                selected_by_sensor[frame["sensor_id"]].append(
                    {
                        **frame,
                        "session_id": session_id,
                        "development_reference_origin_m": [
                            float(np.interp(frame["t"], times, positions[:, axis]))
                            for axis in range(3)
                        ],
                    }
                )
    manifest: dict[str, Any] = {
        "dataset_id": "roboloc-g",
        "split": "development",
        "review_status": "pending",
        "annotation_method": "automated candidate enumeration; no target labels assigned",
        "selection": "30 uniform indices across reference-supported development scans per radar; every third index check, others fit",
        "radars": {},
    }
    for sensor, frames in selected_by_sensor.items():
        frames.sort(key=lambda frame: (frame["t"], frame["scan_id"]))
        if len(frames) < 30:
            raise DataBlockedError(
                f"{sensor} has fewer than 30 reference-supported development scans"
            )
        selected = []
        for index, frame_index in enumerate(np.linspace(0, len(frames) - 1, 30, dtype=int)):
            frame = frames[frame_index]
            clusters = cluster_frame(np.array(frame["points"]), radius=0.2, min_samples=3)
            selected.append(
                {
                    "session_id": frame["session_id"],
                    "scan_id": frame["scan_id"],
                    "role": "check" if index % 3 == 2 else "fit",
                    "t_unix_s": frame["t"],
                    "source_rows": frame["source_rows"],
                    "points_native_m": frame["points"],
                    "development_reference_origin_m": frame["development_reference_origin_m"],
                    "candidate_centroids_native_m": [
                        cluster.centroid.tolist() for cluster in clusters
                    ],
                    "candidate_distinct_returns": [
                        cluster.distinct_returns for cluster in clusters
                    ],
                    "selected_candidate": None,
                    "review_status": "pending",
                }
            )
        manifest["radars"][sensor] = {"radius_m": 0.2, "min_samples": 3, "scans": selected}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:12]
    directory = workspace / "data" / "annotations"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"roboloc-g-candidates-{digest}.json"
    if not path.exists():
        with path.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2)
    return {
        "path": str(path.relative_to(workspace)).replace("\\", "/"),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "review_status": "pending",
        "target_labels_assigned": False,
        "scans_per_radar": 30,
        "fit_scans": 20,
        "check_scans": 10,
    }


def fit_radar_annotations(workspace: Path, candidates: dict[str, Any]) -> dict[str, Any]:
    """Fit bounded, deterministic development-only radar correspondences and review them."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    candidate_path = workspace / candidates["path"]
    manifest = json.loads(candidate_path.read_text(encoding="utf-8"))
    if hashlib.sha256(candidate_path.read_bytes()).hexdigest() != candidates["sha256"]:
        raise DataBlockedError("radar candidate manifest hash differs from calibration input")
    results = {}
    for sensor, entry in manifest["radars"].items():
        scans = entry["scans"]
        fit_indices = [index for index, scan in enumerate(scans) if scan["role"] == "fit"]
        grid = []
        fitted_candidates = []
        for radius, minimum in itertools.product([0.1, 0.2, 0.4], [3, 5, 10]):
            clusters = [
                cluster_frame(np.array(scan["points_native_m"]), radius=radius, min_samples=minimum)
                for scan in scans
            ]
            centroids = [
                np.array([cluster.centroid for cluster in found]).reshape(-1, 3)
                for found in clusters
            ]
            supported_indices = [index for index in fit_indices if len(centroids[index])]
            target = np.array(
                [scans[index]["development_reference_origin_m"] for index in supported_indices]
            )
            record: dict[str, Any] = {
                "radius_m": radius,
                "min_samples": minimum,
                "nonempty_fit_scans": len(supported_indices),
            }
            if len(supported_indices) < 6:
                grid.append({**record, "status": "insufficient nonempty fit scans"})
                continue
            cluster_counts = np.array([len(centroids[index]) for index in supported_indices])
            starts = np.r_[0, np.cumsum(cluster_counts)[:-1]]
            point_scan = np.repeat(np.arange(len(supported_indices)), cluster_counts)
            cloud = np.concatenate([centroids[index] for index in supported_indices])
            rng = np.random.default_rng(123)
            best = None
            best_score = (-1, -np.inf)
            degenerate = 0
            for proposal in range(3000):
                proposal_count = proposal + 1
                sampled_indices = rng.choice(len(supported_indices), 3, replace=False)
                sample = np.array(
                    [
                        centroids[supported_indices[index]][rng.integers(cluster_counts[index])]
                        for index in sampled_indices
                    ]
                )
                try:
                    fitted = fit_radar_calibration(
                        sample, target[sampled_indices], split="development"
                    )
                except ValueError:
                    degenerate += 1
                    continue
                distances_squared = np.sum((fitted.apply(cloud) - target[point_scan]) ** 2, axis=1)
                nearest_squared = np.minimum.reduceat(distances_squared, starts)
                score = (
                    int(np.sum(nearest_squared < 0.6**2)),
                    -float(np.mean(np.sort(nearest_squared)[: max(6, len(target) // 2)])),
                )
                if score > best_score:
                    best_score, best = score, fitted
                if score[0] == len(supported_indices) and -score[1] < 1e-20:
                    break
            if best is None or best_score[0] < 6:
                grid.append(
                    {
                        **record,
                        "status": "fewer than six supported fit correspondences",
                        "best_support": best_score[0],
                        "degenerate_proposals": degenerate,
                    }
                )
                continue
            for _ in range(10):
                source_pairs, target_pairs, inlier_indices = [], [], []
                for index in supported_indices:
                    distance = np.linalg.norm(
                        best.apply(centroids[index])
                        - scans[index]["development_reference_origin_m"],
                        axis=1,
                    )
                    nearest = int(np.argmin(distance))
                    if distance[nearest] < 0.6:
                        source_pairs.append(centroids[index][nearest])
                        target_pairs.append(scans[index]["development_reference_origin_m"])
                        inlier_indices.append(index)
                if len(source_pairs) < 6:
                    break
                best = fit_radar_calibration(
                    np.array(source_pairs), np.array(target_pairs), split="development"
                )
            distances_squared = np.sum((best.apply(cloud) - target[point_scan]) ** 2, axis=1)
            nearest_squared = np.minimum.reduceat(distances_squared, starts)
            score = (
                int(np.sum(nearest_squared < 0.6**2)),
                -float(np.mean(np.sort(nearest_squared)[: max(6, len(target) // 2)])),
            )
            record.update(
                status="fitted",
                fit_support=score[0],
                trimmed_fit_mse_m2=-score[1],
                proposals=proposal_count,
                degenerate_proposals=degenerate,
            )
            grid.append(record)
            fitted_candidates.append((score, best, centroids, clusters, record))
        if not fitted_candidates:
            results[sensor] = {
                "certified": False,
                "reason": "no clustering candidate supplied six rank-two fit correspondences",
                "grid": grid,
            }
            continue
        # The held-out check references have not been inspected during selection.
        fitted_candidates.sort(key=lambda result: result[0], reverse=True)
        _, fitted, centroids, clusters, selected_config = fitted_candidates[0]
        source_pairs, target_pairs, fit_scans = [], [], []
        for index in fit_indices:
            if not len(centroids[index]):
                continue
            distances = np.linalg.norm(
                fitted.apply(centroids[index]) - scans[index]["development_reference_origin_m"],
                axis=1,
            )
            nearest = int(np.argmin(distances))
            if distances[nearest] < 0.6:
                source_pairs.append(centroids[index][nearest])
                target_pairs.append(scans[index]["development_reference_origin_m"])
                fit_scans.append(scans[index]["session_id"])
        source_array, target_array = np.array(source_pairs), np.array(target_pairs)
        loo_residuals = []
        for index in range(len(source_array)):
            mask = np.arange(len(source_array)) != index
            leave_one_out = fit_radar_calibration(
                source_array[mask], target_array[mask], split="development"
            )
            loo_residuals.append(leave_one_out.apply(source_array[index]) - target_array[index])
        covariance = np.cov(np.array(loo_residuals), rowvar=False, ddof=1)
        gates = []
        if fitted.rank < 2 or not np.isclose(np.linalg.det(fitted.rotation), 1):
            gates.append("proper rank-two rotation required")
        if len(source_array) < 6 or len(set(fit_scans) & {"zigzag1", "zigzag2"}) < 2:
            gates.append("six fit correspondences across both moving development sessions required")
        if not np.isfinite(covariance).all() or np.linalg.eigvalsh(covariance).min() <= 0:
            gates.append("finite positive-definite leave-one-out residual covariance required")
        counts = {"fit": 0, "check": 0}
        unresolved, check_residuals, unique_check_residuals = [], [], []
        figure, axes = plt.subplots(10, 3, figsize=(15, 30), constrained_layout=True)
        for index, (scan, axis) in enumerate(zip(scans, axes.flat, strict=True)):
            points = np.array(scan["points_native_m"])
            expected = (
                np.array(scan["development_reference_origin_m"]) - fitted.translation
            ) @ fitted.rotation
            axis.scatter(points[:, 0], points[:, 1], s=5, c="0.65")
            axis.scatter([expected[0]], [expected[1]], c="blue", marker="x", s=50)
            scan["candidate_centroids_native_m"] = centroids[index].tolist()
            scan["candidate_distinct_returns"] = [
                cluster.distinct_returns for cluster in clusters[index]
            ]
            scan["label_method"] = (
                "FIT-only bounded RANSAC, nearest/Kabsch refit; check assessed once after selection"
            )
            scan["selected_candidate"] = None
            gate_m = 0.6 if scan["role"] == "fit" else 1.0
            scan["label"] = "no-supported-target"
            if len(centroids[index]):
                distance = np.linalg.norm(
                    fitted.apply(centroids[index]) - scan["development_reference_origin_m"], axis=1
                )
                nearest = int(np.argmin(distance))
                if scan["role"] == "check":
                    check_residuals.append(
                        fitted.apply(centroids[index][nearest])
                        - scan["development_reference_origin_m"]
                    )
                eligible = np.flatnonzero(distance < gate_m)
                scan["candidate_distances_m"] = distance.tolist()
                if len(eligible) == 1:
                    chosen = int(eligible[0])
                    scan["selected_candidate"] = chosen
                    scan["selected_centroid_native_m"] = centroids[index][chosen].tolist()
                    scan["label"] = "positive"
                    counts[scan["role"]] += 1
                    axis.scatter(
                        [centroids[index][chosen, 0]],
                        [centroids[index][chosen, 1]],
                        c="red",
                        marker="*",
                        s=80,
                    )
                    if scan["role"] == "check":
                        unique_check_residuals.append(
                            fitted.apply(centroids[index][chosen])
                            - scan["development_reference_origin_m"]
                        )
                elif len(eligible) > 1:
                    scan["label"] = "ambiguous"
            scan["label_reason"] = scan["label"] if len(centroids[index]) else "no DBSCAN cluster"
            if scan["label"] != "positive":
                unresolved.append(
                    {
                        "scan_id": scan["scan_id"],
                        "role": scan["role"],
                        "reason": scan["label_reason"],
                    }
                )
            axis.set_title(
                f"{index + 1}: {scan['session_id']} {scan['role']} | {scan['label_reason']}",
                fontsize=8,
            )
            axis.set_xlim(expected[0] - 2, expected[0] + 2)
            axis.set_ylim(expected[1] - 2, expected[1] + 2)
            axis.set_aspect("equal")
            axis.set_xlabel("native x [m]", fontsize=7)
            axis.set_ylabel("native y [m]", fontsize=7)
        if counts["check"] < 3:
            gates.append("three unique held-out check correspondences required")
        figure.suptitle(
            f"{sensor}: MACHINE development annotations, pending independent review. Red=candidate; blue=inverse-fit reference. Native x/y, ±2m crop."
        )
        panel = candidate_path.parent / f"{candidate_path.stem}-{sensor}-ransac.png"
        figure.savefig(panel, dpi=120)
        plt.close(figure)
        fit_residual = fitted.apply(source_array) - target_array
        check_rmse = (
            float(np.sqrt(np.mean(np.sum(np.array(check_residuals) ** 2, axis=1))))
            if check_residuals
            else None
        )
        results[sensor] = {
            "certified": False,
            "engineering_gates_passed": not gates,
            "reason": "; ".join(gates) if gates else "independent annotation review pending",
            "rotation": fitted.rotation.tolist(),
            "translation": fitted.translation.tolist(),
            "covariance": covariance.tolist(),
            "covariance_semantics": "leave-one-out supported FIT residual sample covariance; no division by return count; conditional on detected targets",
            "rank": fitted.rank,
            "fit_support": len(source_array),
            "fit_sessions": sorted(set(fit_scans)),
            "fit_rmse_m": float(np.sqrt(np.mean(np.sum(fit_residual**2, axis=1)))),
            "check_rmse_m": check_rmse,
            "check_all_nonempty_rmse_m": check_rmse,
            "check_nonempty_count": len(check_residuals),
            "check_unique_conditional_rmse_m": float(
                np.sqrt(np.mean(np.sum(np.array(unique_check_residuals) ** 2, axis=1)))
            )
            if unique_check_residuals
            else None,
            "unique_label_counts": counts,
            "unresolved_labels": unresolved,
            "fit_gate_m": 0.6,
            "check_gate_m": 1.0,
            "selected_clustering": selected_config,
            "grid": grid,
            "annotation_method": "seed123; <=3000 proper Kabsch3-pair proposals per 9 approved clustering candidates; score FIT support within.6m then trimmed FIT MSE; 10 inlier refits; CHECK evaluated once after selection",
            "gate_decision": "rank2 proper rotation; >=6 supported FIT pairs across zigzag1/2; >=3 unique CHECK pairs; positive-definite leave-one-out covariance; independent review",
            "review_panel": str(panel.relative_to(workspace)).replace("\\", "/"),
            "interpretation": "effective centroid-to-gantry fit; missing/ambiguous targets remain negative annotations; limited-FOV calibration, not full trajectory coverage",
        }
    manifest["machine_fits"] = results
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:12]
    output = candidate_path.with_name(candidate_path.stem + f"-ransac-fit-{digest}.json")
    if not output.exists():
        with output.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2)
    manifest_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    return {
        "radars": results,
        "manifest": {
            "path": str(output.relative_to(workspace)).replace("\\", "/"),
            "sha256": manifest_hash,
            "review_status": "pending-independent-review",
        },
    }
