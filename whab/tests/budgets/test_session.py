"""Budget sessions preserve committed ledgers and recover interrupted file saves."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from whab_sqlite import Store

from whab.budgets import session as module
from whab.budgets.document import decode_bundle, encode_bundle, new_manifest
from whab.budgets.session import BudgetSession, TrackedStorage, content_digest
from whab.domain.errors import StoreError


def database(session):
    return Store(session.storage_home / "ledger.sqlite", month="2026-10")


@pytest.fixture
def opened(tmp_path):
    session = BudgetSession.create(
        tmp_path / "runtime", "sandbox", "Family", ("plaid", "reports"), path=tmp_path / "family.whab",
    )
    store = database(session)
    session.bind(store)
    try:
        yield session, store
    finally:
        store.close()


@pytest.fixture
def incoming(tmp_path):
    store = Store(":memory:", month="2026-10")
    try:
        store.add_account("personal", {"name": "Portable cash", "type": "depository", "balance": 12345})
        ledger = store.export_budget()
    finally:
        store.close()
    manifest = new_manifest("Imported budget", ("plaid",))
    path = tmp_path / "imported.whab"
    path.write_bytes(encode_bundle(manifest, ledger))
    return path, manifest, ledger


def test_new_session_saves_ledger_and_keeps_runtime_credentials_and_paths_off_bundle(opened):
    session, store = opened
    tracked = TrackedStorage(store, session)
    tracked.add_account("personal", {"name": "Wallet", "type": "depository", "balance": 10})
    store.save_item("private-item", "synthetic-secret-token", "Fixture Bank")
    session.checkpoint()
    manifest, ledger = decode_bundle(session.path.read_bytes())
    assert manifest == session.manifest
    assert ledger == store.export_budget()
    assert session.status == "saved"
    assert session.selection == ("plaid", "reports")
    assert session.name == "Family"
    assert session.storage_home == session.home / "budgets" / session.id / "sandbox"
    assert "synthetic-secret-token" not in json.dumps([manifest, ledger])
    assert str(session.storage_home) not in json.dumps([manifest, ledger])


def test_open_initializes_only_personal_ledger_and_preserves_demo(tmp_path, incoming):
    path, manifest, ledger = incoming
    session = BudgetSession.open(tmp_path / "runtime", "sandbox", path)
    assert session.id == manifest["id"]
    store = database(session)
    try:
        demo = store.state("demo")
        session.bind(store)
        assert store.export_budget() == ledger
        assert store.state("demo") == demo
        assert session.ledger is None
        assert decode_bundle(path.read_bytes()) == (manifest, ledger)
    finally:
        store.close()


def test_new_session_without_explicit_path_saves_inside_its_isolated_runtime(tmp_path):
    session = BudgetSession.create(tmp_path, "production", "Local")
    store = database(session)
    try:
        session.bind(store)
        assert session.path == session.storage_home / "budget.whab"
        assert session.path.is_file()
    finally:
        store.close()


def test_primary_first_publication_preserves_concurrently_created_file_and_local_budget(tmp_path, monkeypatch):
    path = tmp_path / "new.whab"
    session = BudgetSession.create(tmp_path / "runtime", "sandbox", "New", path=path)
    store = database(session)
    store.add_account("personal", {"name": "Recoverable cash", "type": "depository", "balance": 10000})
    original = module.os.link
    external = b"Original file created by another process"

    def appear_before_publish(source, target):
        if Path(target) == path:
            path.write_bytes(external)
        return original(source, target)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(module.os, "link", appear_before_publish)
            session.bind(store)
        assert session.status == "pending"
        assert path.read_bytes() == external
        assert store.accounts("personal")[0]["balance"] == 10000
        recovered_path = tmp_path / "recovered.whab"
        recovered_path.write_bytes(session.bundle())
        recovered = BudgetSession.open(session.home, "sandbox", recovered_path)
        recovered.bind(store)
        assert recovered.status == "saved"
        assert decode_bundle(recovered_path.read_bytes())[1] == store.export_budget()
        assert path.read_bytes() == external
    finally:
        store.close()


@pytest.mark.parametrize("matching", [False, True])
def test_primary_without_expected_digest_only_adopts_matching_content(tmp_path, matching):
    path = tmp_path / "new.whab"
    session = BudgetSession.create(tmp_path / "runtime", "sandbox", "New", path=path)
    store = database(session)
    manifest = deepcopy(session.manifest)
    if not matching:
        manifest["name"] = "An externally created revision"
    appeared = encode_bundle(manifest, store.export_budget())
    path.write_bytes(appeared)
    try:
        session.bind(store)
        assert session.status == ("saved" if matching else "pending")
        assert path.read_bytes() == appeared
    finally:
        store.close()


def test_plugin_selection_preserves_disabled_plugin_settings_and_state(opened):
    session, _ = opened
    session.save_plugin_data("plaid", settings={"historyDays": 90}, state={"seen": ["first"]})
    session.save_selection(("reports", "new-plugin"))
    record = session.plugin_record("plaid")
    assert record["enabled"] is False
    assert record["settings"] == {"historyDays": 90}
    assert record["state"] == {"seen": ["first"]}
    assert session.plugin_record("sqlite")["enabled"] is True
    assert session.plugin_record("new-plugin")["enabled"] is True
    session.save_selection(("plaid",))
    assert session.selection == ("plaid",)
    assert session.plugin_record("plaid")["state"] == {"seen": ["first"]}
    assert decode_bundle(session.path.read_bytes())[0] == session.manifest


@pytest.mark.parametrize("selection", ["plaid", None, ("../bad",), [None], ["Bad Plugin"]])
def test_invalid_selection_does_not_mutate_existing_configuration(opened, selection):
    session, _ = opened
    before = deepcopy(session.manifest)
    raw = session.path.read_bytes()
    with pytest.raises(StoreError):
        session.save_selection(selection)
    assert session.manifest == before
    assert session.path.read_bytes() == raw


@pytest.mark.parametrize("settings,state", [
    ({"valid": 1}, {"accessToken": "secret"}),
    ({"valid": 1}, []), ([], {"valid": 1}), ({"apiKey": "secret"}, {"valid": 1}),
])
def test_rejected_plugin_updates_are_atomic_in_memory_and_on_disk(opened, settings, state):
    session, _ = opened
    before = deepcopy(session.manifest)
    raw = session.path.read_bytes()
    with pytest.raises(StoreError):
        session.save_plugin_data("plaid", settings=settings, state=state)
    assert session.manifest == before
    assert session.path.read_bytes() == raw


@pytest.mark.parametrize("operation", ["selection", "data"])
def test_plugin_updates_require_durable_local_manifest_before_replacing_memory(opened, monkeypatch, operation):
    session, _ = opened
    before = deepcopy(session.manifest)
    local = (session.storage_home / "manifest.json").read_bytes()
    archive = session.path.read_bytes()
    original = module.atomic_write

    def fail_manifest(path, raw):
        if Path(path).name == "manifest.json":
            raise OSError("Plugin setup disk is full")
        return original(path, raw)

    monkeypatch.setattr(module, "atomic_write", fail_manifest)
    with pytest.raises(StoreError, match="previous settings are unchanged"):
        if operation == "selection":
            session.save_selection(())
        else:
            session.save_plugin_data("plaid", settings={"days": 30}, state={"seen": [1]})
    assert session.manifest == before
    assert (session.storage_home / "manifest.json").read_bytes() == local
    assert session.path.read_bytes() == archive


def test_budget_context_is_detached_and_state_callback_persists_to_own_budget_only(opened, tmp_path):
    first, _ = opened
    second = BudgetSession.create(tmp_path / "other", "sandbox", "Other", ("plaid",))
    first.save_plugin_data("plaid", settings={"nested": {"flags": [1]}})
    context = first.context("plaid")
    with pytest.raises(TypeError):
        context.settings["new"] = 1
    context.settings["nested"]["flags"].append(2)
    assert first.plugin_record("plaid")["settings"] == {"nested": {"flags": [1]}}
    context.save_state({"reviewed": ["tx"]})
    assert first.plugin_record("plaid")["state"] == {"reviewed": ["tx"]}
    assert second.plugin_record("plaid")["state"] == {}
    assert context.budget_id == first.id
    assert first.context("missing").settings == {}
    with pytest.raises(StoreError, match="not part"):
        first.context("missing").save_state({"x": 1})
    with pytest.raises(StoreError, match="plugin ID"):
        first.plugin_record("../bad")


def test_projection_exposes_copy_status_without_paths_or_mutable_manifest_references(opened, tmp_path):
    session, _ = opened
    session.missing_plugins = ["uninstalled"]
    session.add_copy("Backup", str(tmp_path / "backups" / "private-location.whab"))
    projected = session.projection()
    assert projected["copies"] == [
        {"name": "Budget file", "status": "saved"}, {"name": "Backup", "status": "saved"},
    ]
    assert "path" not in json.dumps(projected)
    projected["plugins"][0]["settings"]["changed"] = True
    projected["missingPlugins"].append("other")
    assert session.plugin_record("sqlite")["settings"] == {}
    assert session.missing_plugins == ["uninstalled"]


def test_copy_destinations_are_device_local_persisted_and_updated_after_committed_writes(opened, tmp_path):
    session, store = opened
    target = tmp_path / "backup.whab"
    session.add_copy("  Backup  ", str(target))
    TrackedStorage(store, session).add_account("personal", {"name": "Cash", "type": "depository", "balance": 1})
    assert decode_bundle(target.read_bytes()) == decode_bundle(session.path.read_bytes())
    assert session.destinations[0]["name"] == "Backup"
    assert "copies" not in decode_bundle(target.read_bytes())[0]
    reopened = BudgetSession.open(session.home, "sandbox", session.path)
    assert reopened.destinations == [{
        "name": "Backup", "path": str(target), "status": "pending",
        "content": content_digest(*decode_bundle(target.read_bytes())),
    }]


def test_copy_failure_is_visible_and_retries_without_rolling_back_committed_ledger(opened, tmp_path, monkeypatch):
    session, store = opened
    target = tmp_path / "backup.whab"
    session.add_copy("Backup", str(target))
    original = module.atomic_write

    def fail_copy(path, raw):
        if Path(path) == target:
            raise OSError("Destination unavailable")
        return original(path, raw)

    with monkeypatch.context() as patch:
        patch.setattr(module, "atomic_write", fail_copy)
        account = TrackedStorage(store, session).add_account("personal", {"name": "Cash", "type": "depository", "balance": 2})
    assert account in store.state("personal")["accounts"]
    assert session.status == "saved"
    assert session.destinations[0]["status"] == "pending"
    session.checkpoint()
    assert session.destinations[0]["status"] == "saved"
    assert decode_bundle(target.read_bytes())[1] == store.export_budget()


def test_copy_does_not_overwrite_another_budget_or_invalid_file(opened, tmp_path):
    session, _ = opened
    target = tmp_path / "backup.whab"
    session.add_copy("Backup", str(target))
    for replacement in (b"external file", encode_bundle(new_manifest("Other"), session.store.export_budget())):
        target.write_bytes(replacement)
        session.checkpoint()
        assert session.destinations[0]["status"] == "pending"
        assert target.read_bytes() == replacement


def test_same_budget_copy_edited_elsewhere_is_preserved_across_restart(opened, tmp_path):
    session, store = opened
    tracked = TrackedStorage(store, session)
    tracked.add_account("personal", {"name": "Checking", "type": "depository", "balance": 10000})
    target = tmp_path / "backup.whab"
    session.add_copy("Backup", str(target))
    manifest, ledger = decode_bundle(target.read_bytes())
    ledger["accounts"][0]["balance"] = 9000
    edited_elsewhere = encode_bundle(manifest, ledger)
    target.write_bytes(edited_elsewhere)
    tracked.add_category("personal", {"name": "Local category", "group": "Local"})
    assert session.status == "saved"
    assert session.destinations[0]["status"] == "pending"
    assert target.read_bytes() == edited_elsewhere
    assert decode_bundle(session.path.read_bytes())[1]["accounts"][0]["balance"] == 10000
    reopened = BudgetSession.open(session.home, "sandbox", session.path)
    reopened.bind(store)
    assert reopened.destinations[0]["status"] == "pending"
    assert target.read_bytes() == edited_elsewhere


@pytest.mark.parametrize("matching", [False, True])
def test_legacy_untracked_copy_is_adopted_only_when_it_matches_current_budget(opened, tmp_path, matching):
    session, store = opened
    target = tmp_path / "legacy.whab"
    manifest, ledger = decode_bundle(session.path.read_bytes())
    if not matching:
        manifest["name"] = "Changed on another device"
    raw = encode_bundle(manifest, ledger)
    target.write_bytes(raw)
    (session.storage_home / "copies.json").write_text(json.dumps([{"name": "Legacy", "path": str(target)}]))
    reopened = BudgetSession.open(session.home, "sandbox", session.path)
    reopened.bind(store)
    assert target.read_bytes() == raw
    destination = reopened.destinations[0]
    assert destination["status"] == ("saved" if matching else "pending")
    assert bool(destination["content"]) is matching


def test_copy_replacement_recovers_persisted_pending_digest_after_finalization_failure(opened, tmp_path, monkeypatch):
    session, store = opened
    target = tmp_path / "backup.whab"
    session.add_copy("Backup", str(target))
    previous = session.destinations[0]["content"]
    original = module.atomic_write

    def fail_finalize(path, raw):
        if Path(path).name == "copies.json":
            records = json.loads(raw)
            if records and "pending" not in records[0] and records[0]["content"] != previous:
                raise OSError("Metadata finalization interrupted")
        return original(path, raw)

    with monkeypatch.context() as patch:
        patch.setattr(module, "atomic_write", fail_finalize)
        TrackedStorage(store, session).add_account("personal", {"name": "Committed", "type": "depository", "balance": 20})
    assert session.destinations[0]["status"] == "pending"
    saved = json.loads((session.storage_home / "copies.json").read_text())[0]
    assert saved["content"] == previous
    assert saved["pending"] == content_digest(*decode_bundle(target.read_bytes()))
    reopened = BudgetSession.open(session.home, "sandbox", session.path)
    reopened.bind(store)
    assert reopened.destinations[0]["status"] == "saved"
    assert "pending" not in reopened.destinations[0]
    assert decode_bundle(target.read_bytes())[1] == store.export_budget()


def test_interrupted_pending_copy_can_advance_to_newer_committed_revision(opened, tmp_path, monkeypatch):
    session, store = opened
    target = tmp_path / "backup.whab"
    session.add_copy("Backup", str(target))
    original = module.atomic_write

    def fail_finalize(path, raw):
        if Path(path).name == "copies.json" and "pending" not in json.loads(raw)[0]:
            raise OSError("Final marker unavailable")
        return original(path, raw)

    with monkeypatch.context() as patch:
        patch.setattr(module, "atomic_write", fail_finalize)
        TrackedStorage(store, session).add_account("personal", {"name": "First", "type": "depository", "balance": 1})
    assert session.destinations[0]["status"] == "pending"
    TrackedStorage(store, session).add_account("personal", {"name": "Newer", "type": "depository", "balance": 2})
    assert session.destinations[0]["status"] == "saved"
    assert len(decode_bundle(target.read_bytes())[1]["accounts"]) == 2


def test_copy_journal_failure_prevents_target_replacement(opened, tmp_path, monkeypatch):
    session, store = opened
    target = tmp_path / "backup.whab"
    session.add_copy("Backup", str(target))
    before = target.read_bytes()
    original = module.atomic_write

    def fail_journal(path, raw):
        if Path(path).name == "copies.json" and "pending" in json.loads(raw)[0]:
            raise OSError("Journal unavailable")
        return original(path, raw)

    monkeypatch.setattr(module, "atomic_write", fail_journal)
    TrackedStorage(store, session).add_account("personal", {"name": "Local", "type": "depository", "balance": 1})
    assert session.destinations[0]["status"] == "pending"
    assert target.read_bytes() == before


def test_new_destination_appearing_during_publication_is_never_overwritten(opened, tmp_path, monkeypatch):
    session, _ = opened
    target = tmp_path / "backup.whab"
    original = module.os.link
    external = b"A file created concurrently by someone else"

    def appear_before_publish(source, destination):
        Path(destination).write_bytes(external)
        return original(source, destination)

    monkeypatch.setattr(module.os, "link", appear_before_publish)
    session.add_copy("Backup", str(target))
    assert session.destinations[0]["status"] == "pending"
    assert target.read_bytes() == external
    assert not list(tmp_path.glob(".whab-copy-*"))


@pytest.mark.parametrize("name,path", [
    (None, "/tmp/copy.whab"), (" ", "/tmp/copy.whab"), ("x" * 101, "/tmp/copy.whab"),
    ("Copy\x00", "/tmp/copy.whab"), ("Copy", None), ("Copy", "relative.whab"),
    ("Copy", "/tmp/bad\x00"), ("Copy", "/" + "x" * 4096),
])
def test_copy_destinations_require_bounded_names_and_absolute_paths(opened, name, path):
    session, _ = opened
    with pytest.raises(StoreError, match="absolute"):
        session.add_copy(name, path)
    assert session.destinations == []


def test_copy_destinations_cannot_duplicate_primary_existing_files_or_each_other(opened, tmp_path):
    session, _ = opened
    with pytest.raises(StoreError, match="new budget copy"):
        session.add_copy("Primary", str(session.path))
    used = tmp_path / "used.whab"
    used.write_bytes(b"keep")
    with pytest.raises(StoreError, match="unused"):
        session.add_copy("Used", str(used))
    target = tmp_path / "copy.whab"
    session.add_copy("First", str(target))
    with pytest.raises(StoreError, match="new budget copy"):
        session.add_copy("Again", str(target))
    assert used.read_bytes() == b"keep"


def test_number_of_copy_destinations_is_bounded(tmp_path):
    session = BudgetSession.create(tmp_path / "runtime", "sandbox", "Copies")
    for index in range(20):
        session.add_copy(f"Copy {index}", str(tmp_path / f"copy-{index}.whab"))
    with pytest.raises(StoreError, match="at most 20"):
        session.add_copy("Extra", str(tmp_path / "extra.whab"))


def test_failed_copy_configuration_write_does_not_add_in_memory_destination(opened, tmp_path, monkeypatch):
    session, _ = opened

    def fail(*_args):
        raise OSError("No disk space")

    monkeypatch.setattr(module, "atomic_write", fail)
    with pytest.raises(StoreError, match="destination could not be saved"):
        session.add_copy("Copy", str(tmp_path / "copy.whab"))
    assert session.destinations == []


@pytest.mark.parametrize("data", [
    {}, [{}] * 21, [{}], [{"name": "", "path": "/tmp/copy.whab"}],
    [{"name": "Copy", "path": "relative"}], [{"name": "Copy", "path": 3}],
    [{"name": "Copy", "path": "/tmp/copy\x00"}],
    [{"name": "A", "path": "/tmp/same"}, {"name": "B", "path": "/tmp/same"}],
    [{"name": "Copy", "path": "/tmp/copy.whab", "content": "invalid"}],
    [{"name": "Copy", "path": "/tmp/copy.whab", "pending": None}],
])
def test_corrupted_saved_destinations_raise_authored_errors(opened, data):
    session, _ = opened
    (session.storage_home / "copies.json").write_text(json.dumps(data))
    with pytest.raises(StoreError, match="destinations are invalid"):
        BudgetSession.open(session.home, "sandbox", session.path)


def test_saved_destinations_cannot_point_at_the_primary_file(opened):
    session, _ = opened
    (session.storage_home / "copies.json").write_text(json.dumps([{"name": "Primary", "path": str(session.path)}]))
    with pytest.raises(StoreError):
        BudgetSession.open(session.home, "sandbox", session.path)


def test_saved_destination_metadata_is_size_bounded(opened):
    session, _ = opened
    (session.storage_home / "copies.json").write_bytes(b" " * (module.MAX_MANIFEST_BYTES + 1))
    with pytest.raises(StoreError):
        BudgetSession.open(session.home, "sandbox", session.path)


def test_interrupted_primary_save_recovers_committed_ledger_and_disabled_plugin_setup(opened, monkeypatch):
    session, store = opened
    original = module.atomic_write

    def fail_primary(path, raw):
        if Path(path) == session.path:
            raise OSError("Primary unavailable")
        return original(path, raw)

    with monkeypatch.context() as patch:
        patch.setattr(module, "atomic_write", fail_primary)
        session.save_plugin_data("plaid", state={"retained": [1]})
        session.save_selection(())
        account = TrackedStorage(store, session).add_account("personal", {"name": "Committed", "type": "depository", "balance": 100})
    assert session.status == "pending"
    reopened = BudgetSession.open(session.home, "sandbox", session.path)
    assert reopened.selection == ()
    assert reopened.plugin_record("plaid")["state"] == {"retained": [1]}
    reopened.bind(store)
    assert reopened.status == "saved"
    assert account in decode_bundle(session.path.read_bytes())[1]["accounts"][0]["id"]


def test_initial_restore_commit_recovers_if_first_checkpoint_journal_write_fails(tmp_path, incoming, monkeypatch):
    path, _, expected = incoming
    session = BudgetSession.open(tmp_path / "runtime", "sandbox", path)
    store = database(session)
    original = module.atomic_write

    def fail_pending(path, raw):
        if Path(path).name == "source.json" and "pending" in json.loads(raw):
            raise OSError("Journal unavailable after restore")
        return original(path, raw)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(module, "atomic_write", fail_pending)
            session.bind(store)
        assert session.status == "pending"
        assert store.export_budget() == expected
        reopened = BudgetSession.open(session.home, "sandbox", path)
        reopened.bind(store)
        assert reopened.status == "saved"
        assert store.export_budget() == expected
    finally:
        store.close()


def test_initial_restore_recovery_detaches_external_provider_snapshots(tmp_path, incoming, monkeypatch):
    path, manifest, ledger = incoming
    ledger["accounts"][0].update(source="plaid", stale=False)
    path.write_bytes(encode_bundle(manifest, ledger))
    session = BudgetSession.open(tmp_path / "runtime", "sandbox", path)
    store = database(session)
    original = module.atomic_write

    def fail_pending(path, raw):
        if Path(path).name == "source.json" and "pending" in json.loads(raw):
            raise OSError("Interrupted metadata save")
        return original(path, raw)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(module, "atomic_write", fail_pending)
            session.bind(store)
        assert store.accounts("personal")[0]["stale"] is True
        reopened = BudgetSession.open(session.home, "sandbox", path)
        reopened.bind(store)
        assert reopened.status == "saved"
        assert store.items() == []
    finally:
        store.close()


def test_brand_new_budget_recovers_if_first_file_saved_before_final_journal_marker(tmp_path, monkeypatch):
    session = BudgetSession.create(tmp_path / "runtime", "sandbox", "New", path=tmp_path / "new.whab")
    store = database(session)
    original = module.atomic_write

    def fail_complete(path, raw):
        if Path(path).name == "source.json" and set(json.loads(raw)) == {"content"}:
            raise OSError("Final marker unavailable")
        return original(path, raw)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(module, "atomic_write", fail_complete)
            session.bind(store)
        assert session.status == "pending"
        assert json.loads((session.storage_home / "source.json").read_text())["content"] is None
        reopened = BudgetSession.open(session.home, "sandbox", session.path)
        reopened.bind(store)
        assert reopened.status == "saved"
    finally:
        store.close()


def test_replaced_primary_recovers_when_final_source_marker_write_fails(opened, monkeypatch):
    session, store = opened
    original = module.atomic_write

    def fail_complete(path, raw):
        if Path(path).name == "source.json" and set(json.loads(raw)) == {"content"}:
            raise OSError("Final marker unavailable")
        return original(path, raw)

    with monkeypatch.context() as patch:
        patch.setattr(module, "atomic_write", fail_complete)
        TrackedStorage(store, session).add_account("personal", {"name": "Saved before marker", "type": "depository", "balance": 20})
    assert session.status == "pending"
    reopened = BudgetSession.open(session.home, "sandbox", session.path)
    reopened.bind(store)
    assert reopened.status == "saved"
    assert decode_bundle(session.path.read_bytes())[1] == store.export_budget()


def test_missing_local_database_is_rehydrated_without_erasing_portable_original(opened):
    session, store = opened
    account = TrackedStorage(store, session).add_account("personal", {
        "name": "Keep this account", "type": "depository", "balance": 9876,
    })
    raw = session.path.read_bytes()
    reopened = BudgetSession.open(session.home, "sandbox", session.path)
    fresh = Store(":memory:", month="2026-11")
    try:
        reopened.bind(fresh)
        assert fresh.accounts("personal")[0]["id"] == account
        assert fresh.export_budget() == store.export_budget()
        assert decode_bundle(session.path.read_bytes()) == decode_bundle(raw)
    finally:
        fresh.close()


def test_remote_ledger_disappearance_cannot_replace_saved_nonempty_budget(opened):
    session, store = opened
    TrackedStorage(store, session).add_account("personal", {
        "name": "Keep this account", "type": "depository", "balance": 9876,
    })
    raw = session.path.read_bytes()
    fresh = Store(":memory:", month="2026-10")

    class EmptyRemote:
        def export_budget(self, workspace):
            return fresh.export_budget(workspace)

    try:
        reopened = BudgetSession.open(session.home, "sandbox", session.path)
        with pytest.raises(StoreError, match="local ledger is empty"):
            reopened.bind(EmptyRemote())
        assert session.path.read_bytes() == raw
    finally:
        fresh.close()


def test_initial_restore_is_not_started_if_recovery_record_cannot_be_saved(tmp_path, incoming, monkeypatch):
    path, _, _ = incoming
    session = BudgetSession.open(tmp_path / "runtime", "sandbox", path)
    store = database(session)
    before = store.state("personal")

    def fail(*_args):
        raise OSError("Disk full")

    try:
        monkeypatch.setattr(module, "atomic_write", fail)
        with pytest.raises(StoreError, match="ledger was not restored"):
            session.bind(store)
        assert store.state("personal") == before
    finally:
        store.close()


def test_external_revision_is_preserved_and_never_silently_overwrites_local_work(opened):
    session, store = opened
    manifest, ledger = decode_bundle(session.path.read_bytes())
    manifest["name"] = "Edited externally"
    external = encode_bundle(manifest, ledger)
    session.path.write_bytes(external)
    account = TrackedStorage(store, session).add_account("personal", {"name": "Local work", "type": "depository", "balance": 10})
    assert session.status == "pending"
    assert session.path.read_bytes() == external
    assert account in store.state("personal")["accounts"]
    with pytest.raises(StoreError, match="differs"):
        BudgetSession.open(session.home, "sandbox", session.path)


@pytest.mark.parametrize("record", [
    None, [], {}, {"content": "invalid"}, {"content": 2}, {"content": "f" * 64, "other": 1},
    {"content": "f" * 64, "pending": "a" * 64, "restorePending": "a" * 64},
])
def test_corrupt_recovery_markers_fail_without_changing_saved_files(opened, record):
    session, _ = opened
    marker = session.storage_home / "source.json"
    marker.write_text(json.dumps(record))
    raw = session.path.read_bytes()
    with pytest.raises(StoreError, match="recovery record"):
        BudgetSession.open(session.home, "sandbox", session.path)
    assert session.path.read_bytes() == raw


@pytest.mark.parametrize("corruption", ["bad-json", "wrong-id", "wrong-storage"])
def test_corrupt_recovered_plugin_setup_does_not_replace_valid_bundle(opened, corruption):
    session, _ = opened
    value = deepcopy(session.manifest)
    if corruption == "wrong-id":
        value["id"] = new_manifest("Other")["id"]
    if corruption == "wrong-storage":
        value["storage"] = "other"
    path = session.storage_home / "manifest.json"
    path.write_text("not json" if corruption == "bad-json" else json.dumps(value))
    with pytest.raises(StoreError, match="plugin setup"):
        BudgetSession.open(session.home, "sandbox", session.path)


def test_checkpoint_errors_after_database_commit_report_pending_and_keep_committed_result(opened, tmp_path, monkeypatch):
    session, store = opened
    session.add_copy("Copy", str(tmp_path / "copy.whab"))
    old_file = session.path.read_bytes()

    def fail_export(_workspace):
        raise RuntimeError("Synthetic backend failure")

    monkeypatch.setattr(store, "export_budget", fail_export)
    account = TrackedStorage(store, session).add_account("personal", {"name": "Committed", "type": "depository", "balance": 10})
    assert account in store.state("personal")["accounts"]
    assert session.status == "pending"
    assert session.destinations[0]["status"] == "pending"
    assert session.path.read_bytes() == old_file


def test_manifest_write_failure_after_commit_is_pending_instead_of_a_false_failed_mutation(opened, monkeypatch):
    session, store = opened
    original = module.atomic_write

    def fail_manifest(path, raw):
        if Path(path).name == "manifest.json":
            raise OSError("Metadata unavailable")
        return original(path, raw)

    monkeypatch.setattr(module, "atomic_write", fail_manifest)
    assert TrackedStorage(store, session).add_account("personal", {"name": "Committed", "type": "depository", "balance": 1})
    assert session.status == "pending"


def test_unbound_session_has_no_checkpoint_side_effects_and_cannot_export(tmp_path):
    session = BudgetSession.create(tmp_path, "sandbox", "Unbound")
    session.checkpoint()
    assert not session.storage_home.exists()
    with pytest.raises(StoreError, match="cannot export"):
        session.bundle()
    with pytest.raises(StoreError, match="could not be opened"):
        BudgetSession.open(tmp_path, "sandbox", tmp_path / "missing.whab")


def test_new_budget_never_adopts_permission_to_overwrite_existing_target(tmp_path, store):
    target = tmp_path / "appeared-before-construction.whab"
    target.write_bytes(b"An unrelated file appeared before budget initialization")
    session = BudgetSession.create(tmp_path / "runtime", "sandbox", "New budget", path=target)
    session.bind(store)
    assert session.status == "pending"
    assert target.read_bytes() == b"An unrelated file appeared before budget initialization"
    assert decode_bundle(session.bundle())[0]["name"] == "New budget"


def test_export_only_remote_storage_attaches_matching_ledger_without_restore(tmp_path, incoming):
    path, manifest, ledger = incoming
    manifest["storage"] = "client"
    path.write_bytes(encode_bundle(manifest, ledger))

    class RemoteLedger:
        def export_budget(self, workspace):
            assert workspace == "personal"
            return deepcopy(ledger)

    session = BudgetSession.open(tmp_path / "runtime", "sandbox", path)
    session.bind(RemoteLedger())
    assert session.status == "saved"
    assert decode_bundle(path.read_bytes())[1] == ledger


def test_export_only_remote_storage_rejects_mismatched_ledger(tmp_path, incoming):
    path, _, _ = incoming

    class RemoteLedger:
        def export_budget(self, _workspace):
            return {"not": "the original ledger"}

    session = BudgetSession.open(tmp_path / "runtime", "sandbox", path)
    before = path.read_bytes()
    with pytest.raises(StoreError, match="differs from this file"):
        session.bind(RemoteLedger())
    assert path.read_bytes() == before


def test_tracked_storage_retains_noncallable_optional_capabilities_and_invalidates_only_after_success(opened):
    _, store = opened
    store.import_workspace = None
    tracked = TrackedStorage(store)
    calls = []
    tracked.on_mutation = lambda: calls.append("committed")
    assert tracked.import_workspace is None
    assert tracked.database_path == store.database_path
    tracked.accounts("personal")
    assert calls == []
    tracked.add_account("personal", {"name": "Cash", "type": "depository", "balance": 1})
    assert calls == ["committed"]
    with pytest.raises(StoreError):
        tracked.add_account("personal", {"name": "Bad", "type": "unsupported", "balance": 1})
    assert calls == ["committed"]
