"""Write a new known-state consistency study without replacing older evidence."""

import argparse
import csv
import hashlib
import json
import platform
from importlib.metadata import version
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt

from sensorfusion import consistency, models
from sensorfusion.consistency import run_consistency
from sensorfusion.storage import timestamp


def export_consistency(destination: Path, ensemble_size: int = 1000) -> Path:
    """Publish a complete manifest last; existing destinations are refused."""
    if destination.exists():
        raise FileExistsError(f"consistency destination already exists: {destination}")
    result = run_consistency(ensemble_size)
    result["created_at"] = timestamp()
    result["provenance"] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "dependencies": {name: version(name) for name in ("numpy", "scipy", "matplotlib")},
        "source_files": {
            source.name: hashlib.sha256(source.read_bytes()).hexdigest()
            for source in (Path(__file__), Path(consistency.__file__), Path(models.__file__))
        },
    }
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / "study.json").open("x", encoding="utf-8") as output_file:
        json.dump(result, output_file, indent=2, allow_nan=False)
    with (destination / "trials.csv").open("x", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(
            output_file, fieldnames=["case_id", "seed", "marginal_nees", "full_nees", "nis"]
        )
        writer.writeheader()
        for case in result["cases"]:
            writer.writerows({"case_id": case["case_id"], **trial} for trial in case["trials"])
    figure, axes = plt.subplots(1, 2, figsize=(13, 5), layout="constrained")
    labels = [case["case_id"] for case in result["cases"]]
    for axis, metric, expected in zip(
        axes, ("marginal_nees", "pre_update_nis"), (None, 3), strict=True
    ):
        means = [
            case[metric]["mean"] / (expected or case["base_dimension"]) for case in result["cases"]
        ]
        axis.bar(range(len(labels)), means)
        axis.axhline(1, color="black", linestyle="--", linewidth=1)
        axis.set_xticks(range(len(labels)), labels, rotation=65, ha="right")
        axis.set(ylabel="Ensemble mean / degrees of freedom", title=metric)
    figure.savefig(destination / "ensemble-means.png", dpi=150)
    plt.close(figure)
    manifest = {
        "created_at": timestamp(),
        "status": "completed",
        "synthetic": True,
        "files": [
            {
                "name": artifact.name,
                "bytes": artifact.stat().st_size,
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            }
            for artifact in sorted(destination.iterdir())
        ],
    }
    with (destination / "manifest.json").open("x", encoding="utf-8") as output_file:
        json.dump(manifest, output_file, indent=2)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ensemble-size", type=int, default=1000)
    arguments = parser.parse_args()
    print(export_consistency(arguments.output, arguments.ensemble_size))
