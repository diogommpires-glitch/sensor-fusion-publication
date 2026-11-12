"""Authored CSV boundary fixtures, never evidence of real-data study completion."""

import csv
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pytest

from sensorfusion.data import (
    MILUV_ALLOWLIST,
    MILUV_LEVER_ARM,
    DataBlockedError,
    _normalise_reference,
    _primary_ranges,
    _reference,
    _transactions,
    accept_radar_review,
    audit,
    fit_radar_annotations,
    load_session,
    read_miluv_ranges,
)
from sensorfusion.evaluation import align_reference
from sensorfusion.sources import fingerprints, verify_archive


def _csv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def test_primary_units_offsets_and_ftm_transaction_collapse(tmp_path: Path) -> None:
    raw = tmp_path / "data" / "raw" / "roboloc-g"
    raw.mkdir(parents=True)
    with ZipFile(raw / "SENSORS_MEASUREMENTS_CSV.zip", "w") as archive:
        prefix = "SENSORS_MEASUREMENTS_CSV/UWB_FTM_IMU/still/"
        archive.writestr(
            prefix + "gtec-uwb-ranging-pozyx.csv",
            _csv(
                [
                    {
                        "timestamp": "1751476324000000000",
                        "anchor_id": "26714",
                        "tag_id": "0",
                        "range": "3500",
                    },
                ]
            ),
        )
        archive.writestr(
            prefix + "gtec-ftm.csv",
            _csv(
                [
                    {
                        "timestamp": "1751476324100000000",
                        "anchor_id": "7c:df:a1:05:87:c7",
                        "array_index": "0",
                        "dist_est": "7.5",
                    },
                    {
                        "timestamp": "1751476324100000000",
                        "anchor_id": "7c:df:a1:05:87:c7",
                        "array_index": "1",
                        "dist_est": "7.5",
                    },
                ]
            ),
        )
    observations = _primary_ranges(tmp_path, "still")
    assert len(observations) == 2
    assert observations[0]["value"] == 3.5
    assert observations[0]["sensor_id"] == "UWB_685A"
    assert observations[0]["t"] == 1751476324.0
    assert observations[1]["value"] == 7.5
    assert observations[1]["sensor_id"] == "FTM_87C6"
    assert observations[1]["source_row"] == 2


def test_ftm_contradictory_transaction_fails_instead_of_averaging() -> None:
    with pytest.raises(DataBlockedError, match="contradictory"):
        _transactions(
            [
                {"timestamp": "1", "anchor_id": "known", "dist_est": "2"},
                {"timestamp": "1", "anchor_id": "known", "dist_est": "3"},
            ]
        )


def test_miluv_allowlist_discards_truth_and_excludes_other_tags() -> None:
    row = {"timestamp": "1.25", "from_id": "10", "to_id": "3", "range_raw": "4.5"}
    contaminated = {
        **row,
        "range": "9999",
        "gt_range": "forbidden",
        "bias": "forbidden",
        "r_iw_a_ifo001": "not a pose",
        "q_ai_ifo001": "not a quaternion",
    }
    expected = read_miluv_ranges([row])
    assert read_miluv_ranges([contaminated]) == expected
    assert expected[0]["value"] == 4.5
    assert expected[0]["sensor_id"] == "UWB_3"
    assert not (set(contaminated) - set(MILUV_ALLOWLIST)) & set(expected[0])
    assert read_miluv_ranges([{**contaminated, "from_id": "11"}]) == []
    assert read_miluv_ranges([{**contaminated, "to_id": "10", "from_id": "3"}]) == expected


def test_miluv_reference_rotates_lever_arm_separately(tmp_path: Path) -> None:
    raw = tmp_path / "data" / "raw" / "miluv"
    raw.mkdir(parents=True)
    row = {
        "timestamp": "0",
        "pose.position.x": "1",
        "pose.position.y": "2",
        "pose.position.z": "3",
        "pose.orientation.x": "0",
        "pose.orientation.y": "0",
        "pose.orientation.z": "1",
        "pose.orientation.w": "0",
    }
    with ZipFile(raw / "default_1_random3_0.zip", "w") as archive:
        archive.writestr(
            "default_1_random3_0/ifo001/mocap.csv", _csv([row, {**row, "timestamp": "1"}])
        )
    times, positions = _reference(tmp_path, "miluv", "default_1_random3_0")
    np.testing.assert_array_equal(times, [0, 1])
    np.testing.assert_allclose(positions[0], np.array([1, 2, 3]) + MILUV_LEVER_ARM * [-1, -1, 1])


