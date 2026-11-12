"""One local numerical worker with persistent, truthful run lifecycle."""

import multiprocessing as mp
import threading
import time
import traceback
from multiprocessing.process import BaseProcess
from multiprocessing.synchronize import Event
from pathlib import Path
from uuid import uuid4

import psutil
from filelock import FileLock, Timeout

from sensorfusion.contracts import RunConfig, Session
from sensorfusion.evaluation import REFERENCE_MAX_GAP_S, score
from sensorfusion.faults import inject_fault
from sensorfusion.fixtures import make_session
from sensorfusion.reporting import generate_report
from sensorfusion.storage import (
    artefact_manifest,
    provenance,
    read_json,
    registered_path,
    save_estimate,
    timestamp,
    write_json,
)

ACTIVE = {"starting", "running", "finalizing"}
REQUIRED_MODALITIES = {
    "M1": {"uwb"},
    "M2": {"uwb"},
    "M3": {"uwb", "ftm"},
    "M4": {"uwb", "imu"},
    "M5": {"uwb", "ftm", "imu"},
    "M6": {"uwb", "ftm", "imu", "radar"},
    "M7": {"uwb", "ftm", "imu", "radar"},
    "M8": {"uwb", "ftm", "radar"},
}


class BusyError(RuntimeError):
    pass


def require_certified_modalities(metadata: dict, config: RunConfig) -> None:
    if config.dataset_id == "fixture":
        return
    required = REQUIRED_MODALITIES[config.method]
    if config.factors is not None:
        required = {"uwb", "ftm"}
        if config.factors.propagation == "inertial":
            required.add("imu")
        if config.factors.radar:
            required.add("radar")
    missing = required - set(metadata.get("certified_modalities", []))
    if missing:
        raise ValueError(
            f"{config.method} lacks certified modalities: {', '.join(sorted(missing))}"
        )
    if "radar" in required and not metadata.get("radars", {}).get(config.radar_sensor, {}).get(
        "certified", False
    ):
        raise ValueError(
            f"{config.radar_sensor} effective observation calibration is not certified"
        )
    if "radar" in required:
        clustering = metadata["radars"][config.radar_sensor].get("selected_clustering")
        if clustering is None:
            raise ValueError("certified radar calibration must bind its observation clustering")
        if (
            config.radar_cluster_radius_m != clustering["radius_m"]
            or config.radar_min_samples != clustering["min_samples"]
        ):
            raise ValueError("radar clustering differs from the certified observation calibration")


def _status(directory: Path, **changes) -> dict:
    path = directory / "status.json"
    status = read_json(path)
    status.update(changes)
    write_json(path, status)
    return status


