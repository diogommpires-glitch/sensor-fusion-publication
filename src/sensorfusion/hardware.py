"""Positioning-only radar capture and a separately frozen one-dimensional study."""

import hashlib
import json
import math
import os
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.stats import chi2

from sensorfusion.models import cv_discretisation, joseph_update
from sensorfusion.storage import canonical_hash, read_json, registered_path, timestamp, write_json

HARDWARE_BLOCKER = "Equipment is unavailable or unverified; physical verification and H1/H2/H3 recordings are pending."


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    device_id: Literal["A", "B"]
    source_kind: Literal["physical", "synthetic_fixture"] = "physical"
    model: Literal["MR60BHA2"] = "MR60BHA2"
    firmware_version: str = Field(min_length=1, max_length=100)
    baud: Literal[115200] = 115200
    physical_verification_confirmed: bool = False
    freshness: Literal["unknown", "sequence", "device_age"] = "unknown"
    freshness_verified: bool = False
    verification_evidence: str = Field(default="", max_length=2000)
    age_jitter_tolerance_s: float | None = Field(default=None, gt=0, le=0.1)
    maximum_age_s: float = Field(default=1, gt=0, le=4)

    @model_validator(mode="after")
    def verified_claims(self):
        if (
            self.physical_verification_confirmed or self.freshness_verified
        ) and not self.verification_evidence:
            raise ValueError("verification claims require an operator evidence description")
        if self.freshness_verified and self.freshness == "unknown":
            raise ValueError("unknown freshness cannot be verified")
        if (
            self.freshness_verified
            and self.freshness == "device_age"
            and self.age_jitter_tolerance_s is None
        ):
            raise ValueError(
                "verified device-age semantics require measured host/age jitter tolerance"
            )
        return self


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    consent_confirmed: bool = False
    positioning_only: Literal[True] = True
    mounting_description: str = Field(min_length=1, max_length=1000)
    reference_instrument: str | None = Field(default=None, min_length=1, max_length=100)
    reference_start_m: float | None = Field(default=None, gt=0)
    reference_end_m: float | None = Field(default=None, gt=0)
    reference_uncertainty_m: float | None = Field(default=None, gt=0)


def trial_schedule() -> list[dict]:
    rng = np.random.default_rng(1729)
    sessions = []
    for session in ("H1", "H2", "H3"):
        trials: list[dict[str, Any]] = [
            {
                "trial_id": f"{session}-{condition}-d{int(distance * 100):03d}-r{repeat}",
                "session": session,
                "condition": condition,
                "nominal_distance_m": distance,
                "repeat": repeat,
                "target_present": True,
                "duration_s": 40,
                "settling_s": 10,
            }
            for condition in ("clear", "clutter")
            for distance in (0.5, 1.0, 1.5, 2.0)
            for repeat in (1, 2, 3)
        ]
        trials += [
            {
                "trial_id": f"{session}-absent-r{repeat}",
                "session": session,
                "condition": "absent",
                "nominal_distance_m": None,
                "repeat": repeat,
                "target_present": False,
                "duration_s": 40,
                "settling_s": 10,
            }
            for repeat in range(1, 6)
        ]
        sessions.extend([trials[int(index)] for index in rng.permutation(len(trials))])
    return sessions


def reference_template(trial_id: str) -> dict:
    _trial(trial_id)
    return {
        "consent_confirmed": False,
        "positioning_only": True,
        "mounting_description": "REPLACE with measured mounting geometry",
        "reference_instrument": None,
        "reference_start_m": None,
        "reference_end_m": None,
        "reference_uncertainty_m": None,
    }


def _trial(trial_id: str) -> dict:
    matches = [trial for trial in trial_schedule() if trial["trial_id"] == trial_id]
    if not matches:
        raise ValueError(f"trial_id is not in the registered 87-condition schedule: {trial_id!r}")
    return matches[0]


