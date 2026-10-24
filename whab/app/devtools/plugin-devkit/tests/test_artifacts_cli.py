import hashlib
import json
import runpy
import sys
import zipfile
from types import SimpleNamespace

import pytest
from whab_plugin_devkit import artifacts as artifacts
from whab_plugin_devkit import cli as cli

from . import test_configurations as fixtures

artifacts_data = fixtures.artifacts
candidate = fixtures.candidate
reference = fixtures.reference


def wheel_fixture(root):
    installed = root / "installed"
    wheelhouse = root / "wheels"
    installed.mkdir()
    wheelhouse.mkdir()
    files = {"fictional/__init__.py": b"# reviewed fictional code\n", "fictional-1.dist-info/METADATA": b"Name: fictional\nVersion: 1\n",
             "fictional-1.dist-info/WHEEL": b"Wheel-Version: 1.0\n", "fictional-1.dist-info/RECORD": b""}
    wheel = wheelhouse / "fictional-1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
            path = installed / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    distribution = SimpleNamespace(version="1", locate_file=lambda name: installed / name)
    return wheelhouse, installed, wheel, distribution


def test_artifact_evidence_binds_installed_files_versions_and_exact_wheel_hash(tmp_path, monkeypatch):
    wheelhouse, installed, wheel, distribution = wheel_fixture(tmp_path)
    monkeypatch.setattr(artifacts.metadata, "distribution", lambda _: distribution)
    result = artifacts.installed_artifacts(wheelhouse, ["fictional"])
    assert result == [{"distribution": "fictional", "version": "1", "filename": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}]
    (installed / "fictional/__init__.py").write_text("changed code")
    with pytest.raises(ValueError, match="clean wheel installation"):
        artifacts.installed_artifacts(wheelhouse, ["fictional"])


def test_missing_or_wrong_installed_versions_do_not_count_as_approved_artifacts(tmp_path, monkeypatch):
    wheelhouse, _, _, distribution = wheel_fixture(tmp_path)
    monkeypatch.setattr(artifacts.metadata, "distribution", lambda _: SimpleNamespace(version="2"))
    with pytest.raises(ValueError):
        artifacts.installed_artifacts(wheelhouse, ["fictional"])

    def missing(_):
        raise artifacts.metadata.PackageNotFoundError()

    monkeypatch.setattr(artifacts.metadata, "distribution", missing)
    with pytest.raises(ValueError):
        artifacts.installed_artifacts(wheelhouse, ["fictional"])
    monkeypatch.setattr(artifacts.metadata, "distribution", lambda _: distribution)
    assert artifacts.normalized("Whab_Plugin.Fictional") == "whab-plugin-fictional"


def test_artifacts_refuse_ambiguous_duplicate_wheels_symlinks_and_missing_schema(tmp_path, monkeypatch):
    wheelhouse, _, wheel, distribution = wheel_fixture(tmp_path)
    monkeypatch.setattr(artifacts.metadata, "distribution", lambda _: distribution)
    alias = wheelhouse / "alias.whl"
    alias.symlink_to(wheel)
    with pytest.raises(ValueError):
        artifacts.installed_artifacts(wheelhouse, ["fictional"])
    alias.unlink()
    alias.write_bytes(wheel.read_bytes())
    with pytest.raises(ValueError):
        artifacts.installed_artifacts(wheelhouse, ["fictional"])
    alias.unlink()
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("fictional-1.dist-info/METADATA", "Name: fictional\nVersion: 1\n")
    with pytest.raises(ValueError):
        artifacts.installed_artifacts(wheelhouse, ["fictional"])


@pytest.mark.parametrize("failure", ["expanded-size", "member-count", "metadata-size", "duplicate-member", "duplicate-identity", "escape", "unsupported-scheme"])
def test_wheel_evidence_bounds_archive_work_and_refuses_ambiguous_install_paths(tmp_path, monkeypatch, failure):
    wheelhouse, _, wheel, distribution = wheel_fixture(tmp_path)
    monkeypatch.setattr(artifacts.metadata, "distribution", lambda _: distribution)
    if failure in {"expanded-size", "member-count", "metadata-size"}:
        original = zipfile.ZipFile.infolist

        def bounded_fixture(archive):
            entries = original(archive)
            if failure == "expanded-size":
                entries[0].file_size = 300 * 1024 * 1024 + 1
            elif failure == "member-count":
                entries *= 20001
            else:
                archive.getinfo("fictional-1.dist-info/METADATA").file_size = 1024 * 1024 + 1
            return entries

        monkeypatch.setattr(zipfile.ZipFile, "infolist", bounded_fixture)
    else:
        with zipfile.ZipFile(wheel, "a") as archive:
            if failure == "duplicate-member":
                with pytest.warns(UserWarning, match="Duplicate name"):
                    archive.writestr("fictional/__init__.py", "duplicate")
            elif failure == "duplicate-identity":
                # Rewrite rather than add a second metadata member: its identity
                # fields are ambiguous even though the ZIP member is unique.
                pass
            elif failure == "escape":
                archive.writestr("../outside.py", "unsafe")
            else:
                archive.writestr("fictional-1.data/scripts/unsafe", "unsupported")
        if failure == "duplicate-identity":
            with zipfile.ZipFile(wheel, "r") as archive:
                files = {entry.filename: archive.read(entry) for entry in archive.infolist()}
            files["fictional-1.dist-info/METADATA"] += b"Name: second\n"
            with zipfile.ZipFile(wheel, "w") as archive:
                for name, raw in files.items():
                    archive.writestr(name, raw)
    with pytest.raises(ValueError, match="Installed artifacts"):
        artifacts.installed_artifacts(wheelhouse, ["fictional"])


