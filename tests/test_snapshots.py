import hashlib
import json
from pathlib import Path

import pytest

from scripts.restore_snapshots import restore_snapshots
from sensorfusion.study import scientific_source_hash


def saved_snapshots(snapshot_directory: Path) -> dict[str, bytes]:
    contents = {
        "calibrations/reviewed.json": b'{"reviewed":true}\r\n',
        "data/annotations/scans.json": b'{"scans":[1,2,3]}\n',
    }
    entries = []
    for relative_name, snapshot_bytes in contents.items():
        source_file = snapshot_directory / relative_name
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_bytes(snapshot_bytes)
        entries.append(
            {
                "path": relative_name,
                "bytes": len(snapshot_bytes),
                "sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
            }
        )
    (snapshot_directory / "manifest.json").write_text(
        json.dumps({"scientific_source_hash": scientific_source_hash(), "files": entries}),
        encoding="utf-8",
    )
    return contents


def test_snapshot_roundtrip_preserves_exact_bytes_hashes_and_skips_identical_files(tmp_path):
    snapshot_directory = tmp_path / "snapshots"
    contents = saved_snapshots(snapshot_directory)
    workspace = tmp_path / "workspace"
    assert restore_snapshots(snapshot_directory, workspace) == {"restored": 2, "skipped": 0}
    for relative_name, expected_bytes in contents.items():
        restored_bytes = (workspace / relative_name).read_bytes()
        assert restored_bytes == expected_bytes
        assert (
            hashlib.sha256(restored_bytes).hexdigest() == hashlib.sha256(expected_bytes).hexdigest()
        )
        assert (snapshot_directory / relative_name).read_bytes() == expected_bytes
    assert restore_snapshots(snapshot_directory, workspace) == {"restored": 0, "skipped": 2}


def test_late_snapshot_conflict_preserves_old_data_and_prevents_every_copy(tmp_path):
    snapshot_directory = tmp_path / "snapshots"
    saved_snapshots(snapshot_directory)
    workspace = tmp_path / "workspace"
    existing_annotation = workspace / "data" / "annotations" / "scans.json"
    existing_annotation.parent.mkdir(parents=True)
    existing_annotation.write_bytes(b"preserved local annotation")
    with pytest.raises(FileExistsError, match="conflicts with an existing destination"):
        restore_snapshots(snapshot_directory, workspace)
    assert existing_annotation.read_bytes() == b"preserved local annotation"
    assert not (workspace / "calibrations").exists()
    assert sorted(workspace.rglob("*.json")) == [existing_annotation]


def test_ancestor_workspace_cannot_copy_new_files_into_the_immutable_snapshot_tree(tmp_path):
    snapshot_directory = tmp_path / "calibrations"
    saved_snapshots(snapshot_directory)
    before = {
        source_file.relative_to(snapshot_directory): source_file.read_bytes()
        for source_file in snapshot_directory.rglob("*")
        if source_file.is_file()
    }
    with pytest.raises(ValueError, match="must not overlap"):
        restore_snapshots(snapshot_directory, tmp_path)
    after = {
        source_file.relative_to(snapshot_directory): source_file.read_bytes()
        for source_file in snapshot_directory.rglob("*")
        if source_file.is_file()
    }
    assert after == before
    assert not (snapshot_directory / "reviewed.json").exists()
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("failure", ["source_hash", "bytes", "sha256"])
def test_snapshot_preflight_rejects_invalid_manifest_before_creating_workspace(tmp_path, failure):
    snapshot_directory = tmp_path / "snapshots"
    saved_snapshots(snapshot_directory)
    manifest_file = snapshot_directory / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if failure == "source_hash":
        manifest["scientific_source_hash"] = "0" * 64
    elif failure == "bytes":
        manifest["files"][-1]["bytes"] += 1
    else:
        manifest["files"][-1]["sha256"] = "0" * 64
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    workspace = tmp_path / "workspace"
    with pytest.raises(ValueError, match="scientific_source_hash|SHA-256"):
        restore_snapshots(snapshot_directory, workspace)
    assert not workspace.exists()


@pytest.mark.parametrize(
    "invalid_name",
    [
        "../escape.json",
        "/escape.json",
        "C:/escape.json",
        "calibrations\\escape.json",
        "calibrations/../escape.json",
    ],
)
def test_snapshot_preflight_rejects_nonrelative_paths_before_copying(tmp_path, invalid_name):
    snapshot_directory = tmp_path / "snapshots"
    saved_snapshots(snapshot_directory)
    manifest_file = snapshot_directory / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest["files"][-1]["path"] = invalid_name
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    workspace = tmp_path / "workspace"
    with pytest.raises(ValueError, match="canonical and workspace-relative"):
        restore_snapshots(snapshot_directory, workspace)
    assert not workspace.exists()
