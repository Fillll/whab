"""The bundled storage descriptor preserves lazy loading and saved ledger state."""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest
from whab_plugin_sqlite import Store
from whab_plugin_sqlite.plugin import plugin

from whab.domain.dates import current_month
from whab.domain.errors import StoreError
from whab.plugins import StorageContext
from whab.storage.bank import BankStore


def test_descriptor_and_provider_facade_do_not_import_sqlite_backend(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import builtins
import json
import sys

# Coverage may preload sqlite3 for its own storage. Audit imports attempted by
# the descriptor/facade while keeping instrumentation and preloaded modules intact.
sqlite_imports = []
original_import = builtins.__import__

def audit_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == 'sqlite3' or name.startswith('sqlite3.') or name == '_sqlite3':
        sqlite_imports.append(name)
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = audit_import
try:
    from whab.storage.bank import BankStore
    from whab_plugin_sqlite.plugin import plugin
    assert plugin.id == 'sqlite'
    assert callable(plugin.storage)
    assert plugin.bank is None
    assert plugin.insights is None
    assert plugin.export is None
finally:
    builtins.__import__ = original_import

print(json.dumps({
    'backend': 'whab_plugin_sqlite.store' in sys.modules,
    'schema': 'whab_plugin_sqlite.schema' in sys.modules,
    'seeding': 'whab_plugin_sqlite.seeding' in sys.modules,
    'driver_imports': sqlite_imports,
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
        "driver_imports": [],
    }
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("environment", ["sandbox", "production"])
def test_fresh_storage_factory_uses_whab_filename(tmp_path, environment):
    backend = plugin.storage(StorageContext(tmp_path, environment, {}))
    try:
        assert backend.database_path == str(tmp_path / "data" / f"whab-{environment}.sqlite")
        assert backend.accounts("personal") == []
    finally:
        backend.close()
    assert [path.name for path in (tmp_path / "data").iterdir()] == [f"whab-{environment}.sqlite"]


@pytest.mark.parametrize("environment", ["sandbox", "production"])
def test_storage_factory_reopens_previous_filename_and_preserves_ledger(
    tmp_path, environment, legacy_token
):
    context = StorageContext(tmp_path, environment, {})
    previous_path = tmp_path / "data" / f"previous-{environment}.sqlite"
    backend = Store(previous_path)
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
    assert previous_path.is_file()
    assert not (tmp_path / "data" / f"whab-{environment}.sqlite").exists()
    reopened = plugin.storage(context)
    try:
        assert reopened.database_path == str(previous_path)
        assert reopened.workspace("personal", month) == expected_workspace
        assert BankStore(reopened, "plaid").get_item("fixture-item") == expected_item
        assert reopened.db.execute(
            "SELECT token,cursor FROM items WHERE id=?", ("fixture-item",)
        ).fetchone()[:] == (legacy_token, "saved-cursor")
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
    previous_path = tmp_path / "data" / "previous-sandbox.sqlite"
    saved = Store(previous_path)
    saved.add_account(
        "personal", {"name": "Sandbox fixture", "type": "depository", "balance": 1000}
    )
    saved.close()
    sandbox = plugin.storage(StorageContext(tmp_path, "sandbox", {}))
    production = plugin.storage(StorageContext(tmp_path, "production", {}))
    try:
        assert sandbox.database_path == str(previous_path)
        assert production.database_path == str(tmp_path / "data" / "whab-production.sqlite")
        assert production.accounts("personal") == []
        assert len(sandbox.accounts("personal")) == 1
        assert sandbox.accounts("personal")[0]["name"] == "Sandbox fixture"
    finally:
        sandbox.close()
        production.close()


@pytest.mark.parametrize("include_canonical", [False, True])
def test_storage_factory_refuses_ambiguous_ledgers_without_changing_files(
    tmp_path, include_canonical
):
    directory = tmp_path / "data"
    names = [
        "previous-sandbox.sqlite",
        "whab-sandbox.sqlite" if include_canonical else "other-sandbox.sqlite",
    ]
    for name in names:
        saved = Store(directory / name)
        saved.add_account("personal", {"name": name, "type": "depository", "balance": 1000})
        saved.close()
    before = {path.name: path.read_bytes() for path in directory.iterdir()}

    with pytest.raises(StoreError, match="Multiple SQLite ledgers"):
        plugin.storage(StorageContext(tmp_path, "sandbox", {}))

    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


@pytest.mark.parametrize("kind", ["corrupt", "foreign", "empty", "mismatched-columns"])
def test_storage_factory_refuses_unrecognized_previous_file_without_mutation(tmp_path, kind):
    directory = tmp_path / "data"
    directory.mkdir()
    previous_path = directory / "previous-sandbox.sqlite"
    if kind == "corrupt":
        previous_path.write_bytes(b"Synthetic invalid database bytes")
    elif kind == "foreign":
        with closing(sqlite3.connect(previous_path)) as db:
            db.execute("CREATE TABLE unrelated (value TEXT)")
            db.execute("INSERT INTO unrelated VALUES ('preserve this fixture')")
            db.commit()
    elif kind == "mismatched-columns":
        with closing(sqlite3.connect(previous_path)) as db:
            db.executescript("""
                CREATE TABLE meta(workspace);
                CREATE TABLE accounts(id);
                CREATE TABLE categories(id);
                CREATE TABLE transactions(workspace,date);
                CREATE TABLE budget(id);
                CREATE TABLE items(id);
            """)
    else:
        previous_path.touch()
    before = {path.name: path.read_bytes() for path in directory.iterdir()}

    with pytest.raises(StoreError, match="existing SQLite file"):
        plugin.storage(StorageContext(tmp_path, "sandbox", {}))

    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


@pytest.mark.parametrize(
    "table,column",
    [
        ("meta", "opening_cash"),
        ("accounts", "name"),
        ("categories", "name"),
        ("transactions", "merchant"),
        ("budget", "assigned"),
        ("items", "institution_name"),
    ],
)
def test_storage_factory_requires_base_columns_before_opening_previous_file(
    tmp_path, table, column
):
    directory = tmp_path / "data"
    previous_path = directory / "previous-sandbox.sqlite"
    saved = Store(previous_path)
    saved.close()
    with closing(sqlite3.connect(previous_path)) as db:
        db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    before = {path.name: path.read_bytes() for path in directory.iterdir()}

    with pytest.raises(StoreError, match="existing SQLite file"):
        plugin.storage(StorageContext(tmp_path, "sandbox", {}))

    for name, contents in before.items():
        assert (directory / name).read_bytes() == contents
    # SQLite can create WAL coordination files even for a read-only connection.
    # The ledger itself must retain its exact bytes and no fresh budget may appear.
    assert {path.name for path in directory.iterdir()} - before.keys() <= {
        "previous-sandbox.sqlite-wal",
        "previous-sandbox.sqlite-shm",
    }
    assert not (directory / "whab-sandbox.sqlite").exists()