def test_installed_purelib_and_platlib_records_relocate_within_distribution_root(tmp_path, monkeypatch):
    wheelhouse, installed, wheel, distribution = wheel_fixture(tmp_path)
    monkeypatch.setattr(artifacts.metadata, "distribution", lambda _: distribution)
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("fictional/", b"")
        for scheme in ("purelib", "platlib"):
            archive.writestr(f"fictional-1.data/{scheme}/{scheme}.py", b"# fictional\n")
            (installed / f"{scheme}.py").write_bytes(b"# fictional\n")
    assert artifacts.installed_artifacts(wheelhouse, ["fictional"])


@pytest.mark.parametrize(("requirement", "accepted"), [
    ("fictional>=1,<2", True), ("absent; python_version < '3'", True),
    ("absent; extra == 'optional'", True), ("absent>=1", False),
    ("fictional>=2", False), ("fictional[unsupported]>=1", False),
    ("fictional @ https://invalid.example/never-download.whl", False),
    ("invalid requirement !!", False), ("fictional==" + "1" * 2048, False),
])
def test_required_closure_honors_markers_versions_and_rejects_urls_or_unverified_dependencies(tmp_path, monkeypatch, requirement, accepted):
    wheelhouse, installed, wheel, distribution = wheel_fixture(tmp_path)
    monkeypatch.setattr(artifacts.metadata, "distribution", lambda _: distribution)
    with zipfile.ZipFile(wheel, "r") as archive:
        files = {entry.filename: archive.read(entry) for entry in archive.infolist()}
    files["fictional-1.dist-info/METADATA"] += f"Requires-Dist: {requirement}\n".encode()
    (installed / "fictional-1.dist-info/METADATA").write_bytes(files["fictional-1.dist-info/METADATA"])
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    if accepted:
        assert artifacts.installed_artifacts(wheelhouse, ["fictional"])
    else:
        with pytest.raises(ValueError, match="Installed artifacts"):
            artifacts.installed_artifacts(wheelhouse, ["fictional"])


def test_dependency_graph_is_transitive_and_bounded():
    versions = {"root": {"version": "1"}, "middle": {"version": "1"}, "leaf": {"version": "1"}}
    dependencies = {"root": ["middle==1"], "middle": ["leaf>=1", "root==1"], "leaf": []}
    artifacts._validate_closure(versions, dependencies, ["root"])
    with pytest.raises(ValueError):
        artifacts._validate_closure(versions, {**dependencies, "leaf": ["missing==1"]}, ["root"])
    with pytest.raises(ValueError):
        artifacts._validate_closure(versions, {**dependencies, "root": ["middle==1"] * 513}, ["root"])
    versions = {f"plugin-{index}": {"version": "1"} for index in range(129)}
    with pytest.raises(ValueError):
        artifacts._validate_closure(versions, {name: [] for name in versions}, list(versions))


def selected_entries(candidate, reference):
    return tuple(SimpleNamespace(name=plugin.id, dist=SimpleNamespace(metadata={"Name": plugin.id}), load=lambda plugin=plugin: plugin)
                 for plugin in (candidate, reference))


def test_selection_verifies_wheel_evidence_before_loading_any_descriptor(monkeypatch, candidate, reference):
    calls = []
    entries = selected_entries(candidate, reference)
    for entry in entries:
        load = entry.load
        entry.load = lambda load=load: (calls.append("load"), load())[1]
    monkeypatch.setattr(cli.metadata, "entry_points", lambda **_: entries)
    monkeypatch.setattr(cli, "installed_artifacts", lambda *_: calls.append("verify") or [])
    assert cli._selected("candidate", "fictional-wheels")[:2] == (candidate, reference)
    assert calls == ["verify", "load", "load"]
    monkeypatch.setattr(cli.metadata, "entry_points", lambda **_: ())
    with pytest.raises(ValueError, match="exactly one"):
        cli._selected("candidate", "fictional-wheels")


def test_cli_new_check_and_replay_preserve_manifests_and_hide_failure_payloads(tmp_path, monkeypatch, capsys, candidate, reference, artifacts_data):
    assert cli.main(["new", "created", "--capability", "reporting", "--directory", str(tmp_path / "created")]) == 0
    monkeypatch.chdir(tmp_path)
    assert cli.main(["new", "default", "--capability", "branding"]) == 0
    monkeypatch.setattr(cli, "_selected", lambda *_: (candidate, reference, artifacts_data))
    output = tmp_path / "checks"
    assert cli.main(["check", "--plugin", "candidate", "--wheelhouse", "unused", "--output", str(output)]) == 0
    assert len(list(output.glob("configuration-*.json"))) == 10
    manifest = output / "configuration-00.json"
    assert cli.main(["replay", str(manifest), "--wheelhouse", "unused"]) == 0
    assert cli.main(["check", "--plugin", "candidate", "--wheelhouse", "unused", "--output", str(output)]) == 1
    changed = json.loads(manifest.read_text())
    changed["artifacts"][0]["sha256"] = "1" * 64
    manifest.write_text(json.dumps(changed))
    assert cli.main(["replay", str(manifest), "--wheelhouse", "unused"]) == 1
    manifest.write_bytes(b"x" * (1024 * 1024 + 1))
    assert cli.main(["replay", str(manifest), "--wheelhouse", "unused"]) == 1
    assert cli.main(["new", "../unsafe", "--capability", "reporting"]) == 1
    assert "synthetic-private" not in capsys.readouterr().err


def test_cli_module_entrypoint_has_a_real_exit_status(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["whab-plugin", "new", "entrypoint", "--capability", "reporting", "--directory", str(tmp_path / "entrypoint")])
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        with pytest.raises(SystemExit) as raised:
            runpy.run_module("whab_plugin_devkit.cli", run_name="__main__")
    assert raised.value.code == 0
