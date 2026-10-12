"""Backend regressions use isolated SQLite fixtures and mocked Plaid HTTP only."""

import asyncio
import base64
import hashlib
import json
import sqlite3
import time

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from ihab.plaid_service import (
    PlaidError,
    PlaidService,
    decrypt_token,
    encrypt_token,
    plaid_configuration,
)
from ihab.store import Store, StoreError, dollars_to_cents

ENV = {
    "PLAID_CLIENT_ID": "fixture-client",
    "PLAID_SECRET": "fixture-secret",
    "TOKEN_ENCRYPTION_KEY": "aa" * 32,
    "PLAID_ENV": "sandbox",
}
LEGACY_TOKEN = "AAECAwQFBgcICQoL.FFTb+H1beXLKIUNOoBfu7w==./LGogIH+lMvWJWUDjU/XreJ0vjs="


@pytest.fixture
def store():
    ledger = Store(":memory:", month="2026-10")
    yield ledger
    ledger.close()


def account(balance=100, **extra):
    return {
        "account_id": "bank",
        "name": "Checking",
        "type": "depository",
        "balances": {"current": balance, "iso_currency_code": "USD"},
        "mask": "1234",
        **extra,
    }


def transaction(transaction_id="tx", amount=10, **extra):
    return {
        "transaction_id": transaction_id,
        "account_id": "bank",
        "date": "2026-10-01",
        "name": "Market",
        "amount": amount,
        "pending": False,
        "personal_finance_category": {
            "primary": "FOOD_AND_DRINK",
            "detailed": "FOOD_AND_DRINK_GROCERIES",
        },
        **extra,
    }


def changes(added=None, modified=None, removed=None):
    return {"added": added or [], "modified": modified or [], "removed": removed or []}


def save_item(store):
    store.save_item(
        "item",
        encrypt_token("fixture-access-token", ENV["TOKEN_ENCRYPTION_KEY"], "item"),
        "Fixture Bank",
    )


def client_for(handler):
    return httpx.AsyncClient(
        base_url="https://sandbox.plaid.com", transport=httpx.MockTransport(handler)
    )


def test_pending_posted_preserves_edits_and_replay_is_unique(store):
    save_item(store)
    store.apply_sync("item", changes([transaction("pending", pending=True)]), "one", [account()])
    assert store.workspace("personal", "2026-10")["summary"]["spent"] == 0
    store.edit_transaction("personal", "pending", "fun", True)
    store.db.execute(
        "UPDATE transactions SET note='Family outing' WHERE id='pending' AND workspace='personal'"
    )
    posted = transaction("posted", amount=12, pending_transaction_id="pending")
    batch = changes([posted], removed=[{"transaction_id": "pending"}])
    store.apply_sync("item", batch, "two", [account()])
    store.apply_sync("item", batch, "two", [account()])
    rows = store.transactions("personal")
    assert len(rows) == 1
    assert (rows[0]["categoryId"], rows[0]["reviewed"], rows[0]["note"], rows[0]["amount"]) == (
        "fun",
        True,
        "Family outing",
        1200,
    )
    store.apply_sync(
        "item", changes(modified=[transaction("posted", amount=13)]), "three", [account()]
    )
    assert store.transactions("personal")[0]["categoryId"] == "fun"


def test_workspaces_isolated_and_invalid_foreign_references_rejected(store):
    assert store.accounts("personal") == []
    with pytest.raises(StoreError, match="Account not found"):
        store.add_transaction(
            "personal",
            {
                "accountId": "checking",
                "date": "2026-10-01",
                "merchant": "Other workspace",
                "amount": 100,
                "categoryId": None,
            },
        )
    demo_id = store.transactions("demo")[0]["id"]
    with pytest.raises(StoreError, match="Transaction not found"):
        store.edit_transaction("personal", demo_id, "fun", True)
    with pytest.raises(StoreError, match="Invalid workspace"):
        store.workspace({"$ne": "demo"})
    assert store.transactions("personal") == []


