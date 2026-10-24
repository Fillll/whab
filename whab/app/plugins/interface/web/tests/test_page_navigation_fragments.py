"""Content navigation carries fresh scoped markup without replacing the app shell."""

import json
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest
from whab_plugin_web.views import WEB_ASSET_VERSION

from whab.plugins import FileImport, Plugin

FRAGMENT = {"X-WHAB-Page-Fragment": "1"}


class PageMarkup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.fragments = []
        self.transaction_context = None
        self.main_assets = None
        self.options = {"account": [], "category": [], "group": []}
        self.collection = self.option = self.group = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if "data-page-fragment" in attrs:
            self.fragments.append({
                "context": json.loads(attrs["data-page-context"]),
                "assets": json.loads(attrs["data-page-assets"]),
            })
        if "data-transactions-fragment" in attrs:
            self.transaction_context = json.loads(attrs["data-page-context"])
        if tag == "main":
            self.main_assets = json.loads(attrs["data-page-assets"])
        for name in self.options:
            if f"data-page-{name}-options" in attrs:
                self.collection = name
        if tag == "optgroup" and self.collection:
            self.group = attrs["label"]
        if tag == "option" and self.collection:
            self.option = {"value": attrs.get("value"), "text": "", "group": self.group}
            self.options[self.collection].append(self.option)

    def handle_data(self, data):
        if self.option is not None:
            self.option["text"] += data

    def handle_endtag(self, tag):
        if tag == "option":
            self.option = None
        if tag == "optgroup":
            self.group = None
        if tag in {"select", "datalist"}:
            self.collection = self.option = self.group = None


def full_context(response):
    assert response.status_code == 200, response.text
    return json.loads(re.search(r'<script id="whab-context" type="application/json">(.*?)</script>', response.text, re.S)[1])


def fragment(client, path, **headers):
    response = client.get(path, headers={**FRAGMENT, **headers})
    assert response.status_code == 200, response.text
    assert response.headers["X-WHAB-Page-Fragment"] == "1"
    assert response.headers["cache-control"] == "no-store"
    assert "X-WHAB-Page-Fragment" in response.headers["vary"]
    markup = PageMarkup(response.text)
    assert len(markup.fragments) == 1
    assert not [tag for tag, _ in markup.tags if tag in {"html", "head", "body", "main", "aside", "script"}]
    assert not [attrs for _, attrs in markup.tags if attrs.get("class") in {"sidebar", "topbar", "app-shell"}]
    assert not [attrs for tag, attrs in markup.tags if tag == "dialog" and "data-transaction-receipt-dialog" not in attrs]
    assert 'id="whab-context"' not in response.text and "<!DOCTYPE" not in response.text
    for name in ("settings-dialog", "transaction-dialog", "category-dialog", "account-dialog", "account-classification-dialog", "account-currency-choices"):
        assert f'id="{name}"' not in response.text
    assert response.text.count('<template data-account-list>') == 1
    assert response.text.count('<template data-page-options>') == 1
    return response, markup


@pytest.mark.parametrize("page", ["budget", "transactions", "insights", "accounts"])
@pytest.mark.parametrize("key,workspace", [("demo", "demo"), ("home", "personal")])
def test_every_view_fragment_matches_full_page_context_and_runtime_assets(client, page, key, workspace, monkeypatch):
    monkeypatch.setattr("whab.web.routes.pages.current_month", lambda: "2026-10")
    path = f"/budgets/{key}/{page}/current-month/usd"
    full = client.get(path)
    response, markup = fragment(client, path)
    parsed_full = PageMarkup(full.text)
    data = markup.fragments[0]
    expected = parsed_full.transaction_context if page == "transactions" else full_context(full)
    assert data["context"] == expected
    assert data["context"]["pageFragments"] is True
    assert data["context"]["workspace"] == workspace and data["context"]["page"] == page
    assert data["context"]["monthToken"] == "current-month" and data["context"]["month"] == "2026-10"
    assert data["context"]["currency"] == "USD"
    assert data["context"]["apiPrefix"] == f"/budgets/{key}/api"
    assert data["context"]["budgetId"] == ("demo" if key == "demo" else "default")
    assert data["assets"] == parsed_full.main_assets == {"web": WEB_ASSET_VERSION, "plugins": []}
    assert "<!DOCTYPE html>" in full.text and 'class="sidebar"' in full.text
    assert '<script ' in full.text and 'id="settings-dialog"' in full.text
    assert response.text.count("data-transaction-navigation-count") == 1


def test_transaction_fragment_keeps_exact_selection_dates_search_and_requested_page(client, store):
    first = store.add_account("personal", {"name": "First wallet", "type": "depository", "balance": 0})
    second = store.add_account("personal", {"name": "Second wallet", "type": "depository", "balance": 0})
    for identifier in (first, second):
        store.add_transaction("personal", {
            "accountId": identifier, "date": "2026-10-05", "merchant": "MiXeD & quoted",
            "amount": 100, "categoryId": None,
        })
    path = (f"/budgets/home/transactions/2026-10/usd?accounts={first}&accounts={second}"
            "&period=custom&start_date=2026-09-02&end_date=2026-10-29&review=needs-review&q=MiXeD+%26+quoted&tx_page=999")
    _, markup = fragment(client, path)
    context = markup.fragments[0]["context"]
    assert context["selectedAccountIds"] == [first, second] and context["selectedAccountId"] is None
    assert context["transactionPeriod"] == "custom" and context["startDate"] == "2026-09-02" and context["endDate"] == "2026-10-29"
    assert context["reviewFilter"] == "needs-review" and context["transactionSearch"] == "MiXeD & quoted"
    assert context["transactionRequestedPage"] == "999" and context["transactionPage"] == 1
    assert parse_qs(urlsplit(context["canonicalUrl"]).query) == parse_qs(urlsplit(path).query)
    assert urlsplit(context["canonicalUrl"]).path == "/budgets/my-budget/transactions/2026-10/usd"
    legacy = client.get(path, headers={"X-WHAB-Transactions-Fragment": "1"})
    assert PageMarkup(legacy.text).transaction_context == context
    assert client.get(context["canonicalUrl"], headers=FRAGMENT).status_code == 200


