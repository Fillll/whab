"""Readable device-local aliases preserve financial identities and old bookmarks."""

import json

import pytest

from whab.budgets.aliases import RouteAliases
from whab.domain.errors import StoreError
from whab.web.navigation import name_slug, page_url, valid_route_key


@pytest.mark.parametrize("name,expected", [
    ("Family Budget", "family-budget"), (" Crème brûlée / Noël ", "creme-brulee-noel"),
    ("💎 Chase Sapphire Reserve", "chase-sapphire-reserve"),
    ("Семейный бюджет", "semeynyy-byudzhet"), ("Счёт · Сбережения", "schyot-sberezheniya"),
    ("Личный счёт № 2", "lichnyy-schyot-2"), ("Straße Ærø", "strasse-aero"),
    ("Сче\u0308т", "schyot"),
    ("Їжа Ґроші", "yizha-groshi"), ("💰💎", ""), (None, ""),
    ("../../Home%2f", "home-2f"),
])
def test_names_make_lowercase_safe_transliterated_aliases(name, expected):
    assert name_slug(name) == expected
    assert not expected or valid_route_key(expected)


def test_aliases_are_scope_local_durable_and_keep_old_names_after_rename(tmp_path):
    path = tmp_path / "routes-sandbox.json"
    aliases = RouteAliases(path)
    records = [{"id": "second", "name": "Savings"}, {"id": "first", "name": "Sávings"}]
    assert aliases.update("accounts:budget:personal", records) == {"first": "savings", "second": "savings-2"}
    assert aliases.update("accounts:other:personal", records) == {"first": "savings", "second": "savings-2"}
    assert aliases.update("accounts:budget:demo", records) == {"first": "savings", "second": "savings-2"}
    assert aliases.update("accounts:budget:personal", [{"id": "first", "name": "Everyday"}]) == {"first": "everyday"}
    assert aliases.resolve("accounts:budget:personal", "savings") == "first"
    assert aliases.resolve("accounts:budget:personal", "everyday") == "first"
    loaded = RouteAliases(path)
    assert loaded.aliases("accounts:budget:personal", "first") == ["savings", "everyday"]
    assert loaded.resolve("accounts:budget:personal", "savings-2") == "second"
    assert loaded.resolve("accounts:missing:personal", "savings") is None
    assert loaded.update("accounts:budget:personal", [{"id": "third", "name": "Savings"}]) == {"third": "savings-3"}


def test_reserved_budget_paths_and_unnamed_fallbacks_cannot_capture_another_identity(tmp_path):
    aliases = RouteAliases(tmp_path / "routes.json")
    result = aliases.update("budgets", [
        {"id": "budget-1", "name": "Demo"}, {"id": "budget-2", "name": "Home"},
        {"id": "budget-3", "name": "my-budget"}, {"id": "default", "name": "My budget"},
    ], reserved={"demo": "demo", "home": "default"})
    assert result == {"budget-1": "demo-2", "budget-2": "home-2", "budget-3": "my-budget", "default": "my-budget-2"}
    opaque = "ebd4b657-e15c-4d25-8fea-cb78253c386a"
    assert aliases.update("accounts:a:personal", [{"id": opaque, "name": "💰"}]) == {opaque: opaque}
    assert aliases.update("budgets", [{"id": "different", "name": opaque}]) == {"different": opaque + "-2"}
    assert aliases.update("accounts:a:personal", [{"id": "Backup_ID"}]) == {"Backup_ID": "backup-id"}
    fallback = aliases.update("accounts:a:personal", [{"id": "💳", "name": "💰"}])["💳"]
    assert fallback.isdigit() and aliases.resolve("accounts:a:personal", fallback) == "💳"


@pytest.mark.parametrize("value", ["", ".", "..", "../home", "Home", "home/other", "home%2fother", "home\\other", "x" * 201, None])
def test_aliases_cannot_inject_path_segments(value):
    assert not valid_route_key(value)


def test_account_url_keeps_currency_month_and_legacy_case_sensitive_query_identity():
    assert page_url("family-budget", "transactions", "current-month", "EUR", account_slug="cash-wallet", all_dates="true") == "/budgets/family-budget/cash-wallet/transactions/current-month/eur?all_dates=true"
    assert page_url("family-budget", "budget", "2026-10", "USD", account_slug="unused") == "/budgets/family-budget/budget/2026-10/usd"


@pytest.mark.parametrize("data", [
    [], {}, {"version": True, "scopes": {}}, {"version": 2, "scopes": {}},
    {"version": 1, "scopes": []},
    {"version": 1, "scopes": {"budgets": {"a": {"current": "../escape", "aliases": ["../escape"]}}}},
    {"version": 1, "scopes": {"budgets": {"a": {"current": "one", "aliases": ["two"]}}}},
    {"version": 1, "scopes": {"budgets": {"a": {"current": "same", "aliases": ["same"]}, "b": {"current": "same", "aliases": ["same"]}}}},
])
def test_malformed_or_ambiguous_registry_is_not_silently_reset(tmp_path, data):
    path = tmp_path / "routes.json"
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    with pytest.raises(StoreError, match="unreadable"):
        RouteAliases(path)
    assert path.read_bytes() == raw


def test_failed_registry_write_preserves_previous_aliases(tmp_path, monkeypatch):
    aliases = RouteAliases(tmp_path / "routes.json")
    aliases.update("budgets", [{"id": "a", "name": "Original"}])
    def fail(*args):
        raise OSError("Synthetic failure")
    monkeypatch.setattr("whab.budgets.aliases.atomic_write", fail)
    with pytest.raises(StoreError, match="could not be saved"):
        aliases.update("budgets", [{"id": "a", "name": "Changed"}])
    assert aliases.resolve("budgets", "original") == "a"
    assert aliases.resolve("budgets", "changed") is None
    assert RouteAliases(aliases.path).aliases("budgets", "a") == ["original"]
