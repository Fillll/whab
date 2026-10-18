"""Real page routes use bounded storage reads rather than full-ledger exports."""


def test_account_management_and_settings_need_no_envelope_or_portable_history(client, store, monkeypatch):
    account = store.add_account("personal", {"name": "Fast wallet", "type": "depository", "balance": 12345})
    store.add_transaction("personal", {"accountId": account, "date": "2026-10-05",
                                           "merchant": "Review me", "amount": 100, "categoryId": None})

    def full_history(*args, **kwargs):
        raise AssertionError("Account metadata must not load full history")

    for name in ("workspace", "state", "export_budget", "transactions"):
        monkeypatch.setattr(store, name, full_history)
    response = client.get("/budgets/home/accounts/2026-10/usd")
    assert response.status_code == 200
    assert "Fast wallet" in response.text and "$122.45" in response.text
    assert '<span class="nav-count">1</span>' in response.text
    result = client.get(f"/api/accounts/{account}/settings?workspace=personal")
    assert result.status_code == 200
    assert result.json()["currencyEditable"] is False


def test_register_search_counts_are_complete_without_reading_other_pages(client, store, monkeypatch):
    account = store.add_account("personal", {"name": "Fast wallet", "type": "depository", "balance": 0})
    for index in range(3):
        store.add_transaction("personal", {"accountId": account, "date": "2026-10-05",
            "merchant": f"Example {index}", "amount": 100, "categoryId": None})

    def full_register(*args, **kwargs):
        raise AssertionError("Paged registers must not request all transactions")

    for name in ("transaction_register", "transaction_register_filtered", "transactions", "workspace", "state"):
        monkeypatch.setattr(store, name, full_register)
    response = client.get("/budgets/home/transactions/2026-10/usd?q=Example+2&tx_page=999")
    assert response.status_code == 200
    assert response.text.count('class="transaction-row"') == 1
    assert "1 matching transaction of 3" in response.text
    assert '<span class="nav-count">3</span>' in response.text
