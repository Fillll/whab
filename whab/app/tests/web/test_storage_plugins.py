"""Storage selection and lifecycle through the application composition root."""

import asyncio
import gc
import inspect
import json
import subprocess
import sys
import warnings
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
from whab.config import Settings
from whab.domain.dates import current_month
from whab.plugins import UNSET, Plugin, PluginError, PluginRegistry, WebhookResult


def settings(home, storage="sqlite", plugins=()):
    return Settings(home, 5173, "sandbox", "", "", "", False, "", "", plugins, storage, "")


class MemoryBackend:
    """Small independent test backend implementing only the public ledger contract."""

    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.closed = False
        self.saved_accounts = {"personal": [], "demo": []}
        self.saved_items = {}

    def close(self):
        self.closed = True
        self.events.append("storage-closed")

    def accounts(self, workspace):
        assert not self.closed
        return deepcopy(self.saved_accounts[workspace])

    def categories(self, workspace):
        return [{"id": "fun", "name": "Fun", "group": "Life", "target": 0}]

    def transactions(self, workspace, month=None):
        return []

    def workspace(self, workspace, month=None):
        return {
            "workspace": workspace,
            "month": month or current_month(),
            "accounts": self.accounts(workspace),
            "categories": self.categories(workspace),
            "transactions": [],
            "budget": [],
            "summary": {"readyToAssign": 0, "assigned": 0, "spent": 0, "available": 0},
            "budgetStartMonth": current_month(),
        }

    def add_account(self, workspace, account):
        account_id = f"manual-{len(self.saved_accounts[workspace])}"
        self.saved_accounts[workspace].append(
            {**account, "id": account_id, "source": "manual", "institution": "Manual"}
        )
        return account_id

    def add_category(self, workspace, category):
        return "fun"

    def add_transaction(self, workspace, transaction, source="manual"):
        return "fixture-transaction"

    def edit_transaction(self, workspace, transaction_id, category_id=UNSET, reviewed=None):
        pass

    def assign(self, workspace, category_id, month, assigned):
        pass

    def items(self):
        return [
            {
                key: item.get(key)
                for key in ("id", "institutionName", "status", "lastSynced", "error")
            }
            for item in self.saved_items.values()
            if item["token"]
        ]

    def get_item(self, item_id):
        return deepcopy(self.saved_items.get(item_id))

    def save_item(self, item_id, encrypted_token, institution_name, *, provider_id="plaid"):
        self.saved_items[item_id] = {
            "id": item_id,
            "token": encrypted_token,
            "institutionName": institution_name,
            "provider": provider_id,
            "status": "connected",
            "lastSynced": None,
            "error": None,
            "cursor": None,
        }

    def set_item_status(self, item_id, status, error):
        self.saved_items[item_id].update(status=status, error=error)

    def disconnect_item(self, item_id):
        self.saved_items[item_id]["token"] = None

    def apply_sync(
        self,
        item_id,
        changes,
        cursor,
        accounts=None,
        historical_complete=True,
        *,
        expected_cursor=UNSET,
    ):
        if expected_cursor is not UNSET:
            assert self.saved_items[item_id]["cursor"] == expected_cursor
        self.saved_accounts["personal"].extend(deepcopy(accounts or []))
        self.saved_items[item_id]["cursor"] = cursor
        return {"added": len(changes["added"]), "modified": 0, "removed": 0}


class MemoryBank:
    config = {"configured": True, "missingConfig": []}

    def __init__(self, context, events, fail_close=False):
        self.store = context.store
        self.events = events
        self.fail_close = fail_close

    async def link_token(self, item_id=None):
        return "fixture-link-token"

    async def exchange(self, public_token, institution_name=None):
        self.store.save_item("shared", "fictional-ciphertext", "Fixture Bank")

    async def sync(self, item_id=None):
        self.store.apply_sync(
            "shared",
            {"added": [], "modified": [], "removed": []},
            "fixture-cursor",
            [
                {
                    "id": "shared-account",
                    "name": "Fictional checking",
                    "institution": "Fixture Bank",
                    "type": "depository",
                    "balance": 1234,
                    "currency": "USD",
                    "source": "fictional",
                    "itemId": "shared",
                }
            ],
            expected_cursor=None,
        )
        return {"added": 0, "modified": 0, "removed": 0}

    async def disconnect(self, item_id):
        self.store.disconnect_item(item_id)

    async def webhook(self, raw, headers):
        return WebhookResult(ok=False, status_code=401)

    async def close(self):
        assert self.store.get_item("shared") is not None
        self.events.append("bank-closed")
        if self.fail_close:
            raise RuntimeError("FIXTURE_CLEANUP_SECRET")