def execute_run(
    workspace: Path,
    run_id: str,
    config: RunConfig,
    cancel=None,
    *,
    study_id: str | None = None,
    investigation_id: str | None = None,
    session: Session | None = None,
) -> dict:
    from sensorfusion.engine import CancelledError, estimate
    from sensorfusion.study import admit_study_config, protected_session, scientific_source_hash

    study = None
    if investigation_id is not None:
        from sensorfusion.investigation import admit_investigation_config

        if study_id is not None:
            raise ValueError("a run cannot belong to a final study and exploratory investigation")
        study = admit_investigation_config(workspace, investigation_id, config)
    elif config.factors is not None:
        raise ValueError("factor configurations require a registered exploratory investigation")
    elif protected_session(config):
        if study_id is None:
            raise ValueError("Protected final sessions require a registered frozen study")
        study = admit_study_config(workspace, study_id, config)

    directory = registered_path(workspace / "runs", run_id)
    if (directory / "status.json").exists():
        previous = read_json(directory / "status.json")
        if previous["status"] != "starting":
            raise FileExistsError(f"run {run_id!r} already contains evidence; create a new run")
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / "status.json").exists():
        write_json(
            directory / "status.json",
            {
                "run_id": run_id,
                "status": "starting",
                "progress": 0.0,
                "message": "Preparing observations",
                "owner_pid": psutil.Process().pid,
                "owner_started": psutil.Process().create_time(),
                "config": config.model_dump(),
                "created_at": timestamp(),
                "metrics": None,
                "error": None,
            },
        )

    def cancelled() -> bool:
        return cancel is not None and cancel.is_set()

    last_progress = [0.0]
    rss_samples: list[int] = []

    def progress(fraction: float, message: str) -> None:
        now = time.monotonic()
        if now - last_progress[0] >= 0.25 or fraction >= 1:
            rss_samples.append(psutil.Process().memory_info().rss)
            _status(directory, progress=min(0.88, 0.05 + fraction * 0.83), message=message)
            last_progress[0] = now

    try:
        _status(directory, status="running", message="Loading canonical observations")
        if session is not None:
            if session.dataset_id != config.dataset_id or session.session_id != config.session_id:
                raise ValueError(
                    "cached observation session does not match the requested configuration"
                )
            if session.metadata.get("calibration_id") != config.calibration_id:
                raise ValueError(
                    "cached observation calibration does not match the requested configuration"
                )
        elif config.dataset_id == "fixture":
            session = make_session(config)
        else:
            from sensorfusion.data import load_session

            session = load_session(
                workspace, config.dataset_id, config.session_id, config.calibration_id
            )
        require_certified_modalities(session.metadata, config)
        if cancelled():
            raise CancelledError("cancelled before estimation")
        write_json(directory / "config.json", config.model_dump())
        source = provenance(config, session)
        source["study_id"] = study_id
        source["investigation_id"] = investigation_id
        source["result_kind"] = (
            "exploratory_investigation"
            if investigation_id is not None
            else "frozen_final"
            if study_id is not None
            else "experiment"
        )
        source["reference_max_gap_s"] = REFERENCE_MAX_GAP_S
        source["protocol_hash"] = study["protocol_hash"] if study is not None else None
        if config.calibration_id is not None:
            calibration = read_json(
                registered_path(workspace / "calibrations", config.calibration_id).with_suffix(
                    ".json"
                )
            )
            source["calibration_hash"] = session.metadata["calibration_hash"]
            source["raw_sources"] = calibration["source_hashes"]
            write_json(directory / "calibration.json", calibration)
        source["scientific_source_hash"] = scientific_source_hash()
        write_json(directory / "provenance.json", source)
        observations, fault = inject_fault(session.observations, config.fault)
        rss_samples.append(psutil.Process().memory_info().rss)
        start = time.perf_counter()
        result = estimate(observations, config, progress=progress, cancelled=cancelled)
        processing_s = time.perf_counter() - start
        rss_samples.append(psutil.Process().memory_info().rss)
        if cancelled():
            raise CancelledError("cancelled before finalisation")
        _status(directory, status="finalizing", progress=0.9, message="Writing numerical evidence")
        metrics, reference, errors = score(
            result, session.reference_times, session.reference_positions
        )
        duration = float(result.times[-1] - result.times[0]) if len(result.times) > 1 else 0
        metrics.update(
            {
                "processing_s": processing_s,
                "real_time_factor": processing_s / duration if duration > 0 else None,
                "synthetic": bool(session.metadata.get("synthetic", False)),
                "sampled_peak_rss_mib": max(rss_samples) / (1024**2),
                "memory_scope": "Process RSS sampled at estimator endpoints and progress updates; not continuous OS peak.",
                "fault": fault,
            }
        )
        source["fault"] = fault
        write_json(directory / "provenance.json", source)
        save_estimate(directory, result, session, reference, errors)
        write_json(directory / "metrics.json", metrics)
        generate_report(directory)
        if cancelled():
            raise CancelledError("cancelled during finalisation")
        manifest = artefact_manifest(directory)
        write_json(directory / "artifacts.json", {"artifacts": manifest})
        return _status(
            directory,
            status="completed",
            progress=1.0,
            message="Run completed",
            completed_at=timestamp(),
            metrics=metrics,
            provenance=source,
            error=None,
        )
    except CancelledError as error:
        return _status(directory, status="cancelled", message=str(error), completed_at=timestamp())
    except Exception as error:
        # The process boundary records the failure and its traceback; it does not turn it into success.
        _status(
            directory,
            status="failed",
            message="Numerical job failed",
            error=str(error),
            traceback=traceback.format_exc(),
            completed_at=timestamp(),
        )
        raise


def _worker(workspace: str, run_id: str, config: dict, cancel, admitted, ready) -> None:
    root = Path(workspace)
    admitted.wait()
    try:
        with FileLock(str(root / "numerical-worker.lock"), timeout=0):
            ready.set()
            execute_run(root, run_id, RunConfig.model_validate(config), cancel)
    except Timeout:
        _status(
            root / "runs" / run_id,
            status="failed",
            message="Another numerical worker is active",
            error="worker busy",
            completed_at=timestamp(),
        )
        ready.set()


