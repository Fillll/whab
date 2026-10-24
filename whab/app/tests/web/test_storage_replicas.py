"""Real local adapters copy synthetic budgets without changing primary success."""

import asyncio
import os
import signal
import subprocess
import sys
from dataclasses import replace

import pytest

from tests.web.test_budget_sessions import settings
from tests.web.test_live_plugins import running
from tests.web.test_storage_migration import migrate
from whab.app import create_app
from whab.budgets.host import BudgetHost
from whab.budgets.session import BudgetSession


async def attach(client, app, destination, prefix=""):
    preview = await client.post(f"{prefix}/api/storage/copies/preview", json={"destination": destination})
    assert preview.status_code == 200, preview.text
    response = await client.post(f"{prefix}/api/storage/copies/transition", json={"preview_id": preview.json()["preview_id"]})
    assert response.status_code == 202, response.text
    await app.state.storage_replicas._synchronize()
    return response.json()["id"]


async def promoted(app):
    if app.state.storage_replicas.tasks:
        await asyncio.gather(*tuple(app.state.storage_replicas.tasks))


@pytest.mark.parametrize(("primary", "copy"), [("sqlite", "json"), ("json", "sqlite")])
def test_copy_catches_up_late_write_promotes_and_reopens_without_primary_reversion(tmp_path, primary, copy):
    async def scenario():
        config = replace(settings(tmp_path), storage=primary)
        app = create_app(settings=config)
        async with running(app) as client:
            before = await client.post("/api/accounts", json={"workspace": "personal", "name": "First", "type": "depository", "balance": 10})
            assert before.status_code == 200
            service = app.state.ledger_service
            identifier = await attach(client, app, copy)
            status = (await client.get("/api/storage/copies")).json()
            assert status["copies"][0]["state"] == "saved", status
            late = await client.post("/api/accounts", json={"workspace": "personal", "name": "Acknowledged later", "type": "depository", "balance": 20})
            assert late.status_code == 200
            response = await client.post(f"/api/storage/copies/{identifier}/promote", json={})
            assert response.status_code == 202, response.text
            await promoted(app)
            status = (await client.get("/api/storage/copies")).json()
            assert status["operations"][-1]["state"] == "complete", status
            assert app.state.storage_plugin == copy
            assert app.state.ledger_service is service
            assert {row["name"] for row in app.state.tracked_store.accounts("personal")} == {"First", "Acknowledged later"}
            later = await client.post("/api/accounts", json={"workspace": "personal", "name": "New primary", "type": "depository", "balance": 30})
            assert later.status_code == 200
        reopened = create_app(settings=config)
        async with running(reopened) as client:
            assert reopened.state.storage_plugin == copy
            assert {row["name"] for row in reopened.state.tracked_store.accounts("personal")} == {"First", "Acknowledged later", "New primary"}
            assert (await client.get("/api/storage/copies")).json()["copies"][0]["state"] == "promoted"
    asyncio.run(scenario())


def test_namespace_only_save_has_own_durable_watermark_and_preserves_disabled_opaque_files(tmp_path):
    async def scenario():
        session = BudgetSession.create(tmp_path, "sandbox", "Fictional", storage="sqlite")
        session.manifest["plugins"].append({"id": "unavailable", "enabled": False, "apiVersion": 1,
                                            "settings": {"score": 0.8}, "state": {}})
        session.files["plugins/unavailable/model.bin"] = b"model-version-one"
        app = create_app(settings=settings(tmp_path), budget_session=session)
        async with running(app) as client:
            identifier = await attach(client, app, "json")
            first = (await client.get("/api/storage/copies")).json()["copies"][0]["watermark"]
            session.write_file("unavailable", "model.bin", b"model-version-two")
            await app.state.storage_replicas._synchronize()
            second = (await client.get("/api/storage/copies")).json()["copies"][0]["watermark"]
            assert second["source_generation"] == first["source_generation"]
            assert second["namespace_generation"] > first["namespace_generation"]
            assert second["namespace_digest"] != first["namespace_digest"]
            archive = app.state.storage_replicas.archive
            archive.validate(first["namespace_generation"], first["namespace_digest"])
            archive.validate(second["namespace_generation"], second["namespace_digest"])
            original_path, original_raw = session.path, session.path.read_bytes()
            response = await client.post(f"/api/storage/copies/{identifier}/promote", json={})
            assert response.status_code == 202
            await promoted(app)
            assert app.state.storage_plugin == "json"
            assert session.files["plugins/unavailable/model.bin"] == b"model-version-two"
            assert original_path.read_bytes() == original_raw
    asyncio.run(scenario())


