"""Local setup, data gates, experiments and immutable study commands."""

import argparse
import json
import webbrowser
from pathlib import Path
from uuid import uuid4

from filelock import FileLock

from sensorfusion.contracts import RunConfig
from sensorfusion.storage import json_safe, read_json


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="sensorfusion")
    result.add_argument("--workspace", type=Path, default=Path("workspace"))
    commands = result.add_subparsers(dest="command", required=True)
    data = commands.add_parser("data").add_subparsers(dest="action", required=True)
    for action in ("fetch", "audit"):
        item = data.add_parser(action)
        item.add_argument("--dataset", choices=("roboloc-g", "miluv"), default="roboloc-g")
    calibrate = commands.add_parser("calibrate")
    calibrate.add_argument("--dataset", choices=("roboloc-g", "miluv"), default="roboloc-g")
    experiment = commands.add_parser("experiment").add_subparsers(dest="action", required=True)
    run = experiment.add_parser("run")
    run.add_argument("--config", type=Path)
    run.add_argument("--method", choices=tuple(f"M{i}" for i in range(1, 9)), default="M7")
    run.add_argument("--duration", type=float, default=20)
    study = commands.add_parser("study").add_subparsers(dest="action", required=True)
    select = study.add_parser("select")
    select.add_argument("--calibration", required=True)
    freeze = study.add_parser("freeze")
    freeze.add_argument("--id", required=True)
    freeze.add_argument("--calibration", required=True)
    external = study.add_parser("freeze-external")
    external.add_argument("--id", required=True)
    external.add_argument("--calibration", required=True)
    run_study = study.add_parser("run")
    run_study.add_argument("--id", required=True)
    run_study.add_argument("--faults", action="store_true")
    report = commands.add_parser("report")
    report.add_argument("run_id")
    report.add_argument("--output", type=Path)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8766)
    serve.add_argument("--open-browser", action="store_true")
    history = commands.add_parser("history").add_subparsers(dest="action", required=True)
    history.add_parser("catalog")
    history.add_parser("prepare")
    history.add_parser("run").add_argument("arguments", nargs=argparse.REMAINDER)
    investigation = commands.add_parser("investigation").add_subparsers(
        dest="action", required=True
    )
    registration = investigation.add_parser("register")
    registration.add_argument("--id", required=True)
    registration.add_argument("--baseline", type=Path, required=True)
    for action in ("run", "status"):
        investigation.add_parser(action).add_argument("--id", required=True)
    hardware = commands.add_parser("hardware").add_subparsers(dest="action", required=True)
    hardware.add_parser("catalog")
    hardware.add_parser("schedule")
    hardware.add_parser("capability").add_argument("--config", type=Path, required=True)
    hardware.add_parser("reference").add_argument("--trial", required=True)
    capture = hardware.add_parser("record")
    capture.add_argument("--trial", required=True)
    capture.add_argument("--capability", required=True)
    capture.add_argument("--reference", type=Path, required=True)
    capture.add_argument("--port", required=True)
    hardware.add_parser("calibrate")
    hardware_freeze = hardware.add_parser("freeze")
    hardware_freeze.add_argument("--id", required=True)
    hardware_freeze.add_argument("--calibration", required=True)
    hardware.add_parser("evaluate").add_argument("--id", required=True)
    hardware.add_parser("replay").add_argument("--record", required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    output = None
    if args.command == "data":
        if args.action == "fetch":
            from sensorfusion.sources import fetch_dataset

            output = fetch_dataset(workspace, args.dataset)
        else:
            from sensorfusion.data import audit

            output = audit(workspace, args.dataset)
    elif args.command == "calibrate":
        from sensorfusion.data import calibrate_dataset

        output = calibrate_dataset(workspace, args.dataset)
    elif args.command == "experiment":
        from sensorfusion.runtime import execute_run

        config = (
            RunConfig.model_validate(read_json(args.config))
            if args.config
            else RunConfig(method=args.method, duration_s=args.duration)
        )
        with FileLock(str(workspace / "numerical-worker.lock"), timeout=0):
            output = execute_run(workspace, uuid4().hex, config)
    elif args.command == "study":
        from sensorfusion.study import (
            freeze_external_study,
            freeze_study,
            run_study,
            select_parameters,
        )

        with FileLock(str(workspace / "numerical-worker.lock"), timeout=0):
            if args.action == "select":
                output = select_parameters(workspace, args.calibration)
            elif args.action == "freeze":
                output = freeze_study(workspace, args.id, args.calibration)
            elif args.action == "freeze-external":
                output = freeze_external_study(workspace, args.id, args.calibration)
            else:
                output = run_study(workspace, args.id, faults=args.faults)
    elif args.command == "report":
        from sensorfusion.reporting import generate_report
        from sensorfusion.storage import registered_path

        target = args.output or workspace / "exports" / f"{args.run_id}-{uuid4().hex[:8]}"
        output = {
            "report": str(generate_report(registered_path(workspace / "runs", args.run_id), target))
        }
    elif args.command == "history":
        from sensorfusion.history import history_manifest, prepare_history, run_history

        if args.action == "catalog":
            output = history_manifest()
        elif args.action == "prepare":
            output = {"python": str(prepare_history(workspace))}
        else:
            raise SystemExit(run_history(workspace, args.arguments))
    elif args.command == "investigation":
        from sensorfusion.investigation import register_investigation, run_investigation

        if args.action == "register":
            output = register_investigation(workspace, args.id, args.baseline)
        elif args.action == "status":
            from sensorfusion.storage import registered_path

            output = read_json(
                registered_path(workspace / "investigations", args.id) / "investigation.json"
            )
        else:
            with FileLock(str(workspace / "numerical-worker.lock"), timeout=0):
                output = run_investigation(workspace, args.id)
    elif args.command == "hardware":
        from sensorfusion import hardware

        if args.action == "catalog":
            output = hardware.hardware_catalog(workspace)
        elif args.action == "schedule":
            output = {"trials": hardware.trial_schedule()}
        elif args.action == "capability":
            output = hardware.register_capability(workspace, read_json(args.config))
        elif args.action == "reference":
            output = hardware.reference_template(args.trial)
        elif args.action == "record":
            output = hardware.record_serial(
                workspace, args.trial, args.capability, read_json(args.reference), args.port
            )
        elif args.action == "replay":
            output = hardware.hardware_replay(workspace, args.record)
        else:
            with FileLock(str(workspace / "numerical-worker.lock"), timeout=0):
                if args.action == "calibrate":
                    output = hardware.calibrate_hardware(workspace)
                elif args.action == "freeze":
                    output = hardware.freeze_hardware(workspace, args.id, args.calibration)
                else:
                    output = hardware.evaluate_hardware(workspace, args.id)
    elif args.command == "serve":
        import uvicorn

        from sensorfusion.api import create_app

        if args.open_browser:
            webbrowser.open(f"http://127.0.0.1:{args.port}")
        uvicorn.run(create_app(workspace), host="127.0.0.1", port=args.port)
    if output is not None:
        print(json.dumps(json_safe(output), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