def _directory(workspace: Path, registry: str, identifier: str) -> Path:
    return registered_path(workspace / "hardware" / registry, identifier)


def _new_json(path: Path, content: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents a repeated command replacing calibration or evidence.
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(content, stream, indent=2, allow_nan=False)


def register_capability(workspace: Path, metadata: dict) -> dict:
    capability = Capability.model_validate(metadata)
    content = {
        **capability.model_dump(),
        "verification_source": "operator_declaration; not automatic hardware certification",
        "created_at": timestamp(),
    }
    identifier = f"{capability.device_id}-{canonical_hash(content)[:12]}"
    content["capability_id"] = identifier
    _new_json(_directory(workspace, "capabilities", identifier).with_suffix(".json"), content)
    return content


def _capability(workspace: Path, identifier: str) -> tuple[Capability, dict]:
    content = read_json(_directory(workspace, "capabilities", identifier).with_suffix(".json"))
    original = {key: value for key, value in content.items() if key != "capability_id"}
    expected = f"{content['device_id']}-{canonical_hash(original)[:12]}"
    if content.get("capability_id") != identifier or expected != identifier:
        raise ValueError("capability content differs from its registered identity")
    capability = Capability.model_validate({key: content[key] for key in Capability.model_fields})
    return capability, content


def hardware_catalog(workspace: Path) -> dict:
    capabilities = [
        _capability(workspace, path.stem)[1]
        for path in sorted((workspace / "hardware/capabilities").glob("*.json"))
    ]
    trials = [
        read_json(path) for path in sorted((workspace / "hardware/trials").glob("*/status.json"))
    ]
    studies = [
        read_json(path) for path in sorted((workspace / "hardware/studies").glob("*/study.json"))
    ]
    physical_capabilities = [
        c
        for c in capabilities
        if c["source_kind"] == "physical"
        and c["physical_verification_confirmed"]
        and c["freshness_verified"]
    ]
    physical_captures = []
    for status in trials:
        if status["status"] == "completed" and status["fresh_observations"] > 0:
            metadata, _ = _completed_record(workspace, status["record_id"])
            if not metadata["synthetic"]:
                physical_captures.append(status["record_id"])
    blockers = [HARDWARE_BLOCKER] if not physical_capabilities else []
    if physical_capabilities and not any(
        s["status"] == "completed" and not s["protocol"]["calibration"]["synthetic"]
        for s in studies
    ):
        blockers.append(
            "Physical capture exists; complete H1/H2 calibration and frozen H3 evaluation are still required."
        )
    return {
        "kind": "processed_radar_range",
        "dimension": 1,
        "length_unit": "m",
        "documented_devices": ["MR60BHA2 A", "MR60BHA2 B", "XIAO ESP32C6 bridge"],
        "physical_inventory_verified": bool(physical_capabilities),
        "physical_capture_present": bool(physical_captures),
        "current_connection": "unknown; no ports opened or automatically probed",
        "verification_basis": "Operator declarations, not automatic model identification or independent human review.",
        "blocking_reasons": blockers,
        "capabilities": capabilities,
        "trials": trials,
        "studies": studies,
        "schedule": trial_schedule(),
        "scope": "Each radar is an independent range channel, never a 3D point-cloud observation.",
    }


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _packet(
    payload: bytes | str, received: float, elapsed: float, capability: Capability, previous: dict
) -> dict:
    row: dict[str, Any] = {
        "receive_monotonic_s": received,
        "time_s": elapsed,
        "acquisition_time_s": None,
        "clock_basis": "host_monotonic_receive",
        "distance_m": None,
        "presence": None,
        "sequence": None,
        "age_dist_ms": None,
        "observation_time_s": None,
        "new_observation": False,
        "reason": "invalid_packet",
    }
    if len(payload) > 4096:
        row["reason"] = "packet_exceeds_4096_bytes"
        return row
    try:
        packet = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return row
    if not isinstance(packet, dict):
        return row
    distance = _finite_number(packet.get("dist"))
    row["distance_m"] = distance if distance is not None and distance > 0 else None
    row["presence"] = packet.get("presence") if isinstance(packet.get("presence"), bool) else None
    sequence = packet.get("seq")
    row["sequence"] = sequence if type(sequence) is int and sequence >= 0 else None
    age = _finite_number(packet.get("age_dist_ms"))
    row["age_dist_ms"] = age if age is not None and age >= 0 else None
    if row["distance_m"] is None or row["presence"] is False:
        row["reason"] = "no_target_or_invalid_range"
        return row
    if not capability.freshness_verified:
        row["reason"] = "freshness_unverified"
        return row
    if capability.freshness == "sequence":
        if row["sequence"] is None:
            row["reason"] = "missing_sequence"
            return row
        if previous.get("sequence") is not None and row["sequence"] <= previous["sequence"]:
            row["reason"] = "held_or_reset_sequence"
            return row
        previous["sequence"] = row["sequence"]
        row["observation_time_s"] = elapsed
    else:
        if row["age_dist_ms"] is None:
            row["reason"] = "missing_device_age"
            return row
        if row["age_dist_ms"] / 1000 > capability.maximum_age_s:
            row["reason"] = "stale_device_age"
            return row
        proxy = elapsed - row["age_dist_ms"] / 1000
        tolerance = capability.age_jitter_tolerance_s
        assert tolerance is not None
        if proxy <= previous.get("update_proxy", -math.inf) + tolerance:
            row["reason"] = "held_device_age"
            return row
        previous["update_proxy"] = proxy
        row["observation_time_s"] = proxy
        row["clock_basis"] = "host_receive_minus_device_age_proxy; not acquisition clock"
    row["new_observation"] = True
    row["reason"] = "fresh"
    return row


def _reference_ready(reference: dict, trial: dict) -> bool:
    if not trial["target_present"]:
        return True
    start, end, uncertainty = (
        reference.get(key)
        for key in ("reference_start_m", "reference_end_m", "reference_uncertainty_m")
    )
    return bool(
        reference.get("reference_instrument")
        and start is not None
        and end is not None
        and uncertainty is not None
        and abs(start - end) <= 2 * uncertainty
    )


def _capture_declaration(workspace: Path, trial_id: str, capability_id: str, reference: dict):
    trial = _trial(trial_id)
    capability, capability_record = _capability(workspace, capability_id)
    declared_reference = Reference.model_validate(reference)
    if not declared_reference.consent_confirmed:
        raise ValueError("positioning-only consent must be confirmed before recording")
    if not capability.physical_verification_confirmed:
        raise ValueError("recording requires an explicit physical-verification declaration")
    completed = _matching_records(workspace, trial_id, capability.device_id)
    if completed:
        raise FileExistsError(
            f"trial {trial_id} device {capability.device_id} already has completed evidence"
        )
    if trial["session"] == "H3":
        studies = hardware_catalog(workspace)["studies"]
        matching = [
            s
            for s in studies
            if s["status"] == "frozen" and capability_id in s["protocol"]["capability_ids"]
        ]
        if not matching:
            raise ValueError("H3 recording requires a frozen study containing this capability")
        _, protocol = _verify_frozen(workspace, matching[0]["study_id"])
        if canonical_hash(capability_record) != protocol["capability_hashes"][capability_id]:
            raise ValueError("capability differs from its frozen content hash")
    return trial, capability, capability_record, declared_reference


def record_trial(
    workspace: Path,
    trial_id: str,
    capability_id: str,
    reference: dict,
    packets: Iterable[tuple[float, bytes | str]],
    *,
    started_monotonic_s: float,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict:
    trial, capability, capability_record, declared_reference = _capture_declaration(
        workspace, trial_id, capability_id, reference
    )
    if not math.isfinite(started_monotonic_s):
        raise ValueError("capture start must be finite host-monotonic seconds")
    record_id = f"{trial_id}--{capability.device_id}"
    directory = _directory(workspace, "trials", record_id)
    if directory.exists():
        record_id += f"--attempt-{uuid4().hex[:12]}"
        directory = _directory(workspace, "trials", record_id)
    directory.mkdir(parents=True, exist_ok=False)
    metadata = {
        "record_id": record_id,
        "trial": trial,
        "capability_id": capability_id,
        "capability": capability_record,
        "reference": declared_reference.model_dump(),
        "reference_ready": _reference_ready(declared_reference.model_dump(), trial),
        "created_at": timestamp(),
        "kind": "processed_radar_range",
        "dimension": 1,
        "synthetic": capability.source_kind == "synthetic_fixture",
        "scope": "Torso-plane discrepancy, not exact scattering-point accuracy.",
    }
    _new_json(directory / "metadata.json", metadata)
    status: dict[str, Any] = {
        "record_id": record_id,
        "status": "recording",
        "received_packets": 0,
        "fresh_observations": 0,
    }
    write_json(directory / "status.json", status)
    previous: dict[str, Any] = {}
    last_received = started_monotonic_s
    elapsed = 0.0
    try:
        with (directory / "observations.jsonl.partial").open(
            "x", encoding="utf-8", newline="\n"
        ) as output:
            for received, payload in packets:
                if cancelled():
                    status["status"] = "cancelled"
                    break
                if not math.isfinite(received) or received < last_received:
                    raise ValueError("host receive timestamps must be finite and nondecreasing")
                last_received = received
                elapsed = received - started_monotonic_s
                if elapsed >= trial["duration_s"]:
                    status["status"] = "completed"
                    break
                if not payload:
                    continue
                row = _packet(payload, received, elapsed, capability, previous)
                output.write(json.dumps(row, allow_nan=False) + "\n")
                status["received_packets"] += 1
                status["fresh_observations"] += int(row["new_observation"])
            else:
                status["status"] = "cancelled" if cancelled() else "incomplete"
        if status["status"] == "completed":
            os.rename(directory / "observations.jsonl.partial", directory / "observations.jsonl")
            _new_json(
                directory / "manifest.json",
                {
                    "record_id": record_id,
                    "artifacts": {
                        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                        for name in ("metadata.json", "observations.jsonl")
                    },
                },
            )
    except KeyboardInterrupt:
        status["status"] = "cancelled"
    except (OSError, ValueError, TypeError) as error:
        status.update(status="failed", error=f"{type(error).__name__}: {error}")
        write_json(directory / "status.json", status)
        raise
    status.update(elapsed_s=elapsed, completed_at=timestamp())
    write_json(directory / "status.json", status)
    return status


def record_serial(
    workspace: Path,
    trial_id: str,
    capability_id: str,
    reference: dict,
    port: str,
    *,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict:
    _, capability, _, _ = _capture_declaration(workspace, trial_id, capability_id, reference)
    import serial

    connection = serial.Serial()
    connection.port, connection.baudrate, connection.timeout = port, capability.baud, 0.2
    connection.dtr = False
    connection.rts = False
    connection.open()
    try:
        start = time.monotonic()

        def receipts():
            while not cancelled():
                line = connection.read_until(b"\n", size=4097)
                now = time.monotonic()
                yield now, line
                if now - start >= 40:
                    return

        return record_trial(
            workspace,
            trial_id,
            capability_id,
            reference,
            receipts(),
            started_monotonic_s=start,
            cancelled=cancelled,
        )
    finally:
        connection.close()


def _completed_record(workspace: Path, record_id: str) -> tuple[dict, list[dict]]:
    directory = _directory(workspace, "trials", record_id)
    status = read_json(directory / "status.json")
    if status["status"] != "completed":
        raise ValueError(
            f"record {record_id} is {status['status']}; incomplete recordings cannot be evaluated"
        )
    manifest = read_json(directory / "manifest.json")
    if manifest["record_id"] != record_id:
        raise ValueError("hardware manifest record identity differs")
    for name in ("metadata.json", "observations.jsonl"):
        if (
            hashlib.sha256((directory / name).read_bytes()).hexdigest()
            != manifest["artifacts"][name]
        ):
            raise ValueError(f"hardware record checksum differs: {record_id}/{name}")
    metadata = read_json(directory / "metadata.json")
    if metadata["record_id"] != record_id or metadata["trial"] != _trial(
        metadata["trial"]["trial_id"]
    ):
        raise ValueError("hardware metadata differs from registered trial identity")
    with (directory / "observations.jsonl").open(encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    return metadata, rows


def _matching_records(workspace: Path, trial_id: str, device: str) -> list[str]:
    completed = []
    for directory in (workspace / "hardware/trials").glob(f"{trial_id}--{device}*"):
        status_path = directory / "status.json"
        if status_path.exists() and read_json(status_path)["status"] == "completed":
            metadata = read_json(directory / "metadata.json")
            if (
                metadata["trial"]["trial_id"] == trial_id
                and metadata["capability"]["device_id"] == device
            ):
                completed.append(directory.name)
    return completed


def _required_record(workspace: Path, trial_id: str, device: str) -> tuple[dict, list[dict]]:
    matches = _matching_records(workspace, trial_id, device)
    if len(matches) != 1:
        raise ValueError(
            f"require exactly one completed record for {trial_id}/{device}; found {len(matches)}"
        )
    return _completed_record(workspace, matches[0])


def _filter(
    rows: list[dict], offset: float, variance: float, density: float, gated: bool
) -> list[dict]:
    state = covariance = None
    epoch = None
    results = []
    for row in rows:
        time_s = row["time_s"]
        accepted = False
        reason, nis = row["reason"], None
        if row["new_observation"]:
            measured_at = row["observation_time_s"]
            if epoch is not None and measured_at < epoch:
                reason = "stale_arrival"
            elif state is None:
                state = np.array([row["distance_m"] - offset, 0.0])
                covariance = np.diag([variance, 1.0])
                epoch = measured_at
                accepted, reason = True, "initialisation"
            else:
                assert covariance is not None and epoch is not None
                transition, noise = cv_discretisation(measured_at - epoch, density)
                transition, noise = (
                    transition[np.ix_([0, 3], [0, 3])],
                    noise[np.ix_([0, 3], [0, 3])],
                )
                state = transition @ state
                covariance = transition @ covariance @ transition.T + noise
                epoch = measured_at
                residual = row["distance_m"] - offset - state[0]
                nis = float(residual**2 / (covariance[0, 0] + variance))
                if gated and nis > chi2.ppf(0.999, 1):
                    reason = "gate"
                else:
                    state, covariance, _ = joseph_update(
                        state,
                        covariance,
                        np.array([row["distance_m"] - offset]),
                        state[:1],
                        np.array([[1.0, 0.0]]),
                        np.array([[variance]]),
                    )
                    accepted, reason = True, "accepted"
        value, position_variance = None, None
        predicted_state = predicted_covariance = None
        if state is not None:
            assert covariance is not None and epoch is not None
            transition, noise = cv_discretisation(max(0, time_s - epoch), density)
            transition, noise = transition[np.ix_([0, 3], [0, 3])], noise[np.ix_([0, 3], [0, 3])]
            predicted_state = transition @ state
            predicted_covariance = transition @ covariance @ transition.T + noise
            value = float(predicted_state[0])
            position_variance = float(predicted_covariance[0, 0])
        results.append(
            {
                "time_s": time_s,
                "distance_m": value,
                "variance_m2": position_variance,
                "state": None if predicted_state is None else predicted_state.tolist(),
                "covariance": None
                if predicted_covariance is None
                else predicted_covariance.tolist(),
                "accepted": accepted,
                "reason": reason,
                "nis": nis,
            }
        )
    return results


def _metrics(
    metadata: dict, rows: list[dict], offset: float, variance: float, density: float, method: str
) -> dict:
    predicted = (
        _filter(rows, offset, variance, density, method == "D3")
        if method != "D1"
        else [
            {
                "time_s": row["time_s"],
                "distance_m": row["distance_m"] - offset if row["new_observation"] else None,
                "variance_m2": variance,
                "accepted": row["new_observation"],
                "reason": row["reason"],
                "nis": None,
            }
            for row in rows
        ]
    )
    eligible = [row for row in predicted if 10 <= row["time_s"] < 40]
    values = [row["distance_m"] for row in eligible if row["distance_m"] is not None]
    reference = metadata["reference"]
    truth = (reference["reference_start_m"] + reference["reference_end_m"]) / 2
    errors = np.asarray(values) - truth
    fresh_predictions = [
        prediction
        for observation, prediction in zip(rows, predicted, strict=True)
        if observation["new_observation"]
        and 10 <= prediction["time_s"] < 40
        and 10 <= observation["observation_time_s"] < 40
        and prediction["distance_m"] is not None
    ]
    fresh_errors = np.asarray([row["distance_m"] - truth for row in fresh_predictions])
    coverage = [
        (row["distance_m"] - truth) ** 2 <= chi2.ppf(0.95, 1) * row["variance_m2"]
        for row in fresh_predictions
        if row["distance_m"] is not None
    ]
    return {
        "method": method,
        "rmse_m": float(np.sqrt(np.mean(fresh_errors**2))) if len(fresh_errors) else None,
        "fresh_observation_rmse_m": float(np.sqrt(np.mean(fresh_errors**2)))
        if len(fresh_errors)
        else None,
        "host_receipt_prediction_rmse_m": float(np.sqrt(np.mean(errors**2)))
        if len(errors)
        else None,
        "primary_support": "Fresh range-event receipt epochs, 10 <= t < 40 seconds; shared by D1/D2/D3.",
        "fresh_scored_samples": len(fresh_errors),
        "availability": len(values) / len(eligible) if eligible else 0,
        "eligible_receipts": len(eligible),
        "estimated_receipts": len(values),
        "nominal_95_coverage": float(np.mean(coverage)) if coverage else None,
        "uncertainty_scope": "Diagnostic coverage around the measured torso plane; reference uncertainty is a bound, not Gaussian variance.",
        "rejected_updates": sum(row["reason"] == "gate" for row in predicted),
        "predictions": predicted,
    }


def _session_records(workspace: Path, session: str, device: str) -> list[tuple[dict, list[dict]]]:
    records = []
    for trial in trial_schedule():
        if trial["session"] != session:
            continue
        metadata, rows = _required_record(workspace, trial["trial_id"], device)
        identifier = metadata["record_id"]
        if not metadata["capability"]["freshness_verified"]:
            raise ValueError(f"record {identifier} lacks verified freshness semantics")
        if not metadata["reference_ready"]:
            raise ValueError(f"record {identifier} lacks a stable measured torso-plane reference")
        if (
            not any(
                row["new_observation"]
                and 10 <= row["time_s"] < 40
                and 10 <= row["observation_time_s"] < 40
                for row in rows
            )
            and trial["target_present"]
        ):
            raise ValueError(f"record {identifier} has no fresh evaluation observations")
        records.append((metadata, rows))
    return records


def calibrate_hardware(workspace: Path) -> dict:
    from sensorfusion.study import scientific_source_hash

    capabilities = hardware_catalog(workspace)["capabilities"]
    verified = [
        c for c in capabilities if c["physical_verification_confirmed"] and c["freshness_verified"]
    ]
    devices = sorted({c["device_id"] for c in verified})
    if not devices:
        raise ValueError(HARDWARE_BLOCKER)
    fitted = {}
    for device in devices:
        development = _session_records(workspace, "H1", device)
        validation = _session_records(workspace, "H2", device)
        residuals: list[float] = []
        capability_ids = set()
        for metadata, rows in development:
            capability_ids.add(metadata["capability_id"])
            if metadata["trial"]["target_present"]:
                ref = metadata["reference"]
                truth = (ref["reference_start_m"] + ref["reference_end_m"]) / 2
                residuals.extend(
                    row["distance_m"] - truth
                    for row in rows
                    if row["new_observation"]
                    and 10 <= row["time_s"] < 40
                    and 10 <= row["observation_time_s"] < 40
                )
        for metadata, _ in validation:
            capability_ids.add(metadata["capability_id"])
        if len(capability_ids) != 1:
            raise ValueError("H1 and H2 must use the same declared capability per device")
        offset, variance = float(np.mean(residuals)), float(np.var(residuals, ddof=1))
        if not math.isfinite(variance) or variance <= 0:
            raise ValueError("H1 range residuals must identify a positive finite variance")
        candidates = []
        for density in (0.01, 0.1, 1.0):
            scores = [
                _metrics(m, rows, offset, variance, density, "D2")["rmse_m"]
                for m, rows in validation
                if m["trial"]["target_present"]
            ]
            candidates.append({"q_m2_s3": density, "macro_rmse_m": float(np.mean(scores))})
        selected = min(candidates, key=lambda row: (row["macro_rmse_m"], row["q_m2_s3"]))
        fitted[device] = {
            "offset_m": offset,
            "variance_m2": variance,
            "selected_q_m2_s3": selected["q_m2_s3"],
            "candidates": candidates,
            "capability_id": next(iter(capability_ids)),
        }
    result = {
        "created_at": timestamp(),
        "fit_session": "H1",
        "selection_session": "H2",
        "final_accessed": False,
        "source_hash": scientific_source_hash(),
        "development_hashes": _input_hashes(workspace, ("H1", "H2"), devices),
        "devices": fitted,
        "synthetic": any(c["source_kind"] == "synthetic_fixture" for c in verified),
        "measurement": "H1 offset-corrected fresh processed range; D1 has no temporal filter",
        "reference": "Measured torso plane, not exact scattering-point ground truth",
    }
    identifier = f"range-{canonical_hash(result)[:12]}"
    result["calibration_id"] = identifier
    _new_json(_directory(workspace, "calibrations", identifier).with_suffix(".json"), result)
    return result


def _input_hashes(workspace: Path, sessions: tuple[str, ...], devices: list[str]) -> dict:
    hashes = {}
    for trial in trial_schedule():
        if trial["session"] in sessions:
            for device in devices:
                identifier = f"{trial['trial_id']}--{device}"
                metadata, rows = _required_record(workspace, trial["trial_id"], device)
                hashes[identifier] = canonical_hash({"metadata": metadata, "observations": rows})
    return hashes


def freeze_hardware(workspace: Path, study_id: str, calibration_id: str) -> dict:
    from sensorfusion.study import scientific_source_hash

    calibration = read_json(
        _directory(workspace, "calibrations", calibration_id).with_suffix(".json")
    )
    if calibration["source_hash"] != scientific_source_hash():
        raise ValueError("hardware scientific source changed after H1/H2 selection")
    devices = list(calibration["devices"])
    if _input_hashes(workspace, ("H1", "H2"), devices) != calibration["development_hashes"]:
        raise ValueError("hardware development observations changed after calibration")
    directory = _directory(workspace, "studies", study_id)
    if directory.exists():
        raise FileExistsError(f"hardware study already exists: {study_id}")
    if any(path.name.startswith("H3-") for path in (workspace / "hardware/trials").iterdir()):
        raise ValueError(
            "H3 recordings already exist; do not declare inspected recordings protected again"
        )
    protocol = {
        "schema_version": 2,
        "kind": "processed_radar_range",
        "dimension": 1,
        "calibration": calibration,
        "calibration_hash": canonical_hash(calibration),
        "capability_ids": [row["capability_id"] for row in calibration["devices"].values()],
        "capability_hashes": {
            row["capability_id"]: canonical_hash(_capability(workspace, row["capability_id"])[1])
            for row in calibration["devices"].values()
        },
        "source_hash": scientific_source_hash(),
        "schedule_hash": canonical_hash(trial_schedule()),
        "development_hashes": _input_hashes(workspace, ("H1", "H2"), list(calibration["devices"])),
        "final_session": "H3",
        "methods": ["D1", "D2", "D3"],
        "gate_probability": 0.999,
        "automatic_resets": 0,
    }
    study = {
        "study_id": study_id,
        "status": "frozen",
        "created_at": timestamp(),
        "protocol": protocol,
        "protocol_hash": canonical_hash(protocol),
        "results": None,
        "human_review": "pending",
    }
    directory.mkdir(parents=True, exist_ok=False)
    _new_json(directory / "protocol.json", protocol)
    _new_json(directory / "study.json", study)
    return study


def _verify_frozen(workspace: Path, study_id: str) -> tuple[dict, dict]:
    from sensorfusion.study import scientific_source_hash

    directory = _directory(workspace, "studies", study_id)
    study = read_json(directory / "study.json")
    protocol = read_json(directory / "protocol.json")
    if (
        canonical_hash(protocol) != study["protocol_hash"]
        or canonical_hash(study["protocol"]) != study["protocol_hash"]
    ):
        raise ValueError("hardware frozen protocol differs from its registered hash")
    if study["status"] != "frozen":
        raise ValueError("hardware final study has already executed")
    if protocol["source_hash"] != scientific_source_hash():
        raise ValueError("hardware source differs from frozen source")
    return study, protocol


def evaluate_hardware(workspace: Path, study_id: str) -> dict:
    study, protocol = _verify_frozen(workspace, study_id)
    directory = _directory(workspace, "studies", study_id)
    devices = list(protocol["calibration"]["devices"])
    if _input_hashes(workspace, ("H1", "H2"), devices) != protocol["development_hashes"]:
        raise ValueError("development observations changed after freezing")
    results = []
    for device in devices:
        fitted = protocol["calibration"]["devices"][device]
        for metadata, rows in _session_records(workspace, "H3", device):
            if metadata["capability_id"] != fitted["capability_id"]:
                raise ValueError("H3 capability differs from the frozen device capability")
            if not metadata["trial"]["target_present"]:
                window = [r for r in rows if 10 <= r["time_s"] < 40]
                results.append(
                    {
                        "device_id": device,
                        "record_id": metadata["record_id"],
                        "kind": "target_absent",
                        "fresh_positive_ranges": sum(r["new_observation"] for r in window),
                        "received_packets": len(window),
                    }
                )
                continue
            for method in protocol["methods"]:
                result = _metrics(
                    metadata,
                    rows,
                    fitted["offset_m"],
                    fitted["variance_m2"],
                    fitted["selected_q_m2_s3"],
                    method,
                )
                result.update(device_id=device, record_id=metadata["record_id"])
                results.append(result)
    study.update(
        status="completed",
        results=results,
        completed_at=timestamp(),
        final_input_hashes=_input_hashes(workspace, ("H3",), devices),
    )
    write_json(directory / "study.json", study)
    return study


def hardware_replay(workspace: Path, record_id: str) -> dict:
    metadata, rows = _completed_record(workspace, record_id)
    return {
        "metadata": metadata,
        "observations": rows,
        "dimension": 1,
        "length_unit": "m",
        "scope": "Recorded processed ranges; no 3D position or physiological stream.",
    }