def test_primary_commit_succeeds_when_copy_fails_and_explicit_retry_catches_up(tmp_path, monkeypatch):
    async def scenario():
        app = create_app(settings=settings(tmp_path))
        async with running(app) as client:
            replicas = app.state.storage_replicas
            identifier = await attach(client, app, "json")
            original = replicas._copy_one
            def fail(*args):
                raise OSError("private-financial-details-must-not-appear")
            monkeypatch.setattr(replicas, "_copy_one", fail)
            response = await client.post("/api/accounts", json={"workspace": "personal", "name": "Saved despite copy", "type": "depository", "balance": 42})
            assert response.status_code == 200, response.text
            await replicas._synchronize()
            status = (await client.get("/api/storage/copies")).json()
            assert status["copies"][0]["state"] == "failed", status
            assert "private-financial" not in str(status)
            assert app.state.tracked_store.accounts("personal")[0]["name"] == "Saved despite copy"
            monkeypatch.setattr(replicas, "_copy_one", original)
            retry = await client.post(f"/api/storage/copies/{identifier}/retry", json={})
            assert retry.status_code == 202
            await replicas._synchronize()
            assert (await client.get("/api/storage/copies")).json()["copies"][0]["state"] == "saved"
    asyncio.run(scenario())


@pytest.mark.parametrize("namespace_only", [False, True])
def test_restart_discovers_commit_or_namespace_save_missed_before_copy_scheduling(tmp_path, namespace_only):
    async def scenario():
        config = settings(tmp_path)
        session = BudgetSession.create(tmp_path, "sandbox", "Fictional", storage="sqlite")
        session.manifest["plugins"].append({"id": "unavailable", "enabled": False, "apiVersion": 1,
                                            "settings": {}, "state": {}})
        app = create_app(settings=config, budget_session=session)
        async with running(app) as client:
            response = await client.post("/api/accounts", json={"workspace": "personal", "name": "Initial", "type": "depository", "balance": 1})
            assert response.status_code == 200
            await attach(client, app, "json")
            replicas = app.state.storage_replicas
            await promoted(app)
            first = replicas.status()["copies"][0]["watermark"]
            replicas._stop.set()
            await replicas._poller
            app.state.tracked_store.after_commit = None
            session.after_checkpoint = None
            if namespace_only:
                session.write_file("unavailable", "model.bin", b"acknowledged-new-model")
            else:
                response = await client.post("/api/accounts", json={"workspace": "personal", "name": "Unscheduled acknowledged write", "type": "depository", "balance": 3})
                assert response.status_code == 200
            assert replicas.status()["copies"][0]["state"] == "pending"
            old_path = session.path
        reopened_session = BudgetSession.open(tmp_path, "sandbox", old_path)
        reopened = create_app(settings=config, budget_session=reopened_session)
        async with running(reopened) as client:
            status = (await client.get("/api/storage/copies")).json()
            assert status["copies"][0]["state"] == "saved", status
            latest = status["copies"][0]["watermark"]
            if namespace_only:
                assert latest["source_generation"] == first["source_generation"]
                assert latest["namespace_generation"] > first["namespace_generation"]
            else:
                assert latest["source_generation"] > first["source_generation"]
    asyncio.run(scenario())