class ExperimentRuns:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.root = workspace / "runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self._process: BaseProcess | None = None
        self._cancel: Event | None = None
        self._run_id: str | None = None
        self._guard = threading.Lock()
        self._reconcile_orphans()

    def _reconcile_orphans(self) -> None:
        for path in self.root.glob("*/status.json"):
            status = read_json(path)
            if status["status"] not in ACTIVE:
                continue
            pid = status.get("worker_pid", status.get("owner_pid"))
            started = status.get("worker_started", status.get("owner_started"))
            alive = False
            if pid is not None and started is not None:
                try:
                    alive = abs(psutil.Process(pid).create_time() - started) < 0.01
                except psutil.NoSuchProcess:
                    pass
                except psutil.AccessDenied:
                    continue
            if not alive:
                _status(
                    path.parent,
                    status="interrupted",
                    message="Recorded worker no longer exists",
                    completed_at=timestamp(),
                )

    def _reap(self) -> None:
        if self._process is not None and not self._process.is_alive():
            self._process.join()
            if self._run_id:
                directory = self.directory(self._run_id)
                if read_json(directory / "status.json")["status"] in ACTIVE:
                    _status(
                        directory,
                        status="interrupted",
                        message="Worker exited before completion",
                        completed_at=timestamp(),
                    )
            self._process = None
        self._reconcile_orphans()

    def submit(self, config: RunConfig) -> dict:
        if config.factors is not None:
            raise ValueError("factor conditions require registered investigation execution")
        with self._guard:
            self._reap()
            if self._process is not None:
                raise BusyError("A numerical job is already active")
            try:
                with FileLock(str(self.workspace / "numerical-worker.lock"), timeout=0):
                    pass
            except Timeout as error:
                raise BusyError("A numerical worker in this workspace is already active") from error
            run_id = uuid4().hex
            directory = self.directory(run_id)
            directory.mkdir()
            status = {
                "run_id": run_id,
                "status": "starting",
                "progress": 0.0,
                "message": "Starting worker",
                "config": config.model_dump(),
                "created_at": timestamp(),
                "metrics": None,
                "error": None,
                "owner_pid": psutil.Process().pid,
                "owner_started": psutil.Process().create_time(),
            }
            write_json(directory / "status.json", status)
            context = mp.get_context("spawn")
            self._cancel = context.Event()
            admitted = context.Event()
            ready = context.Event()
            process = context.Process(
                target=_worker,
                args=(
                    str(self.workspace),
                    run_id,
                    config.model_dump(),
                    self._cancel,
                    admitted,
                    ready,
                ),
                name=f"sensorfusion-{run_id[:8]}",
            )
            self._process = process
            self._run_id = run_id
            process.start()
            _status(
                directory,
                worker_pid=process.pid,
                worker_started=psutil.Process(process.pid).create_time(),
            )
            admitted.set()
            if not ready.wait(timeout=10):
                self._cancel.set()
                raise RuntimeError("Numerical worker did not acknowledge startup")
            status = read_json(directory / "status.json")
            if status.get("error") == "worker busy":
                self._process.join(timeout=5)
                self._reap()
                raise BusyError("A numerical worker in this workspace is already active")
            return status

    def directory(self, run_id: str) -> Path:
        return registered_path(self.root, run_id)

    def get(self, run_id: str) -> dict:
        with self._guard:
            self._reap()
            path = self.directory(run_id) / "status.json"
            if not path.is_file():
                raise FileNotFoundError(f"Unknown run {run_id}")
            return read_json(path)

    def list(self) -> list[dict]:
        with self._guard:
            self._reap()
            results = [read_json(path) for path in self.root.glob("*/status.json")]
            return sorted(results, key=lambda item: item["created_at"], reverse=True)

    def cancel(self, run_id: str) -> dict:
        with self._guard:
            self._reap()
            if run_id != self._run_id or self._process is None:
                return {
                    "run_id": run_id,
                    "requested": False,
                    "status": self.get_unlocked(run_id)["status"],
                }
            assert self._cancel is not None
            self._cancel.set()
            return {"run_id": run_id, "requested": True, "status": "cancellation_requested"}

    def get_unlocked(self, run_id: str) -> dict:
        return read_json(self.directory(run_id) / "status.json")

    def close(self) -> None:
        with self._guard:
            if self._process is not None and self._process.is_alive():
                assert self._cancel is not None and self._run_id is not None
                self._cancel.set()
                self._process.join(timeout=10)
                if self._process.is_alive():
                    self._process.terminate()
                    self._process.join(timeout=5)
                    _status(
                        self.directory(self._run_id),
                        status="interrupted",
                        message="Worker stopped during server shutdown",
                        completed_at=timestamp(),
                    )
            self._reap()
