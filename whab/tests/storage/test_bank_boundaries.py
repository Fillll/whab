"""Provider namespaces and atomic ownership checks use synthetic in-memory data."""

from copy import deepcopy

import pytest
from whab_sqlite import Store

from whab.domain.errors import StoreError
from whab.storage.bank import BankStore
from whab.web.plugin_snapshot import build_snapshot


def account(account_id="account", item_id="item", source="plaid", balance=10000):
    return {
        "id": account_id,
        "name": "Synthetic checking",
        "institution": "Synthetic bank",
        "type": "depository",
        "balance": balance,
        "currency": "USD",
        "source": source,
        "itemId": item_id,
        "stale": False,
        "balanceUnknown": False,
    }


def transaction(transaction_id="transaction", account_id="account", **extra):
    return {
        "id": transaction_id,
        "accountId": account_id,
        "date": "2026-10-01",
        "merchant": "Synthetic merchant",
        "amount": 1000,
        "suggestedCategoryId": "groceries",
        "pending": False,
        "source": "plaid",
        "inferredTransfer": False,
        "pendingTransactionId": None,
        **extra,
    }


def changes(added=None, modified=None, removed=None):
    return {"added": added or [], "modified": modified or [], "removed": removed or []}


@pytest.fixture
def store():
    ledger = Store(":memory:", month="2026-10")
    yield ledger
    ledger.close()


def test_provider_namespaces_keep_colliding_raw_ids_separate_and_plaid_unchanged(store):
    plaid, alpha, beta = (BankStore(store, provider) for provider in ("plaid", "alpha", "beta"))
    for backend in (plaid, alpha, beta):
        backend.save_item("item", "synthetic-ciphertext", "Synthetic bank")
        backend.apply_sync("item", changes([transaction()]), "cursor", [account()])
        assert backend.items()[0]["id"] == "item"
        assert backend.get_item("item")["id"] == "item"
    rows = store.transactions("personal")
    accounts = store.accounts("personal")
    assert len(rows) == len(accounts) == 3
    assert len({row["id"] for row in rows}) == 3
    assert len({row["id"] for row in accounts}) == 3
    assert all(len(row["id"]) <= 200 for row in rows + accounts)
    assert next(row for row in rows if row["source"] == "plaid")["id"] == "transaction"
    assert next(row for row in accounts if row["source"] == "plaid")["id"] == "account"
    assert plaid.item_identity("item") == "item"
    assert alpha.item_identity("item") != beta.item_identity("item")
    alpha.disconnect_item("item")
    assert alpha.items() == []
    assert len(plaid.items()) == len(beta.items()) == 1
    assert next(row for row in store.accounts("personal") if row["source"] == "alpha")["stale"]
    assert len(store.transactions("personal")) == 3


def test_namespaces_scope_connections_and_preserve_matched_pending_edits(store):
    backend = BankStore(store, "alpha")
    for item_id in ("first", "second"):
        backend.save_item(item_id, "synthetic-ciphertext", "Synthetic bank")
        backend.apply_sync(
            item_id,
            changes([transaction("pending", pending=True)]),
            "one",
            [account(item_id=item_id)],
        )
    pending = store.transactions("personal")
    assert len(pending) == 2
    assert len({row["accountId"] for row in pending}) == 2
    first_account = next(
        row for row in store.accounts("personal") if row["itemId"] == backend.item_identity("first")
    )
    first = next(row for row in pending if row["accountId"] == first_account["id"])
    store.edit_transaction("personal", first["id"], "fun", True)
    backend.apply_sync(
        "first",
        changes([transaction("posted", pendingTransactionId="pending")], removed=["pending"]),
        "two",
        [account(item_id="first")],
    )
    posted = next(row for row in store.transactions("personal") if not row["pending"])
    assert (posted["categoryId"], posted["reviewed"]) == ("fun", True)
    assert len(store.transactions("personal")) == 2


def test_bank_facade_does_not_read_or_disconnect_another_provider(store):
    alpha, beta = BankStore(store, "alpha"), BankStore(store, "beta")
    beta.save_item("item", "synthetic-ciphertext", "Synthetic bank")
    assert alpha.get_item("item") is None
    assert alpha.items() == []
    alpha.set_item_status("item", "error", "Synthetic error")
    assert beta.get_item("item")["status"] == "connected"
    with pytest.raises(StoreError, match="Connection not found"):
        alpha.disconnect_item("item")
    assert beta.get_item("item")["token"] == "synthetic-ciphertext"


