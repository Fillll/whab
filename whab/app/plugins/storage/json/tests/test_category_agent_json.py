"""JSON categorizer annotations survive durable reopen and portable restoration."""

from whab_plugin_json import Store


def test_agent_annotation_is_saved_preserved_and_explicitly_replaced(tmp_path):
    path = tmp_path / "budget.json"
    store = Store(path, month="2026-10")
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
    assert store.supports_category_agent() is True
    store.review_transactions("personal", (identifier,), True)
    store.edit_transaction_details("personal", identifier, {"note": "Human reviewed"})
    assert store.workspace("personal", "2026-10")["transactions"][0]["categoryAgent"] == "Codex"
    exported = store.export_budget_files("personal")
    store.close()
    reopened = Store(path, month="2026-10")
    assert reopened.transactions("personal")[0]["categoryAgent"] == "Codex"
    reopened.close()
    restored = Store(tmp_path / "restored.json", month="2026-10")
    restored.restore_budget_files("personal", exported)
    assert restored.transactions("personal")[0]["categoryAgent"] == "Codex"
    restored.edit_transaction("personal", identifier, category_id="dining")
    assert "categoryAgent" not in restored.transactions("personal")[0]
    restored.close()
