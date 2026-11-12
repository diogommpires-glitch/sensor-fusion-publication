"""Bundle verified exploratory and simulated results into one offline HTML file."""

import argparse
import base64
import hashlib
import html
import json
from pathlib import Path

from sensorfusion.sources import SOURCES


def verified_manifest(directory: Path, name: str, key: str) -> None:
    manifest = json.loads((directory / name).read_text(encoding="utf-8"))
    for entry in manifest[key]:
        path = directory / entry["name"]
        if path.resolve().parent != directory.resolve():
            raise ValueError("report artefact escapes its registered directory")
        content = path.read_bytes()
        if len(content) != entry["bytes"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError(f"report artefact checksum differs: {path.name}")


def portable_report(mechanisms: Path, consistency: Path, destination: Path) -> Path:
    if destination.exists():
        raise FileExistsError(f"portable report exists: {destination}")
    verified_manifest(mechanisms, "artifacts.json", "artifacts")
    verified_manifest(consistency, "manifest.json", "files")
    summary = json.loads((mechanisms / "summary.json").read_text(encoding="utf-8"))
    simulation = json.loads((consistency / "study.json").read_text(encoding="utf-8"))
    protocol = json.loads((mechanisms / "protocol.json").read_text(encoding="utf-8"))
    contents = [
        "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Sensor Fusion research v2</title>",
        "<style>body{font:16px/1.6 system-ui;max-width:1100px;margin:2rem auto;padding:1rem}img{max-width:100%}table{border-collapse:collapse;width:100%}td,th{padding:.5rem;border-bottom:1px solid #ccc;text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>",
        "<h1>Sensor Fusion: research v2</h1><p>Exploratory mechanism experiments and known-state simulations. Previously inspected recordings are not new held-out evidence.</p>",
        f"<p>{html.escape(summary['macro_scope'])}</p>",
        "<h2>Registered configuration contrasts</h2><table><tr><th>Factors</th><th>Order</th><th>RMSE contrast / m</th></tr>",
    ]
    contents.extend(
        f"<tr><td>{html.escape(row['factors'])}</td><td>{row['order']}</td><td>{html.escape(str(row['contrast_rmse_m']))}</td></tr>"
        for row in summary["macro_contrasts"]
    )
    contents.append(
        "</table><p>These contrasts describe the fixed model configurations and their interactions, including state-dependent associations. They do not identify a unique physical cause.</p>"
    )
    contents.append(
        f"<h2>Known-state simulation</h2><p>Each case uses {simulation['protocol']['ensemble_size']:,} seeded trials. Cases share paired draws where declared. Matched compatibility is not real-data consistency; an undetected mismatch is retained.</p><table><tr><th>Case</th><th>Marginal NEES mean</th><th>Pre-update NIS mean</th><th>Within both individual 99% intervals</th></tr>"
    )
    contents.extend(
        f"<tr><td>{html.escape(row['case_id'])}</td><td>{row['marginal_nees']['mean']:.8g}</td><td>{row['pre_update_nis']['mean']:.8g}</td><td>{row['consistency_99_compatible']}</td></tr>"
        for row in simulation["cases"]
    )
    contents.append("</table>")
    for directory, filename, caption in (
        (mechanisms, "circle-error.png", "Circle: complete trace and declared event window"),
        (
            consistency,
            "ensemble-means.png",
            "Known-state simulation; individual ensemble intervals",
        ),
    ):
        encoded = base64.b64encode((directory / filename).read_bytes()).decode()
        contents.append(
            f"<figure><img alt='{html.escape(caption)}' src='data:image/png;base64,{encoded}'><figcaption>{html.escape(caption)}</figcaption></figure>"
        )
    contents.append("<h2>Full numerical tables</h2>")
    for directory, filename in (
        (mechanisms, "results.csv"),
        (mechanisms, "effects.csv"),
        (mechanisms, "circle-events.csv"),
        (consistency, "trials.csv"),
    ):
        encoded = base64.b64encode((directory / filename).read_bytes()).decode()
        contents.append(
            f"<p><a download='{filename}' href='data:text/csv;base64,{encoded}'>{filename}</a></p>"
        )
    source = SOURCES["roboloc-g"]
    contents.append(
        f"<h2>Source and limitations</h2><p>{html.escape(source['attribution'])}. <a href='{source['url']}'>RoboLoc-G v2</a>, <a href='{source['licence_url']}'>CC BY 4.0</a>. Simulated cases are separate analytic fixtures.</p>"
    )
    contents.append(
        "<p>Physical equipment verification and H1/H2/H3 recordings are pending. Capture software tests are not physical measurements. External human scientific review, another person's reproduction and the owner's technical exercises remain pending.</p>"
    )
    contents.append(
        f"<h2>Registered exploratory protocol</h2><pre>{html.escape(json.dumps(protocol, indent=2))}</pre></html>"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as output:
        output.write("\n".join(contents))
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mechanisms", type=Path, required=True)
    parser.add_argument("--consistency", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(portable_report(args.mechanisms, args.consistency, args.output))
