"""Source installation retires renamed packages only after replacing their plugins."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install.py"
spec = importlib.util.spec_from_file_location("source_install", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture(autouse=True)
def isolated_installed_metadata(monkeypatch):
    monkeypatch.setattr(installer.metadata, "distributions", lambda: [])


def installed_distributions(monkeypatch, *names):
    monkeypatch.setattr(
        installer.metadata,
        "distributions",
        lambda: [SimpleNamespace(metadata={"Name": name}) for name in names],
    )


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


def test_detects_only_official_legacy_distributions_with_normalized_names(monkeypatch):
    installed_distributions(
        monkeypatch,
        *installer.LEGACY_PLUGIN_DISTRIBUTIONS,
        "WHAB_SQLITE_PLUGIN",
        "whab.bank_logos-plugin",
        "whab_plugin_sqlite",
        "whab-custom-plugin",
        "whab",
        None,
    )
    assert installer.installed_legacy_plugins() == sorted(installer.LEGACY_PLUGIN_DISTRIBUTIONS)


def plugin_metadata(root, folder, metadata_name, package_name):
    metadata_dir = root / "plugins" / folder / "src" / (metadata_name + ".egg-info")
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "PKG-INFO").write_text(f"Name: {package_name}\nVersion: 1.0.0\n")
    return metadata_dir


def test_plugin_cleanup_removes_only_verified_legacy_metadata_in_owning_projects(tmp_path):
    legacy = plugin_metadata(tmp_path, "storage/sqlite", "whab_sqlite_plugin", "whab-sqlite-plugin")
    current = plugin_metadata(tmp_path, "storage/sqlite", "whab_plugin_sqlite", "whab_plugin_sqlite")
    unrelated = plugin_metadata(tmp_path, "storage/sqlite", "other", "other")
    unrecognized = plugin_metadata(tmp_path, "interface/web", "whab_web_plugin", "other")
    misplaced = plugin_metadata(tmp_path, "interface/third-party", "whab_plaid_plugin", "whab-plaid-plugin")
    assert installer.remove_legacy_plugin_metadata(tmp_path) == 1
    assert not legacy.exists()
    assert all(item.is_dir() for item in (current, unrelated, unrecognized, misplaced))


def test_plugin_cleanup_eliminates_duplicate_registrations_from_shared_editable_source(tmp_path):
    legacy = plugin_metadata(tmp_path, "storage/sqlite", "whab_sqlite_plugin", "whab-sqlite-plugin")
    current = plugin_metadata(tmp_path, "storage/sqlite", "whab_plugin_sqlite", "whab_plugin_sqlite")
    for metadata_dir, module in ((legacy, "whab_sqlite"), (current, "whab_plugin_sqlite")):
        (metadata_dir / "entry_points.txt").write_text(f"[whab.plugins]\nsqlite = {module}.plugin:plugin\n")

    def plugin_entry_points():
        return [
            entry_point.value
            for distribution in installer.metadata.Distribution.discover(path=[str(legacy.parent)])
            for entry_point in distribution.entry_points
            if entry_point.group == "whab.plugins"
        ]

    assert len(plugin_entry_points()) == 2
    assert installer.remove_legacy_plugin_metadata(tmp_path) == 1
    assert plugin_entry_points() == ["whab_plugin_sqlite.plugin:plugin"]


@pytest.mark.parametrize("linked_part", ["plugins", "storage", "sqlite", "src", "whab_sqlite_plugin.egg-info", "PKG-INFO"])
def test_plugin_cleanup_preserves_linked_metadata_and_parent_paths(tmp_path, linked_part):
    legacy = plugin_metadata(tmp_path, "storage/sqlite", "whab_sqlite_plugin", "whab-sqlite-plugin")
    linked = next(part for part in [legacy / "PKG-INFO", legacy, *legacy.parents] if part.name == linked_part)
    actual = tmp_path / "preserved"
    is_dir = linked.is_dir()
    linked.rename(actual)
    linked.symlink_to(actual, target_is_directory=is_dir)
    assert installer.remove_legacy_plugin_metadata(tmp_path) == 0
    assert legacy.is_dir()
    assert (legacy / "PKG-INFO").is_file()


def test_successful_source_install_cleans_metadata_after_local_projects_install(tmp_path, monkeypatch):
    legacy = legacy_metadata(tmp_path)
    for folder in ("plugins/interface/web", "plugins/storage/sqlite", "plugins/example/example", "devtools/plugin-devkit"):
        project = tmp_path / folder / "pyproject.toml"
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
        "-e", str(tmp_path / "devtools/plugin-devkit"),
    ]
    assert options == {"cwd": tmp_path, "check": True}
    assert not legacy.exists()


def test_normal_source_install_does_not_install_developer_toolkit(tmp_path, monkeypatch):
    project = tmp_path / "devtools/plugin-devkit/pyproject.toml"
    project.parent.mkdir(parents=True)
    project.write_text("")
    commands = []
    monkeypatch.setattr(installer, "ROOT", tmp_path)
    monkeypatch.setattr(installer.subprocess, "run", lambda command, **kwargs: commands.append(command))
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    installer.main()
    assert str(project.parent) not in commands[0]


@pytest.mark.parametrize("example_option, legacy_example", [(False, False), (True, False), (False, True)])
def test_source_install_preserves_the_existing_example_selection(tmp_path, monkeypatch, example_option, legacy_example):
    for folder in ("storage/sqlite", "example/example"):
        project = tmp_path / "plugins" / folder / "pyproject.toml"
        project.parent.mkdir(parents=True)
        project.write_text("")
    if legacy_example:
        installed_distributions(monkeypatch, "whab-example-plugin")
    commands = []
    monkeypatch.setattr(installer, "ROOT", tmp_path)
    monkeypatch.setattr(installer.subprocess, "run", lambda command, **kwargs: commands.append(command))
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)] + (["--example"] if example_option else []))
    installer.main()
    assert (str(tmp_path / "plugins/example/example") in commands[0]) == (example_option or legacy_example)
    assert len(commands) == (2 if legacy_example else 1)


def test_upgrade_removes_only_legacy_distributions_after_successful_install(tmp_path, monkeypatch):
    legacy = legacy_metadata(tmp_path)
    legacy_plugin = plugin_metadata(tmp_path, "storage/sqlite", "whab_sqlite_plugin", "whab-sqlite-plugin")
    for relative in ("budgets/saved/manifest.json", "tls/server.key", "data/whab-sandbox.sqlite", ".env"):
        runtime_file = tmp_path / relative
        runtime_file.parent.mkdir(parents=True, exist_ok=True)
        runtime_file.write_text("preserved runtime data")
    installed_distributions(
        monkeypatch,
        "whab-plaid-plugin",
        "whab_sqlite_plugin",
        "whab-plugin-plaid",
        "whab-custom-plugin",
    )
    commands = []

    def run(command, **kwargs):
        assert legacy.exists()
        assert legacy_plugin.exists()
        assert kwargs == {"cwd": tmp_path, "check": True}
        commands.append(command)

    monkeypatch.setattr(installer, "ROOT", tmp_path)
    monkeypatch.setattr(installer.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    installer.main()
    assert commands[0][3] == "install"
    assert commands[1] == [
        sys.executable, "-m", "pip", "uninstall", "--disable-pip-version-check", "--yes",
        "whab-plaid-plugin", "whab-sqlite-plugin",
    ]
    assert not legacy.exists()
    assert not legacy_plugin.exists()
    for relative in ("budgets/saved/manifest.json", "tls/server.key", "data/whab-sandbox.sqlite", ".env"):
        assert (tmp_path / relative).read_text() == "preserved runtime data"


def test_failed_source_install_preserves_legacy_metadata(tmp_path, monkeypatch):
    legacy = legacy_metadata(tmp_path)
    legacy_plugin = plugin_metadata(tmp_path, "storage/sqlite", "whab_sqlite_plugin", "whab-sqlite-plugin")
    installed_distributions(monkeypatch, "whab-sqlite-plugin")
    commands = []

    def fail(command, **kwargs):
        commands.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(installer, "ROOT", tmp_path)
    monkeypatch.setattr(installer.subprocess, "run", fail)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    with pytest.raises(subprocess.CalledProcessError):
        installer.main()
    assert legacy.is_dir()
    assert legacy_plugin.is_dir()
    assert len(commands) == 1
    assert commands[0][3] == "install"


def test_failed_legacy_uninstall_is_reported_before_cleaning_metadata(tmp_path, monkeypatch):
    legacy = legacy_metadata(tmp_path)
    legacy_plugin = plugin_metadata(tmp_path, "storage/sqlite", "whab_sqlite_plugin", "whab-sqlite-plugin")
    installed_distributions(monkeypatch, "whab-sqlite-plugin")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[3] == "uninstall":
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(installer, "ROOT", tmp_path)
    monkeypatch.setattr(installer.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    with pytest.raises(subprocess.CalledProcessError):
        installer.main()
    assert legacy.is_dir()
    assert legacy_plugin.is_dir()
    assert len(commands) == 2


@pytest.mark.parametrize("enabled", [False, True])
def test_reviewed_runtime_fixtures_are_explicit_and_imply_development_tools(tmp_path, monkeypatch, enabled):
    toolkit = tmp_path / "devtools/plugin-devkit"
    fixture = toolkit / "fixtures/runtime-peers"
    fixture.mkdir(parents=True)
    (toolkit / "pyproject.toml").write_text("")
    (fixture / "pyproject.toml").write_text("")
    commands = []
    monkeypatch.setattr(installer, "ROOT", tmp_path)
    monkeypatch.setattr(installer.subprocess, "run", lambda command, **_: commands.append(command))
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--runtime-fixtures"] if enabled else [str(SCRIPT), "--dev"])
    installer.main()
    assert str(tmp_path) + "[dev]" in commands[0]
    assert (str(fixture) in commands[0]) is enabled
    assert str(toolkit) in commands[0]


def test_missing_requested_runtime_fixture_source_fails_before_install(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "ROOT", tmp_path)
    monkeypatch.setattr(installer.subprocess, "run", lambda *_, **__: pytest.fail("Do not install an incomplete requested fixture source"))
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--runtime-fixtures"])
    with pytest.raises(SystemExit) as rejected:
        installer.main()
    assert rejected.value.code == 2
