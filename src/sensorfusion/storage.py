"""Atomic run manifests and registered numerical artefacts."""

import errno
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Any

import h5py
import numpy as np
from scipy.stats import chi2

from sensorfusion.contracts import Estimate, RunConfig, Session

ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,100}$")


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def json_safe(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist())
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def canonical_hash(value: Any) -> str:
    payload = json.dumps(json_safe(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(json_safe(value), indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temp, path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def registered_path(root: Path, identifier: str) -> Path:
    if not ID_PATTERN.fullmatch(identifier):
        raise ValueError(f"invalid registered identifier {identifier!r}")
    path = root / identifier
    if path.resolve().parent != root.resolve():
        raise ValueError("registered identifier resolves outside the registry")
    return path


def resolve_workspace_path(workspace: Path, relative_name: str) -> Path:
    """Keep registered paths inside the workspace or its mounted data tree."""
    if (
        not isinstance(relative_name, str)
        or not relative_name
        or "\\" in relative_name
        or ":" in relative_name
        or "\x00" in relative_name
        or PurePosixPath(relative_name).is_absolute()
        or any(part in ("", ".", "..") for part in relative_name.split("/"))
    ):
        raise ValueError(f"path must be canonical and workspace-relative: {relative_name!r}")
    relative = PurePosixPath(relative_name)
    workspace_root = workspace.resolve()
    boundary = (
        (workspace_root / "data").resolve() if relative.parts[0] == "data" else workspace_root
    )
    path = (workspace_root / relative).resolve()
    if not path.is_relative_to(boundary):
        raise ValueError(f"workspace path escapes its registered root: {relative_name!r}")
    return path


def provenance(config: RunConfig, session: Session) -> dict:
    try:
        source = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parents[2],
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        source = "unavailable"
    return {
        "created_at": timestamp(),
        "source_revision": source,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "dependencies": {
            name: version(name) for name in ("numpy", "scipy", "scikit-learn", "pydantic", "h5py")
        },
        "config_hash": canonical_hash(config.model_dump()),
        "dataset_id": session.dataset_id,
        "session_id": session.session_id,
        "synthetic": session.metadata.get("synthetic", False),
        "session": session.metadata,
        "reference_used_by_estimator": False,
        "calibration_id": config.calibration_id,
    }


def save_estimate(
    directory: Path,
    estimate: Estimate,
    session: Session,
    reference: np.ndarray,
    errors: np.ndarray,
) -> None:
    temp = directory / "numerical.h5.partial"
    with h5py.File(temp, "w") as h5:
        h5.attrs["schema_version"] = "1"
        h5.attrs["length_unit"] = "m"
        h5.attrs["time_unit"] = "s"
        for name, value in (
            ("times", estimate.times),
            ("states", estimate.states),
            ("covariances", estimate.covariances),
            ("reference_positions", reference),
            ("position_errors", errors),
        ):
            h5.create_dataset(name, data=value, compression="gzip", shuffle=True)
        group = h5.create_group("anchors")
        for name, position in session.anchors.items():
            group.create_dataset(name, data=position)
        associations = estimate.metadata.get("radar_associations", [])
        if associations:
            group = h5.create_group("radar_associations")
            group.create_dataset("times", data=[row["time"] for row in associations])
            group.create_dataset(
                "sensor_ids",
                data=[row["sensor_id"] for row in associations],
                dtype=h5py.string_dtype(),
            )
            group.create_dataset(
                "reasons", data=[row["reason"] for row in associations], dtype=h5py.string_dtype()
            )
            group.create_dataset(
                "centroids",
                data=[
                    row["centroid"] if row.get("centroid") is not None else [np.nan] * 3
                    for row in associations
                ],
            )
    os.replace(temp, directory / "numerical.h5")
    write_json(
        directory / "innovations.json",
        {"innovations": estimate.innovations, "metadata": estimate.metadata},
    )
    _save_radar(directory, session)


def _save_radar(directory: Path, session: Session) -> None:
    frames = [
        {**frame, "time": frame.get("time", frame.get("t"))} for frame in session.radar_frames
    ]
    identity = canonical_hash({"frames": frames})
    shared = directory.parent.parent / "session-evidence" / identity
    shared.mkdir(parents=True, exist_ok=True)
    if not (shared / "radar.json").exists():
        write_json(shared / "radar.json", {"frames": frames})
    if not (shared / "radar.h5").exists():
        lengths = [len(frame["points"]) for frame in frames]
        points = (
            np.concatenate([np.asarray(frame["points"]).reshape(-1, 3) for frame in frames])
            if frames
            else np.empty((0, 3))
        )
        with h5py.File(shared / "radar.h5.partial", "w") as h5:
            h5.create_dataset("times", data=[frame["time"] for frame in frames])
            h5.create_dataset(
                "sensor_ids",
                data=[frame["sensor_id"] for frame in frames],
                dtype=h5py.string_dtype(),
            )
            h5.create_dataset(
                "frames", data=[frame["frame"] for frame in frames], dtype=h5py.string_dtype()
            )
            h5.create_dataset("offsets", data=np.r_[0, np.cumsum(lengths)])
            h5.create_dataset("points", data=points, compression="gzip", shuffle=True)
            h5.create_dataset(
                "centroids",
                data=np.array(
                    [
                        frame.get("centroid") if frame.get("centroid") is not None else [np.nan] * 3
                        for frame in frames
                    ]
                ).reshape(-1, 3),
            )
            h5.attrs["length_unit"] = "m"
            h5.attrs["time_unit"] = "s"
        os.replace(shared / "radar.h5.partial", shared / "radar.h5")
    for name in ("radar.json", "radar.h5"):
        try:
            os.link(shared / name, directory / name)
        except OSError as error:
            if error.errno not in (errno.EXDEV, errno.EPERM, errno.EACCES, errno.ENOTSUP):
                raise
            shutil.copyfile(shared / name, directory / name)


def artefact_manifest(directory: Path) -> list[dict]:
    artefacts = []
    allowed = {
        "numerical.h5": "application/x-hdf5",
        "config.json": "application/json",
        "provenance.json": "application/json",
        "metrics.json": "application/json",
        "innovations.json": "application/json",
        "radar.json": "application/json",
        "radar.h5": "application/x-hdf5",
        "calibration.json": "application/json",
        "report.html": "text/html",
        "trajectory.csv": "text/csv",
        "position_error.png": "image/png",
    }
    for name, media_type in allowed.items():
        path = directory / name
        if path.is_file():
            artefacts.append(
                {
                    "id": name,
                    "name": name,
                    "media_type": media_type,
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    return artefacts


def trajectory(directory: Path, max_points: int = 2000) -> dict:
    if not 10 <= max_points <= 20000:
        raise ValueError("max_points must be between 10 and 20000")
    with h5py.File(directory / "numerical.h5", "r") as h5:
        count = len(h5["times"])
        indices = np.unique(np.linspace(0, max(0, count - 1), min(count, max_points), dtype=int))
        states = h5["states"][indices]
        covariances = h5["covariances"][indices]
        position_covariances = covariances[:, :3, :3]

        def vectors(values):
            return [row.tolist() if np.all(np.isfinite(row)) else None for row in values]

        def confidence_axes(values, dimension):
            axes: list = []
            quantile = chi2.ppf(0.95, dimension)
            for covariance in values:
                if not np.all(np.isfinite(covariance)):
                    axes.append(None)
                    continue
                eigenvalues, eigenvectors = np.linalg.eigh(covariance)
                tolerance = 1e-10 * max(1, float(np.max(np.abs(covariance))))
                if eigenvalues[0] < -tolerance:
                    axes.append(None)
                    continue
                axes.append(
                    (eigenvectors * np.sqrt(np.maximum(eigenvalues, 0) * quantile)).tolist()
                )
            return axes

        result = {
            "times": h5["times"][indices],
            "positions": vectors(states[:, :3]),
            "reference_positions": vectors(h5["reference_positions"][indices]),
            "covariance_diagonal": np.diagonal(covariances[:, :3, :3], axis1=1, axis2=2),
            "velocities": vectors(states[:, 3:6]) if states.shape[1] >= 6 else [],
            "biases": vectors(states[:, 6:9]) if states.shape[1] == 9 else [],
            "position_covariances": json_safe(position_covariances),
            "confidence_ellipsoid_axes": confidence_axes(position_covariances, 3),
            "confidence_ellipse_xy_axes": confidence_axes(position_covariances[:, :2, :2], 2),
            "confidence_ellipse_xz_axes": confidence_axes(
                position_covariances[:, [0, 2]][:, :, [0, 2]], 2
            ),
            "confidence_probability": 0.95,
            "position_errors": h5["position_errors"][indices],
            "anchors": [{"id": name, "position": h5["anchors"][name][:]} for name in h5["anchors"]],
            "decimated": count > max_points,
            "total_points": count,
            "frame": "world",
            "length_unit": "m",
            "time_unit": "s",
        }
    return json_safe(result)
