"""Export immutable saved study evidence to a fresh descriptive report directory."""

import argparse
import base64
import csv
import hashlib
import html
import itertools
import json
from collections import Counter
from pathlib import Path
from typing import Any

import h5py
import matplotlib
import numpy as np

matplotlib.use("Agg")
from matplotlib import pyplot as plt

from sensorfusion.sources import SOURCES
from sensorfusion.storage import canonical_hash, registered_path

FAULT_UNITS = {
    "none": "",
    "dropout": "s",
    "delay": "s",
    "range_bias": "m",
    "radar_shift": "m",
    "imu_bias": "m/s²",
}

RECOVERY_POLICY = (
    "Descriptive policy fixed before inspecting final scores: use every eligible saved 20 Hz "
    "pre-episode position-error sample, requiring at least 20 samples, to set its 95th percentile. "
    "Recovery is the start of the first full one-second contiguous interval after episode end "
    "whose samples are all finite and at or below that threshold. Missing samples break the "
    "interval. Report right censoring at the last saved sample if no interval is confirmed. "
    "Outage RMSE uses all finite saved errors during [episode start, episode end), with eligible "
    "and total sample counts. These are descriptive outcomes, not independent-seed inference."
)


def fault_unit(kind: str) -> str:
    try:
        return FAULT_UNITS[kind]
    except KeyError as error:
        raise ValueError(f"unknown fault kind has no declared physical unit: {kind!r}") from error


def fault_outcomes(times: np.ndarray, errors: np.ndarray, fault: dict) -> dict:
    """Compute episode error and a fixed, explicitly censored recovery definition."""
    if times.ndim != 1 or errors.shape != times.shape or np.any(np.diff(times) <= 0):
        raise ValueError("saved times/errors must be matching vectors with increasing times")
    if not np.isfinite(times).all():
        raise ValueError("saved times must be finite")
    result: dict[str, Any] = {
        "outage_rmse_m": None,
        "outage_eligible_samples": 0,
        "outage_total_samples": 0,
        "baseline_samples": 0,
        "recovery_threshold_m": None,
        "recovery_s": None,
        "recovery_confirmed_at_s": None,
        "recovery_censor_s": None,
        "recovery_status": "not_applicable",
    }
    if fault.get("kind", "none") == "none":
        return result
    if fault.get("injected") is not True:
        result["recovery_status"] = "skipped" if fault.get("skipped") else "unconfirmed_injection"
        return result
    episode_start = float(fault["start_s"])
    episode_end = episode_start + float(fault["duration_s"])
    if not np.isfinite([episode_start, episode_end]).all() or episode_end <= episode_start:
        raise ValueError("saved fault episode must have finite start and positive duration")
    episode = (times >= episode_start) & (times < episode_end)
    eligible_episode = episode & np.isfinite(errors)
    result["outage_total_samples"] = int(episode.sum())
    result["outage_eligible_samples"] = int(eligible_episode.sum())
    if eligible_episode.any():
        result["outage_rmse_m"] = float(np.sqrt(np.mean(errors[eligible_episode] ** 2)))
    baseline = errors[(times < episode_start) & np.isfinite(errors)]
    result["baseline_samples"] = len(baseline)
    if len(baseline) < 20:
        result["recovery_status"] = "unavailable_baseline"
        return result
    threshold = float(np.quantile(baseline, 0.95))
    result["recovery_threshold_m"] = threshold
    result["recovery_status"] = "right_censored"
    result["recovery_censor_s"] = max(0.0, float(times[-1]) - episode_end)
    interval_start: float | None = None
    previous_time: float | None = None
    for sample_time, sample_error in zip(times, errors, strict=True):
        if sample_time < episode_end:
            continue
        continuous = previous_time is not None and np.isclose(
            sample_time - previous_time, 0.05, rtol=0, atol=1e-7
        )
        if not np.isfinite(sample_error) or sample_error > threshold:
            interval_start = None
        elif interval_start is None or not continuous:
            interval_start = float(sample_time)
        if interval_start is not None and sample_time - interval_start >= 1 - 1e-7:
            result.update(
                recovery_status="recovered",
                recovery_s=interval_start - episode_end,
                recovery_confirmed_at_s=float(sample_time),
                recovery_censor_s=None,
            )
            return result
        previous_time = float(sample_time)
    return result


