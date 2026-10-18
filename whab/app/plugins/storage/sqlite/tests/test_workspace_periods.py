"""Consecutive periods share one ledger read without caching financial state."""

from copy import deepcopy

import pytest
from whab_plugin_sqlite.projections import workspace_periods

from whab.domain.errors import StoreError


@pytest.mark.parametrize("workspace", ["demo", "personal"])
def test_workspace_periods_match_single_reads_using_one_snapshot(store, monkeypatch, workspace):
    expected = [store.workspace(workspace, month) for month in ("2026-10", "2026-11", "2026-12")]
    state = store.state(workspace)
    original = deepcopy(state)
    reads = []

    def snapshot(scope):
        reads.append(scope)
        return state

    monkeypatch.setattr(store, "state", snapshot)
    actual = workspace_periods(store, workspace, "2026-10", 3)
    assert reads == [workspace]
    assert actual == expected
    assert state == original
    actual[0]["categories"][0]["name"] = "Changed only in the first projection"
    assert actual[1]["categories"][0]["name"] != actual[0]["categories"][0]["name"]
    if actual[0]["accounts"]:
        actual[0]["accounts"][0]["name"] = "Changed account"
        assert actual[1]["accounts"][0]["name"] != "Changed account"
    assert state == original


def test_later_calls_observe_new_transactions_and_keep_workspace_isolation(store):
    first = workspace_periods(store, "personal", "2026-10", 3)
    account = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 10000})
    store.add_transaction("personal", {
        "accountId": account, "date": "2026-10-10", "merchant": "Synthetic purchase",
        "amount": 250, "categoryId": "groceries",
    })
    current = workspace_periods(store, "personal", "2026-10", 3)
    assert first[0]["transactions"] == []
    assert current[0]["summary"]["spent"] == 250
    groceries = next(row for row in current[1]["budget"] if row["categoryId"] == "groceries")
    assert groceries["carryover"] == -250
    assert {row["id"] for row in workspace_periods(store, "demo", "2026-10", 1)[0]["accounts"]} != {account}


def test_workspace_periods_preserve_missing_workspace_and_invalid_month_errors(store):
    with pytest.raises(StoreError, match="Invalid workspace"):
        workspace_periods(store, "invalid", "2026-10", 3)
    with pytest.raises(StoreError, match="Invalid month"):
        workspace_periods(store, "personal", "2026-13", 3)
