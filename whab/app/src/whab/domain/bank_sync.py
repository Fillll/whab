"""Atomic-provider merge rules over detached plain ledger state.

The caller owns persistence and applies the returned state and checkpoint in one
commit. Receipt identities live beside ledger state and move with posting matches.
"""

from .categories import ensure_child_category
from .errors import StoreError
from .imports import (
    annotated_transaction,
    bank_identifier,
    imported_account,
    validate_imported_transaction,
)
from .transactions import UNSET


def merge_sync(state, item_id, changes, cursor, accounts, *, expected_cursor=UNSET, synced_at):
    item = state["items"].get(item_id)
    if not item or not item.get("token"):
        raise StoreError("Connection not found.")
    if expected_cursor is not UNSET and item.get("cursor") != expected_cursor:
        raise StoreError("Sync cursor changed. Please retry.")
    if type(changes) is not dict or changes.keys() != {"added", "modified", "removed"}:
        raise StoreError("Invalid normalized synchronization changes.")
    if any(type(changes[key]) is not list for key in changes) or type(accounts) is not list:
        raise StoreError("Invalid normalized synchronization records.")
    if cursor is not None:
        bank_identifier(cursor, limit=4096)
    provider = item.get("provider", "plaid")

    def owned_transaction(transaction):
        account = state["accounts"].get(transaction.get("original", {}).get("accountId", transaction["accountId"]))
        return account is not None and account.get("itemId") == item_id and transaction["source"] == provider

    for account in state["accounts"].values():
        if account.get("itemId") == item_id:
            account["stale"] = True
    for record in accounts:
        normalized = imported_account(record)
        if normalized["itemId"] != item_id or normalized["source"] != provider:
            raise StoreError("Bank account belongs to another connection.")
        previous = state["accounts"].get(normalized["id"])
        if previous and previous.get("itemId") != item_id:
            raise StoreError("Bank account belongs to another connection.")
        if previous and previous.get("currency", "USD") != normalized["currency"]:
            raise StoreError("An existing account currency cannot change.")
        if previous:
            for field in ("type", "onBudget", "subtype", "bankWebsite", "nameOverridden"):
                if field in previous:
                    normalized[field] = previous[field]
            if previous.get("nameOverridden"):
                normalized["name"] = previous["name"]
        state["accounts"][normalized["id"]] = normalized
    for record in changes["added"] + changes["modified"]:
        validate_imported_transaction(record)
        account = state["accounts"].get(record["accountId"])
        if account is None:
            raise StoreError("Bank account data missing.")
        if account.get("itemId") != item_id or record["source"] != provider:
            raise StoreError("Bank transaction belongs to another connection.")
        identifier = record["id"]
        existing = state["transactions"].get(identifier)
        pending = state["transactions"].get(record.get("pendingTransactionId"))
        if any(not owned_transaction(previous) for previous in (existing, pending) if previous is not None):
            raise StoreError("Bank transaction belongs to another connection.")
        annotations = existing or pending or {}
        normalized = annotated_transaction(record, annotations)
        category = normalized["categoryId"]
        if category is not None:
            try:
                child = ensure_child_category(state["categories"], category)
                if child["currency"] != account["currency"]:
                    raise StoreError("Category currency must match the account currency.")
            except StoreError:
                if annotations.get("categoryEdited"):
                    raise
                normalized["categoryId"] = None
        state["transactions"][identifier] = normalized
        if pending is not None and pending["id"] != identifier:
            image = state["receipts"].pop(pending["id"], None)
            if image is not None:
                state["receipts"].setdefault(identifier, image)
    for identifier in changes["removed"]:
        bank_identifier(identifier)
        transaction = state["transactions"].get(identifier)
        if transaction is None:
            continue
        if not owned_transaction(transaction):
            raise StoreError("Bank transaction does not belong to this connection.")
        transaction["deleted"] = True
    item.update(cursor=cursor, lastSynced=synced_at, status="connected", error=None)
    return {kind: len(changes[kind]) for kind in ("added", "modified", "removed")}
