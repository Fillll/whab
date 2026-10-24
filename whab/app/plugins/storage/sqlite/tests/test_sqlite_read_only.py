"""Native read-only readiness must not create or migrate a SQLite file."""

import pytest
from whab_plugin_sqlite import Store

from whab.domain.errors import StoreError
from whab.plugins.storage_transfer import (
    StorageOperation,
    canonical_storage_json,
    read_storage_json,
)


def test_read_only_candidate_observes_writer_without_changing_data(tmp_path):
    path = tmp_path / "budget.sqlite"
    writer = Store(path)
    writer.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 123})
    raw = path.read_bytes()
    reader = Store(path, read_only=True)
    try:
        assert reader.commit_evidence() == writer.commit_evidence()
        with pytest.raises(StoreError, match="Read-only"):
            reader.add_category("personal", {"name": "Blocked", "target": 0})
        assert path.read_bytes() == raw
    finally:
        reader.close()
        writer.close()


def test_read_only_missing_and_memory_do_not_create_storage(tmp_path):
    with pytest.raises(Exception):
        Store(tmp_path / "absent" / "ledger.sqlite", read_only=True)
    assert not (tmp_path / "absent").exists()
    with pytest.raises(StoreError, match="existing"):
        Store(":memory:", read_only=True)


def test_read_only_validates_native_receipts_before_readiness(tmp_path):
    path = tmp_path / "ledger.sqlite"
    writer = Store(path)
    before = writer.commit_evidence()
    operation = StorageOperation("fictional-request", before.authority_epoch, before.generation, "personal", "add_account",
                                 canonical_storage_json({"name": "Fictional", "type": "depository", "balance": 1}))
    receipt = writer.execute_operation(operation).receipt
    reader = Store(path, read_only=True)
    try:
        assert reader.operation_receipt(operation.operation_id) == receipt
    finally:
        reader.close()
        writer.close()


def test_read_only_stopped_database_does_not_create_wal_or_shared_memory(tmp_path):
    path = tmp_path / "ledger.sqlite"
    writer = Store(path)
    expected = writer.commit_evidence()
    writer.close()
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    reader = Store(path, read_only=True)
    try:
        assert reader.commit_evidence() == expected
    finally:
        reader.close()
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize("change", ["writer", "bytes", "missing"])
def test_immutable_read_snapshot_refuses_external_change(tmp_path, change):
    path = tmp_path / "ledger.sqlite"
    Store(path).close()
    reader = Store(path, read_only=True)
    writer = None
    try:
        if change == "writer":
            writer = Store(path)
        elif change == "bytes":
            path.write_bytes(path.read_bytes() + b"changed")
        else:
            path.unlink()
        with pytest.raises(StoreError, match="snapshot changed"):
            reader.commit_evidence()
    finally:
        reader.close()
        if writer is not None:
            writer.close()


@pytest.mark.parametrize("corruption", ["legacy", "scope", "capacity"])
def test_read_only_refuses_upgrade_wrong_scope_and_unbounded_receipts_without_writes(tmp_path, corruption):
    path = tmp_path / "ledger.sqlite"
    writer = Store(path)
    metadata = read_storage_json(writer.db.execute("SELECT record FROM storage_authority WHERE id=1").fetchone()[0])
    if corruption == "legacy":
        metadata["formatVersion"] = 1
        metadata.pop("replica")
        metadata.pop("promotionId")
    elif corruption == "scope":
        metadata["budgetId"] = "another-fictional-budget"
    else:
        writer.db.executemany("INSERT INTO storage_operations(operation_id,generation,record) VALUES(?,0,?)",
                              [(f"fictional-{index}", b"{}") for index in range(4097)])
    writer.db.execute("UPDATE storage_authority SET record=? WHERE id=1", (canonical_storage_json(metadata),))
    writer.close()
    raw = path.read_bytes()
    with pytest.raises(StoreError):
        Store(path, read_only=True)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("value", [None, 0, "true"])
def test_read_only_mode_requires_boolean(tmp_path, value):
    with pytest.raises(StoreError, match="boolean"):
        Store(tmp_path / "budget.sqlite", read_only=value)
    assert not (tmp_path / "budget.sqlite").exists()