def read_json(input_file: Path) -> dict:
    return json.loads(input_file.read_text(encoding="utf-8"))


def fingerprint(input_file: Path) -> str:
    digest = hashlib.sha256()
    with input_file.open("rb") as input_stream:
        for block in iter(lambda: input_stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checked_artifact(run_directory: Path, manifest: list[dict], name: str) -> Path:
    matches = [entry for entry in manifest if entry["name"] == name]
    if len(matches) != 1:
        raise ValueError(f"run {run_directory.name}: require one registered {name}")
    artifact_file = run_directory / name
    if not artifact_file.resolve().is_relative_to(run_directory.resolve()):
        raise ValueError(f"registered artefact escapes run directory: {name}")
    if fingerprint(artifact_file) != matches[0]["sha256"]:
        raise ValueError(f"run {run_directory.name}: checksum differs for {name}")
    return artifact_file


def write_csv(output_file: Path, rows: list[dict]) -> None:
    columns = list(dict.fromkeys(key for row in rows for key in row)) or ["notice"]
    with output_file.open("x", newline="", encoding="utf-8") as output_stream:
        writer = csv.DictWriter(output_stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def save_figure(figure: Any, output_file: Path) -> None:
    figure.savefig(output_file, dpi=150)
    plt.close(figure)


def export_study(workspace: Path, study_id: str, destination: Path) -> Path:
    """Read saved registered evidence only; refuse an existing output directory."""
    workspace_root = workspace.resolve()
    output_directory = destination.resolve()
    if output_directory.exists():
        raise FileExistsError(f"export destination already exists: {output_directory}")
    for registry_name in ("studies", "runs", "data", "calibrations"):
        if output_directory.is_relative_to(workspace_root / registry_name):
            raise ValueError("export destination must remain outside immutable input registries")
    study_directory = registered_path(workspace_root / "studies", study_id)
    study_file = study_directory / "study.json"
    saved_study = read_json(study_file)
    saved_protocol = read_json(study_directory / "protocol.json")
    saved_results = saved_study.get("results")
    if saved_results is not None and not isinstance(saved_results, dict):
        raise ValueError("saved study results must be an object or null")
    if (
        canonical_hash(saved_protocol) != saved_study["protocol_hash"]
        or canonical_hash(saved_study["protocol"]) != saved_study["protocol_hash"]
    ):
        raise ValueError("saved protocol hash or embedded protocol differs")
    principal_methods = saved_protocol["primary_comparison"]
    if (
        len(principal_methods) != 2
        or len(set(principal_methods)) != 2
        or any(method not in saved_protocol["methods"] for method in principal_methods)
    ):
        raise ValueError("principal comparison requires two distinct registered methods")
    input_records = [{"study_json_sha256": fingerprint(study_file)}]
    rows: list[dict] = []
    association_rows: list[dict] = []
    principal: dict[tuple[str, str], tuple] = {}
    for saved_run in saved_study["runs"]:
        configuration = saved_run["config"]
        metrics = saved_run.get("metrics") or {}
        fault = {
            **configuration.get("fault", {"kind": "none"}),
            **metrics.get("fault", {}),
        }
        fault_execution = "unconfirmed"
        if fault.get("kind", "none") == "none":
            fault_execution = "not_applicable"
        elif fault.get("injected") is True:
            fault_execution = "injected"
        elif fault.get("skipped"):
            fault_execution = "skipped"
        row = {
            "run_id": saved_run["run_id"],
            "key": saved_run["key"],
            "session": configuration["session_id"],
            "method": configuration["method"],
            "radar": configuration.get("radar_sensor", "AOP"),
            "status": saved_run["status"],
            "error": saved_run.get("error"),
            "fault_kind": fault.get("kind", "none"),
            "fault_unit": fault_unit(fault.get("kind", "none")),
            "fault_level": fault.get("level", 0),
            "fault_seed": fault.get("seed", 0),
            "fault_start_s": fault.get("start_s"),
            "fault_duration_s": fault.get("duration_s"),
            "fault_injected": fault.get("injected"),
            "fault_skipped": bool(fault.get("skipped", False)),
            "fault_execution": fault_execution,
            "metrics_json": json.dumps(metrics, sort_keys=True),
        }
        row.update({name: value for name, value in metrics.items() if not isinstance(value, dict)})
        row.update(fault_outcomes(np.array([]), np.array([]), {"kind": "none"}))
        if saved_run["status"] == "completed":
            if configuration.get("output_hz", 20) != 20:
                raise ValueError("descriptive recovery export requires the registered 20 Hz grid")
            run_directory = registered_path(workspace_root / "runs", saved_run["run_id"])
            manifest_file = run_directory / "artifacts.json"
            manifest = read_json(manifest_file)["artifacts"]
            metrics_file = checked_artifact(run_directory, manifest, "metrics.json")
            config_file = checked_artifact(run_directory, manifest, "config.json")
            if canonical_hash(read_json(metrics_file)) != canonical_hash(metrics) or canonical_hash(
                read_json(config_file)
            ) != canonical_hash(configuration):
                raise ValueError("study ledger metrics/configuration differ from registered run")
            numerical_file = checked_artifact(run_directory, manifest, "numerical.h5")
            input_records.append(
                {
                    "run_id": saved_run["run_id"],
                    "manifest_sha256": fingerprint(manifest_file),
                    "numerical_sha256": fingerprint(numerical_file),
                }
            )
            with h5py.File(numerical_file, "r") as numerical:
                times = numerical["times"][:]
                errors = numerical["position_errors"][:]
                row.update(fault_outcomes(times, errors, fault))
                if "radar_associations" in numerical:
                    associations = numerical["radar_associations"]
                    counts = Counter(
                        zip(
                            associations["sensor_ids"].asstr()[:],
                            associations["reasons"].asstr()[:],
                            strict=True,
                        )
                    )
                    association_rows.extend(
                        {
                            "run_id": row["run_id"],
                            "session": row["session"],
                            "method": row["method"],
                            "sensor": sensor,
                            "reason": reason,
                            "count": count,
                        }
                        for (sensor, reason), count in sorted(counts.items())
                    )
                if (
                    row["fault_kind"] == "none"
                    and row["method"] in principal_methods
                    and row["radar"] == saved_protocol.get("radar_primary", "AOP")
                ):
                    principal[(row["session"], row["method"])] = (
                        times,
                        numerical["states"][:, :3],
                        numerical["reference_positions"][:],
                        errors,
                    )
        elif row["fault_kind"] != "none":
            row["recovery_status"] = "unavailable_run"
        rows.append(row)

    originals = {
        (row["session"], row["method"], row["radar"]): row.get("position_rmse_m")
        for row in rows
        if row["fault_kind"] == "none" and row["status"] == "completed"
    }
    for row in rows:
        baseline_rmse = originals.get((row["session"], row["method"], row["radar"]))
        fault_rmse = row.get("position_rmse_m")
        row["fault_delta_rmse_m"] = (
            fault_rmse - baseline_rmse
            if row["status"] == "completed"
            and row["fault_kind"] != "none"
            and row["fault_injected"] is True
            and fault_rmse is not None
            and baseline_rmse is not None
            else None
        )
    fault_summaries = []
    for session, method, kind in itertools.product(
        saved_protocol["final_sessions"],
        saved_protocol.get("fault_methods", []),
        saved_protocol.get("fault_levels", {}),
    ):
        for level in saved_protocol["fault_levels"][kind]:
            group = [
                row
                for row in rows
                if (row["session"], row["method"], row["fault_kind"], row["fault_level"])
                == (session, method, kind, level)
            ]
            complete = (
                bool(group)
                and len(group) == len(saved_protocol["fault_seeds"])
                and {row["fault_seed"] for row in group} == set(saved_protocol["fault_seeds"])
                and all(
                    row["status"] == "completed" and row["fault_delta_rmse_m"] is not None
                    for row in group
                )
            )
            fault_summaries.append(
                {
                    "session": session,
                    "method": method,
                    "kind": kind,
                    "fault_unit": fault_unit(kind),
                    "level": level,
                    "expected_seeds": len(saved_protocol["fault_seeds"]),
                    "recorded_runs": len(group),
                    "completed_runs": sum(row["status"] == "completed" for row in group),
                    "injected_runs": sum(row["fault_injected"] is True for row in group),
                    "skipped_runs": sum(row["fault_skipped"] for row in group),
                    "unconfirmed_runs": sum(
                        row["fault_execution"] == "unconfirmed" for row in group
                    ),
                    "mean_delta_rmse_m": float(
                        np.mean([row["fault_delta_rmse_m"] for row in group])
                    )
                    if complete
                    else None,
                }
            )

    output_directory.mkdir(parents=True, exist_ok=False)
    write_csv(output_directory / "runs.csv", rows)
    write_csv(output_directory / "fault_summary.csv", fault_summaries)
    write_csv(output_directory / "association_reasons.csv", association_rows)
    figure_files: list[Path] = []
    for number, session in enumerate(saved_protocol["final_sessions"], 1):
        figure, axes = plt.subplots(1, 2, figsize=(11, 4), layout="constrained")
        for method in principal_methods:
            principal_values = principal.get((session, method))
            if principal_values is None:
                axes[1].plot([], [], label=f"{method}: unavailable")
                continue
            times, positions, reference, errors = principal_values
            axes[0].plot(
                reference[:, 0],
                reference[:, 1],
                color="0.7",
                linewidth=1,
                label=f"{method} reference support",
            )
            axes[0].plot(positions[:, 0], positions[:, 1], label=method, linewidth=1)
            axes[1].plot(times, errors, label=method, linewidth=1)
        axes[0].set(
            xlabel="x / m",
            ylabel="y / m",
            aspect="equal",
            title=f"{session}: horizontal projection",
        )
        axes[1].set(
            xlabel="Time / s", ylabel="3D position error / m", title="All saved eligible samples"
        )
        axes[1].legend()
        if any(principal.get((session, method)) is not None for method in principal_methods):
            axes[0].legend()
        figure_file = output_directory / f"principal-{number}.png"
        save_figure(figure, figure_file)
        figure_files.append(figure_file)
    figure, axis = plt.subplots(figsize=(10, 4), layout="constrained")
    methods = saved_protocol["methods"]
    for session in saved_protocol["final_sessions"]:
        rmse_values = np.array(
            [
                originals.get((session, method, saved_protocol.get("radar_primary", "AOP")))
                for method in methods
            ],
            dtype=float,
        )
        axis.plot(
            methods,
            rmse_values,
            marker="o",
            label=session,
        )
    axis.set(
        ylabel="Position RMSE / m", title="Original recordings; unavailable results remain gaps"
    )
    axis.legend()
    figure_file = output_directory / "method-rmse.png"
    save_figure(figure, figure_file)
    figure_files.append(figure_file)
    figure, axis = plt.subplots(figsize=(12, 5), layout="constrained")
    categories = [
        (kind, level)
        for kind, levels in saved_protocol.get("fault_levels", {}).items()
        for level in levels
    ]
    for session, method in itertools.product(
        saved_protocol["final_sessions"], saved_protocol.get("fault_methods", [])
    ):
        delta_values = {
            (row["kind"], row["level"]): row["mean_delta_rmse_m"]
            for row in fault_summaries
            if row["session"] == session and row["method"] == method
        }
        axis.plot(
            range(len(categories)),
            np.array([delta_values.get(category) for category in categories], dtype=float),
            marker="o",
            label=f"{session} {method}",
        )
    axis.set_xticks(
        range(len(categories)),
        [f"{kind} {level:g} {fault_unit(kind)}" for kind, level in categories],
        rotation=60,
        ha="right",
    )
    axis.set(
        ylabel="Fault minus original RMSE / m",
        title="Descriptive seed means; incomplete groups remain gaps",
    )
    axis.axhline(0, color="0.5", linewidth=0.7)
    if fault_summaries:
        axis.legend()
    figure_file = output_directory / "fault-delta.png"
    save_figure(figure, figure_file)
    figure_files.append(figure_file)
    final_sessions = saved_protocol["final_sessions"]
    trajectory_label = "trajectory" if len(final_sessions) == 1 else "trajectories"
    scope_text = (
        f"Registered evaluation: {len(final_sessions)} final {trajectory_label}: "
        f"{', '.join(final_sessions)}."
    )
    if saved_protocol.get("dataset_id") == "miluv":
        scope_text += (
            " External calibrated portability check of the shared range-positioning subsystem only."
        )
    elif saved_protocol.get("dataset_id") == "roboloc-g":
        scope_text += " Primary controlled gantry-laboratory study."
    contents = [
        "<!doctype html><html lang='en'><meta charset='utf-8'><title>Saved study export</title>",
        "<style>body{font:16px/1.5 system-ui;max-width:1200px;margin:2rem auto;padding:1rem}img{max-width:100%}pre{white-space:pre-wrap}</style>",
        f"<h1>{html.escape(study_id)}</h1><p>Saved status: {html.escape(saved_study['status'])}. Full saved samples; no estimator rerun or parameter selection.</p>",
        f"<p>Principal comparison: {html.escape(' versus '.join(principal_methods))}.</p>",
        f"<p>{html.escape(scope_text)}</p>",
    ]
    if isinstance(saved_results, dict):
        headline_results = {
            key: value for key, value in saved_results.items() if key != "interpretation"
        }
        contents.append(
            "<h2>Saved study results</h2>"
            f"<pre id='saved-study-results'>{html.escape(json.dumps(headline_results, indent=2))}</pre>"
        )
    else:
        contents.append(
            "<h2>Saved study results</h2><p>Endpoint not computed in this saved study record.</p>"
        )
    dataset_id = saved_protocol.get("dataset_id")
    dataset_source = SOURCES.get(dataset_id) if isinstance(dataset_id, str) else None
    if dataset_source is not None:
        contents.append(
            "<h2>Dataset attribution</h2>"
            f"<p>{html.escape(dataset_source['attribution'])}. "
            f"<a href='{html.escape(dataset_source['url'])}'>"
            f"{html.escape(dataset_source['title'])} {html.escape(dataset_source['version'])}</a>. "
            f"Licensed under <a href='{html.escape(dataset_source['licence_url'])}'>"
            f"{html.escape(dataset_source['licence'])}</a>.</p>"
        )
    contents.append(
        f"<p>{html.escape(RECOVERY_POLICY)}</p><p>Failed runs and absent estimates remain visible in CSV. Seed means do not create independent environments. See the frozen scientific report for interpretation.</p>"
    )
    for csv_file in sorted(output_directory.glob("*.csv")):
        encoded_csv = base64.b64encode(csv_file.read_bytes()).decode()
        contents.append(
            f"<p><a download='{csv_file.name}' href='data:text/csv;base64,{encoded_csv}'>{csv_file.name}</a></p>"
        )
    for image_file in figure_files:
        encoded_image = base64.b64encode(image_file.read_bytes()).decode()
        contents.append(
            f"<img alt='{html.escape(image_file.stem)}' src='data:image/png;base64,{encoded_image}'>"
        )
    contents.append(
        f"<h2>Frozen protocol</h2><pre>{html.escape(json.dumps(saved_protocol, indent=2))}</pre></html>"
    )
    (output_directory / "report.html").write_text("\n".join(contents), encoding="utf-8")
    manifest = {
        "study_id": study_id,
        "study_status": saved_study["status"],
        "protocol_hash": saved_study["protocol_hash"],
        "principal_comparison": principal_methods,
        "principal_series": [
            {"session": session, "method": method, "samples": len(series[0])}
            for (session, method), series in sorted(principal.items())
        ],
        "recovery_policy": RECOVERY_POLICY,
        "exporter_sha256": fingerprint(Path(__file__)),
        "inputs": input_records,
        "artifacts": [
            {
                "name": output_file.name,
                "sha256": fingerprint(output_file),
                "bytes": output_file.stat().st_size,
            }
            for output_file in sorted(output_directory.iterdir())
        ],
    }
    with (output_directory / "manifest.json").open("x", encoding="utf-8") as output_stream:
        json.dump(manifest, output_stream, indent=2)
    return output_directory / "report.html"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    parser.add_argument("--study", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(export_study(arguments.workspace, arguments.study, arguments.output))
