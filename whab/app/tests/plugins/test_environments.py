"""Versioned environments install only approved local artifacts and fictional reports."""

import base64
import csv
import hashlib
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from whab import __version__
from whab.plugins import BudgetSnapshot
from whab.plugins import environments as environments
from whab.plugins.environments import (
    ApprovedWheel,
    EnvironmentPreparationError,
    inspect_wheel,
    prepare_environment,
)
from whab.plugins.report_worker import ReportWorker


def test_application_upgrades_real_reports_without_losing_ledger_or_reopen_version(tmp_path, local_closure, store, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from whab.app import create_app
    from whab.config import Settings
    from whab.plugins import (
        ExportTable,
        Insight,
        Plugin,
        PluginContract,
        PluginError,
        PluginRegistry,
    )

    template = Plugin("fictional-reports", "Fictional reports",
                      insights=lambda _: (Insight("Version", "host"),),
                      export=lambda _: ExportTable(("version",), (("host",),)),
                      contract=PluginContract(lifecycle="live"))
    entry = SimpleNamespace(name=template.id, group="whab.plugins", load=lambda: template)
    monkeypatch.setattr("whab.plugins.management.installed_entrypoints", lambda: (entry,))
    monkeypatch.setattr("whab.app.installed_entrypoints", lambda: (entry,))
    configuration = Settings(tmp_path / "runtime", 5173, "sandbox", "", "", "", False,
                             "", "", (template.id,), ui="")
    def registry():
        return PluginRegistry((template,))
    app = create_app(store=store, settings=configuration, plugins=registry())

    def settle(client):
        async def wait():
            if app.state.live_plugins._tasks:
                await asyncio.gather(*tuple(app.state.live_plugins._tasks), return_exceptions=True)
        client.portal.call(wait)

    def upgrade(client, wheel):
        response = client.post("/api/plugins/management/upgrade", json={
            "pluginId": template.id,
            "wheels": [{"path": str(item.path), "sha256": item.sha256} for item in (*local_closure, approve(wheel))],
        })
        assert response.status_code == 202
        settle(client)
        return next(item for item in client.get("/api/plugins/runtime").json()["operations"] if item["id"] == response.json()["id"])

    def exported(client):
        response = client.get("/api/plugins/fictional-reports/export?workspace=personal&month=2026-10")
        assert response.status_code == 200
        return response.text

    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        account = client.post("/api/accounts", json={"workspace": "personal", "name": "Fictional stable account",
                                                   "type": "depository", "balance": 12345}).json()["accountId"]
        ledger, revision = app.state.ledger_service, app.state.ledger_service.revision
        assert "host" in exported(client)
        assert upgrade(client, make_wheel(tmp_path, "1.0.0"))["state"] == "complete"
        first = app.state.budget_runtime._active.services["plugins"].workers[template.id]
        assert "1.0.0" in exported(client) and first.worker.healthy
        broken = make_wheel(tmp_path, "3.0.0", source="raise RuntimeError('synthetic-private-startup')\n")
        assert upgrade(client, broken)["state"] == "failed"
        assert "1.0.0" in exported(client) and first.worker.healthy
        assert app.state.budget_runtime.status()["generation"] == 2
        broken_hook = make_wheel(tmp_path, "3.1.0", source=(
            "from whab.plugins import Plugin, PluginContract, Insight\n"
            "def broken(snapshot):\n    raise RuntimeError('synthetic-private-hook-detail')\n"
            "plugin=Plugin('fictional-reports','Fictional reports',insights=broken,contract=PluginContract(lifecycle='live'))\n"
        ))
        assert upgrade(client, broken_hook)["state"] == "failed"
        assert "1.0.0" in exported(client) and first.worker.healthy
        assert app.state.budget_runtime.status()["generation"] == 2
        from whab.plugins import worker_selection

        original_save = worker_selection.save_reference

        def lost_response(home, prepared):
            original_save(home, prepared)
            raise OSError("synthetic failure after durable report decision")

        monkeypatch.setattr(worker_selection, "save_reference", lost_response)
        assert upgrade(client, make_wheel(tmp_path, "2.0.0"))["state"] == "failed"
        assert app.state.budget_runtime.status()["health"] == "recovery_required"
        monkeypatch.setattr(worker_selection, "save_reference", original_save)
        refused = client.post("/api/plugins/management/recover", json={"authority": "active"})
        assert refused.status_code == 409 and refused.json()["code"] == "recovery_failed"
        recovered = client.post("/api/plugins/management/recover", json={"authority": "candidate"})
        assert recovered.status_code == 200 and recovered.json()["generation"] == 3
        second = app.state.budget_runtime._active.services["plugins"].workers[template.id]
        assert "2.0.0" in exported(client) and not first.worker.healthy
        assert app.state.ledger_service is ledger and ledger.revision == revision
        for selection in ([], [template.id]):
            response = client.post("/api/plugins/management/transition", json={"plugins": selection})
            assert response.status_code == 202
            settle(client)
        assert not second.worker.healthy and "2.0.0" in exported(client)
        versions = client.get("/api/plugins/runtime").json()["workers"]
        assert versions[0]["version"] == "2.0.0" and versions[0]["health"] == "ready"
        assert "fictional_reports" not in sys.modules
    # A fresh application loads its saved content-addressed environment rather
    # than the host's still-installed report implementation.
    app = create_app(store=store, settings=configuration, plugins=registry())
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        assert "2.0.0" in exported(client)
        found = client.get("/api/workspace?workspace=personal").json()["accounts"][0]
        assert found["id"] == account and found["balance"] == 12345
    code, = second.environment.path.glob("environment/lib/python*/site-packages/fictional_reports/__init__.py")
    code.write_text("raise RuntimeError('tampered environment must not execute')\n")
    with pytest.raises(PluginError) as raised:
        create_app(store=store, settings=configuration, plugins=registry())
    assert raised.value.code == "worker_environment_unavailable"


def approve(path):
    return ApprovedWheel(path, hashlib.sha256(path.read_bytes()).hexdigest())


def make_wheel(directory, version="1.0.0", *, source=None, metadata_suffix="", entrypoint="fictional_reports:plugin", extra_files=None):
    name = "whab_plugin_fictional_reports"
    prefix = f"{name}-{version}.dist-info/"
    source = source if source is not None else (
        "from whab.plugins import Plugin, PluginContract, Insight, ExportTable\n"
        f"def insights(snapshot):\n    return (Insight('Version', '{version}'),)\n"
        f"def export(snapshot):\n    return ExportTable(('version',), (('{version}',),))\n"
        "plugin = Plugin('fictional-reports', 'Fictional reports', insights=insights, export=export, contract=PluginContract(lifecycle='live'))\n"
    )
    files = {"fictional_reports/__init__.py": source.encode(),
             prefix + "METADATA": (f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\nRequires-Python: >=3.14\nRequires-Dist: whab=={__version__}\n" + metadata_suffix).encode(),
             prefix + "WHEEL": b"Wheel-Version: 1.0\nGenerator: reviewed-test-fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
             prefix + "entry_points.txt": f"[whab.plugins]\nfictional-reports = {entrypoint}\n".encode(),
             **(extra_files or {})}
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for path, raw in files.items():
        writer.writerow((path, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("="), str(len(raw))))
    writer.writerow((prefix + "RECORD", "", ""))
    files[prefix + "RECORD"] = record.getvalue().encode()
    path = directory / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, raw in files.items():
            archive.writestr(filename, raw)
    return path


def test_static_inspection_exposes_identity_requirements_and_hash_without_importing(tmp_path):
    wheel = make_wheel(tmp_path, source="raise RuntimeError('must not execute during inspection')\n")
    record = inspect_wheel(approve(wheel))
    assert record.distribution == "whab_plugin_fictional_reports"
    assert record.canonical_name == "whab-plugin-fictional-reports"
    assert record.version == "1.0.0" and record.sha256 == approve(wheel).sha256
    assert record.requires_python == ">=3.14"
    assert record.requirements == (f"whab=={__version__}",)
    assert record.entry_points == (("whab.plugins", "fictional-reports", "fictional_reports:plugin"),)


def test_explicit_approval_hash_is_required_and_modified_wheels_are_rejected(tmp_path):
    path = make_wheel(tmp_path)
    approved = approve(path)
    with pytest.raises(ValueError):
        ApprovedWheel(path, "not-an-approval")
    with pytest.raises(ValueError):
        inspect_wheel(path)
    with path.open("ab") as target:
        target.write(b"changed")
    with pytest.raises(EnvironmentPreparationError) as raised:
        inspect_wheel(approved)
    assert raised.value.code == "wheel_hash_mismatch"


@pytest.mark.parametrize("name", ["../escape.py", "/absolute.py", "a/../../escape.py", "a\\escape.py", "a:escape.py", "a/./escape.py", "a//escape.py", "a/" * 40 + "file.py", "FICTIONAL_REPORTS/__INIT__.PY"])
def test_wheel_paths_cannot_escape_installation_or_alias_namespaces(tmp_path, name):
    path = make_wheel(tmp_path, extra_files={name: b"unexecuted"})
    with pytest.raises(EnvironmentPreparationError) as raised:
        inspect_wheel(approve(path))
    assert raised.value.code == "invalid_wheel"


@pytest.mark.parametrize("metadata_suffix", ["Name: duplicate\n", "Version: 2.0.0\n", "Requires-Python: >=3.14\n",
                                           "Requires-Dist: arbitrary @ https://invalid.example/plugin.whl\n"])
def test_duplicate_identity_python_fields_and_remote_dependency_references_are_rejected(tmp_path, metadata_suffix):
    path = make_wheel(tmp_path, metadata_suffix=metadata_suffix)
    with pytest.raises(EnvironmentPreparationError) as raised:
        inspect_wheel(approve(path))
    assert raised.value.code == "invalid_wheel"


def test_incompatible_python_and_unsafe_entrypoints_fail_statically(tmp_path):
    path = make_wheel(tmp_path, entrypoint="not a valid entrypoint")
    with pytest.raises(EnvironmentPreparationError) as raised:
        inspect_wheel(approve(path))
    assert raised.value.code == "invalid_wheel"
    path = make_wheel(tmp_path)
    with zipfile.ZipFile(path) as archive:
        files = {info.filename: archive.read(info) for info in archive.infolist()}
    metadata_name = next(name for name in files if name.endswith("/METADATA"))
    files[metadata_name] = files[metadata_name].replace(b">=3.14", b">=99")
    with zipfile.ZipFile(path, "w") as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    with pytest.raises(EnvironmentPreparationError) as raised:
        inspect_wheel(approve(path))
    assert raised.value.code == "incompatible_python"


def test_symlinks_duplicate_entries_record_tampering_and_size_bounds_are_rejected(tmp_path, monkeypatch):
    path = make_wheel(tmp_path)
    link = tmp_path / "alias.whl"
    link.symlink_to(path)
    with pytest.raises(EnvironmentPreparationError):
        inspect_wheel(ApprovedWheel(link, approve(path).sha256))
    with zipfile.ZipFile(path, "a") as archive:
        information = zipfile.ZipInfo("symlink")
        information.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(information, "fictional_reports")
    with pytest.raises(EnvironmentPreparationError):
        inspect_wheel(approve(path))
    path = make_wheel(tmp_path)
    with zipfile.ZipFile(path, "a") as archive:
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("fictional_reports/__init__.py", "changed code")
    with pytest.raises(EnvironmentPreparationError):
        inspect_wheel(approve(path))
    path = make_wheel(tmp_path)
    monkeypatch.setattr(environments, "MAX_WHEEL_BYTES", path.stat().st_size - 1)
    with pytest.raises(EnvironmentPreparationError):
        inspect_wheel(approve(path))


@pytest.fixture(scope="module")
def local_closure(tmp_path_factory):
    """Build reviewed host code; download only its declared runtime closure."""
    base = tmp_path_factory.mktemp("approved-wheelhouse")
    root = Path(__file__).resolve().parents[2]
    source = base / "host-source"
    source.mkdir()
    shutil.copytree(root / "src", source / "src", ignore=shutil.ignore_patterns("*.pyc", "__pycache__", "*.egg-info"))
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(root / name, source / name)
    wheelhouse = base / "wheels"
    subprocess.run([sys.executable, "-I", "-m", "pip", "wheel", "--disable-pip-version-check", "--no-deps", "--wheel-dir", str(wheelhouse), str(source)],
                   cwd=base, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    host, = wheelhouse.glob("whab-*.whl")
    subprocess.run([sys.executable, "-I", "-m", "pip", "download", "--disable-pip-version-check", "--only-binary=:all:", "--dest", str(wheelhouse), str(host)],
                   cwd=base, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    return tuple(approve(path) for path in sorted(wheelhouse.glob("*.whl")))


def test_real_versions_coexist_failed_candidate_preserves_active_worker_and_reuse_detects_tampering(tmp_path, local_closure, monkeypatch):
    root = tmp_path / "environments"
    first_wheel = approve(make_wheel(tmp_path, "1.0.0"))
    first = prepare_environment(root, "fictional-reports", (*local_closure, first_wheel))
    snapshot = BudgetSnapshot("personal", "2026-10", (), (), ())
    worker = ReportWorker(first.plugin_id, python=first.python, expected_distribution=first.distribution, expected_version=first.version)
    ready_before = (first.path / "ready.json").read_bytes()
    code, = first.path.glob("environment/lib/python*/site-packages/fictional_reports/__init__.py")
    code_before = code.read_bytes()
    try:
        assert worker.insights(snapshot)[0].value == "1.0.0"
        assert worker.export(snapshot).rows == (("1.0.0",),)
        broken = approve(make_wheel(tmp_path, "3.0.0", source="raise RuntimeError('synthetic-private-startup-detail')\n"))
        with pytest.raises(EnvironmentPreparationError) as raised:
            prepare_environment(root, "fictional-reports", (*local_closure, broken))
        assert raised.value.code == "environment_failed" and raised.value.plugin_id == "fictional-reports"
        assert "synthetic-private" not in str(raised.value)
        assert worker.insights(snapshot)[0].value == "1.0.0"
        assert len(tuple(root.glob("*/ready.json"))) == 1
        second_wheel = approve(make_wheel(tmp_path, "2.0.0"))
        second = prepare_environment(root, "fictional-reports", (*local_closure, second_wheel))
        assert first.identifier != second.identifier and first.python != second.python
        replacement = ReportWorker(second.plugin_id, python=second.python, expected_distribution=second.distribution, expected_version=second.version)
        try:
            assert replacement.insights(snapshot)[0].value == "2.0.0"
            assert worker.insights(snapshot)[0].value == "1.0.0"
        finally:
            replacement.close()
        assert code.read_bytes() == code_before
        assert (first.path / "ready.json").read_bytes() == ready_before
        reused = prepare_environment(root, "fictional-reports", tuple(reversed((*local_closure, first_wheel))))
        assert reused.identifier == first.identifier
        manifest = json.loads(ready_before)
        assert {item["sha256"] for item in manifest["wheels"]} == {wheel.sha256 for wheel in (*local_closure, first_wheel)}
        assert manifest["plugin"] == "fictional-reports"
        assert "synthetic-private" not in ready_before.decode()
    finally:
        worker.close()
    unexpected = code.parent.parent / "unapproved.pth"
    unexpected.write_text("raise RuntimeError('must not execute unapproved additions')\n")
    with monkeypatch.context() as changes:
        changes.setattr(environments, "_run", lambda *_: pytest.fail("Detect changes before executing the reused environment"))
        with pytest.raises(EnvironmentPreparationError) as raised:
            prepare_environment(root, "fictional-reports", (*local_closure, first_wheel))
        assert raised.value.code == "environment_changed"
    assert unexpected.exists()
    unexpected.unlink()
    (first.path / "ready.json").unlink()
    with pytest.raises(EnvironmentPreparationError) as raised:
        prepare_environment(root, "fictional-reports", (*local_closure, first_wheel))
    assert raised.value.code == "environment_changed" and code.read_bytes() == code_before
    (first.path / "ready.json").write_bytes(ready_before)
    code.write_text("changed after approval\n")
    with pytest.raises(EnvironmentPreparationError) as raised:
        prepare_environment(root, "fictional-reports", (*local_closure, first_wheel))
    assert raised.value.code == "environment_changed"
    assert code.read_text() == "changed after approval\n"
    assert (first.path / "ready.json").read_bytes() == ready_before


def test_concurrent_preparation_builds_identical_content_only_once(tmp_path, local_closure, monkeypatch):
    candidate = approve(make_wheel(tmp_path, "4.0.0"))
    barrier = threading.Barrier(2)
    original_lock, original_run = environments._lock, environments._run
    builds = []

    @contextmanager
    def synchronized_lock(path, deadline):
        barrier.wait(timeout=30)
        with original_lock(path, deadline):
            yield

    def observe_run(command, deadline):
        if command[1:4] == ["-I", "-m", "venv"]:
            builds.append(command)
        return original_run(command, deadline)

    monkeypatch.setattr(environments, "_lock", synchronized_lock)
    monkeypatch.setattr(environments, "_run", observe_run)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(prepare_environment, tmp_path / "environments", "fictional-reports", (*local_closure, candidate)) for _ in range(2)]
        first, second = (future.result(timeout=120) for future in futures)
    assert first.identifier == second.identifier and len(builds) == 1


@pytest.mark.parametrize("declaration", ["", ", contract=PluginContract(lifecycle='live'), settings=PluginSettings(lambda: {})"])
def test_preparation_refuses_legacy_and_stateful_report_lifecycle_without_publication(tmp_path, local_closure, declaration):
    source = ("from whab.plugins import Plugin, PluginContract, PluginSettings, Insight\n"
              "def insights(snapshot): return (Insight('Fictional','1'),)\n"
              "plugin=Plugin('fictional-reports','Fictional',insights=insights" + declaration + ")\n")
    candidate = approve(make_wheel(tmp_path, source=source))
    root = tmp_path / "environments"
    with pytest.raises(EnvironmentPreparationError) as raised:
        prepare_environment(root, "fictional-reports", (*local_closure, candidate))
    assert raised.value.code == "unsupported_lifecycle"
    assert [path.name for path in root.iterdir()] == ["locks"]


def test_source_change_after_approval_is_rejected_after_private_copy(tmp_path, local_closure, monkeypatch):
    candidate = approve(make_wheel(tmp_path, "5.0.0"))
    original_copy = environments.shutil.copyfile

    def changed_copy(source, target, *args, **kwargs):
        result = original_copy(source, target, *args, **kwargs)
        if Path(source) == candidate.path:
            with Path(target).open("ab") as output:
                output.write(b"changed during copy")
        return result

    monkeypatch.setattr(environments.shutil, "copyfile", changed_copy)
    with pytest.raises(EnvironmentPreparationError) as raised:
        prepare_environment(tmp_path / "environments", "fictional-reports", (*local_closure, candidate))
    assert raised.value.code == "wheel_hash_mismatch"
    assert not tuple((tmp_path / "environments").glob("*/ready.json"))


def test_missing_dependencies_and_extra_runtime_plugins_fail_before_installation(tmp_path, local_closure, monkeypatch):
    candidate = approve(make_wheel(tmp_path))
    monkeypatch.setattr(environments, "_run", lambda *_: pytest.fail("Invalid closure must fail before executable installation"))
    for closure in ((candidate,), (*local_closure, candidate, candidate), (*local_closure,)):
        with pytest.raises(EnvironmentPreparationError) as raised:
            prepare_environment(tmp_path / "environments", "fictional-reports", closure)
        assert raised.value.code in {"invalid_closure", "incompatible_host"}


def test_preparation_failure_removes_partial_environment_and_sanitizes_subprocess_output(tmp_path, local_closure, monkeypatch):
    candidate = approve(make_wheel(tmp_path))

    def fail(*_):
        raise subprocess.CalledProcessError(7, ["synthetic-private-command"], output="synthetic-private-output")

    monkeypatch.setattr(environments, "_run", fail)
    root = tmp_path / "environments"
    with pytest.raises(EnvironmentPreparationError) as raised:
        prepare_environment(root, "fictional-reports", (*local_closure, candidate))
    assert raised.value.code == "environment_failed"
    assert "synthetic-private" not in str(raised.value)
    assert [path.name for path in root.iterdir()] == ["locks"]


def test_preparation_cancellation_removes_partial_candidate_and_preserves_cancellation(tmp_path, local_closure, monkeypatch):
    candidate = approve(make_wheel(tmp_path))
    cancellation = KeyboardInterrupt("fictional cancellation")

    def cancel(*_):
        raise cancellation

    monkeypatch.setattr(environments, "_run", cancel)
    root = tmp_path / "environments"
    with pytest.raises(KeyboardInterrupt) as raised:
        prepare_environment(root, "fictional-reports", (*local_closure, candidate))
    assert raised.value is cancellation
    assert [path.name for path in root.iterdir()] == ["locks"]


@pytest.mark.parametrize("outcome", ["timeout", "cancel", "success"])
def test_installer_completion_timeout_and_cancellation_stop_only_the_owned_process_group(tmp_path, monkeypatch, outcome):
    cancel = outcome == "cancel"
    marker = tmp_path / "fictional-child.pid"
    child_program = "import os,pathlib,signal,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
    installer = ("import pathlib,subprocess,sys,time\n"
                 "subprocess.Popen([sys.executable,'-I','-c',sys.argv[1],sys.argv[2]],"
                 "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
                 "deadline=time.monotonic()+5\n"
                 "while not pathlib.Path(sys.argv[2]).exists() and time.monotonic()<deadline: time.sleep(.01)\n"
                 + ("" if outcome == "success" else "time.sleep(60)\n"))
    command = [sys.executable, "-I", "-c", installer, child_program, str(marker)]
    original = subprocess.Popen
    processes = []
    cancellation = KeyboardInterrupt("fictional cancellation")

    def spawn(arguments, **kwargs):
        assert kwargs["start_new_session"] is True
        process = original(arguments, **kwargs)
        processes.append(process)
        if cancel:
            original_wait = process.wait
            pending = [True]

            def wait(*, timeout):
                if pending:
                    pending.clear()
                    deadline = time.monotonic() + 5
                    while not marker.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    assert marker.exists()
                    raise cancellation
                return original_wait(timeout=timeout)

            process.wait = wait
        return process

    def running(pid):
        with original(["ps", "-o", "stat=", "-p", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as check:
            state = check.communicate(timeout=2)[0].strip()
        return bool(state) and not state.startswith(b"Z")

    monkeypatch.setattr(environments.subprocess, "Popen", spawn)
    pid = None
    try:
        if outcome == "success":
            environments._run(command, time.monotonic() + 5)
        else:
            with pytest.raises(KeyboardInterrupt if cancel else subprocess.TimeoutExpired) as raised:
                environments._run(command, time.monotonic() + (5 if cancel else 0.3))
            if cancel:
                assert raised.value is cancellation
        process, = processes
        pid = int(marker.read_text())
        deadline = time.monotonic() + 2
        while running(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not running(pid)
        assert process.returncode is not None
        assert process.pid != os.getpgrp()
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
        if pid is not None and running(pid):
            os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("cancel", [False, True])
def test_installer_cleanup_failure_is_authored_and_does_not_replace_cancellation(monkeypatch, cancel):
    failure = KeyboardInterrupt("fictional cancellation") if cancel else subprocess.TimeoutExpired("fictional", 1)

    def fail_wait(*_, **__):
        raise failure

    process = type("FictionalProcess", (), {"wait": fail_wait})()
    monkeypatch.setattr(environments.subprocess, "Popen", lambda *_, **__: process)

    def fail_cleanup(_):
        raise OSError("synthetic-private-cleanup")

    monkeypatch.setattr(environments, "_stop_process_group", fail_cleanup)
    with pytest.raises(KeyboardInterrupt if cancel else EnvironmentPreparationError) as raised:
        environments._run(["fictional"], time.monotonic() + 1)
    if cancel:
        assert raised.value is failure
    else:
        assert raised.value.code == "environment_cleanup_failed"
        assert "synthetic-private" not in str(raised.value)


def test_expired_preparation_budget_stops_before_installation(tmp_path, local_closure, monkeypatch):
    candidate = approve(make_wheel(tmp_path))
    monkeypatch.setattr(environments, "_run", lambda *_: pytest.fail("An expired preparation cannot install code"))
    with pytest.raises(EnvironmentPreparationError) as raised:
        prepare_environment(tmp_path / "environments", "fictional-reports", (*local_closure, candidate), timeout=1e-9)
    assert raised.value.code == "environment_timeout"
    assert not tuple((tmp_path / "environments").glob("*/ready.json"))


def test_durability_failure_cannot_publish_a_usable_environment(tmp_path, local_closure, monkeypatch):
    candidate = approve(make_wheel(tmp_path, "6.0.0"))
    original = environments._installation_hash

    def fail_durability(path, *, durable=False):
        assert not (path / "ready.json").exists()
        if durable:
            raise OSError("synthetic-private-filesystem-detail")
        return original(path)

    monkeypatch.setattr(environments, "_installation_hash", fail_durability)
    root = tmp_path / "environments"
    with pytest.raises(EnvironmentPreparationError) as raised:
        prepare_environment(root, "fictional-reports", (*local_closure, candidate))
    assert raised.value.code == "environment_failed"
    assert "synthetic-private" not in str(raised.value)
    assert [path.name for path in root.iterdir()] == ["locks"]


def test_root_filesystem_failures_are_sanitized_before_any_install(tmp_path, monkeypatch):
    candidate = approve(make_wheel(tmp_path))
    root = tmp_path / "environments"
    original = Path.mkdir

    def fail(path, *args, **kwargs):
        if path == root:
            raise OSError("synthetic-private-directory-detail")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail)
    with pytest.raises(EnvironmentPreparationError) as raised:
        prepare_environment(root, "fictional-reports", (candidate,))
    assert raised.value.code == "environment_failed" and "synthetic-private" not in str(raised.value)
