"""Page-fragment negotiation retains canonical, scoped and replaceable UI transport."""

import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
from whab.config import Settings
from whab.plugins import Plugin, PluginRegistry, UserInterface

BASE = "https://127.0.0.1:5173"
FRAGMENT = {"X-WHAB-Page-Fragment": "1"}


def context(response):
    assert response.status_code == 200, response.text
    return json.loads(re.search(r'<script id="transport-context">(.*?)</script>', response.text, re.S)[1])


@pytest.fixture
def fragment_client(tmp_path, store):
    templates, static = tmp_path / "templates", tmp_path / "static"
    templates.mkdir()
    static.mkdir()
    # The UI owns its markup and header precedence; the host negotiates transport
    # without importing or assuming the bundled web plugin's template structure.
    (templates / "base.html").write_text("""
{% if request.headers.get('X-WHAB-Page-Fragment')=='1' %}<div data-custom-fragment="{{ page }}">
{% elif page=='budget' and request.headers.get('X-WHAB-Budget-Fragment')=='1' %}<div data-old-budget>
{% elif page=='transactions' and request.headers.get('X-WHAB-Transactions-Fragment') in ('1','summary') %}<div data-old-transactions>
{% else %}<!DOCTYPE html><html><body><div data-custom-document>
{% endif %}
<script id="transport-context">{{ client_context|tojson }}</script>
<script id="transport-data">{{ data|tojson }}</script>
</div>
""")

    def build(data, status, items, *, workspace, page, month, selected=None):
        assert items == []
        return {"client_context": {
            "workspace": workspace, "page": page, "month": month, "currency": data["currency"],
        }}

    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="alternate")
    registry = PluginRegistry((Plugin("alternate", "Alternate", ui=UserInterface(templates, static, build)),))
    with TestClient(create_app(store=store, settings=settings, plugins=registry), base_url=BASE) as client:
        yield client


@pytest.mark.parametrize("page", ["budget", "transactions", "insights", "accounts"])
@pytest.mark.parametrize("key,workspace", [("demo", "demo"), ("home", "personal")])
def test_generic_fragment_is_explicit_and_keeps_authoritative_context(fragment_client, page, key, workspace, monkeypatch):
    monkeypatch.setattr("whab.web.routes.pages.current_month", lambda: "2026-10")
    path = f"/budgets/{key}/{page}/current-month/eur"
    full = fragment_client.get(path)
    result = fragment_client.get(path, headers=FRAGMENT)
    assert context(result) == context(full)
    assert "<!DOCTYPE html>" in full.text and "<!DOCTYPE html>" not in result.text
    assert f'data-custom-fragment="{page}"' in result.text
    assert "X-WHAB-Page-Fragment" not in full.headers
    assert result.headers["X-WHAB-Page-Fragment"] == "1"
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["content-type"].startswith("text/html")
    data = context(result)
    assert data["page"] == page and data["workspace"] == workspace
    assert data["month"] == "2026-10" and data["monthToken"] == "current-month" and data["currency"] == "EUR"
    assert data["budgetId"] == ("demo" if key == "demo" else "default")
    assert data["apiPrefix"] == f"/budgets/{key}/api"
    assert data["budgetUrl"] == ("/budgets/demo" if key == "demo" else "/budgets/my-budget")
    assert key in data["budgetAliases"]
    assert data["canonicalUrl"] == f'{data["budgetUrl"]}/{page}/current-month/eur'
    expected_vary = {"x-whab-page-fragment"}
    if page == "budget":
        expected_vary.add("x-whab-budget-fragment")
    if page == "transactions":
        expected_vary.update(("x-whab-transactions-fragment", "x-whab-transactions-sidebar"))
    assert {value.strip().lower() for value in result.headers["vary"].split(",")} == expected_vary
    assert result.headers["vary"] == full.headers["vary"]


@pytest.mark.parametrize("page", ["budget", "transactions", "insights", "accounts"])
@pytest.mark.parametrize("marker", ["", "true", "summary", "0"])
def test_unknown_generic_marker_keeps_ordinary_direct_pages(fragment_client, page, marker):
    response = fragment_client.get(f"/budgets/demo/{page}/2026-10/usd", headers={"X-WHAB-Page-Fragment": marker})
    assert response.status_code == 200 and "<!DOCTYPE html>" in response.text
    assert "data-custom-fragment" not in response.text
    assert "X-WHAB-Page-Fragment" not in response.headers


@pytest.mark.parametrize("page", ["budget", "transactions", "insights", "accounts"])
def test_generic_marker_takes_precedence_over_legacy_markers(fragment_client, page):
    result = fragment_client.get(f"/budgets/demo/{page}/2026-10/usd", headers={
        **FRAGMENT, "X-WHAB-Budget-Fragment": "1", "X-WHAB-Transactions-Fragment": "summary",
        "X-WHAB-Transactions-Sidebar": "omit",
    })
    assert result.status_code == 200 and f'data-custom-fragment="{page}"' in result.text
    assert "data-old-" not in result.text
    assert result.headers["X-WHAB-Page-Fragment"] == "1"
    assert "X-WHAB-Transactions-Fragment" not in result.headers


