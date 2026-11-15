"""Sidebar order is scoped presentation over unchanged ledger accounts."""

import json
import re
from html import unescape
from html.parser import HTMLParser


class SidebarMarkup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.scopes = []
        self.groups = []
        self.rows = []
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if "data-sidebar-order" in attributes:
            self.scopes.append(attributes)
        if "data-account-order-group" in attributes:
            self.groups.append(attributes["data-account-order-group"])
        if "data-account-order-row" in attributes:
            self.rows.append(attributes["data-account-order-row"])


def test_sidebar_order_retains_hidden_and_closed_id_slots_without_exposing_rows(client, store):
    names = ['共同 🍏 <wallet> "primary"', "Fictional hidden", "Fictional archive", "Fictional deposit"]
    identifiers = [store.add_account("personal", {
        "name": name, "type": "depository", "balance": 12500,
        "onBudget": index != 3,
    }) for index, name in enumerate(names)]
    assert client.patch(f"/api/ui/preferences/accounts/{identifiers[1]}", json={
        "workspace": "personal", "visible": False,
    }).status_code == 200
    assert client.patch(f"/api/accounts/{identifiers[2]}", json={
        "workspace": "personal", "closed": True,
    }).status_code == 200
    before = store.accounts("personal"), store.transactions("personal")

    for headers in ({}, {"X-WHAB-Page-Fragment": "1"}, {"X-WHAB-Transactions-Fragment": "1"}):
        response = client.get("/budgets/home/transactions/2026-10", headers=headers)
        assert response.status_code == 200
        markup = SidebarMarkup(response.text)
        assert len(markup.scopes) == 1
        scope = markup.scopes[0]
        assert scope["data-budget-id"] == "default"
        assert scope["data-workspace"] == "personal"
        assert scope["data-environment"] == "sandbox"
        assert set(json.loads(scope["data-default-order"])) == set(identifiers)
        assert markup.rows == [identifiers[0], identifiers[3]]
        assert markup.groups == ["on-budget", "off-budget"]
        assert 'aria-label="Reorder accounts"' in response.text
        assert 'aria-label="Move 共同 🍏 &lt;wallet&gt; &#34;primary&#34; up"' in response.text
        assert "<wallet>" not in response.text
    assert (store.accounts("personal"), store.transactions("personal")) == before


def test_sidebar_order_uses_authoritative_budget_and_workspace_not_route_alias(client):
    identities = []
    for route in ("home", "demo"):
        response = client.get(f"/budgets/{route}/transactions/2026-10")
        assert response.status_code == 200
        context = json.loads(unescape(re.search(
            r'<script id="whab-context" type="application/json">(.*?)</script>', response.text,
        )[1]))
        scope = SidebarMarkup(response.text).scopes[0]
        assert scope["data-budget-id"] == context["budgetId"]
        assert scope["data-workspace"] == context["workspace"]
        identities.append((scope["data-budget-id"], scope["data-workspace"]))
    assert identities == [("default", "personal"), ("demo", "demo")]
