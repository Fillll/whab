"""Fictional plugin namespaces and machine credential stores."""

from types import SimpleNamespace

import pytest
from whab_plugin_llm_categorization.engine import Engine

from whab.plugins import BudgetContext


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_CATEGORIZATION_CREDENTIALS_DIR", str(tmp_path / "credentials"))
    files = {}
    return BudgetContext("fictional", {}, {}, lambda _: None, files.get,
                         lambda name, raw: files.__setitem__(name, raw), lambda name: files.pop(name, None))


@pytest.fixture
def engine(context):
    result = Engine(context)
    result.vault.write("openai", {"api_key": "fictional-key"})
    return result


@pytest.fixture
def config():
    return {"provider": "openai", "model": "test-text-model", "threshold": .8, "examples": 20,
            "automatic": False, "consent": True,
            "categories": [{"id": "food", "name": "Food", "currency": "USD"},
                           {"id": "euro", "name": "Food", "currency": "EUR"}]}


@pytest.fixture
def row():
    return {"id": "private-transaction-id", "merchant": "Fictional cafe", "amount": 1234,
            "currency": "USD", "date": "2026-10-01", "categoryId": None, "reviewed": False,
            "accountId": "private-account-id", "note": "private note", "balance": 1000}


@pytest.fixture
def fake_attempt():
    return SimpleNamespace(status="connected", error=None, url="https://auth.openai.com/authorize", close=lambda: None)
