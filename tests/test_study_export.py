import csv
import html
import json
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pytest

from scripts.export_study import export_study, fault_outcomes, fault_unit, fingerprint
from sensorfusion.sources import SOURCES
from sensorfusion.storage import canonical_hash


def test_recovery_requires_a_complete_second_and_preserves_censoring():
    times = np.arange(0, 5.05, 0.05)
    errors = np.ones(len(times))
    errors[(times >= 2) & (times < 3)] = 4
    errors[np.isclose(times, 3.5)] = np.nan
    fault = {"kind": "dropout", "injected": True, "start_s": 2, "duration_s": 1}
    outcome = fault_outcomes(times, errors, fault)
    assert outcome["outage_rmse_m"] == 4
    assert outcome["recovery_threshold_m"] == 1
    assert outcome["recovery_s"] == pytest.approx(0.55)
    assert outcome["recovery_confirmed_at_s"] == pytest.approx(4.55)
    censored = fault_outcomes(times[:80], errors[:80], fault)
    assert censored["recovery_status"] == "right_censored"
    assert censored["recovery_s"] is None
    assert censored["recovery_censor_s"] == pytest.approx(0.95)


def test_export_refuses_an_existing_target_before_reading_inputs(tmp_path):
    destination = tmp_path / "existing"
    destination.mkdir()
    marker = destination / "preserved.txt"
    marker.write_text("immutable export", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        export_study(tmp_path / "absent-workspace", "study", destination)
    assert marker.read_text(encoding="utf-8") == "immutable export"
    assert list(destination.iterdir()) == [marker]


def write_saved_study(
    workspace: Path, protocol: dict, records: list[dict], results: dict | None = None
) -> None:
    study_directory = workspace / "studies" / "fixture-study"
    study_directory.mkdir(parents=True)
    for record in records:
        if record["status"] == "completed":
            run_directory = workspace / "runs" / record["run_id"]
            run_directory.mkdir(parents=True)
            (run_directory / "metrics.json").write_text(
                json.dumps(record["metrics"]), encoding="utf-8"
            )
            (run_directory / "config.json").write_text(
                json.dumps(record["config"]), encoding="utf-8"
            )
            with h5py.File(run_directory / "numerical.h5", "w") as numerical:
                numerical["times"] = np.arange(40) / 20
                numerical["position_errors"] = np.ones(40)
                numerical["states"] = np.ones((40, 6))
                numerical["reference_positions"] = np.zeros((40, 3))
                associations = numerical.create_group("radar_associations")
                associations.create_dataset("sensor_ids", data=["AOP"], dtype=h5py.string_dtype())
                associations.create_dataset(
                    "reasons", data=["ambiguous"], dtype=h5py.string_dtype()
                )
            manifest = [
                {"name": name, "sha256": fingerprint(run_directory / name)}
                for name in ("config.json", "metrics.json", "numerical.h5")
            ]
            (run_directory / "artifacts.json").write_text(
                json.dumps({"artifacts": manifest}), encoding="utf-8"
            )
    study = {
        "status": "completed_with_failures",
        "protocol": protocol,
        "protocol_hash": canonical_hash(protocol),
        "runs": records,
    }
    if results is not None:
        study["results"] = results
    (study_directory / "study.json").write_text(json.dumps(study), encoding="utf-8")
    (study_directory / "protocol.json").write_text(json.dumps(protocol), encoding="utf-8")


def test_saved_export_preserves_inputs_failed_rows_and_embeds_registered_evidence(tmp_path):
    workspace = tmp_path / "workspace"
    protocol = {
        "dataset_id": "fixture",
        "methods": [f"M{i}" for i in range(1, 9)],
        "primary_comparison": ["M7", "M2"],
        "final_sessions": ["fixture"],
        "radar_primary": "AOP",
        "fault_methods": [],
        "fault_levels": {},
    }
    records = []
    for method in protocol["methods"]:
        record: dict[str, Any] = {
            "run_id": method,
            "key": f"fixture-{method}-original",
            "config": {"method": method, "session_id": "fixture", "radar_sensor": "AOP"},
            "status": "failed",
            "metrics": None,
            "error": "preserved fixture failure",
        }
        if method in ("M2", "M7"):
            record.update(
                status="completed",
                metrics={"position_rmse_m": 1.0, "fault": {"kind": "none"}},
                error=None,
            )
        records.append(record)
    write_saved_study(workspace, protocol, records)
    before = {
        input_file: fingerprint(input_file)
        for input_file in workspace.rglob("*")
        if input_file.is_file()
    }
    report_file = export_study(workspace, "fixture-study", tmp_path / "export")
    assert {input_file: fingerprint(input_file) for input_file in before} == before
    with (report_file.parent / "runs.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 8
    assert sum(row["status"] == "failed" for row in rows) == 6
    contents = report_file.read_text(encoding="utf-8")
    assert "data:image/png;base64," in contents and "data:text/csv;base64," in contents
    assert "Dataset attribution" not in contents and "CC BY 4.0" not in contents
    with (report_file.parent / "association_reasons.csv").open(
        encoding="utf-8", newline=""
    ) as stream:
        associations = list(csv.DictReader(stream))
    assert len(associations) == 2 and all(row["reason"] == "ambiguous" for row in associations)
    export_manifest = json.loads((report_file.parent / "manifest.json").read_text(encoding="utf-8"))
    assert all(
        fingerprint(report_file.parent / entry["name"]) == entry["sha256"]
        for entry in export_manifest["artifacts"]
    )
    (workspace / "runs" / "M2" / "metrics.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum differs"):
        export_study(workspace, "fixture-study", tmp_path / "corrupted-export")
    assert not (tmp_path / "corrupted-export").exists()


def test_external_principal_figure_uses_the_registered_m2_m1_comparison(tmp_path):
    workspace = tmp_path / "workspace"
    protocol = {
        "methods": ["M1", "M2"],
        "primary_comparison": ["M2", "M1"],
        "final_sessions": ["fixture"],
        "fault_methods": [],
        "fault_levels": {},
    }
    records = [
        {
            "run_id": method,
            "key": f"fixture-{method}-original",
            "config": {"session_id": "fixture", "method": method},
            "metrics": {"position_rmse_m": 1.0, "fault": {"kind": "none"}},
            "status": "completed",
        }
        for method in protocol["methods"]
    ]
    write_saved_study(workspace, protocol, records)
    report_file = export_study(workspace, "fixture-study", tmp_path / "export")
    manifest = json.loads((report_file.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["principal_comparison"] == ["M2", "M1"]
    assert manifest["principal_series"] == [
        {"session": "fixture", "method": "M1", "samples": 40},
        {"session": "fixture", "method": "M2", "samples": 40},
    ]
    assert "Principal comparison: M2 versus M1." in report_file.read_text(encoding="utf-8")
    assert (report_file.parent / "principal-1.png").is_file()


@pytest.mark.parametrize(
    ("actual_fault", "execution_status", "skipped_count", "unconfirmed_count"),
    [
        ({"kind": "range_bias", "injected": False, "skipped": True}, "skipped", 1, 0),
        ({"kind": "range_bias"}, "unconfirmed", 0, 1),
    ],
)
def test_skipped_or_unconfirmed_fault_preserves_condition_without_a_delta(
    tmp_path, actual_fault, execution_status, skipped_count, unconfirmed_count
):
    workspace = tmp_path / "workspace"
    protocol = {
        "methods": ["M2", "M7"],
        "primary_comparison": ["M7", "M2"],
        "final_sessions": ["fixture"],
        "fault_methods": ["M2"],
        "fault_levels": {"range_bias": [0.5]},
        "fault_seeds": [3],
    }
    requested_fault = {"kind": "range_bias", "level": 0.5, "seed": 3, "duration_s": 2}
    records = [
        {
            "run_id": "original",
            "key": "fixture-M2-original",
            "config": {"method": "M2", "session_id": "fixture"},
            "status": "completed",
            "metrics": {"position_rmse_m": 1.0, "fault": {"kind": "none"}},
        },
        {
            "run_id": "fault",
            "key": "fixture-M2-range_bias-0.5-3",
            "config": {"method": "M2", "session_id": "fixture", "fault": requested_fault},
            "status": "completed",
            "metrics": {"position_rmse_m": 1.5, "fault": actual_fault},
        },
    ]
    write_saved_study(workspace, protocol, records)
    report_file = export_study(workspace, "fixture-study", tmp_path / "export")
    with (report_file.parent / "runs.csv").open(encoding="utf-8", newline="") as stream:
        fault_row = next(row for row in csv.DictReader(stream) if row["run_id"] == "fault")
    assert fault_row["fault_level"] == "0.5"
    assert fault_row["fault_seed"] == "3"
    assert fault_row["fault_duration_s"] == "2"
    assert fault_row["fault_execution"] == execution_status
    assert fault_row["fault_delta_rmse_m"] == ""
    assert fault_row["outage_rmse_m"] == ""
    with (report_file.parent / "fault_summary.csv").open(encoding="utf-8", newline="") as stream:
        summary = next(csv.DictReader(stream))
    assert summary["recorded_runs"] == "1" and summary["completed_runs"] == "1"
    assert summary["injected_runs"] == "0"
    assert summary["skipped_runs"] == str(skipped_count)
    assert summary["unconfirmed_runs"] == str(unconfirmed_count)
    assert summary["mean_delta_rmse_m"] == ""


@pytest.mark.parametrize("dataset_id", ["roboloc-g", "miluv"])
def test_export_embeds_dataset_attribution_and_exact_saved_headline(tmp_path, dataset_id):
    workspace = tmp_path / "workspace"
    if dataset_id == "roboloc-g":
        methods, comparison, sessions = ["M2", "M7"], ["M7", "M2"], ["circle", "random2"]
    else:
        methods, comparison, sessions = ["M1", "M2"], ["M2", "M1"], ["default_1_random3_2"]
    protocol = {
        "dataset_id": dataset_id,
        "methods": methods,
        "primary_comparison": comparison,
        "final_sessions": sessions,
        "fault_methods": [],
        "fault_levels": {},
    }
    saved_results = {
        "primary_macro_rmse_m": {comparison[0]: 2.5, comparison[1]: 1.5},
        "primary_difference_m": 1.0,
        "notice": "<unmodified & saved>",
        "interpretation": "Two trajectories are not independent sites.",
    }
    write_saved_study(workspace, protocol, [], results=saved_results)
    report_file = export_study(workspace, "fixture-study", tmp_path / "export")
    contents = report_file.read_text(encoding="utf-8")
    headline = contents.split("<pre id='saved-study-results'>", 1)[1].split("</pre>", 1)[0]
    assert json.loads(html.unescape(headline)) == {
        key: value for key, value in saved_results.items() if key != "interpretation"
    }
    assert "Two trajectories are not independent sites." not in contents
    assert f"Principal comparison: {' versus '.join(comparison)}." in contents
    if dataset_id == "miluv":
        assert "1 final trajectory: default_1_random3_2." in contents
        assert (
            "External calibrated portability check of the shared range-positioning subsystem only."
            in contents
        )
    else:
        assert "2 final trajectories: circle, random2." in contents
        assert "Primary controlled gantry-laboratory study." in contents
    assert "<unmodified & saved>" not in contents
    assert "&lt;unmodified &amp; saved&gt;" in contents
    assert contents.index("Saved study results") < contents.index("data:image/png")
    source = SOURCES[dataset_id]
    assert html.escape(source["attribution"]) in contents
    assert f"{source['title']} {source['version']}" in contents
    assert f"href='{source['url']}'" in contents
    assert f"href='{source['licence_url']}'" in contents
    assert ">CC BY 4.0</a>" in contents


@pytest.mark.parametrize(
    ("kind", "unit"),
    [
        ("none", ""),
        ("dropout", "s"),
        ("delay", "s"),
        ("range_bias", "m"),
        ("radar_shift", "m"),
        ("imu_bias", "m/s²"),
    ],
)
def test_declared_fault_units(kind, unit):
    assert fault_unit(kind) == unit


def test_unknown_fault_unit_is_rejected():
    with pytest.raises(ValueError, match="unknown fault kind"):
        fault_unit("unknown")


def test_delay_csv_keeps_fifty_milliseconds_in_declared_seconds(tmp_path):
    workspace = tmp_path / "workspace"
    protocol = {
        "methods": ["M2", "M7"],
        "primary_comparison": ["M7", "M2"],
        "final_sessions": ["fixture"],
        "fault_methods": ["M2"],
        "fault_levels": {"delay": [0.05]},
        "fault_seeds": [0],
    }
    records = [
        {
            "run_id": "fault",
            "key": "fixture-M2-delay-0.05-0",
            "config": {
                "session_id": "fixture",
                "method": "M2",
                "fault": {"kind": "delay", "level": 0.05, "seed": 0},
            },
            "status": "failed",
            "error": "saved fixture failure",
            "metrics": None,
        }
    ]
    write_saved_study(workspace, protocol, records)
    report_file = export_study(workspace, "fixture-study", tmp_path / "export")
    with (report_file.parent / "runs.csv").open(encoding="utf-8", newline="") as stream:
        run_row = next(csv.DictReader(stream))
    with (report_file.parent / "fault_summary.csv").open(encoding="utf-8", newline="") as stream:
        summary = next(csv.DictReader(stream))
    assert run_row["fault_level"] == "0.05" and run_row["fault_unit"] == "s"
    assert summary["level"] == "0.05" and summary["fault_unit"] == "s"


def test_frozen_study_with_null_results_reports_endpoint_not_computed(tmp_path):
    workspace = tmp_path / "workspace"
    protocol = {
        "dataset_id": "fixture",
        "methods": ["M2", "M7"],
        "primary_comparison": ["M7", "M2"],
        "final_sessions": ["fixture"],
        "fault_methods": [],
        "fault_levels": {},
    }
    write_saved_study(workspace, protocol, [])
    study_file = workspace / "studies" / "fixture-study" / "study.json"
    frozen_study = json.loads(study_file.read_text(encoding="utf-8"))
    frozen_study.update(status="frozen", results=None)
    study_file.write_text(json.dumps(frozen_study), encoding="utf-8")
    original_bytes = study_file.read_bytes()
    report_file = export_study(workspace, "fixture-study", tmp_path / "export")
    contents = report_file.read_text(encoding="utf-8")
    assert "Saved status: frozen." in contents
    assert "Endpoint not computed in this saved study record." in contents
    assert "<pre id='saved-study-results'>" not in contents
    assert "primary_macro_rmse_m" not in contents
    assert "Dataset attribution" not in contents
    assert "Frozen protocol" in contents and "M7 versus M2" in contents
    assert study_file.read_bytes() == original_bytes
    manifest = json.loads((report_file.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["study_status"] == "frozen"
    assert manifest["protocol_hash"] == canonical_hash(protocol)
