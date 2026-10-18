"""Older ledger snapshots remain readable without additive planning tables."""

import pytest

from whab.domain.budget import calculate_workspace


@pytest.mark.parametrize("missing_tables", [
    ("future_funding",), ("rollover_modes",), ("future_funding", "rollover_modes"),
])
def test_legacy_state_reads_keep_history_and_default_missing_planning_tables(store, missing_tables):
    account = store.add_account("personal", {"name": "Synthetic cash", "type": "depository", "balance": 10000})
    transaction = store.add_transaction("personal", {
        "accountId": account, "date": "2026-10-01", "merchant": "Synthetic grocer",
        "amount": 250, "categoryId": "groceries", "note": "Preserved legacy memo",
    })
    store.assign("personal", "groceries", "2026-10", 1000)
    before = store.state("personal")
    expected = calculate_workspace(before, "2026-10")
    for table in missing_tables:
        store.db.execute(f"DROP TABLE {table}")
    changes = store.db.total_changes
    actual = store.state("personal")
    assert actual == before
    assert actual["futureFunding"] == actual["rolloverModes"] == {}
    assert actual["transactions"][transaction]["note"] == "Preserved legacy memo"
    assert actual["accounts"][account]["balance"] == 9750
    assert store.workspace("personal", "2026-10") == expected
    assert store.db.total_changes == changes
    for table in missing_tables:
        assert store.db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table,)).fetchone() is None
