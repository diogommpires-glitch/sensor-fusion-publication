"""Loopback application API exposing registered scientific runs."""

import hashlib
from contextlib import asynccontextmanager
from pathlib import Path

import h5py
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from sensorfusion import __version__
from sensorfusion.contracts import RunConfig
from sensorfusion.runtime import BusyError, ExperimentRuns, require_certified_modalities
from sensorfusion.storage import json_safe, read_json, registered_path, trajectory

METHODS = [
    ("M1", "UWB geometric WLS", ["uwb"]),
    ("M2", "UWB constant-velocity EKF", ["uwb"]),
    ("M3", "UWB + FTM EKF", ["uwb", "ftm"]),
    ("M4", "UWB + IMU EKF", ["uwb", "imu"]),
    ("M5", "UWB + FTM + IMU EKF", ["uwb", "ftm", "imu"]),
    ("M6", "Full fusion, ungated", ["uwb", "ftm", "imu", "radar"]),
    ("M7", "Full fusion with innovation gating", ["uwb", "ftm", "imu", "radar"]),
    ("M8", "Full radio/radar fusion without IMU", ["uwb", "ftm", "radar"]),
]


def create_app(workspace: Path | str = "workspace") -> FastAPI:
    root = Path(workspace).resolve()
    runs = ExperimentRuns(root)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        runs.close()

    app = FastAPI(title="Sensor Fusion", version=__version__, lifespan=lifespan)
    app.state.runs = runs
    app.state.workspace = root
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
                return JSONResponse(
                    {"detail": "Mutating requests require the application origin"}, status_code=403
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(ValueError)
    async def invalid_value(request: Request, error: ValueError):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def missing_record(request: Request, error: FileNotFoundError):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "workspace": str(root)}

    @app.get("/api/catalog")
    def catalog() -> dict:
        from sensorfusion.data import catalog as real_catalog

        recorded = real_catalog(root)
        real_datasets = recorded.get("datasets", recorded)
        if isinstance(real_datasets, dict):
            real_datasets = [{"id": key, **value} for key, value in real_datasets.items()]
        for dataset in real_datasets:
            dataset.setdefault("name", dataset["id"])
            dataset.setdefault(
                "reason", "; ".join(dataset.get("reasons", dataset.get("blockers", [])))
            )
            sessions = dataset.get("sessions", [])
            if isinstance(sessions, dict):
                normalized = []
                if all(isinstance(value, str) for value in sessions.values()):
                    session_pairs = list(sessions.items())
                else:
                    session_pairs = [
                        (identifier, partition)
                        for partition, identifiers in sessions.items()
                        for identifier in identifiers
                    ]
                for identifier, partition in session_pairs:
                    normalized.append(
                        {
                            "id": identifier,
                            "label": identifier,
                            "partition": partition,
                            "status": "protected"
                            if partition in ("final", "test")
                            else dataset.get("status", "blocked"),
                        }
                    )
                dataset["sessions"] = normalized
            elif sessions and isinstance(sessions[0], str):
                dataset["sessions"] = [
                    {
                        "id": identifier,
                        "label": identifier,
                        "partition": "recorded",
                        "status": dataset.get("status", "blocked"),
                    }
                    for identifier in sessions
                ]
        calibrations = [
            read_json(path) for path in (root / "calibrations").glob("*.json") if path.is_file()
        ]
        for dataset in real_datasets:
            dataset["study_status"] = dataset.get("status", "blocked")
            relevant = [item for item in calibrations if item.get("dataset_id") == dataset["id"]]
            if relevant and any(item.get("certified_modalities") for item in relevant):
                dataset["status"] = "partial" if dataset["study_status"] != "certified" else "ready"
            for session in dataset.get("sessions", []):
                if session["status"] != "protected":
                    session["status"] = dataset["status"]
        return json_safe(
            {
                "datasets": [
                    {
                        "id": "fixture",
                        "name": "Synthetic validation fixtures",
                        "status": "ready",
                        "reason": "Analytical CPU fixtures; not real-data study evidence.",
                        "synthetic": True,
                        "sessions": [
                            {
                                "id": "helix",
                                "label": "3D curved motion",
                                "partition": "synthetic",
                                "status": "ready",
                            },
                            {
                                "id": "stationary",
                                "label": "Stationary target",
                                "partition": "synthetic",
                                "status": "ready",
                            },
                        ],
                    },
                    *real_datasets,
                ],
                "methods": [
                    {"id": identifier, "name": name, "modalities": modalities}
                    for identifier, name, modalities in METHODS
                ],
                "default_config": RunConfig().model_dump(),
                "calibrations": calibrations,
                "limitations": [
                    "Synthetic runs validate software, not recorded-data accuracy.",
                    "Recorded-data studies require certified schemas and development-only calibration.",
                    "Independent human review and reproduction cannot be self-certified.",
                ],
            }
        )

    @app.post("/api/config/resolve")
    def resolve(config: RunConfig) -> dict:
        return {"config": config.model_dump(), "synthetic": config.dataset_id == "fixture"}

    @app.get("/api/runs")
    def list_runs() -> dict:
        return {"runs": runs.list()}

    @app.post("/api/runs", status_code=202)
    def submit(config: RunConfig) -> dict:
        if config.factors is not None:
            raise HTTPException(
                422, "Factor conditions require a registered exploratory investigation"
            )
        from sensorfusion.study import protected_session

        if protected_session(config):
            raise HTTPException(
                422, "Protected final sessions run only through a registered frozen study"
            )
        if config.dataset_id != "fixture":
            if config.calibration_id is None:
                raise HTTPException(
                    422, "Recorded-data runs require an explicit certified calibration"
                )
            calibration = read_json(
                registered_path(root / "calibrations", config.calibration_id).with_suffix(".json")
            )
            if calibration.get("dataset_id") != config.dataset_id:
                raise HTTPException(422, "Calibration belongs to a different dataset")
            require_certified_modalities(calibration, config)
        try:
            return runs.submit(config)
        except BusyError as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/runs/{run_id}")
    def run(run_id: str) -> dict:
        return runs.get(run_id)

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str) -> dict:
        return runs.cancel(run_id)

    def completed(run_id: str) -> Path:
        if runs.get(run_id)["status"] != "completed":
            raise HTTPException(
                409, "Numerical evidence is available only after completed finalisation"
            )
        return runs.directory(run_id)

    @app.get("/api/runs/{run_id}/trajectory")
    def get_trajectory(run_id: str, max_points: int = Query(2000, ge=10, le=20000)) -> dict:
        return trajectory(completed(run_id), max_points)

    @app.get("/api/runs/{run_id}/diagnostics")
    def diagnostics(run_id: str, max_points: int = Query(2000, ge=10, le=20000)) -> dict:
        data = read_json(completed(run_id) / "innovations.json")
        count = len(data["innovations"])
        stride = max(1, (count + max_points - 1) // max_points)
        records = [
            {**item, "t": item.get("t", item.get("time"))} for item in data["innovations"][::stride]
        ]
        return json_safe(
            {
                "innovations": records,
                "metadata": data["metadata"],
                "total_points": count,
                "decimated": stride > 1,
            }
        )

    @app.get("/api/runs/{run_id}/radar-frame")
    def radar_frame(run_id: str, time: float = 0.0) -> dict:
        directory = completed(run_id)
        sensor = read_json(directory / "config.json")["radar_sensor"]
        if (directory / "radar.h5").exists():
            with h5py.File(directory / "radar.h5", "r") as h5:
                indices = np.flatnonzero(h5["sensor_ids"].asstr()[:] == sensor)
                if len(indices):
                    index = int(indices[np.argmin(np.abs(h5["times"][:][indices] - time))])
                    start, stop = h5["offsets"][index : index + 2]
                    centroid = h5["centroids"][index]
                    association_reason = None
                    with h5py.File(directory / "numerical.h5", "r") as numerical:
                        if "radar_associations" in numerical:
                            group = numerical["radar_associations"]
                            matches = np.flatnonzero(
                                (group["sensor_ids"].asstr()[:] == sensor)
                                & (np.abs(group["times"][:] - h5["times"][index]) < 1e-6)
                            )
                            if len(matches):
                                centroid = group["centroids"][matches[0]]
                                association_reason = group["reasons"].asstr()[matches[0]]
                    return json_safe(
                        {
                            "time": float(h5["times"][index]),
                            "sensor_id": sensor,
                            "points": h5["points"][start:stop],
                            "centroid": centroid,
                            "association_reason": association_reason,
                            "frame": h5["frames"].asstr()[index],
                            "requested_time": time,
                        }
                    )
        frames = [
            {**frame, "time": frame.get("time", frame.get("t"))}
            for frame in read_json(directory / "radar.json")["frames"]
            if frame["sensor_id"] == sensor
        ]
        if not frames:
            return {
                "time": time,
                "points": [],
                "centroid": None,
                "sensor_id": sensor,
                "frame": "world",
                "reason": "No radar frame is available",
            }
        result = min(frames, key=lambda frame: abs(frame["time"] - time))
        return json_safe({**result, "requested_time": time})

    @app.get("/api/runs/{run_id}/artifacts")
    def artifacts(run_id: str) -> dict:
        return read_json(completed(run_id) / "artifacts.json")

    @app.get("/api/runs/{run_id}/artifacts/{artifact_id}")
    def download(run_id: str, artifact_id: str):
        directory = completed(run_id)
        manifest = read_json(directory / "artifacts.json")["artifacts"]
        found = next((item for item in manifest if item["id"] == artifact_id), None)
        if found is None:
            raise HTTPException(404, "Unknown registered artefact")
        digest = hashlib.sha256((directory / found["name"]).read_bytes()).hexdigest()
        if digest != found["sha256"]:
            raise HTTPException(
                409, "Saved artefact checksum no longer matches the completed evidence"
            )
        return FileResponse(
            directory / found["name"], media_type=found["media_type"], filename=found["name"]
        )

    @app.get("/api/studies")
    def studies() -> dict:
        from sensorfusion.study import study_catalog

        return study_catalog(root)

    @app.get("/api/studies/{study_id}")
    def study(study_id: str) -> dict:
        directory = registered_path(root / "studies", study_id)
        return read_json(directory / "study.json")

    @app.get("/api/research")
    def research() -> dict:
        from sensorfusion.hardware import hardware_catalog
        from sensorfusion.history import history_manifest

        return {
            "investigations": [
                read_json(path)
                for path in sorted((root / "investigations").glob("*/investigation.json"))
            ],
            "hardware": hardware_catalog(root),
            "historical_v1": history_manifest(),
        }

    @app.get("/api/hardware/trials/{record_id}")
    def hardware_trial(record_id: str) -> dict:
        from sensorfusion.hardware import hardware_replay

        registered_path(root / "hardware" / "trials", record_id)
        return json_safe(hardware_replay(root, record_id))

    @app.get("/api/investigations/{investigation_id}")
    def investigation(investigation_id: str) -> dict:
        return read_json(
            registered_path(root / "investigations", investigation_id) / "investigation.json"
        )

    web = Path(__file__).parent / "web"
    if (web / "index.html").is_file():
        app.mount("/", StaticFiles(directory=web, html=True), name="application")
    else:

        @app.get("/")
        def frontend_not_built():
            return JSONResponse(
                {"detail": "Build the local frontend with python scripts/build_frontend.py"},
                status_code=503,
            )

    return app