def test_promotion_drains_held_background_write_and_other_budget_stays_available(tmp_path):
    async def scenario():
        config = settings(tmp_path)
        host = BudgetHost(config)
        app = create_app(settings=config, budget_host=host)
        async with running(app) as client:
            created = await client.post("/api/budget/new", json={"name": "Fictional secondary"})
            child_id = created.json()["id"]
            child, session = host.applications[child_id]
            prefix = f"/budgets/{child_id}"
            identifier = await attach(client, child, "json", prefix)
            entered, release = asyncio.Event(), asyncio.Event()
            async def old_work():
                async with child.state.budget_runtime.background():
                    entered.set()
                    await release.wait()
                    child.state.tracked_store.add_account("personal", {"name": "Last old primary write", "type": "depository", "balance": 4})
            task = asyncio.create_task(old_work())
            await entered.wait()
            response = await client.post(f"{prefix}/api/storage/copies/{identifier}/promote", json={})
            assert response.status_code == 202
            await child.state.budget_runtime.wait_for_phase("draining")
            try:
                assert (await client.get("/budgets/home/api/workspace?workspace=personal")).status_code == 200
                status = (await client.get(f"{prefix}/api/storage/copies")).json()
                assert status["operations"][-1]["state"] == "running"
            finally:
                release.set()
            await task
            await promoted(child)
            assert child.state.storage_plugin == "json"
            assert child.state.tracked_store.accounts("personal")[0]["name"] == "Last old primary write"
            assert app.state.storage_plugin == "sqlite"
    asyncio.run(scenario())


@pytest.mark.parametrize("decision_persisted", [False, True])
def test_lost_promotion_decision_response_recovers_only_durable_authority(tmp_path, monkeypatch, decision_persisted):
    async def scenario():
        app = create_app(settings=settings(tmp_path))
        async with running(app) as client:
            identifier = await attach(client, app, "json")
            journal = app.state.storage_runtime.locations.journal
            write = journal.write
            fired = False
            def interrupted(record):
                nonlocal fired
                if record["stage"] == "committed" and not fired:
                    fired = True
                    if decision_persisted:
                        write(record)
                    raise OSError("private-promotion-outcome")
                return write(record)
            monkeypatch.setattr(journal, "write", interrupted)
            response = await client.post(f"/api/storage/copies/{identifier}/promote", json={})
            assert response.status_code == 202
            await promoted(app)
            status = (await client.get("/api/storage/copies")).json()
            assert status["operations"][-1]["state"] == "recovery_required", status
            assert "private-promotion" not in str(status)
            assert app.state.budget_runtime.status()["admission"] == "gated"
            assert status["primary"]["health"] == "recovery_required"
            assert status["primary"]["admission"] == "gated"
            assert status["primary"]["role"] == "fenced"
            assert status["primary"]["authoritative"] is False
            recovered = await client.post("/api/storage/recover", json={})
            assert recovered.status_code == 200, recovered.text
            assert app.state.storage_plugin == ("json" if decision_persisted else "sqlite")
            assert app.state.budget_runtime.status()["admission"] == "open"
            if not decision_persisted:
                await app.state.storage_replicas._synchronize()
                assert (await client.get("/api/storage/copies")).json()["copies"][0]["state"] == "saved"
            response = await client.post("/api/accounts", json={"workspace": "personal", "name": "After recovery", "type": "depository", "balance": 6})
            assert response.status_code == 200
    asyncio.run(scenario())


def test_scheduler_failure_does_not_change_successful_primary_response(tmp_path, monkeypatch):
    async def scenario():
        app = create_app(settings=settings(tmp_path))
        async with running(app) as client:
            replicas = app.state.storage_replicas
            identifier = await attach(client, app, "json")
            await promoted(app)
            schedule = app.state.budget_runtime.schedule
            def unavailable(coroutine):
                coroutine.close()
                raise OSError("private-scheduler-error")
            monkeypatch.setattr(app.state.budget_runtime, "schedule", unavailable)
            response = await client.post("/api/accounts", json={"workspace": "personal", "name": "Saved", "type": "depository", "balance": 7})
            assert response.status_code == 200
            status = (await client.get("/api/storage/copies")).json()
            assert status["copies"][0]["state"] == "pending", status
            assert status["errors"]["scheduler"]["code"] == "copy_scheduling_failed"
            assert "private-scheduler" not in str(status)
            monkeypatch.setattr(app.state.budget_runtime, "schedule", schedule)
            assert (await client.post(f"/api/storage/copies/{identifier}/retry", json={})).status_code == 202
            await replicas._synchronize()
            assert replicas.status()["copies"][0]["state"] == "saved"
    asyncio.run(scenario())


