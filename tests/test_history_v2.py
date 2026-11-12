from zipfile import ZipFile

import pytest

from sensorfusion.history import history_manifest, release_directory, run_history
from sensorfusion.storage import canonical_hash


def test_archived_wheel_contains_exact_frozen_scientific_source():
    manifest = history_manifest()
    with ZipFile(release_directory() / manifest["wheel"]) as wheel:
        sources = {
            name.rsplit("/", 1)[-1]: wheel.read(name).decode().replace("\r\n", "\n")
            for name in wheel.namelist()
            if name.startswith("sensorfusion/") and name.count("/") == 1 and name.endswith(".py")
        }
    assert canonical_hash(sources) == manifest["scientific_source_hash"]
    assert manifest["scientific_source_hash"] == (
        "b1ee916e916667b6208cea424cbbd351a432cf700cc86afb14fe041aaed168e2"
    )
    assert "FactorConfig" not in sources["contracts.py"]


def test_historical_runner_rejects_interpreter_flags_before_installing(tmp_path):
    with pytest.raises(ValueError, match="v1 sensorfusion command"):
        run_history(tmp_path, ["-c", "print('wrong interpreter')"])
    assert not list(tmp_path.iterdir())
