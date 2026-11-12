"""Restore verified calibration and annotation snapshots into an explicit workspace."""

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from sensorfusion.storage import resolve_workspace_path
from sensorfusion.study import scientific_source_hash


def restore_snapshots(snapshot_root: Path, workspace: Path) -> dict[str, int]:
    """Preflight every source and destination before exclusively creating new files."""
    snapshot_directory = snapshot_root.resolve()
    workspace_directory = workspace.resolve()
    if workspace_directory.is_relative_to(snapshot_directory) or snapshot_directory.is_relative_to(
        workspace_directory
    ):
        raise ValueError("workspace and immutable snapshot directory must not overlap")
    manifest_file = snapshot_directory / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest["scientific_source_hash"] != scientific_source_hash():
        raise ValueError(
            "snapshot scientific_source_hash differs from the current scientific source"
        )
    entries = manifest["files"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("snapshot manifest files must be a nonempty list")
    pending_copies: list[tuple[Path, bytes]] = []
    destination_files: set[Path] = set()
    skipped = 0
    for entry in entries:
        relative_name = entry["path"]
        if (
            not isinstance(relative_name, str)
            or not relative_name
            or "\\" in relative_name
            or ":" in relative_name
            or "\x00" in relative_name
            or PurePosixPath(relative_name).is_absolute()
            or any(part in ("", ".", "..") for part in relative_name.split("/"))
        ):
            raise ValueError(
                f"snapshot path must be canonical and workspace-relative: {relative_name!r}"
            )
        relative_parts = PurePosixPath(relative_name).parts
        calibration = len(relative_parts) >= 2 and relative_parts[0] == "calibrations"
        annotation = len(relative_parts) >= 3 and relative_parts[:2] == ("data", "annotations")
        if not calibration and not annotation:
            raise ValueError(
                f"snapshot path must name a calibration or annotation: {relative_name!r}"
            )
        snapshot_file = (snapshot_directory / relative_name).resolve()
        destination_file = resolve_workspace_path(workspace_directory, relative_name)
        if destination_file.is_relative_to(snapshot_directory):
            raise ValueError(
                f"snapshot destination overlaps the immutable snapshot tree: {relative_name!r}"
            )
        if not snapshot_file.is_relative_to(snapshot_directory):
            raise ValueError(f"snapshot source escapes its directory: {relative_name!r}")
        if destination_file in destination_files:
            raise ValueError(f"duplicate snapshot destination: {relative_name!r}")
        destination_files.add(destination_file)
        expected_size, expected_hash = entry["bytes"], entry["sha256"]
        if type(expected_size) is not int or expected_size < 0:
            raise ValueError(f"snapshot bytes must be a nonnegative integer: {relative_name!r}")
        if (
            not isinstance(expected_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
        ):
            raise ValueError(
                f"snapshot sha256 must contain 64 lowercase hex digits: {relative_name!r}"
            )
        if not snapshot_file.is_file():
            raise ValueError(f"snapshot source must be a regular file: {relative_name!r}")
        verified_bytes = snapshot_file.read_bytes()
        if (
            len(verified_bytes) != expected_size
            or hashlib.sha256(verified_bytes).hexdigest() != expected_hash
        ):
            raise ValueError(f"snapshot bytes or SHA-256 differ from manifest: {relative_name!r}")
        for parent_directory in destination_file.parents:
            if parent_directory.exists() and not parent_directory.is_dir():
                raise FileExistsError(
                    f"snapshot parent conflicts with an existing file: {parent_directory}"
                )
            if parent_directory == workspace_directory:
                break
        if destination_file.exists():
            if not destination_file.is_file() or destination_file.read_bytes() != verified_bytes:
                raise FileExistsError(
                    f"snapshot conflicts with an existing destination: {destination_file}"
                )
            skipped += 1
        else:
            pending_copies.append((destination_file, verified_bytes))
    for new_file, verified_content in pending_copies:
        new_file.parent.mkdir(parents=True, exist_ok=True)
        with new_file.open("xb") as destination_stream:
            destination_stream.write(verified_content)
    return {"restored": len(pending_copies), "skipped": skipped}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    arguments = parser.parse_args()
    bundled_snapshots = Path(__file__).resolve().parents[1] / "protocols" / "snapshots"
    print(json.dumps(restore_snapshots(bundled_snapshots, arguments.workspace), sort_keys=True))
