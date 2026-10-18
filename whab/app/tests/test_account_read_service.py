"""Optional metadata projections retain public validation and legacy fallbacks."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from whab.domain.accounts import normalize_account
from whab.plugins import StoreError
from whab.services.ledger import LedgerService


def test_fast_overview_serializes_reads_sanitizes_metadata_and_keeps_currencies_separate():
    active = False

    @contextmanager
    def locked():
        nonlocal active
        assert active is False
        active = True
        try:
            yield
        finally:
            active = False

    class Lock:
        def __enter__(self):
            self.context = locked()
            return self.context.__enter__()

        def __exit__(self, *error):
            return self.context.__exit__(*error)

    accounts = [normalize_account({
        "id": key, "name": key, "type": kind, "balance": balance, "currency": currency,
        "token": "Synthetic private data",
    }) for key, kind, balance, currency in (
        ("cash", "depository", 1000, "USD"), ("credit", "credit", 300, "USD"),
        ("euro", "depository", 5000, "EUR"),
    )]
    categories = [{"id": "yen", "name": "Yen", "group": "Living", "target": 0, "currency": "JPY"}]
    calls = []

    def read(workspace, month, currency):
        assert active is True
        calls.append((workspace, month, currency))
        return {"accounts": accounts, "categories": categories, "toReview": 3 if currency == "USD" else 0}

    service = LedgerService(SimpleNamespace(account_overview=read), lock=Lock())
    result = service.account_overview("personal", "2026-10", "USD")
    assert calls == [("personal", "2026-10", "USD")]
    assert [row["id"] for row in result["accounts"]] == ["cash", "credit"]
    assert result["currencies"] == ["EUR", "JPY", "USD"]
    assert result["accountSummary"] == {"assets": 1000, "liabilities": 300, "netWorth": 700, "onBudget": 700, "offBudget": 0}
    assert result["toReview"] == 3 and result["summary"] == {"spent": 0}
    assert result["transactions"] == result["budget"] == result["periods"] == []
    assert result["categories"] == result["categoryGroups"] == []
    assert all("token" not in row for row in result["accounts"])
    yen = service.account_overview("personal", "2026-10", "JPY")
    assert yen["currencyMinorUnits"] == 0 and yen["accounts"] == []
    assert yen["categoryGroups"][0]["children"][0]["id"] == "yen"


def test_overview_legacy_workspace_fallback_preserves_review_counts_and_snapshot_totals(store, monkeypatch):
    usd = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 1000})
    eur = store.add_account("personal", {"name": "Euro", "type": "depository", "balance": 5000, "currency": "EUR"})
    for account in (usd, eur):
        store.add_transaction("personal", {"accountId": account, "date": "2026-10-05", "merchant": "Purchase", "amount": 100, "categoryId": None})
    monkeypatch.setattr(store, "account_overview", None, raising=False)
    service = LedgerService(store)
    result = service.account_overview("personal", "2026-10", "USD")
    assert [row["id"] for row in result["accounts"]] == [usd]
    assert result["toReview"] == 1 and result["accountSummary"]["netWorth"] == 900
    assert result["transactions"] == [] and result["currencies"] == ["EUR", "USD"]


@pytest.mark.parametrize("arguments", [("invalid", "2026-10", "USD"), ("personal", "2026-13", "USD"), ("personal", "2026-10", "ZZZ")])
def test_overview_validates_before_entering_storage(arguments):
    with pytest.raises(StoreError):
        LedgerService(SimpleNamespace()).account_overview(*arguments)


@pytest.mark.parametrize("count", [-1, True, "3"])
def test_overview_rejects_invalid_optional_review_counts(count):
    backend = SimpleNamespace(account_overview=lambda *_: {"accounts": [], "categories": [], "toReview": count})
    with pytest.raises(StoreError, match="invalid account review count"):
        LedgerService(backend).account_overview("personal", "2026-10", "USD")


def test_fast_settings_are_whitelisted_and_explicit_history_proof_controls_currency():
    record = {"id": "account", "name": "Cash", "currency": "USD", "bankWebsite": "https://bank.example.org", "nameOverridden": True,
              "token": "Synthetic private data", "itemId": "private", "extra": "unavailable"}
    backend = SimpleNamespace(account_settings=lambda *_: {"account": record, "currencyEditable": True})
    service = LedgerService(backend)
    result = service.account_settings("personal", "account")
    assert result == {"account": {key: record[key] for key in ("id", "name", "currency", "bankWebsite", "nameOverridden")}, "currencyEditable": True}
    for proof in (False, "true", None):
        backend.account_settings = lambda *_: {"account": record, "currencyEditable": proof}
        assert service.account_settings("personal", "account")["currencyEditable"] is False
    for account in (None, {"id": "other"}):
        backend.account_settings = lambda *_: {"account": account, "currencyEditable": True}
        with pytest.raises(StoreError, match="Account not found"):
            service.account_settings("personal", "account")
    with pytest.raises(StoreError, match="Invalid workspace"):
        service.account_settings("invalid", "account")
    with pytest.raises(StoreError, match="Invalid identifier"):
        service.account_settings("personal", "")


def test_settings_legacy_export_and_no_export_fallbacks_preserve_history_guards(store, monkeypatch):
    key = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 0})
    monkeypatch.setattr(store, "account_settings", None, raising=False)
    service = LedgerService(store)
    assert service.account_settings("personal", key)["currencyEditable"] is True
    store.add_transaction("personal", {"accountId": key, "date": "2026-09-01", "merchant": "History", "amount": 100, "categoryId": None})
    assert service.account_settings("personal", key)["currencyEditable"] is False
    monkeypatch.setattr(store, "export_budget", None)
    assert service.account_settings("personal", key)["currencyEditable"] is False
    with pytest.raises(StoreError, match="Account not found"):
        service.account_settings("personal", "missing")