@pytest.mark.parametrize("page,headers,marker", [
    ("budget", {"X-WHAB-Budget-Fragment": "1"}, None),
    ("transactions", {"X-WHAB-Transactions-Fragment": "1"}, "1"),
    ("transactions", {"X-WHAB-Transactions-Fragment": "summary"}, "summary"),
    ("transactions", {"X-WHAB-Transactions-Fragment": "1", "X-WHAB-Transactions-Sidebar": "omit"}, "1"),
    ("transactions", {"X-WHAB-Page-Fragment": "unknown", "X-WHAB-Transactions-Fragment": "1"}, "1"),
])
def test_existing_fragments_keep_their_response_contract(fragment_client, page, headers, marker):
    response = fragment_client.get(f"/budgets/demo/{page}/2026-10/usd", headers=headers)
    assert response.status_code == 200 and "<!DOCTYPE html>" not in response.text
    assert "X-WHAB-Page-Fragment" not in response.headers
    assert response.headers.get("X-WHAB-Transactions-Fragment") == marker
    assert response.headers["cache-control"] == "no-store"


def test_account_alias_and_exact_filters_keep_the_same_context_as_direct_pages(fragment_client, store):
    account = store.add_account("personal", {"name": "Daily wallet", "type": "depository", "balance": 0})
    other = store.add_account("personal", {"name": "Other wallet", "type": "depository", "balance": 0})
    query = "?period=custom&start_date=2026-09-01&end_date=2026-10-31&review=needs-review&q=Mixed+CASE&tx_page=2"
    legacy = f"/budgets/home/transactions/2026-10/usd{query}&account={account}"
    first = context(fragment_client.get(legacy, headers=FRAGMENT))
    canonical = first["canonicalUrl"]
    assert canonical.startswith("/budgets/my-budget/daily-wallet/transactions/2026-10/usd?")
    assert first["selectedAccountId"] == account and first["selectedAccountIds"] == [account]
    assert first["transactionPeriod"] == "custom" and first["startDate"] == "2026-09-01" and first["endDate"] == "2026-10-31"
    assert first["reviewFilter"] == "needs-review"
    assert parse_qs(urlsplit(canonical).query) == parse_qs(query[1:])
    assert context(fragment_client.get(canonical, headers=FRAGMENT)) == first
    assert context(fragment_client.get(canonical)) == first
    mismatch = fragment_client.get(canonical + f"&account={other}", headers=FRAGMENT)
    assert mismatch.status_code == 400 and "X-WHAB-Page-Fragment" not in mismatch.headers
    wrong_currency = fragment_client.get(canonical.replace("/usd?", "/eur?"), headers=FRAGMENT)
    assert wrong_currency.status_code == 404 and "X-WHAB-Page-Fragment" not in wrong_currency.headers


@pytest.mark.parametrize("url,headers,status", [
    ("http://127.0.0.1:5173", {"X-Forwarded-Proto": "https", "Forwarded": "proto=https;host=localhost"}, 400),
    ("https://untrusted.invalid", {}, 403),
    (BASE, {"Origin": "https://untrusted.invalid"}, 403),
    (BASE, {"Sec-Fetch-Site": "cross-site"}, 403),
])
def test_fragment_negotiation_cannot_bypass_transport_guards(fragment_client, url, headers, status):
    response = fragment_client.get(url + "/budgets/demo/accounts/2026-10/usd", headers={**FRAGMENT, **headers})
    assert response.status_code == status
    assert "X-WHAB-Page-Fragment" not in response.headers and "data-custom-fragment" not in response.text


@pytest.mark.parametrize("path,status", [
    ("/budgets/demo/accounts/2026-10/usd?workspace=personal", 400),
    ("/budgets/home/accounts/2026-10/usd?page=budget", 400),
    ("/budgets/demo/accounts/2026-99/usd", 404),
    ("/budgets/demo/transactions/2026-10/usd?accounts=foreign-id", 400),
    ("/budgets/demo/transactions/2026-10/usd?period=custom&start_date=2026-10-31&end_date=2026-10-01", 400),
])
def test_fragment_negotiation_keeps_routing_and_filter_validation(fragment_client, path, status):
    response = fragment_client.get(path, headers=FRAGMENT)
    assert response.status_code == status
    assert "X-WHAB-Page-Fragment" not in response.headers


@pytest.mark.parametrize("page", ["budget", "transactions", "insights", "accounts"])
def test_fragment_context_and_data_exclude_private_bank_state(fragment_client, store, page):
    store.save_item("fictional-item", "FICTIONAL_PRIVATE_TOKEN", "Fictional institution")
    store.db.execute("UPDATE items SET cursor='FICTIONAL_PRIVATE_CURSOR' WHERE id='fictional-item'")
    before = store.db.serialize()
    result = fragment_client.get(f"/budgets/home/{page}/2026-10/usd", headers=FRAGMENT)
    assert result.status_code == 200 and "FICTIONAL_PRIVATE" not in result.text
    data = json.loads(re.search(r'<script id="transport-data">(.*?)</script>', result.text, re.S)[1])
    assert not {"items", "token", "cursor", "encryptionKey"}.intersection(data)
    assert store.db.serialize() == before
