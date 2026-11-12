"""Execute frozen v1 through its archived wheel, never the current engine."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from filelock import FileLock


def release_directory() -> Path:
    packaged = Path(__file__).parent / "frozen_v1"
    if packaged.is_dir():
        return packaged
    directory = Path(__file__).resolve().parents[2] / "releases" / "v1"
    if not directory.is_dir():
        raise FileNotFoundError("Historical runtime requires the source checkout's releases/v1")
    return directory


def history_manifest() -> dict:
    directory = release_directory()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for filename, expected in (
        (manifest["wheel"], manifest["wheel_sha256"]),
        ("requirements-lock.txt", manifest["lock_sha256"]),
    ):
        path = directory / filename
        if path.resolve().parent != directory.resolve():
            raise ValueError("historical manifest filename escapes its release")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"historical release checksum differs: {filename}")
    return {**manifest, "execution": "separate pinned v1 interpreter", "read_only_catalog": True}


def prepare_history(workspace: Path) -> Path:
    manifest = history_manifest()
    # CPython 3.12 exposes this venv bootstrap path; typeshed does not declare it.
    base_python = sys._base_executable  # type: ignore[attr-defined]
    directory = release_directory()
    environment = workspace.resolve() / "history" / "v1" / "environment"
    environment.parent.mkdir(parents=True, exist_ok=True)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    process_environment = os.environ.copy()
    process_environment.pop("PYTHONPATH", None)
    process_environment["PYTHONUTF8"] = "1"
    temporary = workspace.resolve() / "history" / "v1" / "temporary"
    temporary.mkdir(parents=True, exist_ok=True)
    for name in ("TEMP", "TMP", "TMPDIR"):
        process_environment[name] = str(temporary)
    with FileLock(str(environment.parent / "install.lock"), timeout=0):
        if not python.exists():
            subprocess.run(
                [base_python, "-m", "venv", "--without-pip", str(environment)],
                check=True,
                env=process_environment,
            )
        ready = environment.parent / "ready.json"
        if not ready.exists():
            subprocess.run(
                [
                    base_python,
                    "-m",
                    "pip",
                    "--python",
                    str(python),
                    "install",
                    "-r",
                    str(directory / "requirements-lock.txt"),
                ],
                check=True,
                env=process_environment,
            )
            subprocess.run(
                [
                    base_python,
                    "-m",
                    "pip",
                    "--python",
                    str(python),
                    "install",
                    "--no-deps",
                    str(directory / manifest["wheel"]),
                ],
                check=True,
                env=process_environment,
            )
        check = subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                "from sensorfusion.study import scientific_source_hash; print(scientific_source_hash())",
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=environment.parent,
            env=process_environment,
        )
        if check.stdout.strip() != manifest["scientific_source_hash"]:
            raise ValueError("installed historical interpreter does not match the frozen source")
        expected_versions = {}
        for line in (directory / "requirements-lock.txt").read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            name, separator, version = line.partition("==")
            if not separator:
                raise ValueError("historical dependencies must use exact version pins")
            expected_versions[name] = version
        versions = subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                "import importlib.metadata as m,json,sys; "
                "print(json.dumps({n:m.version(n) for n in json.loads(sys.argv[1])}))",
                json.dumps(list(expected_versions)),
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=environment.parent,
            env=process_environment,
        )
        if json.loads(versions.stdout) != expected_versions:
            raise ValueError("historical installed dependencies differ from the pinned v1 lock")
        if not ready.exists():
            with ready.open("x", encoding="utf-8") as output:
                json.dump(manifest, output, indent=2)
    return python


def run_history(workspace: Path, arguments: list[str]) -> int:
    if not arguments or arguments[0] not in {
        "data",
        "calibrate",
        "experiment",
        "study",
        "report",
        "serve",
    }:
        raise ValueError("history run requires a v1 sensorfusion command")
    python = prepare_history(workspace)
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [
            str(python),
            "-I",
            "-m",
            "sensorfusion",
            "--workspace",
            str(workspace.resolve()),
            *arguments,
        ],
        cwd=python.parent.parent,
        env=environment,
        check=False,
    ).returncode
