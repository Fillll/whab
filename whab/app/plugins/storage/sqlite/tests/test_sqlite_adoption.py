"""Authority schema and first identity publish in one interruptible native commit."""

import json
import signal
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from whab_plugin_devkit import receipt_png
from whab_plugin_sqlite import Store

_CRASH_ADOPTION = """
import os,signal,sys
from whab_plugin_sqlite import Store

boundary = sys.argv[2]
original = Store._authority_initialize

def stop():
    os.kill(os.getpid(),signal.SIGKILL)

class InterruptedConnection:
    def __init__(self, connection):
        self.connection = connection
    def __getattr__(self,name):
        return getattr(self.connection,name)
    def execute(self,statement,parameters=()):
        if statement == 'COMMIT' and boundary == 'before_commit':
            stop()
        result = self.connection.execute(statement,parameters)
        if ((boundary == 'authority_schema' and statement.startswith('CREATE TABLE IF NOT EXISTS storage_authority'))
            or (boundary == 'operations_schema' and statement.startswith('CREATE TABLE IF NOT EXISTS storage_operations'))
            or (boundary == 'identity' and statement.startswith('INSERT INTO storage_authority'))
            or (boundary == 'after_commit' and statement == 'COMMIT')):
            stop()
        return result

def initialize(self,*args,**kwargs):
    self.db = InterruptedConnection(self.db)
    return original(self,*args,**kwargs)

Store._authority_initialize = initialize
Store(sys.argv[1],month='2026-10',initial_role='staged')
raise RuntimeError('The fictional interruption boundary was not reached')
"""


def legacy_database(path):
    legacy = Store(path, month="2026-10", _authority=False)
    try:
        account = legacy.add_account("personal", {"name": "Fictional checking", "type": "depository", "balance": 10000})
        transaction = legacy.add_transaction("personal", {
            "id": "fictional-purchase", "accountId": account, "date": "2026-10-01",
            "merchant": "Fictional market", "amount": 125, "categoryId": "groceries", "note": "Fictional memo",
        })
        legacy.edit_transaction("personal", transaction, reviewed=True)
        legacy.save_receipt("personal", transaction, receipt_png())
        legacy.save_item("fictional-item", "fictional-ciphertext", "Fictional bank")
        legacy.apply_sync("fictional-item", {"added": [], "modified": [], "removed": []}, "fictional-cursor")
        states = legacy._local_states()
        schema = tuple(tuple(row) for row in legacy.db.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"))
        return states, schema
    finally:
        legacy.close()


@pytest.mark.parametrize("boundary", ["authority_schema", "operations_schema", "identity", "before_commit", "after_commit"])
def test_kill_during_adoption_leaves_legacy_or_complete_identity_with_exact_financial_state(tmp_path, boundary):
    path = tmp_path / "fictional-legacy.sqlite"
    expected, schema = legacy_database(path)
    process = subprocess.run([sys.executable, "-I", "-c", _CRASH_ADOPTION, str(path), boundary],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    assert process.returncode == -signal.SIGKILL, process.stderr.decode()
    committed = boundary == "after_commit"
    with closing(sqlite3.connect(path)) as connection:
        control_tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE name IN ('storage_authority','storage_operations')",
        )}
        assert control_tables == ({"storage_authority", "storage_operations"} if committed else set())
        metadata = json.loads(connection.execute("SELECT record FROM storage_authority WHERE id=1").fetchone()[0]) if committed else None
    legacy = Store(path, month="2026-10", _authority=False)
    try:
        assert legacy._local_states() == expected
        if not committed:
            assert tuple(tuple(row) for row in legacy.db.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name")) == schema
    finally:
        legacy.close()
    recovered = Store(path, month="2026-10")
    try:
        assert recovered._local_states() == expected
        evidence = recovered.commit_evidence()
        assert evidence.role == "primary" and evidence.generation == 0
        assert recovered.get_item("fictional-item")["token"] == "fictional-ciphertext"
        assert recovered.get_item("fictional-item")["cursor"] == "fictional-cursor"
        if metadata is not None:
            assert evidence.instance_id == metadata["instanceId"]
            assert evidence.authority_epoch == metadata["authorityEpoch"]
    finally:
        recovered.close()


def test_concurrent_legacy_adopters_share_one_persisted_identity(tmp_path, monkeypatch):
    path = tmp_path / "fictional-legacy.sqlite"
    expected, _ = legacy_database(path)
    barrier = threading.Barrier(2)
    original = Store._authority_initialize

    def initialize(self, *args, **kwargs):
        barrier.wait(timeout=10)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Store, "_authority_initialize", initialize)

    def open_and_read():
        backend = Store(path, month="2026-10")
        try:
            return backend.commit_evidence(), backend._local_states()
        finally:
            backend.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(open_and_read) for _ in range(2)]
        first, second = [future.result(timeout=15) for future in futures]
    assert first == second
    assert first[1] == expected
