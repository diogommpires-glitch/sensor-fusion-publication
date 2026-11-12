"""Reconstruct the development-only acceleration diagnostic from pinned measurements."""

import json
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.signal import savgol_filter

from sensorfusion.data import _imu_rows, _reference
from sensorfusion.storage import timestamp, write_json

root = Path("workspace")
calibration = json.loads(
    (root / "calibrations/roboloc-g-development-9cd4f70009fc.json").read_text()
)
reference_values, measured_values = [], []
for session_id in ("zigzag1", "zigzag2"):
    times, positions = _reference(root, "roboloc-g", session_id)
    _, imu_times, acceleration = _imu_rows(root, session_id)
    grid = np.arange(times[0], times[-1], 0.1)
    regular = np.column_stack([np.interp(grid, times, positions[:, axis]) for axis in range(3)])
    derivative = savgol_filter(regular, window_length=11, polyorder=3, deriv=2, delta=0.1, axis=0)
    spline = CubicSpline(grid, derivative, extrapolate=False)
    eligible = (imu_times >= grid[0] + 0.6) & (imu_times <= grid[-1] - 0.6)
    for left, right in zip(times[:-1], times[1:], strict=True):
        if right - left > 0.5:
            eligible &= ~((imu_times >= left - 0.6) & (imu_times <= right + 0.6))
    measured_values.extend(acceleration[eligible])
    reference_values.extend(spline(imu_times[eligible]))
reference = np.asarray(reference_values)
measured = np.asarray(measured_values)
imu = calibration["imu"]
corrected = (measured - np.asarray(imu["stationary_mean"])) @ np.asarray(imu["rotation"]).T
diagnostic = {
    "created_at": timestamp(),
    "calibration_id": calibration["id"],
    "fit_sessions": ["zigzag1", "zigzag2"],
    "sample_count": len(reference),
    "fitted_residual_rmse_m_s2": float(
        np.sqrt(np.mean(np.sum((corrected - reference) ** 2, axis=1)))
    ),
    "zero_acceleration_reference_rmse_m_s2": float(np.sqrt(np.mean(np.sum(reference**2, axis=1)))),
    "noise_density_m2_s3": imu["acceleration_noise_density_m2_s3"],
    "derivative_protocol": imu["differentiation"],
    "raw_sources": calibration["source_hashes"],
    "interpretation": "Development diagnostic, not final positioning accuracy. The IMU fit is ineffective on this reference.",
}
write_json(Path("evidence/imu_diagnostic.json"), diagnostic)
print(json.dumps(diagnostic, indent=2))