def test_primary_reference_mm_conversion_uses_gantry_origin(tmp_path: Path) -> None:
    raw = tmp_path / "data" / "raw" / "roboloc-g"
    raw.mkdir(parents=True)
    row = {"time": "1", "Grua.X": "2500", "Grua.Y": "2500", "Grua.Z": "-800", "UWB_tag.X": "9999"}
    with ZipFile(raw / "GROUND_TRUTH.zip", "w") as archive:
        archive.writestr(
            "GROUND_TRUTH/still_ground_truth_gantry.csv", _csv([row, {**row, "time": "2"}])
        )
    _, positions = _reference(tmp_path, "roboloc-g", "still")
    np.testing.assert_array_equal(positions[0], [2.5, 2.5, -0.8])


def test_reference_duplicates_collapse_or_quarantine_without_bridging_conflicts() -> None:
    raw_times = np.array([0, 0.05, 0.05, 0.1, 0.1, 0.1, 0.3, 0.35])
    raw_positions = np.column_stack(([0, 1, 1, 2, 9, 2, 3, 4], np.zeros(8), np.zeros(8)))
    times, positions, provenance = _normalise_reference(
        raw_times, raw_positions, "authored fixture"
    )
    np.testing.assert_array_equal(times, [0, 0.05, 0.3, 0.35])
    np.testing.assert_array_equal(positions[:, 0], [0, 1, 3, 4])
    assert provenance["input_rows"] == 8 and provenance["output_rows"] == 4
    assert provenance["identical_duplicate_rows_removed"] == 1
    assert provenance["conflicting_rows_removed"] == 3
    assert provenance["conflicting_groups"][0]["source_rows"] == [5, 6, 7]
    assert np.all(np.diff(times) > 0)
    aligned = align_reference(np.array([0.025, 0.1, 0.2, 0.325]), times, positions, max_gap_s=0.1)
    np.testing.assert_allclose(aligned[[0, 3], 0], [0.5, 3.5])
    assert np.isnan(aligned[[1, 2]]).all()


@pytest.mark.parametrize(
    "times,positions,reason",
    [
        (np.array([0, np.nan, 1]), np.zeros((3, 3)), "finite"),
        (np.array([0, 0.5, 1]), np.array([[0.0, 0, 0], [np.nan, 0, 0], [1.0, 0, 0]]), "finite"),
        (np.array([0, 0.2, 0.1]), np.zeros((3, 3)), "must not decrease"),
    ],
)
def test_reference_invalid_values_and_decreasing_source_order_fail(
    times: np.ndarray, positions: np.ndarray, reason: str
) -> None:
    with pytest.raises(DataBlockedError, match=reason):
        _normalise_reference(times, positions, "authored fixture")


def test_missing_sources_are_blocked_and_explicit_calibration_required(tmp_path: Path) -> None:
    report = audit(tmp_path, "roboloc-g")
    assert report["status"] == "blocked" and not report["certified"]
    assert len(report["blockers"]) == 3
    assert (
        json.loads((tmp_path / "data/audits/roboloc-g.json").read_text())["certification"][
            "actual_schema"
        ]
        is False
    )
    with pytest.raises(DataBlockedError, match="explicit development calibration_id"):
        load_session(tmp_path, "roboloc-g", "circle")
    with pytest.raises(ValueError, match="invalid registered calibration"):
        load_session(tmp_path, "roboloc-g", "circle", "../../final")


def test_source_digest_rejects_corruption_without_replacing_archive(tmp_path: Path) -> None:
    path = tmp_path / "authored-archive.zip"
    original = b"authored fixture payload"
    path.write_bytes(original)
    entry = {
        "name": path.name,
        "size": len(original),
        "md5": hashlib.md5(original, usedforsecurity=False).hexdigest(),
    }
    verified = verify_archive(path, entry)
    assert verified["sha256"] == hashlib.sha256(original).hexdigest()
    path.write_bytes(b"corrupted fixture payload")
    with pytest.raises(ValueError, match="source integrity failure"):
        verify_archive(path, entry)
    assert path.read_bytes() == b"corrupted fixture payload"
    assert fingerprints(path)["sha256"] != verified["sha256"]


