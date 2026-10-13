"""The bundled storage descriptor preserves lazy loading and saved ledger state."""

import json
import subprocess
import sys

import pytest

from whab.domain.dates import current_month
from whab.plugins import StorageContext
from whab.storage.bank import BankStore
from whab.storage.sqlite.plugin import plugin


def test_descriptor_and_provider_facade_do_not_import_sqlite_backend(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
import sys
from whab.storage.bank import BankStore
from whab.storage.sqlite.plugin import plugin
assert plugin.id == 'sqlite'
assert callable(plugin.storage)
assert plugin.bank is None
assert plugin.insights is None
assert plugin.export is None
print(json.dumps({
    'backend': 'whab.storage.sqlite.store' in sys.modules,
    'schema': 'whab.storage.sqlite.schema' in sys.modules,
    'seeding': 'whab.storage.sqlite.seeding' in sys.modules,
    'driver': 'sqlite3' in sys.modules,
}))
""",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == {
        "backend": False,
        "schema": False,
        "seeding": False,
        "driver": False,
    }
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("environment", ["sandbox", "production"])
def test_storage_factory_reopens_legacy_filename_and_preserves_ledger(
    tmp_path, environment, legacy_token
):
    context = StorageContext(tmp_path, environment, {})
    backend = plugin.storage(context)
    month = current_month()
    try:
        account = backend.add_account(
            "personal", {"name": "Saved cash", "type": "depository", "balance": 10000}
        )
        transaction = backend.add_transaction(
            "personal",
            {
                "accountId": account,
                "date": f"{month}-01",
                "merchant": "Fixture market",
                "amount": 1000,
                "categoryId": "groceries",
                "note": "Saved note",
            },
        )
        backend.edit_transaction("personal", transaction, "fun", True)
        backend.assign("personal", "fun", month, 2000)
        provider = BankStore(backend, "plaid")
        provider.save_item("fixture-item", legacy_token, "Fixture Bank")
        provider.apply_sync(
            "fixture-item",
            {"added": [], "modified": [], "removed": []},
            "saved-cursor",
            [
                {
                    "id": "fixture-bank",
                    "name": "Saved bank",
                    "institution": "Fixture Bank",
                    "type": "depository",
                    "balance": 5000,
                    "currency": "USD",
                    "source": "plaid",
                    "itemId": "fixture-item",
                }
            ],
            expected_cursor=None,
        )
        expected_workspace = backend.workspace("personal", month)
        expected_item = provider.get_item("fixture-item")
        expected_demo = backend.transactions("demo")
    finally:
        backend.close()
    assert (tmp_path / "data" / f"ihab-{environment}.sqlite").is_file()
    assert not (tmp_path / "data" / f"whab-{environment}.sqlite").exists()
    reopened = plugin.storage(context)
    try:
        assert reopened.workspace("personal", month) == expected_workspace
        assert BankStore(reopened, "plaid").get_item("fixture-item") == expected_item
        assert reopened.transactions("demo") == expected_demo
        assert reopened.workspace("personal", month)["budgetStartMonth"] == month
        saved = next(row for row in reopened.transactions("personal") if row["id"] == transaction)
        assert (saved["categoryId"], saved["reviewed"], saved["note"]) == (
            "fun",
            True,
            "Saved note",
        )
    finally:
        reopened.close()


def test_storage_environment_files_keep_households_isolated(tmp_path):
    sandbox = plugin.storage(StorageContext(tmp_path, "sandbox", {}))
    production = plugin.storage(StorageContext(tmp_path, "production", {}))
    try:
        sandbox.add_account(
            "personal", {"name": "Sandbox fixture", "type": "depository", "balance": 1000}
        )
        assert production.accounts("personal") == []
        assert len(sandbox.accounts("personal")) == 1
        assert sandbox.accounts("personal")[0]["name"] == "Sandbox fixture"
    finally:
        sandbox.close()
        production.close()
