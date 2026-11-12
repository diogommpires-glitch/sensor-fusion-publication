import json

import numpy as np
import pytest

from sensorfusion.hardware import (
    Capability,
    _filter,
    _metrics,
    calibrate_hardware,
    evaluate_hardware,
    freeze_hardware,
    hardware_catalog,
    hardware_replay,
    record_serial,
    record_trial,
    reference_template,
    register_capability,
    trial_schedule,
)


def capability(workspace, *, freshness="sequence", verified=True):
    return register_capability(
        workspace,
        {
            "device_id": "A",
            "source_kind": "synthetic_fixture",
            "firmware_version": "fixture-1",
            "physical_verification_confirmed": True,
            "freshness": freshness,
            "freshness_verified": verified,
            "verification_evidence": "Synthetic fixture, not actual hardware verification.",
            "age_jitter_tolerance_s": 0.02 if freshness == "device_age" else None,
        },
    )["capability_id"]


def reference(trial):
    value = trial["nominal_distance_m"]
    return {
        "consent_confirmed": True,
        "mounting_description": "Synthetic fixture mount.",
        "reference_instrument": "Synthetic exact plane" if value else None,
        "reference_start_m": value,
        "reference_end_m": value,
        "reference_uncertainty_m": 0.001 if value else None,
    }


def capture(workspace, trial, cap):
    packets: list[tuple[float, bytes | str]] = []
    for index, instant in enumerate(np.arange(0, 40, 0.5)):
        value = trial["nominal_distance_m"]
        measured = value + 0.1 + 0.01 * np.sin(index) if value else 0
        packets.append(
            (float(instant), json.dumps({"dist": measured, "seq": index, "presence": bool(value)}))
        )
    packets.append((40.0, b""))
    return record_trial(
        workspace, trial["trial_id"], cap, reference(trial), packets, started_monotonic_s=0
    )


def test_schedule_is_complete_reproducible_and_templates_do_not_invent_reference():
    schedule = trial_schedule()
    assert schedule == trial_schedule() and len(schedule) == 87
    assert len({trial["trial_id"] for trial in schedule}) == 87
    for session in ("H1", "H2", "H3"):
        group = [trial for trial in schedule if trial["session"] == session]
        assert len(group) == 29 and sum(trial["target_present"] for trial in group) == 24
    template = reference_template(schedule[0]["trial_id"])
    assert template["reference_start_m"] is None and not template["consent_confirmed"]
    with pytest.raises(ValueError, match="87-condition"):
        reference_template("arbitrary")


def test_allowlist_preserves_repeated_fresh_distances_but_not_held_sequences(tmp_path):
    cap = capability(tmp_path)
    trial = trial_schedule()[0]
    packets: list[tuple[float, bytes | str]] = [
        (0.0, '{"dist":1,"seq":1,"presence":true,"hr":72,"br":15,"face":"private"}'),
        (0.2, '{"dist":1,"seq":1,"presence":true}'),
        (0.5, '{"dist":1,"seq":2,"presence":true}'),
        (40.0, b""),
    ]
    status = record_trial(
        tmp_path, trial["trial_id"], cap, reference(trial), packets, started_monotonic_s=0
    )
    assert status["status"] == "completed" and status["fresh_observations"] == 2
    replay = hardware_replay(tmp_path, status["record_id"])
    assert [row["new_observation"] for row in replay["observations"]] == [True, False, True]
    persisted = (
        tmp_path / "hardware/trials" / status["record_id"] / "observations.jsonl"
    ).read_text()
    assert all(secret not in persisted for secret in ('"hr"', '"br"', "private", "face"))
    assert replay["metadata"]["synthetic"] and replay["dimension"] == 1
    with pytest.raises(FileExistsError):
        capture(tmp_path, trial, cap)


def test_device_age_proxy_distinguishes_hold_fresh_and_stale_without_acquisition_claim(tmp_path):
    cap = capability(tmp_path, freshness="device_age")
    trial = trial_schedule()[0]
    packets: list[tuple[float, bytes | str]] = [
        (0.0, '{"dist":1,"age_dist_ms":0}'),
        (0.1, '{"dist":1,"age_dist_ms":100}'),
        (0.5, '{"dist":1,"age_dist_ms":0}'),
        (2.0, '{"dist":1,"age_dist_ms":1500}'),
        (40.0, b""),
    ]
    status = record_trial(
        tmp_path, trial["trial_id"], cap, reference(trial), packets, started_monotonic_s=0
    )
    rows = hardware_replay(tmp_path, status["record_id"])["observations"]
    assert [row["reason"] for row in rows] == [
        "fresh",
        "held_device_age",
        "fresh",
        "stale_device_age",
    ]
    assert all(row["acquisition_time_s"] is None for row in rows)
    assert "proxy" in rows[0]["clock_basis"]


