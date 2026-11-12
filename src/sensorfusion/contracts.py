"""Shared physical observations and validated experiment settings."""

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator

FloatArray = NDArray[np.float64]
Method = Literal["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8"]
Modality = Literal["uwb", "ftm", "imu", "radar"]


@dataclass(frozen=True)
class Observation:
    """World observations; radar values are a centroid (3,) or scan cloud (N,3)."""

    t: float
    arrival: float
    modality: Modality
    sensor_id: str
    value: FloatArray
    covariance: FloatArray
    anchor: FloatArray | None = None
    offset: FloatArray = field(default_factory=lambda: np.zeros(3))
    source_row: int = 0
    frame: str = "world"


@dataclass
class Session:
    """Reference arrays belong to evaluation, never estimator arguments."""

    dataset_id: str
    session_id: str
    observations: list[Observation]
    reference_times: FloatArray
    reference_positions: FloatArray
    anchors: dict[str, FloatArray]
    radar_frames: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class FaultConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["none", "dropout", "range_bias", "delay", "imu_bias", "radar_shift"] = "none"
    level: float = Field(default=0.0, ge=0.0, le=10.0)
    seed: int = Field(default=0, ge=0, le=2**32 - 1)
    start_s: float | None = Field(default=None, ge=0.0)
    duration_s: float = Field(default=2.0, gt=0.0, le=60.0)
    axis: Literal[0, 1, 2] = 0
    sensor_id: str | None = None


class FactorConfig(BaseModel):
    """Registered exploratory factors; these do not rename a v1 estimator."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    propagation: Literal["cv", "inertial"]
    radar: bool
    gating: bool


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dataset_id: Literal["fixture", "roboloc-g", "miluv"] = "fixture"
    session_id: str = Field(default="helix", pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    method: Method = "M7"
    radar_sensor: Literal["AOP", "ISK0", "ISK1"] = "AOP"
    radar_cluster_radius_m: float = Field(default=0.2, gt=0.0, le=2.0)
    radar_min_samples: int = Field(default=3, ge=1, le=100)
    calibration_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    q_cv: float = Field(default=0.1, ge=0.0, le=100.0)
    acceleration_noise: float = Field(default=0.01, gt=0.0, le=100.0)
    bias_diffusion: float = Field(default=1e-6, ge=0.0, le=1.0)
    gate_probability: float = Field(default=0.999, gt=0.5, lt=1.0)
    wls_window_s: float = Field(default=0.2, gt=0.0, le=1.0)
    output_hz: float = Field(default=20.0, ge=1.0, le=100.0)
    duration_s: float = Field(default=20.0, ge=5.0, le=300.0)
    seed: int = Field(default=7, ge=0, le=2**32 - 1)
    fault: FaultConfig = Field(default_factory=FaultConfig)
    factors: FactorConfig | None = None

    @model_validator(mode="after")
    def validate_dataset_method(self) -> "RunConfig":
        if self.dataset_id == "miluv" and self.method not in ("M1", "M2"):
            raise ValueError("MILUV external study supports only M1 and M2 range estimators")
        if self.factors is not None:
            expected = "M3" if self.factors.propagation == "cv" else "M6"
            if self.dataset_id != "roboloc-g" or self.method != expected:
                raise ValueError("exploratory factors require RoboLoc-G with M3/CV or M6/inertial")
            if self.radar_sensor != "AOP" or self.fault.kind != "none":
                raise ValueError("exploratory factors require AOP and unmodified observations")
        return self


@dataclass
class Estimate:
    times: FloatArray
    states: FloatArray
    covariances: FloatArray
    innovations: list[dict[str, Any]]
    metadata: dict[str, Any] = field(default_factory=dict)
