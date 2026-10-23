"""Metadata-only plugin repairs never imply that an unopened ledger was restored."""

import copy
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from whab_plugin_sqlite import Store

from whab.budgets import session as module
from whab.budgets.container import decode_container, encode_container
from whab.budgets.document import encode_bundle, new_manifest, upgrade_manifest
from whab.budgets.session import BudgetSession, _read_source
from whab.config import Settings
from whab.plugins import StoreError
from whab.plugins.recovery import RecoveryApplication, RecoveryController, RecoveryError


def portable(home, *, legacy=False):
    manifest = upgrade_manifest(new_manifest("Fictional", ("broken", "unavailable")))
    unknown = next(record for record in manifest["plugins"] if record["id"] == "unavailable")
    unknown.update(enabled=False, apiVersion=99, stateVersion=41,
                   settings={"opaque": ["retain", {"value": 7}]}, state={"learned": {"unknown": True}})
    source = Store(":memory:", month="2026-10")
    try:
        account = source.add_account("personal", {"name": "Fictional wallet", "type": "depository", "balance": 10000})
        source.add_transaction("personal", {"id": "fictional-purchase", "accountId": account, "date": "2026-10-01", "merchant": "Fictional shop", "amount": 125, "categoryId": "groceries"})
        expected = source.export_budget()
        files = {"plugins/sqlite/budget.sqlite": source.export_budget_files()["budget.sqlite"],
                 "plugins/unavailable/opaque.bin": b"synthetic-unknown-model"}
        raw = encode_bundle(manifest, expected) if legacy else encode_container(manifest, files)
    finally:
        source.close()
    path = home / "fictional.whab"
    path.write_bytes(raw)
    return path, raw, expected, copy.deepcopy(unknown)


def settings(home, path):
    return Settings(home, 5173, "sandbox", "", "", "", False, "", "", (), ui="", budget_path=path)


@pytest.mark.parametrize("legacy", [False, True])
def test_never_opened_nonempty_budget_survives_metadata_disable_real_retry_and_reopen(tmp_path, legacy):
    path, original, expected, unknown = portable(tmp_path, legacy=legacy)
    config = settings(tmp_path / "runtime", path)
    app = RecoveryApplication(config)
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        projection = client.get("/api/recovery").json()
        response = client.post("/api/recovery/disable", json={"revision": projection["revision"], "plugins": ["broken"]})
        assert response.status_code == 200
        assert path.read_bytes() == original
        assert not list(config.home.rglob("*.sqlite"))
        saved = BudgetSession.open(config.home, "sandbox", path)
        assert saved.store is None and saved.selection == ()
        assert saved.plugin_record("unavailable") == unknown
        if not legacy:
            assert saved.files["plugins/unavailable/opaque.bin"] == b"synthetic-unknown-model"
        marker = _read_source(saved.storage_home / "source.json")
        assert marker["formatVersion"] == 2 and marker["selectionOnly"] is True
        assert marker["restoreRequired"] is True
        assert marker["manifest"] == saved.manifest
        started = client.post("/api/recovery/retry", json={"revision": response.json()["revision"]})
        assert started.status_code == 200, started.text
        assert app._candidate.state.store.export_budget() == expected
        assert client.get("/api/plugins").status_code == 200
    reopened = BudgetSession.open(config.home, "sandbox", path)
    assert reopened.selection == () and reopened.plugin_record("unavailable") == unknown
    assert reopened._source.get("formatVersion") is None
    decoded = decode_container(path.read_bytes())
    assert next(record for record in decoded.manifest["plugins"] if record["id"] == "unavailable") == unknown
    if not legacy:
        assert decoded.files["plugins/unavailable/opaque.bin"] == b"synthetic-unknown-model"


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("restored", [False, True])
def test_interrupted_first_restore_retains_original_source_identity_selection_and_nonempty_payload(tmp_path, legacy, restored, monkeypatch):
    path, original, expected, unknown = portable(tmp_path, legacy=legacy)
    home = tmp_path / "runtime"
    session = BudgetSession.open(home, "sandbox", path)
    session.save_selection(())
    backend_path = session.storage_home / "data" / "whab-sandbox.sqlite"
    backend = Store(backend_path, month="2026-10")
    method = "restore_budget" if legacy else "prepare_budget_files"
    original_restore = getattr(backend, method)

    def interrupted(*args, **kwargs):
        if restored:
            original_restore(*args, **kwargs)
        raise StoreError("Fictional interrupted restore")

    monkeypatch.setattr(backend, method, interrupted)
    with pytest.raises(StoreError, match="interrupted"):
        session.bind(backend)
    backend.close()
    assert path.read_bytes() == original
    reopened = BudgetSession.open(home, "sandbox", path)
    assert reopened.selection == () and reopened.plugin_record("unavailable") == unknown
    assert reopened._source["formatVersion"] == 2 and reopened._source["restoreRequired"]
    recovered = Store(backend_path, month="2026-10")
    try:
        reopened.bind(recovered)
        assert recovered.export_budget() == expected
        assert reopened.status == "saved"
        assert reopened._source.get("formatVersion") is None
    finally:
        recovered.close()


def test_selection_only_upgrade_of_existing_native_recovery_does_not_restore_over_saved_changes(tmp_path):
    path, _, _, unknown = portable(tmp_path)
    home = tmp_path / "runtime"
    first = BudgetSession.open(home, "sandbox", path)
    database = first.storage_home / "data" / "whab-sandbox.sqlite"
    active = Store(database, month="2026-10")
    first.bind(active)
    active.add_account("personal", {"name": "Later fictional wallet", "type": "depository", "balance": 7})
    first.checkpoint()
    expected = active.export_budget()
    active.close()
    repair = BudgetSession.open(home, "sandbox", path)
    repair.save_selection(())
    assert repair._source["formatVersion"] == 2 and repair._source["restoreRequired"] is False
    repair = BudgetSession.open(home, "sandbox", path)
    restored = Store(database, month="2026-10")
    try:
        repair.bind(restored)
        assert restored.export_budget() == expected
        assert repair.plugin_record("unavailable") == unknown
    finally:
        restored.close()


