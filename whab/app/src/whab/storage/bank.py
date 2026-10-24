"""Provider-scoped bank persistence for trusted in-process integration plugins.

Legacy Plaid IDs stay unchanged. Other providers receive a reserved namespace;
their account/transaction identities also include the connection to avoid ID
collisions across different account-link sessions.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import quote, unquote

from whab.domain.errors import StoreError
from whab.domain.imports import ImportedAccount, ImportedTransaction, SyncChanges
from whab.domain.transactions import UNSET
from whab.plugins import StorageBackend


class BankStore:
    def __init__(self, store: StorageBackend, provider_id: str, *, suggestions_only=False):
        if (
            type(provider_id) is not str
            or len(provider_id) > 64
            or not re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", provider_id)
            or provider_id in {"manual", "demo"}
        ):
            raise StoreError("Invalid bank provider.")
        self._store = store
        self._suggestions_only = suggestions_only
        self._provider = provider_id

    @staticmethod
    def _raw_id(value: str) -> str:
        if type(value) is not str or not value or len(value) > 200 or "\x00" in value:
            raise StoreError("Invalid bank identifier.")
        return value

    def item_identity(self, item_id: str) -> str:
        """Stable storage identity, also suitable as a token encryption AAD."""
        raw = self._raw_id(item_id)
        if self._provider == "plaid":
            if raw.startswith("provider:"):
                raise StoreError("Reserved bank identifier namespace.")
            return raw
        return f"provider:{self._provider}:item:{quote(raw, safe='')}"

    def _record_identity(self, kind: str, item_id: str, record_id: str) -> str:
        raw = self._raw_id(record_id)
        if self._provider == "plaid":
            if raw.startswith("provider:"):
                raise StoreError("Reserved bank identifier namespace.")
            return raw
        # A bounded public ID remains compatible with the API's 200-char limit.
        digest = hashlib.sha256(
            json.dumps([self._raw_id(item_id), raw], separators=(",", ":")).encode()
        ).hexdigest()
        return f"provider:{self._provider}:{kind}:{digest}"

    def _external_item_id(self, stored_id: str) -> str:
        if self._provider == "plaid":
            return stored_id
        prefix = f"provider:{self._provider}:item:"
        if not stored_id.startswith(prefix):
            raise StoreError("Connection belongs to another bank provider.")
        return unquote(stored_id[len(prefix) :])

    def _owned_item(self, item_id: str) -> dict | None:
        try:
            stored_id = self.item_identity(item_id)
        except StoreError:
            return None  # Reserved/invalid identities are absent from this provider's view.
        item = self._store.get_item(stored_id)
        if not item or item.get("provider", "plaid") != self._provider:
            return None
        return {**item, "id": item_id}

    def items(self) -> list[dict]:
        result = []
        for item in self._store.items():
            stored = self._store.get_item(item["id"])
            if stored and stored.get("provider", "plaid") == self._provider:
                result.append({**item, "id": self._external_item_id(item["id"])})
        return result

    def get_item(self, item_id: str) -> dict | None:
        return self._owned_item(item_id)

    def save_item(self, item_id: str, encrypted_token: str, institution_name: str) -> None:
        self._store.save_item(
            self.item_identity(item_id),
            encrypted_token,
            institution_name,
            provider_id=self._provider,
        )

    def set_item_status(self, item_id: str, status: str, error: str | None) -> None:
        if self._owned_item(item_id):
            self._store.set_item_status(self.item_identity(item_id), status, error)

    def disconnect_item(self, item_id: str) -> None:
        if not self._owned_item(item_id):
            raise StoreError("Connection not found.")
        self._store.disconnect_item(self.item_identity(item_id))

    def apply_sync(
        self,
        item_id: str,
        changes: SyncChanges,
        cursor: str | None,
        accounts: list[ImportedAccount] | None = None,
        historical_complete: bool = True,
        *,
        expected_cursor: Any = UNSET,
    ) -> dict:
        payload = self.prepare_sync(item_id, changes, cursor, accounts, historical_complete,
                                    expected_cursor=expected_cursor)
        return self._store.apply_sync(payload["itemId"], payload["changes"], payload["cursor"], payload["accounts"],
                                     payload["historicalComplete"], expected_cursor=payload.get("expectedCursor", UNSET))

    def prepare_sync(
        self,
        item_id: str,
        changes: SyncChanges,
        cursor: str | None,
        accounts: list[ImportedAccount] | None = None,
        historical_complete: bool = True,
        *,
        expected_cursor: Any = UNSET,
    ) -> dict:
        """Host-owned provider scoping for either legacy or durable command writes."""
        if not self._owned_item(item_id):
            raise StoreError("Connection not found.")
        stored_item_id = self.item_identity(item_id)
        scoped_accounts = [
            {
                **account,
                "id": self._record_identity("account", item_id, account["id"]),
                "itemId": stored_item_id,
                "source": self._provider,
            }
            for account in accounts or []
        ]

        suggestions_only = self._suggestions_only() if callable(self._suggestions_only) else self._suggestions_only
        existing = {row["id"]: row for row in self._store.transactions("personal")} if suggestions_only else {}

        def scoped_transaction(transaction: ImportedTransaction) -> ImportedTransaction:
            pending = transaction.get("pendingTransactionId")
            previous = existing.get(self._record_identity("transaction", item_id, transaction["id"]))
            if previous is None and pending:
                previous = existing.get(self._record_identity("transaction", item_id, pending))
            return {
                **transaction,
                "id": self._record_identity("transaction", item_id, transaction["id"]),
                "accountId": self._record_identity("account", item_id, transaction["accountId"]),
                "pendingTransactionId": self._record_identity("transaction", item_id, pending)
                if pending
                else None,
                "source": self._provider,
                **({"suggestedCategoryId": previous.get("categoryId") if previous else None} if suggestions_only else {}),
            }

        scoped_changes = {
            "added": [scoped_transaction(transaction) for transaction in changes.get("added", [])],
            "modified": [
                scoped_transaction(transaction) for transaction in changes.get("modified", [])
            ],
            "removed": [
                self._record_identity("transaction", item_id, transaction_id)
                for transaction_id in changes.get("removed", [])
            ],
        }
        return {"itemId": stored_item_id, "changes": scoped_changes, "cursor": cursor,
                "accounts": scoped_accounts, "historicalComplete": historical_complete,
                **({"expectedCursor": expected_cursor} if expected_cursor is not UNSET else {})}
