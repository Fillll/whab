"""SQLite persists optional categorizer provenance in every public read and export."""

import sqlite3

from whab_plugin_sqlite.store import Store


def tagged(store):
    account = store.add_account(
        "personal", {"name": "Fictional checking", "type": "depository", "balance": 100000}
    )
    identifier = store.add_transaction(
        "personal",
        {
            "accountId": account,
            "date": "2026-10-01",
            "merchant": "Fictional shop",
            "amount": 1234,
            "categoryId": None,
        },
    )
    store.edit_transaction_details(
        "personal", identifier, {"categoryId": "groceries", "categoryAgent": "Codex"}
    )
    return identifier


def test_agent_labels_survive_optimized_reads_review_portable_and_reopen(tmp_path):
    path = tmp_path / "budget.sqlite"
    store = Store(path, month="2026-10")
    identifier = tagged(store)
    assert store.supports_category_agent() is True
    for records in [
        store.transactions("personal"),
        store.workspace("personal", "2026-10")["transactions"],
        store.transaction_register_filtered("personal", None, None, "USD", None)["transactions"],
        store.transaction_register_page("personal", None, None, "USD", None, "", "all", 1, 100)[
            "transactions"
        ],
        store.export_budget("personal")["transactions"],
    ]:
        assert records[0]["categoryAgent"] == "Codex"
    store.review_transactions("personal", (identifier,), True)
    store.edit_transaction_details("personal", identifier, {"note": "A reviewed note"})
    assert store.transactions("personal")[0]["categoryAgent"] == "Codex"
    files = store.export_budget_files("personal")
    store.close()
    reopened = Store(path, month="2026-10")
    assert reopened.transactions("personal")[0]["categoryAgent"] == "Codex"
    reopened.close()
    restored = Store(tmp_path / "restored.sqlite", month="2026-10")
    restored.restore_budget_files("personal", files)
    assert restored.transactions("personal")[0]["categoryAgent"] == "Codex"
    restored.edit_transaction("personal", identifier, category_id="dining")
    assert "categoryAgent" not in restored.transactions("personal")[0]
    restored.close()


def test_legacy_nullable_column_is_added_without_attributing_existing_history(tmp_path):
    path = tmp_path / "legacy.sqlite"
    store = Store(path, month="2026-10")
    tagged(store)
    store.close()
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE transactions DROP COLUMN category_agent")
    candidate = Store(path, month="2026-10", read_only=True)
    assert "categoryAgent" not in candidate.export_budget("personal")["transactions"][0]
    files = candidate.export_budget_files("personal")
    candidate.close()
    restored = Store(tmp_path / "legacy-copy.sqlite", month="2026-10")
    restored.restore_budget_files("personal", files)
    assert "categoryAgent" not in restored.transactions("personal")[0]
    restored.close()
    reopened = Store(path, month="2026-10")
    assert "categoryAgent" not in reopened.transactions("personal")[0]
    assert "category_agent" in {
        row["name"] for row in reopened.db.execute("PRAGMA table_info(transactions)")
    }
    reopened.close()
