"""Bank metadata reads stay independent of fictional financial history."""

import sqlite3

from whab_plugin_sqlite import Store

from tests.storage.test_bank_boundaries import account, changes, transaction


def test_bank_reads_do_not_load_financial_tables_or_change_saved_state(tmp_path, monkeypatch):
    path = tmp_path / "fictional.sqlite"
    store = Store(path, environment="sandbox")
    try:
        store.save_item("item", "fictional-ciphertext", "Fictional bank")
        store.apply_sync("item", changes([transaction()]), "fictional-cursor", [account()])
        store.configure_bank_sync("item", [account()], {"account": {"accountId": "account", "since": "2026-10-01"}})
        store.save_item("other", "fictional-other-ciphertext", "Other bank", provider_id="alpha")
        store.save_item("closed", "fictional-closed-ciphertext", "Archived bank")
        store.disconnect_item("closed")
        expected = store.state("personal")
        evidence = store.commit_evidence()
        summaries = [
            {key: item[key] for key in ("id", "institutionName", "status", "lastSynced", "error")}
            for item in expected["items"].values() if item["token"]
        ]
        financial_tables = {"accounts", "categories", "transactions", "budget", "budget_history", "transaction_receipts", "future_funding", "rollover_modes"}

        def authorize(action, table, *_args):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table in financial_tables else sqlite3.SQLITE_OK

        def refuse_full_state(*_args, **_kwargs):
            raise AssertionError("Bank metadata must not load full ledger state")

        with monkeypatch.context() as scoped:
            scoped.setattr(store, "state", refuse_full_state)
            store.db.set_authorizer(authorize)
            try:
                assert store.items() == summaries
                for item_id, item in expected["items"].items():
                    assert store.get_item(item_id) == item
                assert store.get_item("absent") is None
                detached = store.get_item("item")
                detached["syncSettings"]["account"]["since"] = "2020-01-01"
                assert store.get_item("item") == expected["items"]["item"]
            finally:
                store.db.set_authorizer(None)
        assert store.state("personal") == expected
        assert store.commit_evidence() == evidence
    finally:
        store.close()
    before = {file.name: file.read_bytes() for file in tmp_path.iterdir()}
    reader = Store(path, environment="sandbox", read_only=True)
    try:
        assert reader.items() == summaries
        assert reader.get_item("item") == expected["items"]["item"]
    finally:
        reader.close()
    assert {file.name: file.read_bytes() for file in tmp_path.iterdir()} == before
