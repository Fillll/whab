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


def test_next_month_funding_uses_revision_replay_and_commit_boundary(store, monkeypatch):
    monkeypatch.setattr("whab_plugin_sqlite.store.current_month", lambda: "2026-10")
    saved = []
    service = LedgerService(store, on_commit=lambda: saved.append(True))
    service.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    original = service.revision
    payload = {"month": "2026-10", "currency": "USD", "assigned": 3000}
    response = service.command(
        "assign_next_month", "personal", payload, request_id="funding", expected_revision=original,
    )
    assert service.command(
        "assign_next_month", "personal", payload, request_id="funding", expected_revision=original,
    ) == response
    assert len(saved) == 2
    projection = service.workspace("personal", "2026-10")
    assert projection["nextMonthFundingEditable"] is True
    assert projection["summary"]["assignedNextMonth"] == 3000
    assert projection["summary"]["readyToAssign"] == 7000
    with pytest.raises(RevisionConflict):
        service.command(
            "assign_next_month", "personal", {**payload, "assigned": 4000},
            request_id="stale", expected_revision=original,
        )
    service.assign_next_month("personal", "2026-10", "USD", 2000)
    assert service.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 8000


def test_next_month_funding_is_optional_for_existing_storage():
    service = LedgerService(SimpleNamespace())
    revision = service.revision
    with pytest.raises(StoreError, match="does not support next-month funding"):
        service.assign_next_month("personal", "2026-10", "USD", 0)
    assert service.revision == revision


def test_monthly_rollover_uses_revision_replay_without_moving_money(store):
    service = LedgerService(store)
    cash = service.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    service.assign("personal", "groceries", "2026-10", 2000)
    service.add_transaction("personal", {
        "accountId": cash, "date": "2026-10-05", "merchant": "Grocer", "amount": 1000,
        "categoryId": "groceries",
    })
    before = store.export_budget()
    revision = service.revision
    payload = {"month": "2026-10", "currency": "USD", "mode": "balance"}
    response = service.command(
        "set_rollover_mode", "personal", payload,
        request_id="rollover", expected_revision=revision,
    )
    assert service.command(
        "set_rollover_mode", "personal", payload,
        request_id="rollover", expected_revision=revision,
    ) == response
    assert service.workspace("personal", "2026-10")["rolloverMode"] == "balance"
    november = service.workspace("personal", "2026-11")
    assert november["rolloverModeEditable"] is True
    assert november["summary"]["balanceCarryover"] == 1000
    assert november["summary"]["readyToAssign"] == 9000
    assert all(row["carryover"] == 0 for row in november["budget"])
    after = store.export_budget()
    assert {key: value for key, value in after.items() if key != "rolloverModes"} == {
        key: value for key, value in before.items() if key != "rolloverModes"
    }
    service.set_rollover_mode("personal", "2026-10", "USD", "category")
    november = service.workspace("personal", "2026-11")
    assert november["summary"]["balanceCarryover"] == 0
    assert next(row for row in november["budget"] if row["categoryId"] == "groceries")["carryover"] == 1000


def test_rollover_settings_are_optional_for_existing_storage():
    service = LedgerService(SimpleNamespace())
    revision = service.revision
    with pytest.raises(StoreError, match="does not support monthly rollover settings"):
        service.set_rollover_mode("personal", "2026-10", "USD", "category")
    assert service.revision == revision


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
    ("assign_next_month", {"month": "2026-10", "currency": "USD", "assigned": -1}),
    ("assign_next_month", {"month": "2026-10", "currency": "xyz", "assigned": 1}),
    ("assign_next_month", {"month": "2026-10", "currency": "USD", "assigned": True}),
    ("assign_next_month", {"month": "2026-13", "currency": "USD", "assigned": 0}),
    ("assign_next_month", {"month": "2026-10", "assigned": 0}),
    ("set_rollover_mode", {"month": "2026-10", "currency": "USD", "mode": "reset"}),
    ("set_rollover_mode", {"month": "2026-10", "currency": "USD", "mode": True}),
    ("set_rollover_mode", {"month": "2026-10", "currency": "USD", "mode": []}),
    ("set_rollover_mode", {"month": "2026-10", "currency": "USD"}),
    ("set_rollover_mode", {"month": "2026-13", "currency": "USD", "mode": "category"}),
    ("set_rollover_mode", {"month": "2026-10", "currency": "xyz", "mode": "category"}),
    ("edit_transaction", {"transactionId": "tx"}),
    ("edit_transaction", {"transactionId": "tx", "reviewed": "true"}),
    ("edit_transaction", {"transactionId": "tx", "note": None}),
    ("edit_transaction", {"transactionId": "tx", "note": False}),
    ("edit_transaction", {"transactionId": "tx", "note": []}),
    ("edit_transaction", {"transactionId": "tx", "note": "x" * 1001}),
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


