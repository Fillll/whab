"""Compact suggestion controls preserve provisional selection and full metadata."""

import re
from copy import deepcopy
from html import unescape

import pytest


@pytest.mark.parametrize("plugin_id,label,low", [
    ("llm-categorization", "LLM", False), ("random-forest-categorization", "ML", True),
])
def test_suggestion_shares_category_editor_with_short_label_and_accessible_metadata(client, store, monkeypatch, plugin_id, label, low):
    account = store.add_account("personal", {"name": "Fictional account", "type": "depository", "balance": 0})
    category = store.add_category("personal", {"name": "Fictional category", "group": "Spending", "target": 0})
    tx = store.add_transaction("personal", {"accountId": account, "merchant": "Fictional cafe", "date": "2026-10-01",
                                           "amount": 1200, "categoryId": None})
    before = deepcopy(store.export_budget("personal"))
    suggestion = {"id": "fictional-suggestion", "pluginId": plugin_id, "modelId": "fictional-model",
                  "createdAt": "2026-10-27T00:00:00Z", "categoryId": category, "score": .6 if low else .95, "belowCutoff": low}
    monkeypatch.setattr(client.app.state.ledger_service, "category_suggestions", lambda *args: {tx: suggestion})
    page = client.get("/budgets/home/transactions/2026-10/usd").text
    row = re.search(r'<tr class="transaction-row" data-transaction-id="' + re.escape(tx) + r'".*?</tr>', page, re.S)[0]
    cell = re.search(r'<td class="transaction-category-cell">(.*?)</td>', row, re.S)[1]
    assert '<div class="transaction-category-editor"><select' in cell
    assert f'<option value="{category}" selected>Fictional category</option>' in cell
    assert f'<span>{label}</span>' in cell
    assert f'aria-label="{label} suggested category' in cell
    title = unescape(re.search(r'class="ml-suggestion".*?title="([^"]+)"', cell, re.S)[1])
    assert "fictional-model" in title and "Applied only when reviewed." in title
    if low:
        assert "60% · Low score" in title and '<span aria-hidden="true">60%</span>' in cell
    assert 'data-dismiss-suggestion aria-label="Dismiss category suggestion for Fictional cafe"' in cell
    assert cell.endswith("</span></div>")
    assert store.export_budget("personal") == before
