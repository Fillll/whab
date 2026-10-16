"""One command boundary preserves validation, revisions and idempotent replay."""

from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from types import SimpleNamespace

import pytest

from whab.domain.errors import StoreError
from whab.services.ledger import LedgerService, RequestConflict, RevisionConflict


def test_local_facade_and_portable_snapshot(store):
    saved = []
    service = LedgerService(store, budget_id="household", on_commit=lambda: saved.append(True))
    initial = service.revision
    account = service.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    category = service.add_category("personal", {"name": "Food", "group": "Living", "target": 0})
    transaction = service.add_transaction("personal", {
        "accountId": account, "date": "2026-10-01", "merchant": "Shop", "amount": 100,
        "categoryId": category,
    })
    service.edit_transaction("personal", transaction, reviewed=True)
    service.edit_transaction("personal", transaction, category_id=None)
    service.assign("personal", category, "2026-10", 100)
    assert len(saved) == 6 and service.revision != initial
    assert service.accounts("personal")[0]["id"] == account
    assert category in {row["id"] for row in service.categories("personal")}
    assert service.transactions("personal")[0]["reviewed"] is True
    assert service.workspace("personal", "2026-10")["accounts"][0]["id"] == account
    portable = service.query("portable", "personal")["data"]
    assert portable["accounts"][0]["id"] == account
    assert "items" not in portable
    with pytest.raises(StoreError, match="manual"):
        service.add_transaction("personal", {}, source="plaid")


def test_replay_is_bound_to_payload_and_original_revision(store):
    service = LedgerService(store)
    original = service.revision
    arguments = ("add_account", "personal", {"name": "Cash", "type": "depository", "balance": 0})
    result = service.command(*arguments, request_id="request", expected_revision=original)
    assert service.command(*arguments, request_id="request", expected_revision=original) == result
    with pytest.raises(RequestConflict):
        service.command(*arguments, request_id="request", expected_revision=service.revision)
    with pytest.raises(RevisionConflict):
        service.command(*arguments, request_id="other", expected_revision=original)
    assert len(service.accounts("personal")) == 1


def test_concurrent_writers_cannot_both_use_a_revision(store):
    service = LedgerService(store)
    revision = service.revision

    def save(number):
        try:
            return service.command("add_account", "personal", {
                "name": "Cash", "type": "depository", "balance": number,
            }, request_id=str(number), expected_revision=revision)
        except RevisionConflict:
            return None

    with ThreadPoolExecutor(2) as pool:
        result = list(pool.map(save, [1, 2]))
    assert sum(row is not None for row in result) == 1
    assert len(service.accounts("personal")) == 1


@pytest.mark.parametrize("operation,payload", [
    ("save_item", {}),
    ("add_account", []),
    ("add_account", {"name": "Cash"}),
    ("add_account", {"name": "Cash", "type": "credit", "balance": 0, "token": "no"}),
    ("add_account", {"name": "Cash", "type": [], "balance": 0}),
    ("add_account", {"name": "Cash", "type": "investment", "balance": 0}),
    ("add_account", {"name": "Cash", "type": "credit", "balance": True}),
    ("add_account", {"name": "Cash", "type": "credit", "balance": 0, "currency": "xyz"}),
    ("add_category", {"name": "Food", "target": -1}),
    ("assign", {"categoryId": "cat", "month": "2026-99", "assigned": 0}),
    ("assign", {"categoryId": "", "month": "2026-10", "assigned": 0}),
    ("edit_transaction", {"transactionId": "tx"}),
    ("edit_transaction", {"transactionId": "tx", "reviewed": "true"}),
    ("add_transaction", {"accountId": "account", "date": "2026-10-01", "merchant": "Cash", "amount": 0, "categoryId": None}),
    ("add_transaction", {"accountId": "account", "date": "2026-10-01", "merchant": "Cash", "amount": 1, "categoryId": None, "note": []}),
])
def test_invalid_commands_never_dispatch(store, operation, payload):
    service = LedgerService(store)
    revision = service.revision
    with pytest.raises(StoreError):
        service.command(operation, "personal", payload, request_id="id", expected_revision=revision)
    assert service.revision == revision


def test_scoping_and_safe_public_queries():
    service = LedgerService(SimpleNamespace(accounts=lambda workspace: [{
        "id": "account", "token": "private", "access_token": "private",
        "nested": {"cursor": "private", "name": "safe"},
    }]))
    assert service.accounts("personal") == [{"id": "account", "nested": {"name": "safe"}}]
    with pytest.raises(StoreError, match="workspace"):
        service.query("accounts", "other")
    with pytest.raises(StoreError, match="query"):
        service.query("get_item", "personal")
    with pytest.raises(StoreError, match="portable"):
        service.query("portable", "personal")
    with pytest.raises(StoreError, match="month"):
        service.query("workspace", "personal", "bad")


def test_receipts_are_bounded_and_old_revisions_remain_rejected():
    service = LedgerService(SimpleNamespace(add_account=lambda *args: "account"))
    initial = service.revision
    payload = {"name": "Cash", "type": "depository", "balance": 0}
    for index in range(1025):
        service.command("add_account", "personal", payload, request_id=str(index), expected_revision=service.revision)
    assert len(service._receipts) == 1024
    with pytest.raises(RevisionConflict):
        service.command("add_account", "personal", payload, request_id="0", expected_revision=initial)


def test_external_ingestion_shares_revision_boundary(store):
    saved = []
    service = LedgerService(store, on_commit=lambda: saved.append(True))
    revision = service.revision
    result = service.external_mutation(lambda: store.add_account("personal", {
        "name": "Import", "type": "depository", "balance": 0,
    }))
    assert result in {row["id"] for row in service.accounts("personal")}
    assert service.revision != revision and saved == [True]


def test_shared_lock_invalidation_does_not_checkpoint(store):
    lock = RLock()
    saved = []
    service = LedgerService(store, lock=lock, on_commit=lambda: saved.append(True))
    assert service._lock is lock
    original = service.revision
    with lock:
        service.invalidate_revision()
    assert service.revision != original and saved == []


@pytest.mark.parametrize("date", ["0001-01-01", "1899-12-31", "2201-01-01", "9999-01-01"])
def test_public_dates_cannot_commit_a_nonportable_transaction(store, date):
    service = LedgerService(store)
    account = service.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    before = store.export_budget()
    revision = service.revision
    with pytest.raises(StoreError, match="Transaction date must be between"):
        service.add_transaction("personal", {
            "accountId": account, "date": date, "merchant": "Fixture", "amount": 1,
            "categoryId": None,
        })
    assert service.revision == revision and store.export_budget() == before


@pytest.mark.parametrize("date", ["1900-01-01", "2200-12-31"])
def test_supported_date_boundaries_remain_portable(store, date):
    service = LedgerService(store)
    account = service.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    transaction = service.add_transaction("personal", {
        "accountId": account, "date": date, "merchant": "Fixture", "amount": 1,
        "categoryId": None,
    })
    assert store.export_budget()["transactions"][0]["id"] == transaction


@pytest.mark.parametrize("month", ["1899-12", "2201-01", "2299-12", "9999-01"])
def test_query_and_assignment_month_range_matches_every_public_interface(store, month):
    service = LedgerService(store)
    before = store.export_budget()
    revision = service.revision
    with pytest.raises(StoreError):
        service.workspace("personal", month)
    with pytest.raises(StoreError):
        service.assign("personal", "groceries", month, 0)
    assert service.revision == revision and store.export_budget() == before
