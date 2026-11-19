"""Fictional empty registers retain unknown balances, identity and durable intent."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from whab.domain import commands
from whab.domain.accounts import recorded_account_activity
from whab.domain.ledger_state import initial_state
from whab.plugins import (
    LedgerService,
    ManualAccountCreationStorage,
    RequestConflict,
    RevisionConflict,
    StoreError,
    account_balance_position,
    is_liability,
    normalize_manual_account,
)


def payload(**changes):
    return {"name": "🏡 Сбережения", "type": "depository", "subtype": "savings",
            "currency": "USD", "onBudget": False, "balance": 0,
            "balanceUnknown": True, "uniqueName": True, **changes}


@pytest.mark.parametrize("changes", [
    {"balanceUnknown": 1}, {"balanceUnknown": "true"}, {"uniqueName": None},
    {"uniqueName": 1}, {"balance": 100}, {"balance": True},
    {"type": "deposit", "subtype": "savings"}, {"type": "loan", "onBudget": True},
    {"currency": "ZZZ"}, {"source": "plaid"}, {"name": "   "},
])
def test_creation_validation_is_shared_and_pure(changes):
    proposed = payload(**changes)
    before = deepcopy(proposed)
    with pytest.raises(StoreError):
        normalize_manual_account(proposed)
    assert proposed == before


@pytest.mark.parametrize("proposed", [None, [], {}, {"name": "Fictional"}])
def test_creation_requires_a_complete_public_payload(proposed):
    with pytest.raises(StoreError, match="creation fields"):
        normalize_manual_account(proposed)


def test_creation_normalization_has_no_identifier_or_origin_and_unique_guard_is_explicit():
    proposed = payload(name="  Straße 🏡  ", currency="USD")
    rows = [{"id": "existing", "name": " STRASSE 🏡 "}]
    with pytest.raises(StoreError, match="existing"):
        normalize_manual_account(proposed, rows)
    normalized = normalize_manual_account({**proposed, "uniqueName": False}, rows)
    assert normalized["name"] == "Straße 🏡" and normalized["currency"] == "USD"
    assert not {"id", "source", "institution"} & normalized.keys()
    assert proposed["name"] == "  Straße 🏡  "
    assert ManualAccountCreationStorage.supports_manual_account_creation


@pytest.mark.parametrize("kind,subtype", [("depository", "savings"), ("loan", "mortgage")])
def test_unknown_register_activity_never_verifies_a_current_balance(kind, subtype):
    state = initial_state("personal", "2026-11")
    commands.add_account(state, "fictional", payload(type=kind, subtype=subtype))
    saved = state["accounts"]["fictional"]
    assert saved["source"] == "manual" and saved["balanceUnknown"] and saved["balance"] == 0
    assert not state["transactions"] and not state["items"] and "uniqueName" not in saved
    for identifier, amount in (("expense", 12345), ("income", -50000)):
        commands.add_transaction(state, identifier, {"accountId": "fictional", "date": "2002-01-02",
            "merchant": "Fictional historical entry", "amount": amount, "categoryId": None})
    assert (-saved["balance"] if is_liability(saved) else saved["balance"]) == 37655
    assert saved["balanceUnknown"] and account_balance_position(saved) == {"value": None, "status": "unavailable"}


def test_recorded_activity_domain_counts_all_live_records_without_persisting_projection():
    accounts = [dict(payload(), id="unknown", source="manual"),
                dict(payload(), id="known", source="manual", balanceUnknown=False)]
    transactions = [{"accountId": "unknown", "amount": -1000, "pending": True, "source": "custom"},
                    {"accountId": "unknown", "amount": 125, "pending": False},
                    {"accountId": "unknown", "amount": 500, "deleted": True},
                    {"accountId": "known", "amount": -9000}]
    before = deepcopy(accounts)
    assert recorded_account_activity(accounts, transactions) == {"unknown": 875}
    assert recorded_account_activity(accounts[1:], transactions) == {}
    assert accounts == before


@pytest.fixture(params=["sqlite", "json"])
def native(request, tmp_path):
    if request.param == "sqlite":
        from whab_plugin_sqlite import Store
    else:
        from whab_plugin_json import Store
    path = tmp_path / ("fictional." + request.param)
    store = Store(path, month="2026-11")
    yield store, Store, path
    store.close()


def test_guarded_creation_has_one_commit_exact_retries_and_durable_restore(native, tmp_path):
    store, factory, path = native
    assert store.supports_manual_account_creation() is True
    saves = []
    service = LedgerService(store, on_commit=lambda: saves.append(True))
    original = service.revision
    generation = store.commit_evidence().generation
    history = {"actor": "Codex", "actorKind": "agent", "batchId": "fictional-create"}
    request = dict(request_id="fictional-create", expected_revision=original, history=history)
    result = service.command("add_account", "personal", payload(), **request)
    identifier = result["data"]
    assert service.command("add_account", "personal", payload(), **request) == result
    assert len(saves) == 1 and store.commit_evidence().generation == generation + 1
    entry = store.budget_history("personal")["entries"][0]
    assert entry["actor"] == "Codex" and entry["batchId"] == "fictional-create"
    assert len(store.budget_history("personal")["entries"]) == 1
    row = service.accounts("personal")[0]
    assert row["id"] == identifier and row["balanceUnknown"] and row["balance"] == 0
    assert service.transactions("personal") == [] and store.items() == []
    assert not row.get("itemId") and not row.get("bankAccountId") and "uniqueName" not in row
    before = store.export_budget()
    with pytest.raises(StoreError, match="already exists"):
        service.command("add_account", "personal", payload(name="  🏡 СБЕРЕЖЕНИЯ  "),
            request_id="duplicate-name", expected_revision=service.revision)
    with pytest.raises(RevisionConflict):
        service.command("add_account", "personal", payload(name="Another"),
            request_id="stale", expected_revision=original)
    with pytest.raises(RequestConflict):
        service.command("add_account", "personal", payload(name="Changed"), **request)
    assert store.export_budget() == before and len(saves) == 1
    store.close()
    reopened = factory(path, month="2026-11")
    try:
        assert reopened.accounts("personal") == [row] and len(saves) == 1
        assert len(reopened.budget_history("personal")["entries"]) == 1
        restored = factory(tmp_path / "restored", month="2026-11")
        try:
            restored.restore_budget_files("personal", reopened.export_budget_files())
            assert restored.accounts("personal") == [row]
            assert restored.transactions("personal") == []
            assert restored.workspace("personal", "2026-11")["accountSummary"]["netWorth"] == 0
        finally:
            restored.close()
    finally:
        reopened.close()


def test_unique_name_guard_is_atomic_and_scoped_but_legacy_duplicates_remain_supported(native):
    store, _, _ = native
    first = store.add_account("personal", payload())
    before = store.commit_evidence()
    with pytest.raises(StoreError, match=first):
        store.add_account("personal", payload())
    assert store.commit_evidence() == before
    assert store.add_account("demo", payload()) != first
    assert store.add_account("personal", payload(uniqueName=False)) != first
    assert len(store.accounts("personal")) == 2


def test_optional_subtype_is_null_and_known_balance_creation_stays_compatible(native):
    store, _, _ = native
    service = LedgerService(store)
    proposed = payload()
    proposed.pop("subtype")
    normalized = normalize_manual_account(proposed)
    assert normalized["subtype"] is None
    identifier = service.add_account("personal", normalized)
    row = next(row for row in store.accounts("personal") if row["id"] == identifier)
    assert row["subtype"] is None and row["balanceUnknown"]
    known = service.add_account("personal", {"name": "Fictional known balance", "type": "depository", "balance": 10000})
    row = next(row for row in store.accounts("personal") if row["id"] == known)
    assert not row["balanceUnknown"] and account_balance_position(row) == {"value": 10000, "status": "current"}


def test_empty_unknown_account_creation_is_auditable_and_reversible(native):
    store, _, _ = native
    service = LedgerService(store)
    identifier = service.add_account("personal", payload())
    saved = deepcopy(store.accounts("personal"))
    target = store.budget_history("personal")["entries"][0]["id"]
    for action in ("undo", "redo"):
        preview = service.query("history_preview", "personal",
            parameters={"action": action, "target": target})["data"]
        service.command("history_apply", "personal", {
            "action": action, "target": target, "fingerprint": preview["fingerprint"]},
            request_id="creation-" + action, expected_revision=service.revision)
        assert store.accounts("personal") == ([] if action == "undo" else saved)
    assert store.accounts("personal")[0]["id"] == identifier


def test_activity_survives_reclassification_and_new_entries_without_entering_saved_state(native):
    store, _, _ = native
    service = LedgerService(store)
    identifier = service.add_account("personal", payload())
    service.add_transaction("personal", {"accountId": identifier, "date": "2002-01-01",
        "merchant": "Fictional inflow", "amount": -10000, "categoryId": None})
    before = store.export_budget()
    files = store.export_budget_files()
    target = store.budget_history("personal")["entries"][0]["id"]
    history = store.budget_history("personal", snapshot=True, target=target)
    evidence = store.commit_evidence()
    assert service.accounts("personal")[0]["recordedActivity"] == 10000
    assert store.export_budget() == before and store.export_budget_files() == files
    assert store.budget_history("personal", snapshot=True, target=target) == history and store.commit_evidence() == evidence
    assert all("recordedActivity" not in row for row in before["accounts"])
    service.update_account("personal", identifier, {"type": "credit"})
    assert service.accounts("personal")[0]["recordedActivity"] == 10000
    assert service.accounts("personal")[0]["balance"] == 10000
    service.add_transaction("personal", {"accountId": identifier, "date": "2002-02-01",
        "merchant": "Fictional outflow after reclassification", "amount": 155, "categoryId": None})
    row = service.accounts("personal")[0]
    assert row["recordedActivity"] == 9845 and row["balance"] == 10155 and row["balanceUnknown"]
    service.update_account("personal", identifier, {"type": "depository"})
    assert service.accounts("personal")[0]["recordedActivity"] == 9845
    target = store.budget_history("personal")["entries"][0]["id"]
    assert "recordedActivity" not in str(store.budget_history("personal", snapshot=True, target=target))


@pytest.mark.parametrize("support", [None, False])
def test_older_backend_refuses_new_creation_contract_before_mutation(support):
    backend = SimpleNamespace()
    if support is not None:
        backend.supports_manual_account_creation = lambda: support
    service = LedgerService(backend)
    revision = service.revision
    with pytest.raises(StoreError, match="Update the storage plugin/authority"):
        service.command("add_account", "personal", payload(), request_id="no-support", expected_revision=revision)
    assert service.revision == revision
