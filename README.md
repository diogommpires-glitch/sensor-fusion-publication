# Sensor Fusion Lab

A local laboratory for three-dimensional positioning with UWB, Wi-Fi FTM,
fixed-attitude IMU propagation and radar observations. One Python engine serves
the command line, FastAPI service and React viewer.

This source-only snapshot contains code, synthetic contract tests, mathematical
derivations and dataset provenance. It excludes original screenshots, recordings,
calibrations, annotations, saved results, execution logs and the archived v1
wheel. It makes no new empirical-performance claim. Synthetic fixtures do not
establish real-data or physical-hardware validation.

## Scientific contracts

The estimator receives observations and configuration, never evaluation reference
tables. Calibration uses development sessions. Final references only score
estimates. Radar association is causal; source, protocol and calibration hashes
remain mandatory. Missing and failed runs remain visible. Units are metres,
seconds and radians internally. The mathematical derivations describe the
continuous-noise models and uncertainty calculations.

Historical v1 commands still require their exact hash-verified archived runtime.
That runtime and the historical study materials are not included, so those
commands refuse to run in this snapshot. Do not substitute the current engine
or remove a checksum guard to recreate a historical result.

## Local setup

Use Python 3.12 and an isolated project environment:

```powershell
$env:PYTHONUTF8 = '1'
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
.venv/Scripts/python.exe -m pip install --no-deps --no-build-isolation -e .
npm --prefix frontend ci
.venv/Scripts/python.exe scripts/build_frontend.py
.venv/Scripts/python.exe -m sensorfusion --help
```

The frontend build packages the local viewer. Starting a server, fetching external
datasets and running physical recorders are separate actions. Tests create small
labelled synthetic inputs in temporary directories; they do not download data.

## Checks

```powershell
.venv/Scripts/python.exe -m pytest -q --ignore=tests/test_history_v2.py --ignore=tests/test_investigation.py --ignore=tests/test_research_api.py
.venv/Scripts/python.exe -m ruff format --check src tests scripts
.venv/Scripts/python.exe -m ruff check src tests scripts
.venv/Scripts/python.exe -m mypy src tests
npm --prefix frontend run typecheck
npm --prefix frontend run lint
```

Historical-runtime, investigation-registration and research-catalogue tests require
excluded archived material. Those three test modules remain in source and are
excluded from this snapshot's CI selection. The rest of the
suite retains estimator, isolation, integrity, storage, synthetic acquisition and
API contracts. See the workflow for the precise publication CI selection.

## Methods and provenance

- [Derivations and units](docs/DERIVATIONS.md)
- [Architecture and lifecycle](docs/ARCHITECTURE.md)
- [Dataset sources, versions, attribution and limitations](docs/DATA.md)
- [Hardware protocol and unverified physical gates](docs/HARDWARE.md)

Original project code is source-available under the [PolyForm Noncommercial licence](LICENSE). RoboLoc-G and MILUV are separately licensed
CC BY 4.0 datasets. No dataset archive is bundled. The source catalogue retains
version-specific source URLs and fingerprints; obtain and verify any dataset
separately under its terms.

## Licence

Original project code is source-available under the [PolyForm Noncommercial
License 1.0.0](LICENSE). Commercial use requires separate permission. Third-party
code, assets and dependencies retain their own terms; see [LICENSING.md](LICENSING.md).
Earlier grants for previously released copies remain valid.
