"""Fictional plugin namespaces and machine credential stores."""

import pytest

from whab.plugins import BudgetContext


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_CATEGORIZATION_CREDENTIALS_DIR", str(tmp_path / "credentials"))
    files = {}
    return BudgetContext("fictional", {}, {}, lambda _: None, files.get,
                         lambda name, raw: files.__setitem__(name, raw), lambda name: files.pop(name, None))



@pytest.fixture
def engine(context):
    from types import SimpleNamespace

    from whab_plugin_ai_credentials.private import Vault
    vault = Vault(context)
    vault.write("openai", {"api_key": "fictional-key"})
    return SimpleNamespace(vault=vault)
