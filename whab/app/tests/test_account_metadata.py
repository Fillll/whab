"""Account settings preserve names, public domains and historical currency meaning."""

from threading import RLock

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
from whab.config import Settings
from whab.domain.accounts import bank_website, normalize_account
from whab.domain.errors import StoreError
from whab.plugins import BankLogos, Plugin, PluginRegistry
from whab.services.ledger import LedgerService


@pytest.mark.parametrize("value", [
    "http://bank.example.org", "https://user:password@bank.example.org", "https://bank.example.org:443",
    "https://bank.example.org/?account=secret", "https://bank.example.org/#secret", "https://localhost",
    "https://127.0.0.1", "https://192.168.1.1", "https://[::1]", "https://bank.local",
    "https://bank.internal", "https://bank.123", "https://bank_example.org", "https://-bank.example.org",
    "https://bank.example.org\n", 42, {}, "https://" + "a" * 300 + ".org",
])
def test_bank_website_only_keeps_explicit_public_https_domains(value):
    with pytest.raises(StoreError):
        bank_website(value)


def test_bank_website_normalizes_public_hostname_and_discards_paths():
    assert bank_website("https://BANK.example.org/accounts/login") == "https://bank.example.org"
    assert bank_website(None) is None and bank_website("") is None
    with pytest.raises(StoreError):
        normalize_account({"type": "depository", "nameOverridden": "yes"})


def test_account_settings_only_returns_one_public_account_and_blocks_historical_currency_edits(store, tmp_path):
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", ui="")
    with TestClient(create_app(store=store, settings=settings, plugins=PluginRegistry()), base_url="https://127.0.0.1:5173") as client:
        key = client.post("/api/accounts", json={"workspace": "personal", "name": "Cash", "type": "depository", "balance": 0}).json()["accountId"]
        endpoint = f"/api/accounts/{key}"
        assert client.get(endpoint + "/settings?workspace=personal").json()["currencyEditable"] is True
        assert client.patch(endpoint, json={"workspace": "personal", "name": "New name", "bankWebsite": "https://BANK.example.org/login", "currency": "CAD"}).status_code == 200
        settings_response = client.get(endpoint + "/settings?workspace=personal").json()
        assert settings_response["account"]["name"] == "New name"
        assert settings_response["account"]["bankWebsite"] == "https://bank.example.org"
        assert "transactions" not in settings_response and "items" not in settings_response["account"]
        store.db.execute("INSERT INTO transactions(id,workspace,account_id,date,merchant,amount,source,deleted) VALUES(?,?,?,?,?,?,?,?)", ("old", "personal", key, "2026-01-01", "Synthetic shop", 100, "manual", 1))
        assert client.get(endpoint + "/settings?workspace=personal").json()["currencyEditable"] is False
        assert client.patch(endpoint, json={"workspace": "personal", "currency": "USD"}).status_code == 400
        assert client.patch(endpoint, json={"workspace": "personal", "currency": "CAD"}).status_code == 200
        assert client.patch(endpoint, json={"workspace": "personal", "bankWebsite": "https://localhost"}).status_code == 400
        assert client.get("/api/accounts/missing/settings?workspace=personal").status_code == 400
        assert client.get(endpoint + "/settings?workspace=demo").status_code == 400
    service = LedgerService(store, lock=RLock())
    with pytest.raises(StoreError):
        service.update_account("personal", key, {"name": ""})


def test_manual_website_hook_gets_only_hostname_and_public_attribution():
    seen = []
    registry = PluginRegistry((Plugin("artwork", "Artwork", bank_logos=BankLogos(
        resolve=lambda institution: "curated",
        resolve_domain=lambda hostname: seen.append(hostname) or "manual-domain",
        fetch=lambda key: None,
        provider_name="Synthetic icons", provider_url="https://icons.example.org",
    )),))
    assert registry.bank_logo("Public Bank", website="https://bank.example.org") == ("artwork", "manual-domain")
    assert seen == ["bank.example.org"]
    assert registry.bank_logo("Public Bank", website="http://localhost") is None
    assert registry.bank_logo_provider()["provider_name"] == "Synthetic icons"
    assert PluginRegistry().bank_logo_provider() is None
    with pytest.raises(ValueError):
        BankLogos(resolve=lambda name: None, fetch=lambda key: None, resolve_domain=True)
    with pytest.raises(ValueError):
        BankLogos(resolve=lambda name: None, fetch=lambda key: None, provider_url="javascript:invalid")


def test_settings_on_backend_without_portable_history_keeps_currency_locked(store, monkeypatch):
    key = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 0})
    monkeypatch.setattr(store, "export_budget", None)
    result = LedgerService(store).account_settings("personal", key)
    assert result["account"]["name"] == "Cash" and result["currencyEditable"] is False
    assert set(result["account"]) <= {"id", "name", "institution", "type", "balance", "currency", "source", "stale", "balanceUnknown", "onBudget", "subtype"}
