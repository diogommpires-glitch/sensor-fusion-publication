"""Regenerate portable figures and a standalone report from saved numerical evidence."""

import base64
import csv
import html
import io
import json
from pathlib import Path

import h5py
import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt

from sensorfusion.storage import read_json


def generate_report(directory: Path, destination: Path | None = None) -> Path:
    status_path = directory / "status.json"
    if (
        destination is None
        and status_path.exists()
        and read_json(status_path)["status"] == "completed"
    ):
        raise ValueError("Completed evidence is immutable; export the report to a new directory")
    output = directory if destination is None else destination
    if destination is not None:
        output.mkdir(parents=True, exist_ok=False)
    metrics = read_json(directory / "metrics.json")
    provenance = read_json(directory / "provenance.json")
    config = read_json(directory / "config.json")
    with h5py.File(directory / "numerical.h5", "r") as h5:
        times = h5["times"][:]
        states = h5["states"][:]
        reference = h5["reference_positions"][:]
        errors = h5["position_errors"][:]
    with (output / "trajectory.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "time_s",
                "x_m",
                "y_m",
                "z_m",
                "reference_x_m",
                "reference_y_m",
                "reference_z_m",
                "position_error_m",
            ]
        )
        writer.writerows(
            [float(t), *state[:3], *truth, float(error)]
            for t, state, truth, error in zip(times, states, reference, errors, strict=True)
        )
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), layout="constrained")
    axes[0].plot(reference[:, 0], reference[:, 1], label="Reference", color="#687a83")
    axes[0].plot(states[:, 0], states[:, 1], label=config["method"], color="#a4472d")
    axes[0].set(xlabel="x / m", ylabel="y / m", title="Horizontal projection", aspect="equal")
    axes[0].legend()
    axes[1].plot(times, errors, color="#a4472d", linewidth=1)
    axes[1].set(xlabel="Time / s", ylabel="3D position error / m", title="Position error")
    for ax in axes:
        ax.grid(alpha=0.2)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=150)
    plt.close(fig)
    image = buffer.getvalue()
    (output / "position_error.png").write_bytes(image)
    notice = (
        "Synthetic validation fixture. These results do not establish real-data positioning accuracy."
        if provenance.get("synthetic")
        else "Recorded-data result. Interpret it within the documented calibration, split and reference limits."
    )
    rows = "".join(
        f"<tr><th>{html.escape(key)}</th><td>{html.escape(str(value))}</td></tr>"
        for key, value in metrics.items()
        if key != "notes"
    )
    contents = (
        "<!doctype html><html lang='en'><meta charset='utf-8'><title>Sensor Fusion report</title>"
        "<style>body{font:16px/1.6 system-ui;max-width:1000px;margin:3rem auto;padding:0 1rem;color:#24343c}"
        "table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:.4rem;border-bottom:1px solid #ccd4d5}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f1eb;padding:1rem}img{max-width:100%}"
        ".notice{border-left:4px solid #a4472d;padding:1rem;background:#fbf0e7}</style>"
        "<h1>Sensor Fusion</h1>"
        f"<p class='notice'>{html.escape(notice)}</p>"
        f"<p>{html.escape(config['dataset_id'])} / {html.escape(config['session_id'])} / {html.escape(config['method'])}</p>"
        f"<img alt='Trajectory and measured position errors' src='data:image/png;base64,{base64.b64encode(image).decode()}'>"
        f"<h2>Measured results</h2><table>{rows}</table>"
        "<h2>Interpretation</h2><p>Position-only reference supports position error and empirical ellipsoid coverage. "
        "It does not establish full-state NEES, independent timestamps or deployment readiness.</p>"
        f"<h2>Resolved configuration</h2><pre>{html.escape(json.dumps(config, indent=2))}</pre>"
        f"<h2>Provenance</h2><pre>{html.escape(json.dumps(provenance, indent=2))}</pre></html>"
    )
    report_path = output / "report.html"
    report_path.write_text(contents, encoding="utf-8")
    return report_path
