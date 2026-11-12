"""Pinned source downloads with published MD5 and locally computed SHA-256."""

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SOURCES: dict[str, dict[str, Any]] = {
    "roboloc-g": {
        "title": "RoboLoc-G",
        "version": "v2",
        "doi": "10.5281/zenodo.15989282",
        "url": "https://zenodo.org/records/15989282",
        "metadata_url": "https://zenodo.org/api/records/15989282",
        "licence": "CC BY 4.0",
        "licence_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "Domínguez-Bolaño, Carro Lagoa, Barral Vales, Escudero and García-Naya, Universidade da Coruña (2025)",
        "files": [
            {
                "name": "SENSORS_MEASUREMENTS_CSV.zip",
                "size": 39518189,
                "md5": "849cbc1fab3ff1ba6a36c34325575d06",
            },
            {
                "name": "GROUND_TRUTH.zip",
                "size": 1084609,
                "md5": "73436d35986a42d33844e2e5e5587edd",
            },
            {
                "name": "GANTRY_LINUX_CNC.zip",
                "size": 3788,
                "md5": "1e71d21facf7df5b15441d5bf057249e",
            },
        ],
    },
    "miluv": {
        "title": "MILUV",
        "version": "v1",
        "doi": "10.25452/figshare.plus.28386041.v1",
        "url": "https://doi.org/10.25452/figshare.plus.28386041.v1",
        "metadata_url": "https://api.figshare.com/v2/articles/28386041/versions/1",
        "licence": "CC BY 4.0",
        "licence_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "Shalaby, Ahmed, Dahdah, Cossette, Le Ny and Forbes, MILUV (2025)",
        "files": [
            {
                "name": "default_1_random3_0.zip",
                "size": 2551133105,
                "md5": "c998884140799ac69f17b0ea1afbce77",
                "file_id": 52250735,
            },
            {
                "name": "default_1_random3_2.zip",
                "size": 2753877846,
                "md5": "acb77820c00e930538ab9bedbd81bbeb",
                "file_id": 52250747,
            },
        ],
    },
}


def source(dataset_id: str) -> dict[str, Any]:
    if dataset_id not in SOURCES:
        raise ValueError(f"unknown real dataset {dataset_id!r}; choose roboloc-g or miluv")
    return SOURCES[dataset_id]


def file_url(dataset_id: str, entry: dict[str, Any]) -> str:
    if dataset_id == "roboloc-g":
        return f"https://zenodo.org/api/records/15989282/files/{entry['name']}/content"
    return f"https://ndownloader.figshare.com/files/{entry['file_id']}"


def fingerprints(path: Path) -> dict[str, Any]:
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            md5.update(chunk)
            sha256.update(chunk)
    return {"size": size, "md5": md5.hexdigest(), "sha256": sha256.hexdigest()}


def verify_archive(path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    actual = fingerprints(path)
    if actual["size"] != entry["size"] or actual["md5"] != entry["md5"]:
        raise ValueError(
            f"source integrity failure for {path.name}: expected size/MD5 "
            f"{entry['size']}/{entry['md5']}, got {actual['size']}/{actual['md5']}"
        )
    return actual


def fetch_dataset(workspace: Path, dataset_id: str) -> dict[str, Any]:
    """Download pinned archives atomically, preserving every existing source file."""
    definition = source(dataset_id)
    raw = workspace / "data" / "raw" / dataset_id
    raw.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "dataset_id": dataset_id,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "source": definition,
        "files": [],
        "status": "downloaded",
        "schema_certified": False,
    }
    for entry in definition["files"]:
        target = raw / entry["name"]
        url = file_url(dataset_id, entry)
        result: dict[str, Any] = {"name": entry["name"], "url": url}
        temporary = raw / f".{entry['name']}.{uuid.uuid4().hex}.part"
        try:
            if target.exists():
                result.update(verify_archive(target, entry), status="verified-existing")
            else:
                request = urllib.request.Request(url, headers={"User-Agent": "SensorFusionLab/0.1"})
                with (
                    urllib.request.urlopen(request, timeout=90) as response,
                    temporary.open("xb") as output,
                ):
                    result["http_status"] = response.status
                    result["resolved_url"] = (
                        urllib.parse.urlsplit(response.url)._replace(query="").geturl()
                    )
                    for chunk in iter(lambda: response.read(1024 * 1024), b""):
                        output.write(chunk)
                result.update(verify_archive(temporary, entry), status="downloaded")
                # Hard-link publication fails if a concurrent writer already published this name.
                os.link(temporary, target)
                manifest = {**result, "retrieved_at": report["retrieved_at"], "source": definition}
                with target.with_suffix(".zip.json").open("x", encoding="utf-8") as stream:
                    json.dump(manifest, stream, indent=2)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            result.update(status="blocked", error=f"{type(error).__name__}: {error}")
            if isinstance(error, urllib.error.HTTPError):
                result["http_status"] = error.code
            report["status"] = "blocked"
        finally:
            temporary.unlink(missing_ok=True)
        report["files"].append(result)
    history = workspace / "data" / "access"
    history.mkdir(parents=True, exist_ok=True)
    with (history / f"{dataset_id}-{uuid.uuid4().hex}.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    return report
