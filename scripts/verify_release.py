"""Execute quality gates, build a wheel and prove an isolated installation."""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sensorfusion.study import scientific_source_hash

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--browser", action="store_true")
args = parser.parse_args()
record = {"platform": platform.platform(), "python": platform.python_version(), "checks": []}
record["scientific_source_hash"] = scientific_source_hash()
record["source_revision"] = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=root, text=True
).strip()
verification = root / "tmp" / f"release-{uuid4().hex[:12]}"
verification.mkdir(parents=True)
environment = os.environ.copy()
environment["PYTHONUTF8"] = "1"
environment.pop("PYTHONPATH", None)
environment["TMP"] = environment["TEMP"] = environment["TMPDIR"] = str(root / "tmp" / "pip")


def run(command: list[str], cwd: Path = root) -> None:
    result = subprocess.run(command, cwd=cwd, env=environment, text=True)
    record["checks"].append({"command": command, "cwd": str(cwd), "exit_code": result.returncode})
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command)


try:
    run([sys.executable, "-m", "ruff", "format", "--check", "src", "tests", "scripts"])
    run([sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"])
    run([sys.executable, "-m", "mypy", "src/sensorfusion", "tests"])
    run([sys.executable, "-m", "pytest", "-q", "--basetemp", str(verification / "pytest")])
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm is required for source-release verification")
    run([npm, "--prefix", "frontend", "run", "typecheck"])
    run([npm, "--prefix", "frontend", "run", "lint"])
    run([sys.executable, "scripts/build_frontend.py"])
    if args.browser:
        run([npm, "--prefix", "frontend", "test"])
    run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(verification / "dist"),
        ]
    )
    base_python = Path(sys._base_executable)
    fresh = verification / "fresh"
    run([str(base_python), "-m", "venv", "--without-pip", str(fresh)])
    fresh_python = fresh / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run(
        [
            str(base_python),
            "-m",
            "pip",
            "--python",
            str(fresh_python),
            "install",
            "-r",
            str(root / "requirements-lock.txt"),
        ]
    )
    wheel = next((verification / "dist").glob("*.whl"))
    run(
        [
            str(base_python),
            "-m",
            "pip",
            "--python",
            str(fresh_python),
            "install",
            "--no-deps",
            str(wheel),
        ]
    )
    check = verification / "outside-source"
    check.mkdir()
    run(
        [
            str(fresh_python),
            "-c",
            "import sensorfusion; from pathlib import Path; from fastapi.testclient import TestClient; "
            "from sensorfusion.api import create_app; "
            "assert 'site-packages' in sensorfusion.__file__; "
            "assert (Path(sensorfusion.__file__).parent/'web/index.html').is_file(); "
            "c=TestClient(create_app(Path('workspace'))); "
            "assert c.get('/api/health').status_code==200; assert c.get('/').status_code==200; "
            "r=c.get('/api/research'); assert r.status_code==200; "
            "assert r.json()['historical_v1']['scientific_source_hash']=='b1ee916e916667b6208cea424cbbd351a432cf700cc86afb14fe041aaed168e2'; "
            "print('Fresh wheel API and packaged frontend passed')",
        ],
        check,
    )
    run(
        [
            str(fresh_python),
            "-m",
            "sensorfusion",
            "--workspace",
            str(check / "workspace"),
            "experiment",
            "run",
            "--duration",
            "5",
        ],
        check,
    )
    record["status"] = "passed"
    record["wheel"] = str(wheel)
except (subprocess.CalledProcessError, RuntimeError, StopIteration) as error:
    record["status"] = "failed"
    record["error"] = str(error)
    raise
finally:
    record["recorded_at"] = datetime.now(UTC).isoformat()
    path = root / "evidence" / f"release-{platform.system().lower()}-{uuid4().hex[:8]}.json"
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"Verification evidence: {path}", flush=True)
