"""Fictional budget namespaces and providers; no live calls."""

import pytest
from whab_plugin_ai_credentials import api as ai

from whab.plugins import BudgetContext


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_CREDENTIALS_DIR", str(tmp_path / "credentials"))
    files = {}
    return BudgetContext(
        "fictional",
        {},
        {},
        lambda _: None,
        files.get,
        lambda name, raw: files.__setitem__(name, raw),
    )


@pytest.fixture
def config(context):
    vault = ai.Vault(context)
    vault.write("openai", {"api_key": "fictional-key"})
    return {
        "provider": "openai",
        "model": "fictional-model",
        "connectionId": vault.references()["openai"],
        "effort": "default",
        "consent": True,
    }


@pytest.fixture
def row():
    return {
        "id": "one",
        "accountId": "account",
        "merchant": "Fictional streaming",
        "amount": 1200,
        "date": "2026-10-15",
        "currency": "USD",
        "categoryId": "media",
        "reviewed": True,
        "note": "private note",
        "pending": False,
    }