def test_monthly_rollover_cents_refunds_and_credit_balances():
    store = Store(":memory:", month="2026-08")
    try:
        cash = store.add_account(
            "personal", {"name": "Cash", "type": "depository", "balance": 100000}
        )
        store.assign("personal", "groceries", "2026-08", 20000)
        store.add_transaction(
            "personal",
            {
                "accountId": cash,
                "date": "2026-08-10",
                "merchant": "Market",
                "amount": 7000,
                "categoryId": "groceries",
            },
        )
        store.add_transaction(
            "personal",
            {
                "accountId": cash,
                "date": "2026-09-10",
                "merchant": "Refund",
                "amount": -500,
                "categoryId": "groceries",
            },
        )
        store.assign("personal", "groceries", "2026-09", 10000)
        snapshot = store.workspace("personal", "2026-09")
        row = next(row for row in snapshot["budget"] if row["categoryId"] == "groceries")
        assert (row["carryover"], row["assigned"], row["spent"], row["available"]) == (
            13000,
            10000,
            -500,
            23500,
        )
        assert snapshot["summary"]["readyToAssign"] == 70000
        card = store.add_account("personal", {"name": "Card", "type": "credit", "balance": 2000})
        store.add_transaction(
            "personal",
            {
                "accountId": card,
                "date": "2026-09-10",
                "merchant": "Café",
                "amount": 300,
                "categoryId": "dining",
            },
        )
        assert next(a for a in store.accounts("personal") if a["id"] == card)["balance"] == 2300
    finally:
        store.close()


def test_initial_history_does_not_consume_new_budget(store):
    save_item(store)
    store.apply_sync(
        "item",
        changes([transaction("old", amount=8000, date="2024-11-01")]),
        "one",
        [account(1000)],
    )
    store.assign("personal", "groceries", "2026-10", 20000)
    snapshot = store.workspace("personal", "2026-10")
    row = next(row for row in snapshot["budget"] if row["categoryId"] == "groceries")
    assert snapshot["budgetStartMonth"] == "2026-10"
    assert (row["carryover"], row["available"], snapshot["summary"]["readyToAssign"]) == (
        0,
        20000,
        80000,
    )
    assert store.workspace("personal", "2024-11")["summary"]["spent"] == 800000
    with pytest.raises(StoreError, match="before the budget start"):
        store.assign("personal", "groceries", "2024-11", 100)


def test_transfers_history_and_card_debt_never_invent_cash(store):
    save_item(store)
    store.apply_sync(
        "item",
        changes(
            [
                transaction("old", amount=50),
                transaction(
                    "transfer", amount=50, personal_finance_category={"primary": "TRANSFER_OUT"}
                ),
            ]
        ),
        "one",
        [account(100)],
    )
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 10000
    store.add_account("personal", {"name": "Card", "type": "credit", "balance": 2000})
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 8000
    with pytest.raises(StoreError, match="manual account"):
        store.add_transaction(
            "personal",
            {
                "accountId": "bank",
                "date": "2026-10-01",
                "merchant": "Duplicate",
                "amount": 100,
                "categoryId": None,
            },
        )


def test_category_reduction_can_repair_deficit(store):
    cash = store.add_account("personal", {"name": "Cash", "type": "depository", "balance": 20000})
    store.assign("personal", "fun", "2026-10", 5000)
    store.db.execute("UPDATE accounts SET balance=0 WHERE workspace='personal' AND id=?", (cash,))
    store.assign("personal", "fun", "2026-10", 0)
    assert (
        next(
            row
            for row in store.workspace("personal", "2026-10")["budget"]
            if row["categoryId"] == "fun"
        )["assigned"]
        == 0
    )


def test_review_only_does_not_override_category_and_transfer_can_restore(store):
    save_item(store)
    purchase = transaction(
        "payment", amount=20, personal_finance_category={"primary": "TRANSFER_OUT"}
    )
    store.apply_sync("item", changes([purchase]), "one", [account()])
    store.edit_transaction("personal", "payment", reviewed=True)
    assert store.state("personal")["transactions"]["payment"]["categoryEdited"] is False
    store.edit_transaction("personal", "payment", "dining", True)
    store.apply_sync("item", changes(modified=[purchase]), "two", [account()])
    assert store.transactions("personal")[0]["isTransfer"] is False
    assert store.workspace("personal", "2026-10")["summary"]["spent"] == 2000
    store.edit_transaction("personal", "payment", None)
    assert store.transactions("personal")[0]["isTransfer"] is True


def test_atomic_batch_and_cursor_rollback_on_invalid_bank_amount(store):
    save_item(store)
    store.apply_sync("item", changes([transaction("prior")]), "one", [account()])
    with pytest.raises(StoreError):
        store.apply_sync(
            "item",
            changes([transaction("new"), transaction("broken", amount="NaN")]),
            "advance",
            [account(999)],
        )
    assert [row["id"] for row in store.transactions("personal")] == ["prior"]
    assert store.get_item("item")["cursor"] == "one"
    assert store.accounts("personal")[0]["balance"] == 10000
    with pytest.raises(StoreError, match="cursor changed"):
        store.apply_sync("item", changes(), "later", [account()], expected_cursor="stale")
    assert store.get_item("item")["cursor"] == "one"