@pytest.mark.parametrize("page", ["budget", "transactions", "insights", "accounts"])
def test_fragment_options_are_fresh_currency_scoped_and_escaped(client, store, page):
    original = store.add_account("personal", {"name": "Original wallet", "type": "depository", "balance": 0})
    store.add_account("personal", {"name": "Other currency wallet", "type": "depository", "balance": 0, "currency": "EUR"})
    store.add_category("personal", {"name": "Other currency category", "group": "Other currency group", "target": 0, "currency": "EUR"})
    path = f"/budgets/home/{page}/2026-10/usd"
    _, before = fragment(client, path)
    assert {row["value"] for row in before.options["account"]} == {original}
    account_name = '<img src=x onerror="bad()"> & "wallet"'
    category_name = '<script>bad()</script> & "food"'
    group_name = '<svg onload="bad()"> & "group"'
    store.update_account("personal", original, {"name": account_name})
    added = store.add_account("personal", {"name": "New wallet", "type": "depository", "balance": 0})
    category = store.add_category("personal", {"name": category_name, "group": group_name, "target": 0})
    response, after = fragment(client, path)
    assert {(row["value"], row["text"]) for row in after.options["account"]} == {(original, account_name), (added, "New wallet")}
    assert {"value": category, "text": category_name, "group": group_name} in after.options["category"]
    assert group_name in {row["value"] for row in after.options["group"]}
    assert "Other currency" not in response.text and "Original wallet" not in response.text
    assert account_name not in response.text and category_name not in response.text and group_name not in response.text
    assert "&lt;img" in response.text and "&lt;script&gt;" in response.text and "&lt;svg" in response.text
    assert not [name for _, attrs in after.tags for name in attrs if name.startswith("on")]
    sidebar = re.search(r'<template data-account-list>(.*?)</template>', response.text, re.S)[1]
    assert f'data-account-id="{added}"' in sidebar and "New wallet" in sidebar and "&lt;img" in sidebar
    demo, demo_markup = fragment(client, f"/budgets/demo/{page}/2026-10/usd")
    assert added not in demo.text and category not in {row["value"] for row in demo_markup.options["category"]}


def test_fragment_assets_match_loaded_plugin_scripts_without_executing_or_replacing_them(store, tmp_path, client_for):
    templates, static = tmp_path / "import-templates", tmp_path / "import-static"
    templates.mkdir()
    static.mkdir()
    (templates / "accounts.html").write_text('<div data-fictional-import>Fictional import panel</div>')
    (static / "import.js").write_text("/* Fictional plugin module. */")
    plugin = Plugin("fictional-import", "Fictional import", file_import=FileImport(lambda raw: None, templates, static))
    with client_for(store, tmp_path, plugin) as client:
        full = client.get("/budgets/home/accounts/2026-10/usd")
        _, parsed = fragment(client, "/budgets/home/accounts/2026-10/usd")
        assets = parsed.fragments[0]["assets"]
        assert assets == PageMarkup(full.text).main_assets
        assert len(assets["plugins"]) == 1
        assert re.fullmatch(r"/budgets/home/imports/fictional-import/[0-9a-f]{32}/static/import\.js", assets["plugins"][0])
        assert client.get(assets["plugins"][0]).text == "/* Fictional plugin module. */"
        assert any(tag == "script" and attrs.get("src") == assets["plugins"][0] for tag, attrs in PageMarkup(full.text).tags)
        assert "data-fictional-import" in full.text
        assert not [tag for tag, _ in parsed.tags if tag == "script"]


@pytest.mark.parametrize("page", ["budget", "transactions", "insights", "accounts"])
def test_generic_marker_precedes_old_fragment_markers_and_full_pages_stay_default(client, page):
    path = f"/budgets/demo/{page}/2026-10/usd"
    _, generic = fragment(client, path, **{
        "X-WHAB-Budget-Fragment": "1", "X-WHAB-Transactions-Fragment": "summary", "X-WHAB-Transactions-Sidebar": "omit",
    })
    assert generic.fragments[0]["context"]["page"] == page
    for headers in ({}, {"X-WHAB-Page-Fragment": "true"}):
        full = client.get(path, headers=headers)
        assert full.status_code == 200 and "<!DOCTYPE html>" in full.text
        assert not PageMarkup(full.text).fragments
        assert "X-WHAB-Page-Fragment" not in full.headers
    if page == "budget":
        old = client.get(path, headers={"X-WHAB-Budget-Fragment": "1"})
        assert 'class="budget-planner"' in old.text and not PageMarkup(old.text).fragments
    if page == "transactions":
        for marker in ("1", "summary"):
            old = client.get(path, headers={"X-WHAB-Transactions-Fragment": marker})
            assert old.headers["X-WHAB-Transactions-Fragment"] == marker
            assert not PageMarkup(old.text).fragments