def test_memo_edits_share_revision_replay_and_atomic_commit_boundary(store):
    saved = []
    service = LedgerService(store, on_commit=lambda: saved.append(True))
    account = service.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    transaction = service.add_transaction("personal", {
        "accountId": account, "date": "2026-10-01", "merchant": "Shop", "amount": 100,
        "categoryId": None, "note": "Original memo",
    })
    revision = service.revision
    payload = {"transactionId": transaction, "categoryId": "fun", "reviewed": True, "note": "Updated memo"}
    response = service.command(
        "edit_transaction", "personal", payload, request_id="memo", expected_revision=revision,
    )
    assert service.command(
        "edit_transaction", "personal", payload, request_id="memo", expected_revision=revision,
    ) == response
    assert len(saved) == 3
    assert service.transactions("personal")[0]["note"] == "Updated memo"
    assert service.transactions("personal")[0]["categoryId"] == "fun"
    assert service.transactions("personal")[0]["reviewed"] is True
    assert service.accounts("personal")[0]["balance"] == 9900
    with pytest.raises(RequestConflict):
        service.command(
            "edit_transaction", "personal", {**payload, "note": "Different memo"},
            request_id="memo", expected_revision=revision,
        )
    service.edit_transaction("personal", transaction, note="")
    assert service.transactions("personal")[0]["note"] == ""


def test_omitted_memo_preserves_older_backend_call_signature():
    calls = []

    def edit_transaction(workspace, transaction_id, category_id, reviewed):
        calls.append((workspace, transaction_id, category_id, reviewed))

    service = LedgerService(SimpleNamespace(edit_transaction=edit_transaction))
    service.edit_transaction("personal", "saved", category_id=None, reviewed=True)
    assert calls == [("personal", "saved", None, True)]


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
    monkeypatch.setattr("whab_plugin_sqlite.store.current_month", lambda: "2026-10")
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
    assert [period["summary"]["carriedIn"] for period in usd] == [80000, 60000, 30000]
    assert all(period["monthlyFundingFlow"] is True for period in usd)
    eur = [select_currency(period, "EUR") for period in periods]
    assert [period["summary"]["readyToAssign"] for period in eur] == [42000, 42000, 42000]
    assert all({row["currency"] for row in period["accounts"]} == {"EUR"} for period in eur)
    # The monthly display does not reserve later plans, while edits protect their funding.
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


def test_register_projection_fallback_scopes_public_reads_without_workspace():
    accounts = [
        {"id": "usd", "currency": "USD"}, {"id": "second", "currency": "USD"},
        {"id": "eur", "currency": "EUR"},
    ]
    categories = [{"id": "food", "name": "Food", "group": "Living", "target": 0, "currency": "USD"}]
    rows = [
        {"id": "first", "accountId": "usd", "date": "2026-10-01", "token": "secret"},
        {"id": "other", "accountId": "second", "date": "2026-10-01"},
        {"id": "prior", "accountId": "usd", "date": "2026-09-01"},
        {"id": "euro", "accountId": "eur", "date": "2026-10-01"},
    ]
    store = SimpleNamespace(
        accounts=lambda _: accounts, categories=lambda _: categories,
        transactions=lambda *_: rows,
    )
    service = LedgerService(store)
    scoped = service.transaction_register("personal", "2026-10", "USD", "usd")
    assert scoped["transactions"] == [{"id": "first", "accountId": "usd", "date": "2026-10-01"}]
    assert [row["id"] for row in scoped["accounts"]] == ["usd", "second"]
    assert scoped["currencies"] == ["EUR", "USD"]
    assert [row["id"] for row in scoped["categoryGroups"][0]["children"]] == ["food"]
    assert len(service.transaction_register("personal", None, "USD")["transactions"]) == 3
    assert len(service.transaction_register("personal", None, "USD", "eur")["transactions"]) == 3
    for arguments in (("invalid", None, "USD"), ("personal", "bad", "USD"),
                      ("personal", None, "USD", []), ("personal", None, "bad")):
        with pytest.raises(StoreError):
            service.transaction_register(*arguments)


