"""Slim projection inputs preserve complete-ledger rules and coherent reads."""

import random
import sqlite3
from copy import deepcopy

import pytest
from whab_plugin_sqlite import Store
from whab_plugin_sqlite.projection_reads import snapshot

from whab.domain.budget import calculate_workspace, calculate_workspace_periods
from whab.domain.dates import shift_month
from whab.domain.money import MAX_SAFE_INTEGER


def ledger(seed):
    rng = random.Random(seed)
    state = {
        "_id": "personal", "budgetStartMonth": "2026-03", "accounts": {}, "categories": {},
        "transactions": {}, "budget": {}, "futureFunding": {}, "rolloverModes": {}, "items": {},
    }
    for currency in ("USD", "EUR", "JPY"):
        for index, kind in enumerate(("depository", "credit", "investment", "depository")):
            key = f"{currency}-a{index}"
            state["accounts"][key] = {
                "id": key, "name": key, "institution": "Synthetic Bank", "type": kind,
                "balance": rng.randrange(-10000, 100000), "currency": currency, "source": "manual",
                "stale": rng.random() < .1, "balanceUnknown": rng.random() < .2,
                "onBudget": index != 2 and rng.random() > .15,
            }
        for index in range(3):
            key = f"{currency}-c{index}"
            state["categories"][key] = {
                "id": key, "name": key, "group": f"Group {index % 2}",
                "target": rng.randrange(20000), "currency": currency,
            }
        for offset in range(14):
            month = shift_month("2026-03", offset)
            state["budget"].setdefault(month, {}).update(
                {f"{currency}-c{index}": rng.randrange(20000) for index in range(3)}
            )
            if offset and rng.random() < .4:
                state["futureFunding"].setdefault(month, {})[currency] = rng.randrange(10000)
            if rng.random() < .4:
                state["rolloverModes"].setdefault(month, {})[currency] = rng.choice(("category", "balance"))
    for index in range(140):
        currency = rng.choice(("USD", "EUR", "JPY"))
        key = f"t{index:04}"
        state["transactions"][key] = {
            "id": key, "accountId": f"{currency}-a{rng.randrange(4)}",
            "date": f'{shift_month("2025-12", rng.randrange(20))}-{rng.randrange(1, 29):02d}',
            "merchant": f"Synthetic merchant {index % 7}", "merchantLogo": "synthetic-logo",
            "amount": rng.randrange(-5000, 10000), "note": "Synthetic café\n" + "x" * (900 if index % 9 else 10),
            "categoryId": None if rng.random() < .3 else f"{currency}-c{rng.randrange(3)}",
            "pending": rng.random() < .15, "reviewed": rng.random() < .4, "source": "manual",
            "isTransfer": rng.random() < .2, "inferredTransfer": rng.random() < .1,
            "categoryEdited": rng.random() < .3, "deleted": rng.random() < .1,
        }
    return state


def install(store, state):
    with store._snapshot():
        store.db.execute("DELETE FROM categories WHERE workspace='personal'")
        store._write_state(state)


def outcome(operation):
    try:
        return "ok", operation()
    except Exception as error:
        return "error", type(error), str(error)


def test_only_requested_months_materialize_display_fields_and_keep_metadata_order(store):
    install(store, ledger(1))
    complete = store.state("personal")
    statements = []
    store.db.set_trace_callback(statements.append)
    slim = snapshot(store, "personal", "2026-10", 3)
    store.db.set_trace_callback(None)
    assert "items" not in slim
    for field in ("accounts", "categories", "budget", "futureFunding", "rolloverModes"):
        assert slim[field] == complete[field]
    assert list(slim["transactions"]) == list(complete["transactions"])
    history = {"id", "accountId", "date", "amount", "categoryId", "pending", "isTransfer", "deleted"}
    display = {"merchant", "merchantLogo", "reviewed", "source", "note", "inferredTransfer"}
    for key, row in slim["transactions"].items():
        fields = history | display if "2026-10" <= row["date"][:7] <= "2026-12" else history
        assert set(row) == fields
        assert row == {field: complete["transactions"][key][field] for field in fields}
    transaction_selects = [sql for sql in statements if "FROM transactions" in sql]
    assert len(transaction_selects) == 2
    assert all(field not in transaction_selects[0] for field in ("merchant", "note", "reviewed"))
    assert "date>='2026-10' AND date<'2027-01'" in transaction_selects[1]


