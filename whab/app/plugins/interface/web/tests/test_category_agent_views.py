"""Visible category provenance survives review and is escaped as untrusted text."""

import re

import pytest


@pytest.mark.parametrize("headers", [{}, {"X-WHAB-Transactions-Fragment": "1"}])
def test_agent_badge_is_next_to_category_survives_review_and_clears_on_manual_edit(
    client, store, headers
):
    account = store.add_account(
        "personal", {"name": "Fictional checking", "type": "depository", "balance": 100000}
    )
    identifier = store.add_transaction(
        "personal",
        {
            "accountId": account,
            "date": "2026-10-01",
            "merchant": "Fictional shop",
            "amount": 1200,
            "categoryId": None,
        },
    )
    response = client.patch(
        f"/api/transactions/{identifier}",
        json={
            "workspace": "personal",
            "categoryId": "groceries",
            "categoryAgent": "Codex <b>Agent</b>",
        },
    )
    assert response.status_code == 200
    path = "/budgets/home/transactions/2026-10/usd?review=all"
    page = client.get(path, headers=headers).text
    row = re.search(
        r'<tr class="transaction-row" data-transaction-id="' + re.escape(identifier) + r'".*?</tr>',
        page,
        re.S,
    )[0]
    cell = re.search(r'<td class="transaction-category-cell">(.*?)</td>', row, re.S)[1]
    assert "data-category-agent" in cell and "Agent · Codex &lt;b&gt;Agent&lt;/b&gt;" in cell
    assert 'aria-label="Categorized by agent Codex &lt;b&gt;Agent&lt;/b&gt;"' in cell
    assert "<b>Agent</b>" not in cell
    assert (
        client.patch(
            f"/api/transactions/{identifier}", json={"workspace": "personal", "reviewed": True}
        ).status_code
        == 200
    )
    assert "data-category-agent" in client.get(path, headers=headers).text
    assert store.transactions("personal")[0]["source"] == "manual"
    assert (
        client.patch(
            f"/api/transactions/{identifier}",
            json={"workspace": "personal", "categoryId": "dining"},
        ).status_code
        == 200
    )
    assert "data-category-agent" not in client.get(path, headers=headers).text


def test_agent_label_requires_category_in_same_web_write(client, store):
    account = store.add_account(
        "personal", {"name": "Fictional checking", "type": "depository", "balance": 100000}
    )
    identifier = store.add_transaction(
        "personal",
        {
            "accountId": account,
            "date": "2026-10-01",
            "merchant": "Fictional shop",
            "amount": 1200,
            "categoryId": None,
        },
    )
    result = client.patch(
        f"/api/transactions/{identifier}",
        json={"workspace": "personal", "reviewed": True, "categoryAgent": "Codex"},
    )
    assert result.status_code == 400
    assert store.transactions("personal")[0]["reviewed"] is False
