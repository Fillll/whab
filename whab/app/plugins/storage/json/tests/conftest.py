"""Fictional records and temporary stores; no configuration or private runtimes."""

import pytest
from whab_plugin_json import Store

from whab.plugins import WorkspaceImport


@pytest.fixture
def json_store(tmp_path):
    backend = Store(tmp_path / "budget.json", month="2026-10")
    yield backend
    backend.close()


@pytest.fixture
def json_account():
    def record(**changes):
        return {
            "id": "bank", "itemId": "item", "name": "Fictional checking",
            "institution": "Fictional Bank", "type": "depository", "balance": 100000,
            "currency": "USD", "source": "plaid", "mask": "0000", **changes,
        }
    return record


@pytest.fixture
def json_transaction():
    def record(identifier="pending", **changes):
        return {
            "id": identifier, "accountId": "bank", "date": "2026-10-01",
            "merchant": "Fictional shop", "amount": 125, "suggestedCategoryId": "groceries",
            "pending": True, "source": "plaid", "inferredTransfer": False,
            "pendingTransactionId": None, **changes,
        }
    return record


@pytest.fixture
def json_changes():
    def record(added=(), modified=(), removed=()):
        return {"added": list(added), "modified": list(modified), "removed": list(removed)}
    return record


@pytest.fixture
def json_import():
    return WorkspaceImport(
        accounts=({"id": "import-account", "name": "Fictional savings", "institution": "Fictional Bank",
                   "type": "depository", "balance": 100000, "source": "fictional", "stale": False,
                   "balanceUnknown": False},),
        categories=({"id": "import-category", "name": "Fictional", "group": "Fictional", "target": 0},),
        transactions=({"id": "import-transaction", "accountId": "import-account", "date": "2026-10-01",
                       "merchant": "Fictional shop", "amount": 125, "categoryId": "import-category",
                       "pending": False, "reviewed": True, "source": "fictional", "note": "Fictional memo",
                       "isTransfer": False, "inferredTransfer": False, "categoryEdited": True, "deleted": False},),
        assignments=({"month": "2026-10", "categoryId": "import-category", "assigned": -25},),
        budget_start_month="2026-10",
    )