def test_sql_commit_failure_rolls_back_every_ledger_change(store):
    save_item(store)
    store.db.execute(
        "CREATE TRIGGER fail_import BEFORE INSERT ON transactions WHEN NEW.id='blocked' BEGIN SELECT RAISE(ABORT,'fixture failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.apply_sync(
            "item",
            changes([transaction("valid"), transaction("blocked")]),
            "advance",
            [account(500)],
        )
    assert store.transactions("personal") == []
    assert store.accounts("personal") == []
    assert store.get_item("item")["cursor"] is None


def test_removed_and_unknown_balances_are_conservative(store):
    save_item(store)
    store.apply_sync("item", changes([transaction()]), "one", [account()])
    store.apply_sync("item", changes(), "two", [])
    assert store.accounts("personal")[0]["stale"] is True
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] == 0
    assert len(store.transactions("personal")) == 1
    store.apply_sync(
        "item",
        changes(),
        "three",
        [
            account(),
            {
                "account_id": "card",
                "name": "Card",
                "type": "credit",
                "balances": {"current": None, "available": 10000},
            },
        ],
    )
    assert store.workspace("personal", "2026-10")["summary"]["readyToAssign"] <= 0


def test_usd_money_validation(store):
    assert dollars_to_cents("12.345") == 1235
    for invalid in [True, float("inf"), "NaN"]:
        with pytest.raises(StoreError):
            dollars_to_cents(invalid)
    with pytest.raises(StoreError):
        store.add_account("personal", {"name": "Cash", "type": "depository", "balance": True})
    save_item(store)
    with pytest.raises(StoreError, match="USD"):
        store.apply_sync(
            "item",
            changes(),
            "one",
            [account(balances={"current": 100, "iso_currency_code": "EUR"})],
        )