@pytest.mark.parametrize("seed", range(24))
def test_randomized_single_and_batch_projections_equal_complete_state(store, seed):
    state = ledger(seed)
    if seed % 11 == 0:
        state["transactions"]["t0000"].update(accountId="missing", deleted=True)
    elif seed % 13 == 0:
        state["transactions"]["t0000"].update(accountId="USD-a0", categoryId="EUR-c0")
    elif seed % 17 == 0:
        state["accounts"]["USD-a0"]["balance"] = MAX_SAFE_INTEGER
        state["accounts"]["USD-a3"]["balance"] = MAX_SAFE_INTEGER
    install(store, state)
    complete = store.state("personal")
    original = deepcopy(complete)
    for month in ("2026-01", "2026-03", "2026-08", "2027-04"):
        expected = outcome(lambda: calculate_workspace(complete, month, as_of_month="2026-10"))
        assert outcome(lambda: store.workspace("personal", month)) == expected
        for count in (1, 3):
            expected = outcome(lambda: calculate_workspace_periods(complete, month, count, as_of_month="2026-10"))
            assert outcome(lambda: store.workspace_periods("personal", month, count)) == expected
    assert store.state("personal") == original


@pytest.mark.parametrize("suffix", ["-01", "-31", "", "malformed", "\x00tail"])
def test_selected_date_prefixes_match_core_even_with_unusual_suffixes(store, suffix):
    state = ledger(1)
    state["transactions"]["t0000"].update(date="2026-10" + suffix, deleted=False)
    state["transactions"]["t0001"].update(date="2026-10" + suffix, deleted=False)
    install(store, state)
    expected = calculate_workspace_periods(store.state("personal"), "2026-10", 1)
    actual = store.workspace_periods("personal", "2026-10", 1)
    assert actual == expected
    assert {row["id"] for row in actual[0]["transactions"]} >= {"t0000", "t0001"}


@pytest.mark.parametrize("month,count", [("2026-01", 12), ("2299-12", 1)])
def test_supported_period_boundaries_keep_complete_outputs(store, month, count):
    expected = calculate_workspace_periods(store.state("demo"), month, count, as_of_month="2026-10")
    assert store.workspace_periods("demo", month, count) == expected


@pytest.mark.parametrize("count", [None, True, False, 0, -1, 13, 1.5, "3"])
def test_invalid_period_counts_preserve_core_errors(store, count):
    expected = outcome(lambda: calculate_workspace_periods(store.state("personal"), "2026-10", count))
    assert outcome(lambda: store.workspace_periods("personal", "2026-10", count)) == expected


@pytest.mark.parametrize("scenario", ["invalid-workspace", "missing", "account", "category", "reference", "valid"])
def test_invalid_request_errors_keep_legacy_precedence(store, scenario):
    workspace = "invalid" if scenario == "invalid-workspace" else "personal"
    state = ledger(1)
    if scenario == "reference":
        state["transactions"]["t0000"].update(accountId="missing", deleted=True, date="2019-01-01")
    install(store, state)
    if scenario == "missing":
        store.db.execute("DELETE FROM meta WHERE workspace='personal'")
    elif scenario == "account":
        store.db.execute("UPDATE accounts SET type='investment',on_budget=1 WHERE workspace='personal'")
    elif scenario == "category":
        store.db.execute("UPDATE categories SET currency='$' WHERE workspace='personal'")
    for month, count in (("2026-13", 0), ("2026-10", 0), ("2299-12", 2), ("2026-10", 1)):
        def legacy():
            return calculate_workspace_periods(store.state(workspace), month, count, as_of_month="2026-10")
        assert outcome(lambda: store.workspace_periods(workspace, month, count)) == outcome(legacy)
    for month in ("2026-13", "2026-10"):
        expected = outcome(lambda: calculate_workspace(store.state(workspace), month, as_of_month="2026-10"))
        assert outcome(lambda: store.workspace(workspace, month)) == expected


@pytest.mark.parametrize("workspace", [None, {}, "invalid"])
def test_invalid_workspace_is_rejected_before_request_fields(store, workspace):
    expected = outcome(lambda: calculate_workspace(store.state(workspace), "invalid"))
    assert outcome(lambda: store.workspace(workspace, "invalid")) == expected


