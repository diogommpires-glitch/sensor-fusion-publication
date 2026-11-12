import hashlib
import json
from pathlib import Path

import pytest

from scripts.restore_snapshots import restore_snapshots
from sensorfusion import storage
from sensorfusion.study import scientific_source_hash


def test_registered_data_mount_resolves_outside_workspace_without_a_real_junction(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    external_data = tmp_path / "external-data"
    annotation = external_data / "annotations" / "fixture.json"
    annotation.parent.mkdir(parents=True)
    annotation.write_bytes(b'{"fixture":true}')
    original_resolve = Path.resolve

    def mounted_resolve(path, *args, **kwargs):
        # Emulate Windows junction resolution without changing a real dataset path.
        try:
            relative = path.relative_to(workspace / "data")
        except ValueError:
            return original_resolve(path, *args, **kwargs)
        return original_resolve(external_data / relative, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", mounted_resolve)
    resolved = storage.resolve_workspace_path(workspace, "data/annotations/fixture.json")
    assert resolved == annotation
    assert resolved.read_bytes() == b'{"fixture":true}'
    assert storage.resolve_workspace_path(workspace, "calibrations/fixture.json") == (
        workspace / "calibrations/fixture.json"
    )


@pytest.mark.parametrize(
    "relative_name",
    [
        "../escape.json",
        "data/../escape.json",
        "/escape.json",
        "C:/escape.json",
        "data\\escape.json",
    ],
)
def test_registered_workspace_path_rejects_noncanonical_names(tmp_path, relative_name):
    with pytest.raises(ValueError, match="canonical and workspace-relative"):
        storage.resolve_workspace_path(tmp_path, relative_name)


@pytest.mark.parametrize(
    "relative_name", ["data/annotations/escape.json", "calibrations/escape.json"]
)
def test_registered_workspace_path_rejects_descendant_link_escape(
    tmp_path, monkeypatch, relative_name
):
    workspace = tmp_path / "workspace"
    escaped = tmp_path / "unregistered" / "escape.json"
    original_resolve = Path.resolve

    def escaped_resolve(path, *args, **kwargs):
        if path == workspace / relative_name:
            return escaped
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", escaped_resolve)
    with pytest.raises(ValueError, match="escapes its registered root"):
        storage.resolve_workspace_path(workspace, relative_name)


@pytest.mark.parametrize("inside_snapshot", [False, True])
def test_snapshot_restore_checks_the_resolved_data_destination(
    tmp_path, monkeypatch, inside_snapshot
):
    snapshots = tmp_path / "snapshots"
    source = snapshots / "data/annotations/scans.json"
    source.parent.mkdir(parents=True)
    payload = b'{"scans":[1,2,3]}'
    source.write_bytes(payload)
    (snapshots / "manifest.json").write_text(
        json.dumps(
            {
                "scientific_source_hash": scientific_source_hash(),
                "files": [
                    {
                        "path": "data/annotations/scans.json",
                        "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    external_data = snapshots if inside_snapshot else tmp_path / "external-data"
    original_resolve = Path.resolve

    def mounted_resolve(path, *args, **kwargs):
        try:
            relative = path.relative_to(workspace / "data")
        except ValueError:
            return original_resolve(path, *args, **kwargs)
        return original_resolve(external_data / relative, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", mounted_resolve)
    if inside_snapshot:
        with pytest.raises(ValueError, match="immutable snapshot"):
            restore_snapshots(snapshots, workspace)
        assert not (snapshots / "annotations").exists()
    else:
        assert restore_snapshots(snapshots, workspace) == {"restored": 1, "skipped": 0}
        assert (external_data / "annotations/scans.json").read_bytes() == payload
    assert source.read_bytes() == payload