def test_injected_legacy_backend_reports_copy_unsupported_without_native_method_call(tmp_path, store):
    class LegacyStore:
        def __getattr__(self, name):
            if name == "commit_evidence":
                pytest.fail("Unsupported storage must not read native commit evidence.")
            return getattr(store, name)
    async def scenario():
        app = create_app(settings=settings(tmp_path), store=LegacyStore())
        async with running(app) as client:
            response = await client.get("/api/storage/copies")
            assert response.status_code == 200, response.text
            status = response.json()
            assert status["primary"]["supported"] is False
            assert status["primary"]["authoritative"] is False
            assert status["errors"]["unsupported"]["code"] == "copy_unsupported"
    asyncio.run(scenario())


@pytest.mark.parametrize("authority", ["remote", "legacy"])
def test_owned_remote_or_legacy_storage_status_is_explicitly_unsupported(tmp_path, authority):
    from whab.plugins import Plugin, PluginContract, PluginRegistry, load_descriptors
    reference = load_descriptors(("sqlite",))[0]
    class NoAuthority:
        def __init__(self, backend):
            self.backend = backend
        def __getattr__(self, name):
            if name == "commit_evidence":
                pytest.fail("An unsupported backend must not receive authority reads.")
            return getattr(self.backend, name)
    descriptor = Plugin(f"fictional-{authority}", "Fictional backend", storage=lambda context: NoAuthority(reference.storage(context)),
                        contract=PluginContract(authority="remote") if authority == "remote" else None)
    async def scenario():
        app = create_app(settings=replace(settings(tmp_path), storage=descriptor.id), plugins=PluginRegistry((descriptor,)))
        async with running(app) as client:
            response = await client.get("/api/storage/copies")
            assert response.status_code == 200, response.text
            assert response.json()["primary"]["supported"] is False
            assert response.json()["errors"]["unsupported"]["code"] == "copy_unsupported"
    asyncio.run(scenario())


def test_corrupt_namespace_artifact_blocks_copy_and_promotion_without_changing_primary(tmp_path):
    async def scenario():
        app = create_app(settings=settings(tmp_path))
        async with running(app) as client:
            replicas = app.state.storage_replicas
            identifier = await attach(client, app, "json")
            await promoted(app)
            row = replicas.status()["copies"][0]
            watermark = row["watermark"]
            artifact = replicas.archive.artifact(watermark["namespace_generation"], watermark["namespace_digest"])
            original = artifact.read_bytes()
            artifact.write_bytes(b"changed-opaque-artifact")
            await replicas._synchronize()
            assert replicas.status()["copies"][0]["state"] == "failed"
            response = await client.post(f"/api/storage/copies/{identifier}/promote", json={})
            assert response.status_code == 202
            await promoted(app)
            assert app.state.storage_plugin == "sqlite"
            assert replicas.status()["operations"][-1]["state"] == "recovery_required"
            assert app.state.tracked_store.commit_evidence().role == "primary"
            artifact.write_bytes(original)
            assert (await client.post("/api/storage/recover", json={})).status_code == 200
            await replicas._synchronize()
            assert replicas.status()["copies"][0]["state"] == "saved"
    asyncio.run(scenario())


def test_disable_retains_native_copy_and_its_namespace_artifact(tmp_path):
    async def scenario():
        app = create_app(settings=settings(tmp_path))
        async with running(app) as client:
            replicas = app.state.storage_replicas
            identifier = await attach(client, app, "json")
            await promoted(app)
            entry = replicas.entries[identifier]
            backend = replicas._factory(entry)
            before = backend.commit_evidence()
            watermark = backend.replica_watermark()
            backend.close()
            assert (await client.post(f"/api/storage/copies/{identifier}/disable", json={})).status_code == 200
            response = await client.post("/api/accounts", json={"workspace": "personal", "name": "Primary after disable", "type": "depository", "balance": 9})
            assert response.status_code == 200
            await replicas._synchronize()
            backend = replicas._factory(entry)
            assert backend.commit_evidence() == before
            assert backend.replica_watermark() == watermark
            backend.close()
            replicas.archive.validate(watermark.namespace_generation, watermark.namespace_digest)
            assert replicas.status()["copies"][0]["state"] == "disabled"
    asyncio.run(scenario())