@pytest.mark.parametrize("month", [None, "", False, 0, [], {}])
def test_single_projection_keeps_legacy_falsy_month_alias(store, monkeypatch, month):
    monkeypatch.setattr("whab_plugin_sqlite.store.current_month", lambda: "2026-10")
    assert store.workspace("demo", month) == calculate_workspace(store.state("demo"), "2026-10", as_of_month="2026-10")


def test_as_of_month_errors_still_follow_requested_month_and_count_errors(store, monkeypatch):
    monkeypatch.setattr("whab_plugin_sqlite.store.current_month", lambda: "invalid-clock")
    monkeypatch.setattr("whab_plugin_sqlite.projections.current_month", lambda: "invalid-clock")
    for month in ("2026-10", "invalid-month"):
        expected = outcome(lambda: calculate_workspace(store.state("demo"), month, as_of_month="invalid-clock"))
        assert outcome(lambda: store.workspace("demo", month)) == expected
        for count in (0, 1):
            expected = outcome(lambda: calculate_workspace_periods(store.state("demo"), month, count, as_of_month="invalid-clock"))
            assert outcome(lambda: store.workspace_periods("demo", month, count)) == expected


@pytest.mark.parametrize("tables", [("future_funding",), ("rollover_modes",), ("future_funding", "rollover_modes")])
def test_legacy_optional_planning_tables_remain_optional(store, tables):
    for table in tables:
        store.db.execute(f"DROP TABLE {table}")
    expected = calculate_workspace_periods(store.state("demo"), "2026-10", 3)
    assert store.workspace_periods("demo", "2026-10", 3) == expected


def test_projection_never_reads_items_or_writes_and_preserves_private_export_and_mutation_paths(store):
    state = ledger(1)
    state["accounts"]["USD-a0"].update(itemId="synthetic-item", mask="0000")
    state["items"] = {"synthetic-item": {
        "id": "synthetic-item", "token": "fictional-token-only", "institutionName": "Synthetic Bank",
        "status": "connected", "provider": "plaid",
    }}
    install(store, state)
    complete = store.state("personal")
    exported = store.export_budget()
    expected = calculate_workspace_periods(complete, "2026-10", 3)
    changes = store.db.total_changes

    def authorize(action, table, column, database, source):
        if action == sqlite3.SQLITE_READ and table == "items":
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    store.db.set_authorizer(authorize)
    try:
        assert store.workspace_periods("personal", "2026-10", 3) == expected
        assert store.workspace("personal", "2026-10") == expected[0]
        assert store.db.total_changes == changes
    finally:
        store.db.set_authorizer(None)
    assert store.state("personal") == complete
    assert store.export_budget() == exported
    store.edit_transaction("personal", "t0001", note="Synthetic changed note")
    assert store.state("personal")["transactions"]["t0001"]["note"] == "Synthetic changed note"
    assert next(row for row in store.export_budget()["transactions"] if row["id"] == "t0001")["note"] == "Synthetic changed note"


def test_both_transaction_reads_share_one_snapshot_during_another_connection_commit(tmp_path):
    store = Store(tmp_path / "snapshot.sqlite", month="2026-10")
    state = ledger(1)
    state["transactions"]["t0000"].update(date="2026-10-01", deleted=False)
    install(store, state)
    store.db.execute("PRAGMA journal_mode=WAL")
    before = store.workspace_periods("personal", "2026-10", 3)
    connection = store.db
    other = sqlite3.connect(store.database_path, isolation_level=None)
    changed = []

    class ConcurrentCommit:
        def __getattr__(self, name):
            return getattr(connection, name)

        def execute(self, sql, *args):
            if sql.startswith("SELECT id,merchant,merchant_logo") and not changed:
                other.execute("BEGIN IMMEDIATE")
                other.execute("UPDATE transactions SET note=?,amount=amount+1000 WHERE workspace='personal' AND id='t0000'", ("Synthetic concurrent note",))
                other.execute("UPDATE accounts SET balance=balance+7000 WHERE workspace='personal' AND id='USD-a0'")
                other.execute("COMMIT")
                changed.append(True)
            return connection.execute(sql, *args)

    store.db = ConcurrentCommit()
    try:
        assert store.workspace_periods("personal", "2026-10", 3) == before
        assert changed == [True]
        after = calculate_workspace_periods(store.state("personal"), "2026-10", 3)
        assert after != before
        assert store.workspace_periods("personal", "2026-10", 3) == after
    finally:
        store.db = connection
        other.close()
        store.close()
