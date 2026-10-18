"""Portable readers accept exact older index sets while rejecting changed SQL."""

import sqlite3
from itertools import combinations

import pytest
from whab_plugin_sqlite import Store
from whab_plugin_sqlite.portable import read_files

from whab.plugins import StoreError

INDEXES = ("tx_register", "tx_register_account", "tx_account_history")
MISSING_INDEXES = [subset for count in range(4) for subset in combinations(INDEXES, count)]


@pytest.fixture
def portable_payload(store):
    account = store.add_account("personal", {"name": "Synthetic cash", "type": "depository", "balance": 10000})
    store.add_transaction("personal", {
        "accountId": account, "date": "2026-10-01", "merchant": "Synthetic purchase",
        "amount": 100, "categoryId": "groceries", "note": "Retained annotation",
    })
    return store.export_budget_files()


def alter(payload, statements):
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(payload["budget.sqlite"])
        for statement in statements:
            connection.execute(statement)
        connection.commit()
        return {"budget.sqlite": connection.serialize()}
    finally:
        connection.close()


@pytest.mark.parametrize("missing_indexes", MISSING_INDEXES)
@pytest.mark.parametrize("missing_tables", [(), ("future_funding", "rollover_modes", "transaction_receipts")])
def test_exact_historical_and_partial_index_schemas_restore_without_migrating_input(
    store, portable_payload, missing_indexes, missing_tables,
):
    files = alter(portable_payload, [
        *(f"DROP INDEX {name}" for name in missing_indexes),
        *(f"DROP TABLE {name}" for name in missing_tables),
    ])
    before = files["budget.sqlite"]
    expected = store.export_budget()
    assert read_files(files) == expected
    target = Store(":memory:", month="2026-10")
    try:
        target.restore_budget_files("personal", files)
        assert target.export_budget() == expected
        assert {row[0] for row in target.db.execute(
            "SELECT name FROM sqlite_schema WHERE type='index'",
        )} >= set(INDEXES)
    finally:
        target.close()
    assert files["budget.sqlite"] == before


@pytest.mark.parametrize("name,sql", [
    ("tx_register", "CREATE INDEX tx_register ON transactions(workspace,amount) WHERE deleted=0"),
    ("tx_register", "CREATE INDEX tx_register ON transactions(workspace,date DESC,id DESC) WHERE deleted=1"),
    ("tx_register_account", "CREATE INDEX tx_register_account ON transactions(account_id,workspace,date DESC,id DESC) WHERE deleted=0"),
    ("tx_account_history", "CREATE INDEX tx_account_history ON transactions(workspace,merchant)"),
])
@pytest.mark.parametrize("omit_others", [False, True])
def test_known_index_names_cannot_allow_modified_index_sql(portable_payload, name, sql, omit_others):
    omitted = INDEXES if omit_others else (name,)
    files = alter(portable_payload, [*(f"DROP INDEX {index}" for index in omitted), sql])
    with pytest.raises(StoreError, match="unsupported database schema"):
        read_files(files)


def test_omitting_additive_indexes_does_not_allow_other_schema_changes(portable_payload):
    for change in (
        "DROP INDEX tx_month", "CREATE INDEX untrusted ON transactions(amount)",
        "CREATE TRIGGER untrusted AFTER UPDATE ON transactions BEGIN DELETE FROM accounts; END",
    ):
        files = alter(portable_payload, [*(f"DROP INDEX {index}" for index in INDEXES), change])
        with pytest.raises(StoreError, match="unsupported database schema"):
            read_files(files)
