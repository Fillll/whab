"""Scoped register reads skip envelope state and retain storage isolation."""

import pytest

from whab.domain.errors import StoreError


def test_register_reads_match_public_rows_without_loading_full_ledger(store, monkeypatch):
    usd = store.add_account("personal", {"name": "Dollar", "type": "depository", "balance": 0})
    other = store.add_account("personal", {"name": "Other", "type": "depository", "balance": 0})
    eur = store.add_account("personal", {
        "name": "Euro", "type": "depository", "balance": 0, "currency": "EUR",
    })
    for account, date in ((usd, "2026-09-30"), (usd, "2026-10-01"),
                          (other, "2026-10-05"), (eur, "2026-10-05")):
        store.add_transaction("personal", {
            "accountId": account, "date": date, "merchant": "Synthetic purchase",
            "amount": 123, "categoryId": None, "note": "Private memo",
        })
    deleted = store.add_transaction("personal", {
        "accountId": usd, "date": "2026-10-08", "merchant": "Deleted",
        "amount": 456, "categoryId": None,
    })
    store.db.execute("UPDATE transactions SET deleted=1 WHERE workspace=? AND id=?", ("personal", deleted))
    all_rows = store.transactions("personal")
    accounts, categories = store.accounts("personal"), store.categories("personal")
    monkeypatch.setattr(store, "state", lambda *_: pytest.fail("Register must not load full ledger state"))
    for currency, account, month in (
        ("USD", usd, "2026-10"), ("USD", usd, None), ("USD", None, None),
        ("EUR", eur, "2026-10"), ("USD", eur, None), ("USD", "unknown", None),
    ):
        result = store.transaction_register("personal", month, currency, account)
        expected_account = account if any(
            row["id"] == account and row["currency"] == currency for row in accounts
        ) else None
        assert result["transactions"] == [
            row for row in all_rows if row["currency"] == currency
            and (expected_account is None or row["accountId"] == expected_account)
            and (month is None or row["date"].startswith(month))
        ]
        assert result["accounts"] == accounts
        assert result["categories"] == categories
        assert all(type(row["reviewed"]) is bool for row in result["transactions"])
    assert store.transaction_register("demo", None, "USD", usd)["transactions"] != all_rows


@pytest.mark.parametrize("method", ["accounts", "categories", "transaction_register"])
def test_lightweight_metadata_reads_keep_workspace_guards(store, method):
    def read(workspace):
        arguments = (workspace, None, "USD", None) if method == "transaction_register" else (workspace,)
        return getattr(store, method)(*arguments)
    for workspace in ("invalid", None, []):
        with pytest.raises(StoreError, match="Invalid workspace"):
            read(workspace)
    store.db.execute("DELETE FROM meta WHERE workspace='personal'")
    with pytest.raises(StoreError, match="Workspace not found"):
        read("personal")


def test_filtered_register_limits_sql_rows_to_accounts_and_inclusive_dates(store, monkeypatch):
    accounts = [store.add_account("personal", {"name": name, "type": "depository", "balance": 0})
                for name in ("One", "Two", "Three")]
    euro = store.add_account("personal", {"name": "Euro", "type": "depository", "balance": 0, "currency": "EUR"})
    for account in [*accounts, euro]:
        for date in ("2026-09-30", "2026-10-01", "2026-10-03", "2026-10-04"):
            store.add_transaction("personal", {"accountId": account, "date": date, "merchant": date,
                                                "amount": 100, "categoryId": None})
    all_rows = store.transactions("personal")
    monkeypatch.setattr(store, "state", lambda *_: pytest.fail("Filtered register must not read full state"))
    for start, end, selected in (
        ("2026-10-01", "2026-10-03", tuple(accounts[:2])),
        (None, "2026-10-01", (accounts[0],)),
        ("2026-10-03", None, None), (None, None, ()),
        (None, None, (euro, "missing")),
    ):
        result = store.transaction_register_filtered("personal", start, end, "USD", selected)
        assert result["transactions"] == [row for row in all_rows if row["currency"] == "USD"
            and (selected is None or row["accountId"] in selected)
            and (start is None or row["date"] >= start) and (end is None or row["date"] <= end)]
    assert store.transaction_register_filtered("demo", None, None, "USD", tuple(accounts))["transactions"] == []
    with pytest.raises(StoreError, match="Invalid workspace"):
        store.transaction_register_filtered("invalid", None, None, "USD", None)
