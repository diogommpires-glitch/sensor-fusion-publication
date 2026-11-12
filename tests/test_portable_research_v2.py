import json

import pytest

from scripts.portable_research_v2 import portable_report, verified_manifest


def test_portable_export_never_replaces_a_previous_result(tmp_path):
    destination = tmp_path / "report.html"
    destination.write_text("original", encoding="utf-8")
    with pytest.raises(FileExistsError):
        portable_report(tmp_path / "absent", tmp_path / "absent2", destination)
    assert destination.read_text() == "original"


def test_portable_export_rejects_modified_or_escaping_artefacts(tmp_path):
    (tmp_path / "table.csv").write_text("changed", encoding="utf-8")
    manifest = {"artifacts": [{"name": "table.csv", "bytes": 7, "sha256": "0" * 64}]}
    (tmp_path / "artifacts.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="checksum"):
        verified_manifest(tmp_path, "artifacts.json", "artifacts")
    manifest["artifacts"][0]["name"] = "../private.csv"
    (tmp_path / "artifacts.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="escapes"):
        verified_manifest(tmp_path, "artifacts.json", "artifacts")
