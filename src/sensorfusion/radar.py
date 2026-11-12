"""Per-frame radar clustering and prediction-only single-centroid association."""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy.stats import chi2
from sklearn.cluster import DBSCAN

from sensorfusion.contracts import FloatArray
from sensorfusion.models import validate_covariance


def spherical_to_cartesian(
    ranges: FloatArray, azimuth: FloatArray, elevation: FloatArray
) -> FloatArray:
    """RoboLoc-G native convention: x=r sin(a) cos(e), y=r cos(a) cos(e)."""
    ranges, azimuth, elevation = np.broadcast_arrays(ranges, azimuth, elevation)
    if (
        not np.isfinite(ranges).all()
        or not np.isfinite(azimuth).all()
        or not np.isfinite(elevation).all()
    ):
        raise ValueError("radar spherical coordinates must be finite")
    if np.any(ranges < 0):
        raise ValueError("radar slant ranges must be nonnegative metres")
    return np.column_stack(
        (
            ranges * np.cos(elevation) * np.sin(azimuth),
            ranges * np.cos(elevation) * np.cos(azimuth),
            ranges * np.sin(elevation),
        )
    )


@dataclass(frozen=True)
class RadarCluster:
    centroid: FloatArray
    distinct_returns: int


def cluster_frame(points: FloatArray, *, radius: float, min_samples: int) -> list[RadarCluster]:
    """DBSCAN one scan; identical duplicate returns are not independent support."""
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError(f"radar points must have finite shape (N,3), got {points.shape}")
    if not np.isfinite(radius) or radius <= 0 or min_samples < 1:
        raise ValueError("DBSCAN radius must be positive metres and min_samples>=1")
    if points.shape[0] == 0:
        return []
    return [
        RadarCluster(np.array(centroid), count)
        for centroid, count in _cluster_scan(points.tobytes(), radius, min_samples)
    ]


@lru_cache(maxsize=4096)
def _cluster_scan(payload: bytes, radius: float, min_samples: int) -> tuple:
    """Repeated ablations share immutable clustering, never state-dependent association."""
    distinct = np.unique(np.frombuffer(payload, dtype=np.float64).reshape(-1, 3), axis=0)
    labels = DBSCAN(eps=radius, min_samples=min_samples).fit_predict(distinct)
    return tuple(
        (tuple(distinct[labels == label].mean(axis=0)), int(np.sum(labels == label)))
        for label in sorted(set(labels) - {-1})
    )


@dataclass(frozen=True)
class RadarAssociation:
    accepted: bool
    reason: str
    centroid: FloatArray | None = None
    covariance: FloatArray | None = None
    nis: float | None = None
    candidate_count: int = 0


def associate_centroid(
    clusters: list[RadarCluster],
    predicted_position: FloatArray,
    position_covariance: FloatArray,
    observation_covariance: FloatArray,
    *,
    gate_probability: float = 0.999,
) -> RadarAssociation:
    """Associate only a unique gated cluster, retaining empirical covariance [m²]."""
    if np.shape(predicted_position) != (3,) or not np.isfinite(predicted_position).all():
        raise ValueError("radar prediction must have finite shape (3,)")
    if position_covariance.shape != (3, 3) or observation_covariance.shape != (3, 3):
        raise ValueError("radar covariances must have shape (3,3)")
    validate_covariance(position_covariance)
    validate_covariance(observation_covariance, positive=True)
    if not 0.5 < gate_probability < 1:
        raise ValueError("radar gate probability must lie in (0.5,1)")
    if not clusters:
        return RadarAssociation(False, "no_clusters")
    innovation_covariance = position_covariance + observation_covariance
    threshold = float(chi2.ppf(gate_probability, 3))
    candidates = []
    for cluster in clusters:
        residual = cluster.centroid - predicted_position
        nis = float(residual @ np.linalg.solve(innovation_covariance, residual))
        if nis <= threshold:
            candidates.append((cluster, nis))
    if not candidates:
        return RadarAssociation(False, "outside_gate")
    if len(candidates) > 1:
        return RadarAssociation(False, "ambiguous", candidate_count=len(candidates))
    cluster, nis = candidates[0]
    return RadarAssociation(
        True, "associated", cluster.centroid.copy(), observation_covariance.copy(), nis, 1
    )
