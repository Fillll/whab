"""Bounded SQL reads preserve complete-register search and count semantics."""

import sqlite3

import pytest
from whab_plugin_sqlite.register import transaction_register_page

from whab.plugins import StoreError


@pytest.fixture
def register_accounts(store):
    accounts = [
        store.add_account("personal", {
            "name": name, "type": "depository", "balance": 0, "currency": currency,
        })
        for name, currency in (("One", "USD"), ("Two", "USD"), ("Euro", "EUR"))
    ]
    rows = []
    for index in range(325):
        merchant, note = f"Synthetic purchase {index}", f"Receipt {index}"
        if index == 0:
            merchant, note = "Straße Markt", "A STRASSE receipt"
        elif index == 1:
            merchant, note = "Fifty 50% store", "a_b literal"
        elif index == 2:
            merchant, note = "Fifty 500 store", "axb counterpart"
        elif index == 3:
            merchant, note = "JOIN", "OVER"
        pending = index % 7 == 0
        rows.append((
            "personal", f"synthetic-{index:04}", accounts[index % 3],
            "2026-09-30" if index < 10 else "2026-10-01" if index < 150 else "2026-10-02",
            merchant, note, 100, pending, index % 4 == 0 and not pending,
            index > 0 and index % 13 == 0, "manual",
        ))
    store.db.executemany(
        "INSERT INTO transactions(workspace,id,account_id,date,merchant,note,amount,"
        "pending,reviewed,deleted,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows,
    )
    return accounts


def read(store, *, workspace="personal", start=None, end=None, currency="USD", accounts=None,
         search="", review="all", page=1, page_size=100):
    return transaction_register_page(
        store, workspace, start, end, currency, accounts, search, review, page, page_size,
    )


@pytest.mark.parametrize("selection", [
    {}, {"page": 2}, {"page": 999}, {"page": 0}, {"page_size": 17},
    {"search": " STRASSE "}, {"search": "straße"}, {"search": "%"},
    {"search": "_"}, {"search": "join over"}, {"search": "not present"},
    {"search": "receipt", "review": "reviewed", "page": 2, "page_size": 7},
    {"review": "needs-review"}, {"review": "reviewed"},
    {"start": "2026-10-01", "end": "2026-10-01"},
    {"start": "2026-10-02"}, {"end": "2026-09-30"},
    {"start": "2026-10-03", "end": "2026-10-01"},
    {"currency": "EUR"}, {"accounts": ()}, {"accounts": ("missing",)},
])
def test_page_matches_existing_public_rows_and_python_search(store, register_accounts, selection):
    options = {"workspace": "personal", "start": None, "end": None, "currency": "USD",
               "accounts": None, "search": "", "review": "all", "page": 1, "page_size": 100,
               **selection}
    scoped = store.transaction_register_filtered(
        options["workspace"], options["start"], options["end"], options["currency"], options["accounts"],
    )
    query = options["search"].strip().casefold()
    matches = [row for row in scoped["transactions"]
               if (not query or query in f"{row['merchant']} {row['note']}".casefold())
               and (options["review"] != "needs-review" or not row["reviewed"])
               and (options["review"] != "reviewed" or row["reviewed"])]
    pages = max(1, (len(matches) + options["page_size"] - 1) // options["page_size"])
    page = max(1, min(options["page"], pages))
    offset = (page - 1) * options["page_size"]
    result = read(store, **options)
    assert result["transactions"] == matches[offset:offset + options["page_size"]]
    assert result["accounts"] == scoped["accounts"]
    assert result["categories"] == scoped["categories"]
    assert result["transactionPage"] == {
        "total": len(scoped["transactions"]), "count": len(matches), "page": page, "pages": pages,
        "start": offset + 1 if matches else 0, "end": min(offset + options["page_size"], len(matches)),
        "toReview": sum(not row["reviewed"] and not row["pending"] for row in scoped["transactions"]),
    }
    for row in result["transactions"]:
        assert all(type(row[key]) is bool for key in (
            "pending", "reviewed", "isTransfer", "inferredTransfer",
        ))


def test_page_limits_sql_materialization_and_retains_date_id_order(store, register_accounts, monkeypatch):
    expected = store.transaction_register_filtered("personal", None, None, "USD", None)["transactions"]
    assert len(expected) > 200
    monkeypatch.setattr(store, "state", lambda *_: pytest.fail("Page must not load full ledger state"))
    monkeypatch.setattr(store, "transaction_register_filtered", lambda *_: pytest.fail("Page must use bounded SQL"))
    materialized = []

    def record(cursor, values):
        row = sqlite3.Row(cursor, values)
        if "accountId" in row.keys():
            materialized.append(row["id"])
        return row

    store.db.row_factory = record
    try:
        result = read(store, page=2)
    finally:
        store.db.row_factory = sqlite3.Row
    assert result["transactions"] == expected[100:200]
    assert materialized == [row["id"] for row in expected[100:200]]
    assert [(row["date"], row["id"]) for row in result["transactions"]] == sorted(
        [(row["date"], row["id"]) for row in result["transactions"]], reverse=True,
    )


@pytest.mark.parametrize("selected", [(0,), (0, 1), (2,), (0, 2)])
def test_page_account_selection_never_widens_across_currency(store, register_accounts, selected):
    accounts = tuple(register_accounts[index] for index in selected)
    result = read(store, accounts=accounts, page_size=1000)
    expected = store.transaction_register_filtered("personal", None, None, "USD", accounts)
    assert result["transactions"] == expected["transactions"]
    assert all(row["accountId"] in accounts and row["currency"] == "USD" for row in result["transactions"])
    assert read(store, workspace="demo", accounts=accounts)["transactionPage"]["total"] == 0


def test_page_keeps_workspace_validation_and_missing_workspace_guard(store):
    for workspace in ("invalid", None, []):
        with pytest.raises(StoreError, match="Invalid workspace"):
            read(store, workspace=workspace)
    store.db.execute("DELETE FROM meta WHERE workspace='personal'")
    with pytest.raises(StoreError, match="Workspace not found"):
        read(store)