def test_alternate_storage_host_starts_without_importing_sqlite_driver(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import builtins
import json
import runpy
import sys
from pathlib import Path

# Check import attempts even when coverage's SQLite data writer loaded the driver
# before this script started. Keep the audit active through host shutdown.
sqlite_imports = []
original_import = builtins.__import__

def audit_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == 'sqlite3' or name.startswith('sqlite3.') or name == '_sqlite3':
        sqlite_imports.append(name)
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = audit_import
try:
    namespace = runpy.run_path(sys.argv[1])
    backend = namespace['MemoryBackend']()
    registry = namespace['PluginRegistry']((
        namespace['Plugin']('memory', 'Memory fixture', storage=lambda context: backend),
    ))
    app = namespace['create_app'](
        settings=namespace['settings'](Path(sys.argv[2]), 'memory'), plugins=registry,
    )
    with namespace['TestClient'](app, base_url='https://127.0.0.1:5173') as client:
        assert client.get('/api/workspace?workspace=personal').status_code == 200
    assert backend.closed
finally:
    builtins.__import__ = original_import

print(json.dumps({
    'backend': 'whab_plugin_sqlite.store' in sys.modules,
    'schema': 'whab_plugin_sqlite.schema' in sys.modules,
    'seeding': 'whab_plugin_sqlite.seeding' in sys.modules,
    'driver_imports': sqlite_imports,
}))
""",
            str(Path(__file__).resolve()),
            str(tmp_path),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == {
        "backend": False, "schema": False, "seeding": False, "driver_imports": [],
    }
    assert {path.name for path in tmp_path.iterdir()} == {"visual-ui", "plugin-workers"}
    assert {path.name for path in (tmp_path / "visual-ui").iterdir()} == {
        "visual-selection.json", "visual-selection.json.lock",
    }
    # An inactive host journal makes no native authority or live-worker claim.
    control = tmp_path / "plugin-workers" / "storage" / "memory"
    assert {path.relative_to(tmp_path / "plugin-workers").as_posix()
            for path in (tmp_path / "plugin-workers").rglob("*") if path.is_file()} == {
        "storage/memory/storage-upgrade.json", "storage/memory/storage-upgrade.json.lock",
    }
    assert json.loads((control / "storage-upgrade.json").read_bytes())["body"] == {
        "format": 2, "kind": "storage-code", "active": None, "native": None, "pending": None, "repair": None,
    }


@pytest.mark.parametrize("fail_bank_close", [False, True])
def test_alternate_backend_owns_lifecycle_routes_and_scoped_banks(
    tmp_path, monkeypatch, fail_bank_close
):
    events, contexts = [], []
    backend = MemoryBackend(events)
    backend.save_item("other-item", "other-ciphertext", "Other Bank")
    monkeypatch.setenv("MEMORY_FIXTURE_OPTION", "fixture")

    def storage_factory(context):
        contexts.append(context)
        events.append("storage-started")
        return backend

    def bank_factory(context):
        assert context.store.items() == []
        events.append("bank-started")
        return MemoryBank(context, events, fail_bank_close)

    registry = PluginRegistry(
        (
            Plugin("memory", "Memory fixture", storage=storage_factory),
            Plugin("fictional", "Fictional bank", bank=bank_factory),
        )
    )
    app = create_app(settings=settings(tmp_path, "memory"), plugins=registry)
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        assert app.state.store is backend
        assert app.state.storage_plugin == "memory"
        assert contexts[0].home == tmp_path
        assert contexts[0].environment == "sandbox"
        assert contexts[0].config["MEMORY_FIXTURE_OPTION"] == "fixture"
        assert "TOKEN_ENCRYPTION_KEY" not in contexts[0].config
        assert client.get("/api/workspace?workspace=personal").status_code == 200
        response = client.post(
            "/api/accounts",
            json={
                "workspace": "personal",
                "name": "Memory cash",
                "type": "depository",
                "balance": 1000,
            },
        )
        assert response.status_code == 200
        assert (
            client.post(
                "/api/banks/fictional/exchange", json={"publicToken": "fixture-public-token"}
            ).status_code
            == 200
        )
        assert client.post("/api/banks/fictional/sync", json={}).status_code == 200
        items = client.get("/api/banks/fictional/items").json()["items"]
        assert [item["id"] for item in items] == ["shared"]
        assert "token" not in items[0]
        accounts = client.get("/api/workspace?workspace=personal").json()["accounts"]
        imported = next(row for row in accounts if row["source"] == "fictional")
        assert imported["id"].startswith("provider:fictional:account:")
        assert imported["itemId"] == "provider:fictional:item:shared"
        assert backend.get_item("other-item")["cursor"] is None
        assert backend.get_item("provider:fictional:item:shared")["cursor"] == "fixture-cursor"
        assert client.get("/api/export?workspace=personal").status_code == 200
    assert events[:2] == ["storage-started", "bank-started"]
    assert events[-1] == "storage-closed"
    assert events[2:-1] and set(events[2:-1]) == {"bank-closed"}
    if fail_bank_close:
        # Failed retirement retains the original service for explicit cleanup;
        # repeated shutdown paths must not mistake its first failure for closure.
        assert app.state.budget_runtime.status()["cleanup_failed"] > 0
        pending, = app.state.bank_lifecycle.services
        pending.fail_close = False
        asyncio.run(app.state.budget_runtime.retry_cleanup())
        assert app.state.budget_runtime.status()["cleanup_failed"] == 0
        assert app.state.bank_lifecycle.services == []
    else:
        assert events == ["storage-started", "bank-started", "bank-closed", "storage-closed"]
    assert backend.closed is True
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("invalid", [None, object(), {"token": "FIXTURE_SECRET"}])
def test_invalid_storage_result_fails_safely_without_sqlite_fallback(tmp_path, invalid):
    registry = PluginRegistry(
        (Plugin("broken", "Broken storage", storage=lambda context: invalid),)
    )
    with pytest.raises(PluginError) as captured:
        create_app(settings=settings(tmp_path, "broken"), plugins=registry)
    assert captured.value.code == "storage_startup_failed"
    assert "FIXTURE_SECRET" not in str(captured.value)
    assert not (tmp_path / "data").exists()


def test_invalid_backend_is_closed_and_cleanup_errors_stay_private(tmp_path):
    events = []

    class IncompleteBackend:
        def close(self):
            events.append("closed")
            raise RuntimeError("FIXTURE_CLEANUP_SECRET")

    registry = PluginRegistry(
        (Plugin("broken", "Incomplete storage", storage=lambda context: IncompleteBackend()),)
    )
    with pytest.raises(PluginError) as captured:
        create_app(settings=settings(tmp_path, "broken"), plugins=registry)
    assert events == ["closed"]
    assert "FIXTURE_CLEANUP_SECRET" not in str(captured.value)
    assert not (tmp_path / "data").exists()


def test_async_storage_factory_is_rejected_and_coroutine_closed(tmp_path):
    produced = []

    async def async_factory(context):
        return MemoryBackend()

    def factory(context):
        result = async_factory(context)
        produced.append(result)
        return result

    registry = PluginRegistry((Plugin("broken", "Async storage", storage=factory),))
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always", RuntimeWarning)
        with pytest.raises(PluginError) as captured:
            create_app(settings=settings(tmp_path, "broken"), plugins=registry)
        assert inspect.getcoroutinestate(produced[0]) == inspect.CORO_CLOSED
        produced.clear()
        gc.collect()
    assert captured.value.code == "storage_startup_failed"
    assert not any("never awaited" in str(warning.message) for warning in seen)
    assert not (tmp_path / "data").exists()


def test_async_backend_method_is_rejected_and_backend_closed(tmp_path):
    backend = MemoryBackend()

    async def accounts(workspace):
        return []

    backend.accounts = accounts
    registry = PluginRegistry((Plugin("broken", "Async backend", storage=lambda context: backend),))
    with pytest.raises(PluginError) as captured:
        create_app(settings=settings(tmp_path, "broken"), plugins=registry)
    assert captured.value.code == "storage_startup_failed"
    assert backend.closed is True


@pytest.mark.parametrize("missing", [False, True])
def test_missing_or_failed_selected_storage_has_no_fallback(tmp_path, missing):
    def fail(context):
        raise RuntimeError("FIXTURE_CONFIGURATION_SECRET")

    registry = PluginRegistry(
        () if missing else (Plugin("broken", "Broken storage", storage=fail),)
    )
    with pytest.raises(PluginError) as captured:
        create_app(settings=settings(tmp_path, "broken"), plugins=registry)
    assert captured.value.code == (
        "missing_storage_plugin" if missing else "storage_startup_failed"
    )
    assert "FIXTURE_CONFIGURATION_SECRET" not in str(captured.value)
    assert not (tmp_path / "data").exists()


def test_bank_startup_failure_closes_owned_storage(tmp_path):
    events = []
    backend = MemoryBackend(events)

    def fail(context):
        raise RuntimeError("FIXTURE_BANK_SECRET")

    registry = PluginRegistry(
        (
            Plugin("memory", "Memory storage", storage=lambda context: backend),
            Plugin("broken", "Broken bank", bank=fail),
        )
    )
    app = create_app(settings=settings(tmp_path, "memory"), plugins=registry)
    with pytest.raises(PluginError) as captured:
        with TestClient(app, base_url="https://127.0.0.1:5173"):
            pass
    assert captured.value.code == "bank_startup_failed"
    assert "FIXTURE_BANK_SECRET" not in str(captured.value)
    assert events == ["storage-closed"]


def test_injected_storage_bypasses_factory_and_remains_open(tmp_path):
    backend = MemoryBackend()

    def fail(context):
        raise AssertionError("Injected storage must bypass its factory")

    registry = PluginRegistry((Plugin("sqlite", "Selected storage", storage=fail),))
    app = create_app(store=backend, settings=settings(tmp_path), plugins=registry)
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        assert app.state.storage_plugin is None
        assert client.get("/api/workspace?workspace=personal").status_code == 200
    assert backend.closed is False
    assert backend.accounts("personal") == []
    assert not (tmp_path / "data").exists()
