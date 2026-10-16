"""One command boundary preserves validation, revisions and idempotent replay."""

from concurrent.futures import ThreadPoolExecutor
from threading import RLock
from types import SimpleNamespace

import pytest

from whab.domain.budget import select_currency
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
    ("add_account", {"name": "Cash", "type": "invalid", "balance": 0}),
    ("add_account", {"name": "IRA", "type": "retirement", "balance": 0, "onBudget": True}),
    ("add_account", {"name": "IRA", "type": "retirement", "balance": 0, "onBudget": "false"}),
    ("update_account", {"accountId": "a"}),
    ("update_account", {"accountId": "a", "onBudget": None}),
    ("update_account", {"accountId": "a", "subtype": []}),
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


def test_account_classification_uses_shared_revision_and_idempotency(store):
    service = LedgerService(store)
    account = service.add_account("personal", {
        "name": "Brokerage", "type": "investment", "balance": 50000, "subtype": "brokerage",
    })
    revision = service.revision
    payload = {"accountId": account, "type": "retirement", "subtype": "roth_ira", "onBudget": False}
    response = service.command(
        "update_account", "personal", payload, request_id="classify", expected_revision=revision,
    )
    assert service.command(
        "update_account", "personal", payload, request_id="classify", expected_revision=revision,
    ) == response
    record = service.accounts("personal")[0]
    assert (record["type"], record["subtype"], record["onBudget"]) == ("retirement", "roth_ira", False)
    service.update_account("personal", account, {"type": "depository", "subtype": "savings", "onBudget": True})
    assert service.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 50000


def test_older_storage_can_open_without_optional_account_update():
    service = LedgerService(SimpleNamespace())
    with pytest.raises(StoreError, match="does not support account classification"):
        service.update_account("personal", "a", {"onBudget": False})


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


def test_workspace_periods_share_core_rollover_refunds_debt_and_currency_rules(store, monkeypatch):
    monkeypatch.setattr("whab_sqlite.store.current_month", lambda: "2026-10")
    service = LedgerService(store)
    cash = service.add_account("personal", {"name": "Cash", "type": "depository", "balance": 100000})
    service.add_account("personal", {"name": "Card", "type": "credit", "balance": 20000})
    service.add_account("personal", {
        "name": "Euro cash", "type": "depository", "balance": 50000, "currency": "EUR",
    })
    euro_category = service.add_category("personal", {
        "name": "Euro food", "group": "Euro living", "target": 0, "currency": "EUR",
    })
    service.assign("personal", "groceries", "2026-10", 20000)
    service.assign("personal", "groceries", "2026-11", 30000)
    service.assign("personal", euro_category, "2026-10", 8000)
    for date, amount in (("2026-10-10", 7000), ("2026-11-10", -500)):
        service.add_transaction("personal", {
            "accountId": cash, "date": date, "merchant": "Fixture grocer", "amount": amount,
            "categoryId": "groceries",
        })
    before, revision = store.export_budget(), service.revision
    periods = service.workspace_periods("personal", "2026-10", 3)
    assert [period["month"] for period in periods] == ["2026-10", "2026-11", "2026-12"]
    assert periods == [service.workspace("personal", period["month"]) for period in periods]
    usd = [select_currency(period, "USD") for period in periods]
    grocery_rows = [next(row for row in period["budget"] if row["categoryId"] == "groceries") for period in usd]
    assert [(row["carryover"], row["assigned"], row["spent"], row["available"]) for row in grocery_rows] == [
        (0, 20000, 7000, 13000), (13000, 30000, -500, 43500), (43500, 0, 0, 43500),
    ]
    assert [period["summary"]["readyToAssign"] for period in usd] == [60000, 30000, 30000]
    eur = [select_currency(period, "EUR") for period in periods]
    assert [period["summary"]["readyToAssign"] for period in eur] == [42000, 42000, 42000]
    assert all({row["currency"] for row in period["accounts"]} == {"EUR"} for period in eur)
    # Reading another month does not weaken the command's future reservation rule.
    with pytest.raises(StoreError, match="exceeds available money"):
        service.assign("personal", "groceries", "2026-10", 50001)
    assert service.revision == revision and store.export_budget() == before


def test_workspace_periods_hold_shared_lock_and_sanitize_each_projection():
    class Lock:
        held = False
        entries = 0

        def __enter__(self):
            assert not self.held
            self.held = True
            self.entries += 1

        def __exit__(self, *args):
            self.held = False

    lock = Lock()
    months = []
    private = {"access_token": "private", "safe": [{"cursor": "private", "name": "safe"}]}

    def workspace(scope, month):
        assert scope == "personal" and lock.held
        months.append(month)
        return {"month": month, "nested": private}

    service = LedgerService(SimpleNamespace(workspace=workspace), lock=lock)
    assert service.workspace_periods("personal", "2026-12", 3) == [
        {"month": month, "nested": {"safe": [{"name": "safe"}]}}
        for month in ("2026-12", "2027-01", "2027-02")
    ]
    assert months == ["2026-12", "2027-01", "2027-02"]
    assert lock.entries == 1 and not lock.held
    assert private["access_token"] == "private" and private["safe"][0]["cursor"] == "private"


@pytest.mark.parametrize("workspace,month,count", [
    ("other", "2026-10", 3),
    ([], "2026-10", 3),
    ("personal", None, 3),
    ("personal", True, 3),
    ("personal", "2026-13", 3),
    ("personal", "1899-12", 3),
    ("personal", "2201-01", 3),
    ("personal", "2026-10", True),
    ("personal", "2026-10", 0),
    ("personal", "2026-10", -1),
    ("personal", "2026-10", 13),
    ("personal", "2026-10", 3.0),
    ("personal", "2026-10", "3"),
    ("personal", "2026-10", None),
])
def test_invalid_workspace_period_requests_never_dispatch(workspace, month, count):
    service = LedgerService(SimpleNamespace(workspace=lambda *args: pytest.fail("Invalid read dispatched")))
    with pytest.raises(StoreError):
        service.workspace_periods(workspace, month, count)


@pytest.mark.parametrize("month,count,expected", [
    ("1900-01", 12, [f"1900-{number:02d}" for number in range(1, 13)]),
    ("2199-12", 3, ["2199-12", "2200-01", "2200-02"]),
    ("2200-11", 12, ["2200-11", "2200-12"]),
    ("2200-12", 3, ["2200-12"]),
])
def test_workspace_periods_clamp_at_the_last_supported_month(store, month, count, expected):
    periods = LedgerService(store).workspace_periods("personal", month, count)
    assert [period["month"] for period in periods] == expected