@pytest.mark.parametrize("kind", ["item", "account", "transaction", "pending", "removed"])
def test_plaid_cannot_use_other_provider_reserved_namespace(store, kind):
    backend = BankStore(store, "plaid")
    if kind == "item":
        with pytest.raises(StoreError, match="Reserved"):
            backend.save_item("provider:alpha:item:raw", "synthetic-ciphertext", "Synthetic bank")
        return
    backend.save_item("item", "synthetic-ciphertext", "Synthetic bank")
    before = deepcopy(store.state("personal"))
    records, accounts, removed = [transaction()], [account()], []
    if kind == "account":
        accounts = [account(account_id="provider:alpha:account:raw")]
    elif kind == "transaction":
        records = [transaction("provider:alpha:transaction:raw")]
    elif kind == "pending":
        records = [transaction(pendingTransactionId="provider:alpha:transaction:raw")]
    elif kind == "removed":
        removed = ["provider:alpha:transaction:raw"]
    with pytest.raises(StoreError, match="Reserved"):
        backend.apply_sync("item", changes(records, removed=removed), "advance", accounts)
    assert store.state("personal") == before


def seed_two_connections(store):
    for suffix in ("a", "b"):
        item_id, account_id, transaction_id = f"item-{suffix}", f"account-{suffix}", f"tx-{suffix}"
        store.save_item(item_id, "synthetic-ciphertext", "Synthetic bank")
        store.apply_sync(
            item_id,
            changes([transaction(transaction_id, account_id)]),
            "one",
            [account(account_id, item_id)],
        )


@pytest.mark.parametrize(
    "violation",
    [
        "account",
        "account-item",
        "account-source",
        "transaction-account",
        "transaction-source",
        "existing",
        "pending",
        "removed",
    ],
)
def test_cross_connection_imports_roll_back_valid_rows_balances_and_cursor(store, violation):
    seed_two_connections(store)
    before = deepcopy(store.state("personal"))
    accounts = [account("account-a", "item-a", balance=50000)]
    added, modified, removed = [transaction("new-valid", "account-a")], [], []
    if violation == "account":
        accounts.append(account("account-b", "item-a"))
    elif violation == "account-item":
        accounts.append(account("new-account", "item-b"))
    elif violation == "account-source":
        accounts.append(account("new-account", "item-a", source="beta"))
    elif violation == "transaction-account":
        added.append(transaction("new-cross-account", "account-b"))
    elif violation == "transaction-source":
        added.append(transaction("new-cross-source", "account-a", source="beta"))
    elif violation == "existing":
        modified.append(transaction("tx-b", "account-a"))
    elif violation == "pending":
        added.append(transaction("new-cross-pending", "account-a", pendingTransactionId="tx-b"))
    elif violation == "removed":
        removed.append("tx-b")
    with pytest.raises(StoreError, match="another connection|does not belong"):
        store.apply_sync("item-a", changes(added, modified, removed), "advance", accounts)
    assert store.state("personal") == before


def test_absent_removal_and_unknown_pending_remain_replayable(store):
    store.save_item("item", "synthetic-ciphertext", "Synthetic bank")
    batch = changes(
        [transaction(pendingTransactionId="never-seen-pending")], removed=["already-absent"]
    )
    store.apply_sync("item", batch, "cursor", [account()])
    store.apply_sync("item", batch, "cursor", [account()])
    assert len(store.transactions("personal")) == 1
    assert store.get_item("item")["cursor"] == "cursor"


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", True),
        ("amount", "1000"),
        ("pending", "false"),
        ("id", {"bad": "id"}),
        ("date", "2026-02-30"),
        ("merchant", 123),
    ],
)
def test_normalized_type_errors_do_not_advance_ledger(store, field, value):
    store.save_item("item", "synthetic-ciphertext", "Synthetic bank")
    before = deepcopy(store.state("personal"))
    bad = transaction("bad", **{field: value})
    with pytest.raises(StoreError):
        store.apply_sync("item", changes([transaction("valid"), bad]), "advance", [account()])
    assert store.state("personal") == before


def test_unknown_provider_category_suggestion_stays_unclassified_and_snapshot_usable(store):
    store.save_item("item", "synthetic-ciphertext", "Synthetic bank")
    imported = transaction(suggestedCategoryId="category-not-in-this-workspace")
    store.apply_sync("item", changes([imported]), "one", [account()])
    data = store.workspace("personal", "2026-10")
    assert data["transactions"][0]["categoryId"] is None
    assert data["summary"]["spent"] == 1000
    assert build_snapshot("personal", data).transactions[0].category_id is None
    assert store.get_item("item")["cursor"] == "one"
    store.edit_transaction("personal", "transaction", "fun", True)
    store.apply_sync("item", changes(modified=[imported]), "two", [account()])
    assert store.transactions("personal")[0]["categoryId"] == "fun"
    assert build_snapshot("personal", store.workspace("personal", "2026-10"))
    assert store.get_item("item")["cursor"] == "two"
