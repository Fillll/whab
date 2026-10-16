"""Source installation replaces obsolete app metadata only after a successful install."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install.py"
spec = importlib.util.spec_from_file_location("source_install", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def legacy_metadata(root, name="whab"):
    legacy = root / "whab.egg-info"
    legacy.mkdir(parents=True)
    (legacy / "PKG-INFO").write_text(f"Metadata-Version: 2.4\nName: {name}\nVersion: 1.9.0\n")
    (legacy / "entry_points.txt").write_text("[whab.plugins]\nsqlite = obsolete:plugin\n")
    return legacy


def test_cleanup_removes_only_known_obsolete_root_metadata(tmp_path):
    legacy = legacy_metadata(tmp_path)
    current = tmp_path / "src" / "whab.egg-info"
    current.mkdir(parents=True)
    unrelated = tmp_path / "other.egg-info"
    unrelated.mkdir()
    assert installer.remove_legacy_metadata(tmp_path)
    assert not legacy.exists()
    assert current.is_dir() and unrelated.is_dir()


@pytest.mark.parametrize("damage", ["other-project", "missing-header", "duplicate-name", "symlink-directory", "symlink-header"])
def test_cleanup_preserves_unrecognized_or_linked_paths(tmp_path, damage):
    legacy = legacy_metadata(tmp_path, "other" if damage == "other-project" else "whab")
    metadata = legacy / "PKG-INFO"
    if damage == "missing-header":
        metadata.unlink()
    elif damage == "duplicate-name":
        metadata.write_text("Name: whab\nName: other\n")
    elif damage == "symlink-directory":
        actual = tmp_path / "preserved"
        legacy.rename(actual)
        legacy.symlink_to(actual, target_is_directory=True)
    elif damage == "symlink-header":
        actual = tmp_path / "preserved-PKG-INFO"
        metadata.rename(actual)
        metadata.symlink_to(actual)
    assert not installer.remove_legacy_metadata(tmp_path)
    assert legacy.exists()
    assert (legacy / "entry_points.txt").exists()


def test_successful_source_install_cleans_metadata_after_local_projects_install(tmp_path, monkeypatch):
    legacy = legacy_metadata(tmp_path)
    for folder in ("interface/web", "storage/sqlite", "example/example"):
        project = tmp_path / "plugins" / folder / "pyproject.toml"
        project.parent.mkdir(parents=True)
        project.write_text("")
    calls = []

    def install(command, **kwargs):
        assert legacy.exists()
        calls.append((command, kwargs))

    monkeypatch.setattr(installer, "ROOT", tmp_path)
    monkeypatch.setattr(installer.subprocess, "run", install)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--dev", "--example"])
    installer.main()
    command, options = calls[0]
    assert command[:5] == [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    assert command[5:] == [
        "-e", str(tmp_path) + "[dev]",
        "-e", str(tmp_path / "plugins/example/example"),
        "-e", str(tmp_path / "plugins/interface/web"),
        "-e", str(tmp_path / "plugins/storage/sqlite"),
    ]
    assert options == {"cwd": tmp_path, "check": True}
    assert not legacy.exists()


def test_failed_source_install_preserves_legacy_metadata(tmp_path, monkeypatch):
    legacy = legacy_metadata(tmp_path)

    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(installer, "ROOT", tmp_path)
    monkeypatch.setattr(installer.subprocess, "run", fail)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    with pytest.raises(subprocess.CalledProcessError):
        installer.main()
    assert legacy.is_dir()