def test_node_schema_and_encrypted_fixture_remain_usable(tmp_path):
    path = tmp_path / "legacy.sqlite"
    # Synthetic legacy Node schema intentionally lacks all additive Python flags.
    original = sqlite3.connect(path)
    original.executescript("""
        CREATE TABLE meta(workspace TEXT PRIMARY KEY,opening_cash INTEGER NOT NULL DEFAULT 0,baseline_ready INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE accounts(id TEXT NOT NULL,workspace TEXT NOT NULL,name TEXT NOT NULL,institution TEXT NOT NULL,type TEXT NOT NULL,balance INTEGER NOT NULL,source TEXT NOT NULL,item_id TEXT,mask TEXT,PRIMARY KEY(workspace,id));
        CREATE TABLE categories(id TEXT NOT NULL,workspace TEXT NOT NULL,name TEXT NOT NULL,group_name TEXT NOT NULL,target INTEGER NOT NULL,PRIMARY KEY(workspace,id));
        CREATE TABLE transactions(id TEXT NOT NULL,workspace TEXT NOT NULL,account_id TEXT NOT NULL,date TEXT NOT NULL,merchant TEXT NOT NULL,amount INTEGER NOT NULL,category_id TEXT,pending INTEGER NOT NULL DEFAULT 0,reviewed INTEGER NOT NULL DEFAULT 0,source TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',is_transfer INTEGER NOT NULL DEFAULT 0,category_edited INTEGER NOT NULL DEFAULT 0,deleted INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(workspace,id));
        CREATE TABLE budget(workspace TEXT NOT NULL,month TEXT NOT NULL,category_id TEXT NOT NULL,assigned INTEGER NOT NULL,PRIMARY KEY(workspace,month,category_id));
        CREATE TABLE items(id TEXT PRIMARY KEY,token TEXT,institution_name TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'connected',last_synced TEXT,error TEXT,cursor TEXT,baseline_ready INTEGER NOT NULL DEFAULT 0);
        INSERT INTO meta VALUES('personal',0,0);
        INSERT INTO categories VALUES('fun','personal','Fun','Life',10000);
        INSERT INTO accounts VALUES('bank','personal','Checking','Fixture Bank','depository',10000,'plaid','fixture-item','1234');
        INSERT INTO transactions VALUES('legacy','personal','bank','2026-10-01','Fixture transaction',1000,'fun',0,1,'plaid','Fixture note',0,1,0);
        INSERT INTO budget VALUES('personal','2026-10','fun',2000);
    """)
    original.execute(
        "INSERT INTO items(id,token,institution_name,cursor) VALUES(?,?,?,?)",
        ("fixture-item", LEGACY_TOKEN, "Fixture Bank", "legacy-cursor"),
    )
    original.commit()
    original.close()
    reopened = Store(path, month="2026-11")
    try:
        assert reopened.get_item("fixture-item")["cursor"] == "legacy-cursor"
        assert reopened.get_item("fixture-item")["token"] == LEGACY_TOKEN
        assert (
            decrypt_token(
                reopened.get_item("fixture-item")["token"],
                ENV["TOKEN_ENCRYPTION_KEY"],
                "fixture-item",
            )
            == "fixture-access-token"
        )
        assert reopened.transactions("personal")[0]["categoryId"] == "fun"
        assert reopened.transactions("personal")[0]["reviewed"] is True
        assert reopened.transactions("personal")[0]["note"] == "Fixture note"
        snapshot = reopened.workspace("personal", "2026-10")
        assert snapshot["budgetStartMonth"] == "2026-10"
        assert snapshot["accounts"][0]["balance"] == 10000
        assert snapshot["accounts"][0]["institution"] == "Fixture Bank"
        assert snapshot["accounts"][0]["mask"] == "1234"
        assert snapshot["accounts"][0]["stale"] is False
        assert snapshot["categories"] == [
            {"id": "fun", "name": "Fun", "group": "Life", "target": 10000}
        ]
        assert snapshot["budget"][0]["assigned"] == 2000
        assert snapshot["budget"][0]["spent"] == 1000
        assert snapshot["budget"][0]["available"] == 1000
        for table, expected in [
            ("accounts", {"active", "balance_unknown"}),
            ("transactions", {"inferred_transfer"}),
            ("meta", {"budget_start_month"}),
        ]:
            columns = {row["name"] for row in reopened.db.execute(f"PRAGMA table_info({table})")}
            assert expected <= columns
        assert len(reopened.transactions("demo")) == 55
    finally:
        reopened.close()
    twice = Store(path, month="2026-12")
    try:
        assert len(twice.transactions("personal")) == 1
        assert len(twice.transactions("demo")) == 55
        assert twice.get_item("fixture-item")["cursor"] == "legacy-cursor"
    finally:
        twice.close()


def test_encryption_compatible_with_node_and_authenticates_item():
    assert (
        decrypt_token(LEGACY_TOKEN, ENV["TOKEN_ENCRYPTION_KEY"], "fixture-item")
        == "fixture-access-token"
    )
    encrypted = encrypt_token("fixture-access-token", ENV["TOKEN_ENCRYPTION_KEY"], "item")
    assert decrypt_token(encrypted, ENV["TOKEN_ENCRYPTION_KEY"], "item") == "fixture-access-token"
    with pytest.raises(PlaidError):
        decrypt_token(encrypted, ENV["TOKEN_ENCRYPTION_KEY"], "wrong-item")
    assert "fixture-access-token" not in encrypted


def test_production_guard_and_missing_key():
    assert plaid_configuration({**ENV, "PLAID_ENV": "production"})["plaidConfigured"] is False
    assert (
        plaid_configuration({**ENV, "PLAID_ENV": "production", "ALLOW_LIVE_PLAID": "true"})[
            "plaidConfigured"
        ]
        is True
    )
    assert plaid_configuration({})["missingConfig"] == [
        "PLAID_CLIENT_ID",
        "PLAID_SECRET",
        "TOKEN_ENCRYPTION_KEY",
    ]


def test_pagination_restarts_original_cursor_and_saves_all_pages(store):
    async def scenario():
        save_item(store)
        cursors = []

        def handler(request):
            if request.url.path == "/accounts/get":
                return httpx.Response(200, json={"accounts": [account()]})
            payload = json.loads(request.content)
            cursors.append(payload.get("cursor"))
            number = len(cursors)
            if number == 2:
                return httpx.Response(
                    400,
                    json={
                        "error_code": "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION",
                        "error_message": "private bank detail never exposed",
                    },
                )
            return httpx.Response(
                200,
                json={
                    **changes([transaction("discarded" if number == 1 else f"kept-{number}")]),
                    "next_cursor": f"cursor-{number}",
                    "has_more": number in {1, 3},
                    "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE",
                },
            )

        async with client_for(handler) as client:
            service = PlaidService(store, ENV, client)
            await service.sync("item")
        assert cursors == [None, "cursor-1", None, "cursor-3"]
        assert {t["id"] for t in store.transactions("personal")} == {"kept-3", "kept-4"}
        assert store.get_item("item")["cursor"] == "cursor-4"

    asyncio.run(scenario())