@pytest.mark.parametrize("change", [
    lambda value: value.update(formatVersion=True), lambda value: value.update(formatVersion=3),
    lambda value: value.update(selectionOnly=False), lambda value: value.update(restoreRequired=1),
    lambda value: value.update(extra=True), lambda value: value.pop("manifest"),
    lambda value: value.update(manifest={}), lambda value: value["manifest"].update(id=str(uuid4())),
    lambda value: value.update(pending="1" * 64, restorePending="2" * 64),
    lambda value: value.update(pending="1" * 64, restoreRequired=True),
    lambda value: value.update(content="invalid"),
])
def test_invalid_versioned_selection_records_are_not_guessed_or_rewritten(tmp_path, change):
    path, original, _, _ = portable(tmp_path)
    home = tmp_path / "runtime"
    session = BudgetSession.open(home, "sandbox", path)
    session.save_selection(())
    marker = session.storage_home / "source.json"
    record = json.loads(marker.read_bytes())
    change(record)
    raw = json.dumps(record).encode()
    marker.write_bytes(raw)
    with pytest.raises(StoreError):
        BudgetSession.open(home, "sandbox", path)
    assert path.read_bytes() == original and marker.read_bytes() == raw
    controller = RecoveryController(settings(home, path))
    projection = controller.inspect()[0]
    assert projection["errors"][0]["code"] == "budget_unreadable"
    assert not projection["can_clear_selection"]
    with pytest.raises(RecoveryError):
        controller.disable(projection["revision"], [])
    with pytest.raises(RecoveryError):
        controller.clear_invalid(projection["revision"])


def test_v2_upgrade_keeps_exact_old_source_records_readable_and_does_not_mutate_them(tmp_path):
    path = tmp_path / "source.json"
    records = ({"content": "1" * 64}, {"content": None, "pending": "2" * 64}, {"content": "1" * 64, "restorePending": "3" * 64})
    for record in records:
        raw = json.dumps(record).encode()
        path.write_bytes(raw)
        assert _read_source(path) == record
        assert path.read_bytes() == raw


def test_metadata_write_failure_keeps_portable_original_and_refresh_reads_actual_atomic_decision(tmp_path, monkeypatch):
    path, original, _, _ = portable(tmp_path)
    home = tmp_path / "runtime"
    session = BudgetSession.open(home, "sandbox", path)

    def uncertain(*_):
        raise OSError("Fictional directory sync failure")

    monkeypatch.setattr(module, "_flush_source_home", uncertain)
    with pytest.raises(StoreError, match="could not be confirmed"):
        session.save_selection(())
    assert path.read_bytes() == original
    refreshed = BudgetSession.open(home, "sandbox", path)
    assert refreshed.selection == () and refreshed._source["restoreRequired"]


def test_recovery_disable_and_real_retry_preserve_migrated_authority_late_writes_and_original(tmp_path):
    import asyncio
    from dataclasses import replace

    from tests.web.test_budget_sessions import settings as migration_settings
    from tests.web.test_live_plugins import running
    from tests.web.test_storage_migration import migrate
    from whab.app import create_app

    async def migrated():
        session = BudgetSession.create(tmp_path, "sandbox", "Fictional recovered migration", plugins=("example",))
        session.manifest["plugins"].append({"id": "unavailable", "enabled": False, "apiVersion": 1,
                                            "settings": {"minimumScore": 0.8}, "state": {"weight": 0.25}})
        session.files["plugins/unavailable/model.bin"] = b"fictional-opaque-model"
        app = create_app(settings=migration_settings(tmp_path), budget_session=session)
        async with running(app) as client:
            response = await client.post("/api/accounts", json={"workspace": "personal", "name": "Before", "type": "depository", "balance": 10})
            assert response.status_code == 200
            original_path = session.path
            original = original_path.read_bytes()
            status = await migrate(client, app, "json")
            assert status["operations"][-1]["state"] == "complete"
            response = await client.post("/api/accounts", json={"workspace": "personal", "name": "Acknowledged after", "type": "depository", "balance": 20})
            assert response.status_code == 200
            expected = app.state.tracked_store.export_budget()
        return original_path, original, expected

    path, original, expected = asyncio.run(migrated())
    config = replace(migration_settings(tmp_path), budget_path=path)
    app = RecoveryApplication(config)
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        projection = client.get("/api/recovery").json()
        assert next(row for row in projection["plugins"] if row["id"] == "json")["required"]
        response = client.post("/api/recovery/disable", json={"revision": projection["revision"], "plugins": ["example"]})
        assert response.status_code == 200
        assert path.read_bytes() == original
        repaired = BudgetSession.open(tmp_path, "sandbox", path)
        assert repaired.manifest["storage"] == "json" and repaired.selection == ()
        assert repaired.files["plugins/unavailable/model.bin"] == b"fictional-opaque-model"
        started = client.post("/api/recovery/retry", json={"revision": response.json()["revision"]})
        assert started.status_code == 200, started.text
        assert app._candidate.state.tracked_store.export_budget() == expected
        assert app._candidate.state.storage_plugin == "json"
    reopened = BudgetSession.open(tmp_path, "sandbox", path)
    assert reopened.manifest["storage"] == "json" and reopened.selection == ()
    assert reopened.plugin_record("unavailable")["settings"] == {"minimumScore": 0.8}
    assert path.read_bytes() == original