def test_register_projection_fast_path_ignores_private_and_out_of_scope_rows():
    calls = []
    data = {
        "accounts": [{"id": "usd", "currency": "USD"}], "categories": [],
        "transactions": [
            {"id": "ok", "accountId": "usd", "date": "2026-10-05", "cursor": "private"},
            {"id": "wrong-account", "accountId": "unknown", "date": "2026-10-05"},
            {"id": "wrong-month", "accountId": "usd", "date": "2026-09-05"},
        ],
    }
    service = LedgerService(SimpleNamespace(transaction_register=lambda *args: calls.append(args) or data))
    result = service.transaction_register("personal", "2026-10", "USD", "usd")
    assert calls == [("personal", "2026-10", "USD", "usd")]
    assert result["transactions"] == [{"id": "ok", "accountId": "usd", "date": "2026-10-05"}]


def test_register_filters_multiple_accounts_and_inclusive_dates_on_legacy_backend():
    accounts = [{"id": identifier, "currency": currency} for identifier, currency in (
        ("one", "USD"), ("two", "USD"), ("three", "USD"), ("euro", "EUR"),
    )]
    rows = [{"id": f"{account}-{date}", "accountId": account, "date": date} for account in (
        "one", "two", "three", "euro",
    ) for date in ("2026-09-30", "2026-10-01", "2026-10-05", "2026-10-06")]
    store = SimpleNamespace(accounts=lambda _: accounts, categories=lambda _: [], transactions=lambda *_: rows)
    service = LedgerService(store)
    result = service.transaction_register("personal", None, "USD", account_ids=["one", "two", "one"],
                                          start_date="2026-10-01", end_date="2026-10-05")
    assert {row["id"] for row in result["transactions"]} == {
        "one-2026-10-01", "one-2026-10-05", "two-2026-10-01", "two-2026-10-05",
    }
    assert service.transaction_register("personal", None, "USD", account_ids=[])["transactions"] == []
    for changes in ({"account_ids": "one"}, {"account_ids": [None]}, {"account_ids": ["one"] * 501},
                    {"start_date": "2026-02-30"}, {"end_date": "bad"},
                    {"start_date": "2026-10-02", "end_date": "2026-10-01"}):
        with pytest.raises(StoreError):
            service.transaction_register("personal", None, "USD", **changes)


def test_register_filtered_capability_is_scoped_again_and_keeps_old_signature_compatible():
    calls = []
    data = {"accounts": [{"id": "one", "currency": "USD"}], "categories": [], "transactions": [
        {"id": "ok", "accountId": "one", "date": "2026-10-05", "token": "private"},
        {"id": "outside", "accountId": "one", "date": "2026-10-01"},
        {"id": "foreign", "accountId": "other", "date": "2026-10-05"},
    ]}
    service = LedgerService(SimpleNamespace(
        accounts=lambda _: data["accounts"],
        transaction_register_filtered=lambda *args: calls.append(args) or data,
    ))
    result = service.transaction_register("personal", None, "USD", "one",
                                          start_date="2026-10-05", end_date="2026-10-05")
    assert calls == [("personal", "2026-10-05", "2026-10-05", "USD", ("one",))]
    assert result["transactions"] == [{"id": "ok", "accountId": "one", "date": "2026-10-05"}]
    service.transaction_register("personal", None, "USD", start_date="2026-10-05")
    assert calls[-1][-1] is None
    service.transaction_register("personal", None, "USD", "missing", start_date="2026-10-05")
    assert calls[-1][-1] is None
    legacy_calls = []
    service = LedgerService(SimpleNamespace(transaction_register=lambda *args: legacy_calls.append(args) or data))
    service.transaction_register("personal", "2026-10", "USD", account_ids=["one"])
    assert legacy_calls == [("personal", "2026-10", "USD", None)]


def test_public_command_validation_is_detached_and_never_queries_or_mutates_storage():
    from whab.services.ledger import LedgerService

    class UnavailableStorage:
        def __getattr__(self, name):
            raise AssertionError("Command validation accessed storage")

    service = LedgerService(UnavailableStorage())
    revision = service.revision
    original = {"transactionIds": ["fictional-one", "fictional-two"], "reviewed": True}
    validated = service.validate_command("review_transactions", "personal", original)
    assert validated == original and validated is not original
    validated["transactionIds"].clear()
    assert original["transactionIds"] == ["fictional-one", "fictional-two"]
    assert service.revision == revision