def test_failed_sync_does_not_advance_and_does_not_leak_remote_error(store):
    async def scenario():
        save_item(store)

        def handler(request):
            if request.url.path == "/accounts/get":
                return httpx.Response(500, json={"error_message": "fixture-secret private-token"})
            return httpx.Response(
                200,
                json={**changes([transaction("new")]), "has_more": False, "next_cursor": "advance"},
            )

        async with client_for(handler) as client:
            service = PlaidService(store, ENV, client)
            with pytest.raises(PlaidError, match="Bank sync could not complete") as error:
                await service.sync("item")
            assert "fixture-secret" not in str(error.value)
        assert store.transactions("personal") == []
        assert store.get_item("item")["cursor"] is None
        assert store.items()[0]["error"] == "Sync could not complete. Try again."

    asyncio.run(scenario())


def test_reconnect_update_mode_and_disconnect_preserves_ledger(store):
    async def scenario():
        save_item(store)
        store.apply_sync("item", changes([transaction()]), "one", [account()])
        payloads = []

        def handler(request):
            payloads.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"link_token": "fixture-link"})

        async with client_for(handler) as client:
            service = PlaidService(store, ENV, client)
            assert await service.link_token("item") == "fixture-link"
            assert "products" not in payloads[0][1]
            assert "access_token" in payloads[0][1]
            await service.disconnect("item")
        assert store.get_item("item")["token"] is None
        assert store.accounts("personal")[0]["stale"] is True
        assert len(store.transactions("personal")) == 1

    asyncio.run(scenario())


def test_mid_sync_webhook_is_not_lost(store):
    async def scenario():
        service = PlaidService(store, ENV, client_for(lambda _: httpx.Response(200, json={})))
        calls = 0
        first = asyncio.Event()

        async def sync(_):
            nonlocal calls
            calls += 1
            if calls == 1:
                await first.wait()

        service.sync = sync
        service.queue_sync("item")
        await asyncio.sleep(0)
        service.queue_sync("item")
        service.queue_sync("item")
        first.set()
        await asyncio.sleep(0)
        assert calls == 2
        assert not service._queued
        await service.client.aclose()

    asyncio.run(scenario())


def _url64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def test_verified_webhook_requires_signature_exact_body_and_freshness(store):
    async def scenario():
        private = ec.generate_private_key(ec.SECP256R1())
        public = private.public_key().public_numbers()
        key = {
            "kid": "key-id",
            "alg": "ES256",
            "kty": "EC",
            "crv": "P-256",
            "x": _url64(public.x.to_bytes(32)),
            "y": _url64(public.y.to_bytes(32)),
        }
        body = b'{"item_id":"item"}'

        def jwt(age=0, alg="ES256"):
            header = _url64(json.dumps({"alg": alg, "kid": "key-id"}).encode())
            payload = _url64(
                json.dumps(
                    {
                        "iat": time.time() - age,
                        "request_body_sha256": hashlib.sha256(body).hexdigest(),
                    }
                ).encode()
            )
            message = f"{header}.{payload}".encode()
            r, s = decode_dss_signature(private.sign(message, ec.ECDSA(hashes.SHA256())))
            return message.decode() + "." + _url64(r.to_bytes(32) + s.to_bytes(32))

        async with client_for(lambda _: httpx.Response(200, json={"key": key})) as client:
            service = PlaidService(store, ENV, client)
            assert await service.verify_webhook(body, jwt()) is True
            assert await service.verify_webhook(b"{}", jwt()) is False
            assert await service.verify_webhook(body, jwt(301)) is False
            assert await service.verify_webhook(body, jwt(-60)) is False
            assert await service.verify_webhook(body, jwt(alg="HS256")) is False
            assert await service.verify_webhook(body, "invalid") is False
            key["expired_at"] = time.time() - 10
            service._keys.clear()
            assert await service.verify_webhook(body, jwt()) is False
            assert await service.verify_webhook(body, jwt(20)) is True

    asyncio.run(scenario())