def test_machine_radar_fit_cannot_use_check_references_to_fit_transform(tmp_path: Path) -> None:
    scans = []
    translation = np.array([2.8416, 2.1599, 1.239])
    for index, angle in enumerate(np.linspace(0, 2 * np.pi, 30, endpoint=False)):
        reference = np.array([2 + np.cos(angle), 2 + np.sin(angle), -0.8 + 0.2 * np.cos(2 * angle)])
        centroid = reference - translation
        scans.append(
            {
                "role": "check" if index % 3 == 2 else "fit",
                "session_id": "authored",
                "scan_id": str(index),
                "development_reference_origin_m": reference.tolist(),
                "candidate_centroids_native_m": [
                    centroid.tolist(),
                    (centroid + [10, 0, 0]).tolist(),
                ],
                "points_native_m": [
                    (centroid + [0.01, 0, 0]).tolist(),
                    (centroid + [0, 0.01, 0]).tolist(),
                    (centroid + [-0.01, -0.01, 0]).tolist(),
                ],
            }
        )
    first = tmp_path / "first.json"
    first.write_text(json.dumps({"radars": {"AOP": {"scans": scans}}}), encoding="utf-8")
    fitted = fit_radar_annotations(
        tmp_path, {"path": first.name, "sha256": fingerprints(first)["sha256"]}
    )["radars"]["AOP"]
    assert not fitted["certified"]
    assert fitted["unique_label_counts"] == {"fit": 20, "check": 10}
    np.testing.assert_allclose(fitted["rotation"], np.eye(3), atol=1e-12)
    np.testing.assert_allclose(fitted["translation"], translation, atol=1e-12)
    for scan in scans:
        if scan["role"] == "check":
            scan["development_reference_origin_m"] = [1000, 2000, 3000]
    second = tmp_path / "second.json"
    second.write_text(json.dumps({"radars": {"AOP": {"scans": scans}}}), encoding="utf-8")
    poisoned = fit_radar_annotations(
        tmp_path, {"path": second.name, "sha256": fingerprints(second)["sha256"]}
    )["radars"]["AOP"]
    np.testing.assert_array_equal(poisoned["rotation"], fitted["rotation"])
    np.testing.assert_array_equal(poisoned["translation"], fitted["translation"])
    assert poisoned["unique_label_counts"] == {"fit": 20, "check": 0}


def test_review_admission_preserves_old_calibration_and_rejects_wrong_hash(tmp_path: Path) -> None:
    directory = tmp_path / "calibrations"
    directory.mkdir()
    manifest = tmp_path / "authored-annotations.json"
    manifest.write_text('{"fixture": true}', encoding="utf-8")
    calibration = {
        "dataset_id": "roboloc-g",
        "id": "authored-development",
        "split": "development",
        "created_at": "2026-10-29T00:00:00+00:00",
        "certified_modalities": ["uwb", "ftm", "imu"],
        "radars": {
            sensor: {
                "certified": False,
                "engineering_gates_passed": True,
                "reason": "pending review",
            }
            for sensor in ["AOP", "ISK0", "ISK1"]
        },
        "radar_annotation_fit": {"path": manifest.name, "sha256": fingerprints(manifest)["sha256"]},
    }
    original = directory / "authored-development.json"
    original.write_text(json.dumps(calibration), encoding="utf-8")
    original_bytes = original.read_bytes()
    review = {
        "kind": "agent-scientific-review",
        "reviewer": "authored-fixture-reviewer",
        "reviewed_at": "2026-10-30T00:00:00+00:00",
        "verdict": "approved",
        "checks": ["authored review gate fixture"],
        "calibration_sha256": fingerprints(original)["sha256"],
        "manifest_sha256": fingerprints(manifest)["sha256"],
        "radars": dict.fromkeys(["AOP", "ISK0", "ISK1"], "approved"),
    }
    review_path = tmp_path / "authored-review.json"
    review_path.write_text(json.dumps(review), encoding="utf-8")
    admitted = accept_radar_review(tmp_path, "authored-development", review_path)
    assert admitted["status"] == "certified"
    assert admitted["id"] != "authored-development"
    assert admitted["parent_calibration_id"] == "authored-development"
    assert admitted["external_human_review"] == "pending"
    assert original.read_bytes() == original_bytes
    assert (directory / f"{admitted['id']}.json").exists()
    review["manifest_sha256"] = "0" * 64
    review_path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(DataBlockedError, match="manifest_sha256"):
        accept_radar_review(tmp_path, "authored-development", review_path)
