"""Minimum SDK tests use a real compatible backend artifact, never edited metadata."""

from importlib import util
from pathlib import Path

import pytest


@pytest.fixture
def checker(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/check_plugin_devkit.py"
    spec = util.spec_from_file_location("whab_reference_checks", path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "host_version", lambda _: "2.27.0")
    return module


def project(path, version, dependency):
    path.mkdir(parents=True)
    (path / "pyproject.toml").write_text(
        f'[project]\nname = "whab_plugin_sqlite"\nversion = "{version}"\ndependencies = [{dependency!r}]\n'
    )
    return path


def test_current_reference_is_shared_only_if_compatible_with_both_hosts(checker, tmp_path):
    current = project(tmp_path / "current", "1.9.0", "whab>=2.21,<3")
    assert checker.reference_projects(current, tmp_path / "pinned") == {"candidate": current, "minimum": current}


def test_oldest_host_uses_unmodified_backend_from_same_pinned_commit(checker, tmp_path):
    current = project(tmp_path / "current", "1.10.0", "whab>=2.27,<3")
    pinned = tmp_path / "pinned"
    reference = project(pinned / "plugins/storage/sqlite", "1.9.0", "whab>=2.21,<3")
    original = (reference / "pyproject.toml").read_bytes()
    assert checker.reference_projects(current, pinned) == {"candidate": current, "minimum": reference}
    assert (reference / "pyproject.toml").read_bytes() == original


@pytest.mark.parametrize("dependency", ["whab>=3", "whab>=2; python_version < '3.14'", "whab[extra]>=2", "whab @ https://example.invalid/host.whl", "unrelated>=1"])
def test_invalid_reference_cannot_weaken_actual_host_compatibility(checker, tmp_path, dependency):
    current = project(tmp_path / "current", "1.10.0", dependency)
    with pytest.raises(ValueError, match="reference backend"):
        checker.reference_projects(current, tmp_path / "pinned")


def test_incompatible_pinned_reference_or_same_version_source_collision_is_rejected(checker, tmp_path):
    current = project(tmp_path / "current", "1.10.0", "whab>=2.27,<3")
    pinned = tmp_path / "pinned"
    reference = project(pinned / "plugins/storage/sqlite", "1.10.0", "whab>=2.27,<3")
    with pytest.raises(ValueError, match="pinned reference"):
        checker.reference_projects(current, pinned)
    (reference / "pyproject.toml").write_text('[project]\nversion = "1.10.0"\ndependencies = ["whab>=2.21,<3"]\n')
    with pytest.raises(ValueError, match="distinct versions"):
        checker.reference_projects(current, pinned)


@pytest.mark.parametrize("floor", ["2.34.0", "2.36.0"])
def test_native_json_runtime_reference_uses_its_genuine_compatible_host_source(checker, tmp_path, monkeypatch, floor):
    monkeypatch.setattr(checker, "host_version", lambda _: "2.39.0")
    monkeypatch.setattr(checker, "MINIMUM_HOST_VERSION", floor)
    current = project(tmp_path / "current-json", "1.3.0", "whab>=2.37,<3")
    pinned = tmp_path / "pinned"
    older = project(pinned / "plugins/storage/json", "1.2.0", "whab>=2.30,<3")
    for path in (current, older):
        metadata = path / "pyproject.toml"
        metadata.write_text(metadata.read_text().replace("whab_plugin_sqlite", "whab_plugin_json"))
    original = (older / "pyproject.toml").read_bytes()
    assert checker.reference_projects(current, pinned, plugin="json") == {"candidate": current, "minimum": older}
    assert (older / "pyproject.toml").read_bytes() == original
    with pytest.raises(ValueError, match="reviewed native"):
        checker.reference_projects(current, pinned, plugin="../private")
