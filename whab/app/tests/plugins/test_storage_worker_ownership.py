"""Native actors and private journal owners survive partial construction/reaping."""

import subprocess
import sys

import pytest

from whab.plugins import PluginError, StorageContext, report_worker, storage_worker
from whab.plugins.effect_store import EffectError
from whab.plugins.storage_intents import StorageIntents
from whab.plugins.storage_proxy import StorageWorkerProxy
from whab.plugins.storage_worker import StorageWorker
from whab.plugins.storage_worker_codec import encode


def malformed_child(monkeypatch):
    popen = subprocess.Popen
    packet = encode({"kind": "invalid-ready"})
    source = f"import sys;sys.stdout.buffer.write({packet!r});sys.stdout.buffer.flush();sys.stdin.buffer.read()"
    monkeypatch.setattr(storage_worker.subprocess, "Popen", lambda _args, **kwargs: popen(
        [sys.executable, "-I", "-B", "-c", source], **kwargs))
    monkeypatch.setattr(report_worker, "_stop_process_group", lambda _process: (_ for _ in ()).throw(OSError("fictional private reap")))


def test_native_constructor_retains_allocated_actor_before_real_bad_hello_and_failed_reap(monkeypatch):
    allocated = []
    stop = report_worker._stop_process_group
    malformed_child(monkeypatch)

    def own(worker):
        assert worker._process is None and worker._starting
        allocated.append(worker)

    try:
        with pytest.raises(PluginError):
            StorageWorker("json", on_create=own)
        assert len(allocated) == 1 and allocated[0].cleanup_pending
        with pytest.raises(PluginError):
            allocated[0].close()
        assert allocated[0].cleanup_pending
    finally:
        monkeypatch.setattr(report_worker, "_stop_process_group", stop)
        for worker in allocated:
            worker.close()
    assert not allocated[0].healthy and not allocated[0].cleanup_pending


def test_proxy_constructor_keeps_intent_owner_until_native_close_is_confirmed(tmp_path, monkeypatch):
    worker = StorageWorker("json")
    close = worker.close
    validate = StorageIntents.validate_current
    allocated = []

    def invalid(owner, evidence):
        validate(owner, evidence)
        raise ValueError("fictional invalid startup evidence")

    monkeypatch.setattr(StorageIntents, "validate_current", invalid)
    monkeypatch.setattr(worker, "close", lambda: (_ for _ in ()).throw(storage_worker.storage_error("json", "storage_cleanup_pending")))
    try:
        with pytest.raises(PluginError):
            StorageWorkerProxy(worker, StorageContext(tmp_path, "sandbox", {}), tmp_path / "intents", on_create=allocated.append)
        backend = allocated[0]
        evidence = worker.call("commit_evidence")
        assert backend.worker_owned() and backend.intents.snapshot()["pending"] is None
        with pytest.raises(PluginError):
            backend.close()
        with pytest.raises(EffectError):
            StorageIntents(tmp_path / "intents" / evidence.instance_id, "json", evidence)
        assert not backend._closed
    finally:
        monkeypatch.setattr(worker, "close", close)
        for backend in allocated:
            backend.close()
    owner = StorageIntents(tmp_path / "intents" / evidence.instance_id, "json", evidence)
    owner.close()
    assert backend._closed


def test_partial_recovery_child_keeps_proxy_intents_owned_until_explicit_cleanup(tmp_path, monkeypatch):
    backend = StorageWorkerProxy(StorageWorker("sqlite"), StorageContext(tmp_path, "sandbox", {}), tmp_path / "intents")
    evidence = backend.commit_evidence()
    stop = report_worker._stop_process_group
    malformed_child(monkeypatch)
    try:
        with pytest.raises(PluginError):
            backend.recover()
        assert backend.worker_recovery_required() and backend.worker_cleanup_pending()
        with pytest.raises(PluginError):
            backend.close()
        with pytest.raises(EffectError):
            StorageIntents(tmp_path / "intents" / evidence.instance_id, "sqlite", evidence)
    finally:
        monkeypatch.setattr(report_worker, "_stop_process_group", stop)
        backend.close()
    assert not backend.worker_owned() and backend._closed


def test_native_code_journal_stays_owned_when_recovery_replacement_cannot_be_reaped(tmp_path, monkeypatch):
    import asyncio

    from whab.app import create_app
    from whab.budgets import storage_upgrade

    from .test_storage_upgrade import configuration
    from .test_storage_upgrade_barriers import finish, prepared_fixture

    async def scenario():
        app = create_app(settings=configuration(tmp_path, "json"))
        prepared = prepared_fixture(tmp_path, "json")
        monkeypatch.setattr(storage_upgrade, "prepare_environment", lambda *_args, **_kwargs: prepared)
        controller = app.state.storage_upgrade
        stop = report_worker._stop_process_group
        try:
            await controller.upgrade("json", ())
            backend = app.state.tracked_store.store.backend
            malformed_child(monkeypatch)
            with pytest.raises(PluginError):
                backend.recover()
            assert backend.worker_cleanup_pending()
            with pytest.raises(PluginError):
                controller.release()
            with pytest.raises(PluginError):
                await controller.close()
            with pytest.raises(EffectError):
                storage_upgrade._journal(tmp_path, "default", "sandbox", "json")
            assert not controller.closed
        finally:
            monkeypatch.setattr(report_worker, "_stop_process_group", stop)
            await finish(app)
        assert controller.closed
    asyncio.run(scenario())