def test_unknown_freshness_is_not_a_position_observation(tmp_path):
    cap = capability(tmp_path, freshness="unknown", verified=False)
    status = capture(tmp_path, trial_schedule()[0], cap)
    assert status["fresh_observations"] == 0
    assert {
        row["reason"] for row in hardware_replay(tmp_path, status["record_id"])["observations"]
    } == {"freshness_unverified"}
    with pytest.raises(ValueError, match="unknown freshness"):
        Capability(
            device_id="A",
            firmware_version="unknown",
            freshness_verified=True,
            verification_evidence="unproved",
        )


def test_interrupted_and_short_recordings_cannot_be_evaluated(tmp_path):
    cap = capability(tmp_path)
    trial = trial_schedule()[0]
    status = record_trial(
        tmp_path, trial["trial_id"], cap, reference(trial), [(0, b"{}")], started_monotonic_s=0
    )
    assert status["status"] == "incomplete"
    with pytest.raises(ValueError, match="incomplete"):
        hardware_replay(tmp_path, status["record_id"])
    retried = capture(tmp_path, trial, cap)
    assert retried["status"] == "completed" and "--attempt-" in retried["record_id"]
    old = json.loads(
        (tmp_path / "hardware/trials" / status["record_id"] / "status.json").read_text()
    )
    assert old["status"] == "incomplete"
    second = trial_schedule()[1]

    def interrupted():
        yield 0.0, b'{"dist":1,"seq":1}'
        raise KeyboardInterrupt

    cancelled = record_trial(
        tmp_path, second["trial_id"], cap, reference(second), interrupted(), started_monotonic_s=0
    )
    assert cancelled["status"] == "cancelled"
    assert not (tmp_path / "hardware/trials" / cancelled["record_id"] / "manifest.json").exists()


def test_serial_preflight_rejects_consent_and_protected_capture_before_open(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    def forbidden_open():
        pytest.fail("Serial constructor must not run before declaration/protocol checks")

    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=forbidden_open))
    cap = capability(tmp_path)
    trial = trial_schedule()[0]
    bad_reference = reference(trial) | {"consent_confirmed": False}
    with pytest.raises(ValueError, match="consent"):
        record_serial(tmp_path, trial["trial_id"], cap, bad_reference, "COM99")
    final = next(trial for trial in trial_schedule() if trial["session"] == "H3")
    with pytest.raises(ValueError, match="frozen"):
        record_serial(tmp_path, final["trial_id"], cap, reference(final), "COM99")