def test_authority_change_requires_new_attachment_and_never_overwrites_retired_primary(tmp_path):
    async def scenario():
        app = create_app(settings=settings(tmp_path))
        async with running(app) as client:
            replicas = app.state.storage_replicas
            first = await attach(client, app, "json")
            status = await migrate(client, app, "json")
            assert status["operations"][-1]["state"] == "complete"
            assert replicas.status()["copies"][0]["state"] == "reattach_required"
            assert (await client.post(f"/api/storage/copies/{first}/disable", json={})).status_code == 200
            second = await attach(client, app, "sqlite")
            journal = app.state.storage_runtime.locations.journal.read()
            assert replicas.entries[second]["instance_id"] != journal["source"]["instance_id"]
            rows = {row["id"]: row for row in replicas.status()["copies"]}
            assert rows[first]["state"] == "disabled"
            assert rows[second]["state"] == "saved", rows
    asyncio.run(scenario())


_CRASH_PROMOTION = r'''
import asyncio, os, signal, sys
from pathlib import Path
from tests.web.test_budget_sessions import settings
from tests.web.test_live_plugins import running
from tests.web.test_storage_replicas import attach
from whab.app import create_app
from whab.budgets.session import BudgetSession

home, primary, stage = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
async def main():
    session = BudgetSession.create(home, "sandbox", "Fictional", storage=primary)
    session.manifest["plugins"].append({"id":"unavailable","enabled":False,"apiVersion":1,
                                        "settings":{"minimumScore":0.8},"state":{}})
    session.files["plugins/unavailable/model.bin"] = b"preserved-opaque-model"
    app = create_app(settings=settings(home), budget_session=session)
    async with running(app) as client:
        response = await client.post("/api/accounts", json={"workspace":"personal","name":"Acknowledged before promotion","type":"depository","balance":11})
        assert response.status_code == 200
        (home / "original-path.txt").write_text(str(session.path))
        identifier = await attach(client, app, "json" if primary == "sqlite" else "sqlite")
        original_write = app.state.storage_runtime.locations.journal.write
        def write(record):
            original_write(record)
            if record["stage"] == stage:
                os.kill(os.getpid(), signal.SIGKILL)
        app.state.storage_runtime.locations.journal.write = write
        response = await client.post(f"/api/storage/copies/{identifier}/promote", json={})
        assert response.status_code == 202
        await asyncio.gather(*tuple(app.state.storage_replicas.tasks))
asyncio.run(main())
'''


@pytest.mark.skipif(os.name == "nt", reason="POSIX process termination boundary")
@pytest.mark.parametrize("primary", ["sqlite", "json"])
@pytest.mark.parametrize("stage", ["prepared", "imported", "validated", "source_fenced", "committed", "active", "retired"])
def test_real_process_termination_at_promotion_boundaries_keeps_durable_authority(tmp_path, primary, stage):
    process = subprocess.run([sys.executable, "-c", _CRASH_PROMOTION, str(tmp_path), primary, stage],
                             capture_output=True, timeout=30)
    assert process.returncode == -signal.SIGKILL, process.stderr.decode()
    old_path = tmp_path.joinpath("original-path.txt").read_text()
    expected = ("json" if primary == "sqlite" else "sqlite") if stage in {"committed", "active", "retired"} else primary
    async def scenario():
        session = BudgetSession.open(tmp_path, "sandbox", old_path)
        app = create_app(settings=settings(tmp_path), budget_session=session)
        async with running(app) as client:
            assert app.state.storage_plugin == expected
            assert app.state.tracked_store.accounts("personal")[0]["name"] == "Acknowledged before promotion"
            assert session.files["plugins/unavailable/model.bin"] == b"preserved-opaque-model"
            assert session.plugin_record("unavailable")["settings"] == {"minimumScore": 0.8}
            later = await client.post("/api/accounts", json={"workspace": "personal", "name": "Acknowledged after recovery", "type": "depository", "balance": 12})
            assert later.status_code == 200
        reopened = create_app(settings=settings(tmp_path), budget_session=BudgetSession.open(tmp_path, "sandbox", old_path))
        async with running(reopened):
            assert reopened.state.storage_plugin == expected
            assert {row["name"] for row in reopened.state.tracked_store.accounts("personal")} == {"Acknowledged before promotion", "Acknowledged after recovery"}
    asyncio.run(scenario())