def test_record_integrity_and_clock_failures_surface(tmp_path):
    cap = capability(tmp_path)
    trial = trial_schedule()[0]
    status = capture(tmp_path, trial, cap)
    directory = tmp_path / "hardware/trials" / status["record_id"]
    (directory / "observations.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="checksum"):
        hardware_replay(tmp_path, status["record_id"])
    second = trial_schedule()[1]
    with pytest.raises(ValueError, match="nondecreasing"):
        record_trial(
            tmp_path,
            second["trial_id"],
            cap,
            reference(second),
            [(1, b"{}"), (0, b"{}")],
            started_monotonic_s=0,
        )
    failed = json.loads(
        (tmp_path / "hardware/trials" / f"{second['trial_id']}--A" / "status.json").read_text()
    )
    assert failed["status"] == "failed"


def test_range_filter_matches_linear_update_and_keeps_gating_causal():
    rows = [
        {
            "time_s": t,
            "new_observation": True,
            "observation_time_s": t,
            "distance_m": value,
            "reason": "fresh",
        }
        for t, value in [(0.0, 1.0), (1.0, 2.0)]
    ]
    result = _filter(rows, 0, 0.04, 0, False)
    assert result[-1]["distance_m"] == pytest.approx(1 + 1.04 / 1.08)
    np.testing.assert_allclose(
        result[-1]["covariance"],
        [[0.0385185185185, 0.037037037037], [0.037037037037, 0.074074074074]],
        atol=1e-12,
    )
    extended = _filter(
        rows
        + [
            {
                "time_s": 2.0,
                "new_observation": True,
                "observation_time_s": 2.0,
                "distance_m": 1000,
                "reason": "fresh",
            }
        ],
        0,
        0.04,
        0,
        False,
    )
    assert result == extended[:2]
    gated = _filter([rows[0], rows[1] | {"distance_m": 1000}], 0, 0.04, 0, True)
    assert gated[-1]["reason"] == "gate" and gated[-1]["distance_m"] == 1


def test_primary_hardware_metrics_share_fresh_support_instead_of_held_receipts():
    metadata = {"reference": {"reference_start_m": 1, "reference_end_m": 1}}
    rows = [
        {
            "time_s": t,
            "new_observation": fresh,
            "observation_time_s": t if fresh else None,
            "distance_m": value,
            "reason": "fresh" if fresh else "held",
        }
        for t, fresh, value in [(10, True, 1), (11, False, 1), (12, True, 2), (13, False, 2)]
    ]
    scores = [_metrics(metadata, rows, 0, 0.04, 0.01, method) for method in ("D1", "D2", "D3")]
    assert {score["fresh_scored_samples"] for score in scores} == {2}
    assert all(score["rmse_m"] == score["fresh_observation_rmse_m"] for score in scores)
    assert scores[0]["estimated_receipts"] == 2 and scores[1]["estimated_receipts"] == 4
    assert scores[1]["host_receipt_prediction_rmse_m"] != scores[1]["rmse_m"]


def test_complete_fixture_protocol_freezes_before_h3_and_keeps_physical_status_pending(tmp_path):
    cap = capability(tmp_path)
    for trial in trial_schedule():
        if trial["session"] != "H3":
            capture(tmp_path, trial, cap)
    calibration = calibrate_hardware(tmp_path)
    assert calibration["synthetic"] and calibration["final_accessed"] is False
    assert calibration["devices"]["A"]["offset_m"] == pytest.approx(0.1, abs=0.001)
    study = freeze_hardware(tmp_path, "fixture-study", calibration["calibration_id"])
    with pytest.raises(FileExistsError):
        freeze_hardware(tmp_path, "fixture-study", calibration["calibration_id"])
    cap_path = tmp_path / "hardware/capabilities" / f"{cap}.json"
    original_cap_bytes = cap_path.read_bytes()
    changed = json.loads(original_cap_bytes)
    changed["firmware_version"] = "changed-after-freeze"
    cap_path.write_text(json.dumps(changed))
    first_h3 = next(trial for trial in trial_schedule() if trial["session"] == "H3")
    with pytest.raises(ValueError, match="registered identity"):
        capture(tmp_path, first_h3, cap)
    assert not (tmp_path / "hardware/trials" / f"{first_h3['trial_id']}--A").exists()
    cap_path.write_bytes(original_cap_bytes)
    for trial in trial_schedule():
        if trial["session"] == "H3":
            capture(tmp_path, trial, cap)
    result = evaluate_hardware(tmp_path, study["study_id"])
    assert result["status"] == "completed" and len(result["results"]) == 77
    assert len(result["final_input_hashes"]) == 29
    assert {row["method"] for row in result["results"] if "method" in row} == {"D1", "D2", "D3"}
    assert all(row["rmse_m"] < 0.02 for row in result["results"] if "method" in row)
    with pytest.raises(ValueError, match="already executed"):
        evaluate_hardware(tmp_path, study["study_id"])
    with pytest.raises(ValueError, match="already exist"):
        freeze_hardware(tmp_path, "reused-final", calibration["calibration_id"])
    catalogue = hardware_catalog(tmp_path)
    assert (
        not catalogue["physical_inventory_verified"]
        and "unverified" in catalogue["blocking_reasons"][0]
    )


def test_missing_measured_reference_blocks_calibration_without_reading_h3(tmp_path):
    cap = capability(tmp_path)
    trial = next(trial for trial in trial_schedule() if trial["target_present"])
    ref = reference(trial) | {"reference_uncertainty_m": None}
    record_trial(
        tmp_path,
        trial["trial_id"],
        cap,
        ref,
        [(0, '{"dist":1,"seq":1}'), (40, b"")],
        started_monotonic_s=0,
    )
    assert not json.loads(
        (tmp_path / "hardware/trials" / f"{trial['trial_id']}--A" / "metadata.json").read_text()
    )["reference_ready"]
    with pytest.raises(ValueError):
        calibrate_hardware(tmp_path)
    assert not (tmp_path / "hardware/studies").exists()
