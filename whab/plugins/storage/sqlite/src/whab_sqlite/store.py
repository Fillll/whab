"""SQLite plugin backend preserving the transactional legacy household ledger schema."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from whab.domain import commands
from whab.domain.budget import calculate_workspace
from whab.domain.categories import create_category, ensure_child_category, public_categories
from whab.domain.dates import current_month
from whab.domain.errors import StoreError
from whab.domain.file_imports import WorkspaceImport, validate_workspace_import
from whab.domain.imports import (
    ImportedAccount,
    SyncChanges,
    annotated_transaction,
    bank_identifier,
    imported_account,
    validate_imported_transaction,
)
from whab.domain.portable import (
    ACCOUNT_FIELDS,
    CATEGORY_FIELDS,
    TRANSACTION_FIELDS,
    validate_portable_budget,
)
from whab.domain.transactions import UNSET, transaction_rows
from whab.domain.validation import validate_label

from .schema import initialize_schema
from .seeding import build_initial_state

__all__ = ["Store"]


class Store:
    def __init__(
        self, database_path: str | Path = "data/whab-sandbox.sqlite", *, month: str | None = None
    ):
        self._lock = RLock()
        self.database_path = str(database_path)
        os.umask(0o077)
        if self.database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(
            self.database_path, timeout=10, isolation_level=None, check_same_thread=False
        )
        self.db.row_factory = sqlite3.Row
        if self.database_path != ":memory:":
            os.chmod(self.database_path, 0o600)
        selected_month = month or current_month()
        initialize_schema(self.db, selected_month)
        for workspace in ("demo", "personal"):
            with self._lock:
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    if not self.db.execute(
                        "SELECT 1 FROM meta WHERE workspace=?", (workspace,)
                    ).fetchone():
                        self._write_state(build_initial_state(workspace, selected_month))
                    self.db.execute("COMMIT")
                except Exception:
                    self.db.execute("ROLLBACK")
                    raise

    def close(self) -> None:
        with self._lock:
            self.db.close()

    @contextmanager
    def _snapshot(self):
        with self._lock:
            # A read transaction makes the account/ledger/cursor snapshot coherent
            # even if another process commits a sync between these SELECTs.
            own_transaction = not self.db.in_transaction
            if own_transaction:
                self.db.execute("BEGIN")
            try:
                yield
                if own_transaction:
                    self.db.execute("COMMIT")
            except Exception:
                if own_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def state(self, workspace: str) -> dict:
        if not isinstance(workspace, str) or workspace not in {"demo", "personal"}:
            raise StoreError("Invalid workspace.")
        with self._snapshot():
            return self._read_state(workspace)

    def _read_state(self, workspace: str) -> dict:
        meta = self.db.execute("SELECT * FROM meta WHERE workspace=?", (workspace,)).fetchone()
        if not meta:
            raise StoreError("Workspace not found.")
        state = {
            "_id": workspace,
            "budgetStartMonth": meta["budget_start_month"],
            "accounts": {},
            "categories": {},
            "transactions": {},
            "budget": {},
            "items": {},
        }
        for row in self.db.execute(
            "SELECT * FROM accounts WHERE workspace=? ORDER BY rowid", (workspace,)
        ):
            account = {
                "id": row["id"],
                "name": row["name"],
                "institution": row["institution"],
                "type": row["type"],
                "balance": row["balance"],
                "currency": row["currency"],
                "source": row["source"],
                "stale": not bool(row["active"]),
                "balanceUnknown": bool(row["balance_unknown"]),
            }
            if row["item_id"]:
                account["itemId"] = row["item_id"]
            if row["mask"]:
                account["mask"] = row["mask"]
            state["accounts"][row["id"]] = account
        for row in self.db.execute(
            "SELECT * FROM categories WHERE workspace=? ORDER BY rowid", (workspace,)
        ):
            state["categories"][row["id"]] = {
                "id": row["id"],
                "name": row["name"],
                "group": row["group_name"],
                "target": row["target"],
                "currency": row["currency"],
            }
        for row in self.db.execute(
            "SELECT * FROM transactions WHERE workspace=? ORDER BY rowid", (workspace,)
        ):
            state["transactions"][row["id"]] = {
                "id": row["id"],
                "accountId": row["account_id"],
                "date": row["date"],
                "merchant": row["merchant"],
                "merchantLogo": row["merchant_logo"],
                "amount": row["amount"],
                "categoryId": row["category_id"],
                "pending": bool(row["pending"]),
                "reviewed": bool(row["reviewed"]),
                "source": row["source"],
                "note": row["note"],
                "isTransfer": bool(row["is_transfer"]),
                "inferredTransfer": bool(row["inferred_transfer"]),
                "categoryEdited": bool(row["category_edited"]),
                "deleted": bool(row["deleted"]),
            }
        for row in self.db.execute("SELECT * FROM budget WHERE workspace=?", (workspace,)):
            state["budget"].setdefault(row["month"], {})[row["category_id"]] = row["assigned"]
        if workspace == "personal":
            for row in self.db.execute("SELECT * FROM items"):
                state["items"][row["id"]] = {
                    "id": row["id"],
                    "token": row["token"],
                    "institutionName": row["institution_name"],
                    "status": row["status"],
                    "lastSynced": row["last_synced"],
                    "error": row["error"],
                    "cursor": row["cursor"],
                    "provider": row["provider"],
                }
        return state

    def _write_state(self, state: dict) -> None:
        workspace = state["_id"]
        self.db.execute(
            "INSERT INTO meta(workspace,budget_start_month) VALUES(?,?) ON CONFLICT(workspace) DO UPDATE SET budget_start_month=excluded.budget_start_month",
            (workspace, state["budgetStartMonth"]),
        )
        self.db.executemany(
            "INSERT INTO accounts(id,workspace,name,institution,type,balance,source,item_id,mask,active,balance_unknown,currency) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET name=excluded.name,institution=excluded.institution,type=excluded.type,balance=excluded.balance,source=excluded.source,item_id=excluded.item_id,mask=excluded.mask,active=excluded.active,balance_unknown=excluded.balance_unknown,currency=excluded.currency",
            [
                (
                    a["id"],
                    workspace,
                    a["name"],
                    a["institution"],
                    a["type"],
                    a["balance"],
                    a["source"],
                    a.get("itemId"),
                    a.get("mask"),
                    int(not a.get("stale")),
                    int(a.get("balanceUnknown", False)),
                    a.get("currency", "USD"),
                )
                for a in state["accounts"].values()
            ],
        )
        self.db.executemany(
            "INSERT INTO categories(id,workspace,name,group_name,target,currency) VALUES(?,?,?,?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET name=excluded.name,group_name=excluded.group_name,target=excluded.target,currency=excluded.currency",
            [
                (c["id"], workspace, c["name"], c["group"], c["target"], c.get("currency", "USD"))
                for c in state["categories"].values()
            ],
        )
        self.db.executemany(
            "INSERT INTO transactions(id,workspace,account_id,date,merchant,amount,category_id,pending,reviewed,source,note,is_transfer,category_edited,deleted,inferred_transfer,merchant_logo) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET account_id=excluded.account_id,date=excluded.date,merchant=excluded.merchant,amount=excluded.amount,category_id=excluded.category_id,pending=excluded.pending,reviewed=excluded.reviewed,source=excluded.source,note=excluded.note,is_transfer=excluded.is_transfer,category_edited=excluded.category_edited,deleted=excluded.deleted,inferred_transfer=excluded.inferred_transfer,merchant_logo=excluded.merchant_logo",
            [
                (
                    t["id"],
                    workspace,
                    t["accountId"],
                    t["date"],
                    t["merchant"],
                    t["amount"],
                    t.get("categoryId"),
                    int(t.get("pending", False)),
                    int(t.get("reviewed", False)),
                    t["source"],
                    t.get("note", ""),
                    int(t.get("isTransfer", False)),
                    int(t.get("categoryEdited", False)),
                    int(t.get("deleted", False)),
                    int(t.get("inferredTransfer", False)),
                    t.get("merchantLogo"),
                )
                for t in state["transactions"].values()
            ],
        )
        self.db.executemany(
            "INSERT INTO budget(workspace,month,category_id,assigned) VALUES(?,?,?,?) ON CONFLICT(workspace,month,category_id) DO UPDATE SET assigned=excluded.assigned",
            [
                (workspace, month, category, assigned)
                for month, rows in state["budget"].items()
                for category, assigned in rows.items()
            ],
        )
        if workspace == "personal":
            self.db.executemany(
                "INSERT INTO items(id,token,institution_name,status,last_synced,error,cursor,provider) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET token=excluded.token,institution_name=excluded.institution_name,status=excluded.status,last_synced=excluded.last_synced,error=excluded.error,cursor=excluded.cursor,provider=excluded.provider",
                [
                    (
                        item["id"],
                        item.get("token"),
                        item["institutionName"],
                        item["status"],
                        item.get("lastSynced"),
                        item.get("error"),
                        item.get("cursor"),
                        item.get("provider", "plaid"),
                    )
                    for item in state["items"].values()
                ],
            )

    def _mutate(
        self,
        workspace: str,
        operation: Callable[[dict], Any],
        *,
        write_if: Callable[[Any], bool] | None = None,
    ) -> Any:
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                state = self.state(workspace)
                result = operation(state)
                if write_if is None or write_if(result):
                    self._write_state(state)
                self.db.execute("COMMIT")
                return result
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def import_workspace(
        self, workspace: str, plugin_id: str, fingerprint: str, data: WorkspaceImport
    ) -> dict:
        """Atomically initialize an empty personal ledger from a validated file."""
        self._validate_import_identifiers(workspace, plugin_id, fingerprint)

        def operation(state):
            imported = validate_workspace_import(data)
            receipt = self._workspace_import_receipt(state, plugin_id, fingerprint)
            if receipt:
                return {
                    "counts": {
                        "accounts": receipt["account_count"],
                        "categories": receipt["category_count"],
                        "transactions": receipt["transaction_count"],
                        "assignments": receipt["assignment_count"],
                    },
                    "alreadyImported": True,
                }
            counts = {
                "accounts": len(imported.accounts),
                "categories": len(imported.categories),
                "transactions": len(imported.transactions),
                "assignments": len(imported.assignments),
            }
            self.db.execute("DELETE FROM categories WHERE workspace=?", (workspace,))
            state["accounts"] = {
                record["id"]: {**record, "currency": imported.currency}
                for record in imported.accounts
            }
            state["categories"] = {
                record["id"]: {**record, "currency": imported.currency}
                for record in imported.categories
            }
            state["transactions"] = {record["id"]: record for record in imported.transactions}
            state["budget"] = {}
            for assignment in imported.assignments:
                state["budget"].setdefault(assignment["month"], {})[assignment["categoryId"]] = (
                    assignment["assigned"]
                )
            state["budgetStartMonth"] = imported.budget_start_month
            self.db.execute(
                "INSERT INTO workspace_imports(workspace,plugin_id,fingerprint,account_count,category_count,transaction_count,assignment_count) VALUES(?,?,?,?,?,?,?)",
                (workspace, plugin_id, fingerprint, *counts.values()),
            )
            return {"counts": counts, "alreadyImported": False}

        return self._mutate(
            workspace, operation, write_if=lambda result: not result["alreadyImported"]
        )

    @staticmethod
    def _validate_import_identifiers(workspace: str, plugin_id: str, fingerprint: str) -> None:
        if workspace != "personal":
            raise StoreError("Workspace imports require the personal workspace.")
        bank_identifier(plugin_id, limit=64)
        bank_identifier(fingerprint)

    def _workspace_import_receipt(
        self, state: dict, plugin_id: str, fingerprint: str
    ) -> sqlite3.Row | None:
        receipt = self.db.execute(
            "SELECT * FROM workspace_imports WHERE workspace=?", (state["_id"],)
        ).fetchone()
        if receipt:
            if receipt["plugin_id"] != plugin_id or receipt["fingerprint"] != fingerprint:
                raise StoreError("This workspace has already received a different file import.")
            return receipt
        if any(state[key] for key in ("accounts", "transactions", "budget", "items")):
            raise StoreError("File imports require an empty personal workspace.")
        templates = build_initial_state("personal", state["budgetStartMonth"])["categories"]
        if state["categories"] and state["categories"] != templates:
            raise StoreError("File imports require untouched template categories.")
        return None

    def preview_workspace_import(self, workspace: str, plugin_id: str, fingerprint: str) -> dict:
        """Report eligibility from one read snapshot without changing the ledger."""
        try:
            self._validate_import_identifiers(workspace, plugin_id, fingerprint)
            with self._snapshot():
                state = self._read_state(workspace)
                receipt = self._workspace_import_receipt(state, plugin_id, fingerprint)
            return {"canImport": True, "reason": "", "alreadyImported": receipt is not None}
        except StoreError as error:
            return {"canImport": False, "reason": str(error), "alreadyImported": False}

    def accounts(self, workspace: str) -> list[dict]:
        return list(self.state(workspace)["accounts"].values())

    def export_budget(self, workspace: str = "personal") -> dict:
        """Export one coherent ledger, excluding every provider connection field."""
        state = self.state(workspace)
        return validate_portable_budget({
            "formatVersion": 1,
            "budgetStartMonth": state["budgetStartMonth"],
            "accounts": [
                {key: record[key] for key in ACCOUNT_FIELDS}
                for record in state["accounts"].values()
            ],
            "categories": [
                {key: record[key] for key in CATEGORY_FIELDS}
                for record in state["categories"].values()
            ],
            "transactions": [
                {key: record[key] for key in TRANSACTION_FIELDS}
                for record in state["transactions"].values()
            ],
            "assignments": [
                {"month": month, "categoryId": category, "assigned": assigned}
                for month, rows in state["budget"].items()
                for category, assigned in rows.items()
            ],
        })

    def restore_budget(self, workspace: str, data: dict) -> None:
        """Explicitly initialize a pristine personal ledger; never replace saved data."""
        if workspace != "personal":
            raise StoreError("Portable budgets require the personal workspace.")

        def operation(state):
            restored = validate_portable_budget(data)
            receipt = self._workspace_import_receipt(state, "portable-budget", "restore")
            if receipt is not None:
                raise StoreError("Portable budgets require an empty personal workspace.")
            self.db.execute("DELETE FROM categories WHERE workspace=?", (workspace,))
            state["accounts"] = {
                record["id"]: {
                    **record,
                    "stale": record["stale"] or record["source"] not in {"manual", "demo"},
                }
                for record in restored["accounts"]
            }
            state["categories"] = {record["id"]: record for record in restored["categories"]}
            state["transactions"] = {record["id"]: record for record in restored["transactions"]}
            state["budget"] = {}
            for assignment in restored["assignments"]:
                state["budget"].setdefault(assignment["month"], {})[assignment["categoryId"]] = (
                    assignment["assigned"]
                )
            state["budgetStartMonth"] = restored["budgetStartMonth"]

        self._mutate(workspace, operation)

    def categories(self, workspace: str) -> list[dict]:
        return public_categories(self.state(workspace)["categories"])

    def transactions(self, workspace: str, month: str | None = None) -> list[dict]:
        return transaction_rows(self.state(workspace), month)

    def workspace(self, workspace: str, month: str | None = None) -> dict:
        return calculate_workspace(self.state(workspace), month or current_month())

    def add_account(self, workspace: str, account: dict) -> str:
        account_id = str(uuid4())
        return self._mutate(workspace, lambda state: commands.add_account(state, account_id, account))

    def add_category(self, workspace: str, category: dict) -> str:
        category_id = str(uuid4())
        if set(category) - {"name", "group", "target", "parentId", "currency"}:
            raise StoreError("Categories support exactly two levels.")

        def operation(state):
            state["categories"][category_id] = create_category(
                category_id,
                category["name"],
                category.get("group"),
                category.get("target", 0),
                categories=state["categories"],
                parent_id=category.get("parentId"),
                currency=category.get("currency", "USD"),
            )
            return category_id

        return self._mutate(workspace, operation)

    def add_transaction(self, workspace: str, transaction: dict, source: str = "manual") -> str:
        transaction_id = transaction.get("id") or str(uuid4())
        return self._mutate(
            workspace, lambda state: commands.add_transaction(state, transaction_id, transaction, source),
        )

    def edit_transaction(
        self,
        workspace: str,
        transaction_id: str,
        category_id: Any = UNSET,
        reviewed: bool | None = None,
    ) -> None:
        self._mutate(
            workspace, lambda state: commands.edit_transaction(state, transaction_id, category_id, reviewed),
        )

    def assign(self, workspace: str, category_id: str, month: str, assigned: int) -> None:
        self._mutate(
            workspace,
            lambda state: commands.assign(state, category_id, month, assigned, as_of_month=current_month()),
        )

    def items(self) -> list[dict]:
        return [
            {
                key: item.get(key)
                for key in ("id", "institutionName", "status", "lastSynced", "error")
            }
            for item in self.state("personal")["items"].values()
            if item.get("token")
        ]

    def get_item(self, item_id: str) -> dict | None:
        return self.state("personal")["items"].get(item_id)

    def save_item(
        self,
        item_id: str,
        encrypted_token: str,
        institution_name: str,
        *,
        provider_id: str = "plaid",
    ) -> None:
        def operation(state):
            previous = state["items"].get(item_id, {})
            if previous and previous.get("provider", "plaid") != provider_id:
                raise StoreError("Connection belongs to another bank provider.")
            state["items"][item_id] = {
                "id": item_id,
                "token": encrypted_token,
                "institutionName": validate_label(institution_name),
                "status": "connected",
                "lastSynced": previous.get("lastSynced"),
                "error": None,
                "cursor": previous.get("cursor"),
                "provider": provider_id,
            }

        self._mutate("personal", operation)

    def set_item_status(self, item_id: str, status: str, error: str | None) -> None:
        def operation(state):
            item = state["items"].get(item_id)
            if item and item.get("token"):
                item.update(status=status, error=error)

        self._mutate("personal", operation)

    def disconnect_item(self, item_id: str) -> None:
        def operation(state):
            item = state["items"].get(item_id)
            if not item or not item.get("token"):
                raise StoreError("Connection not found.")
            item.update(token=None, cursor=None, status="disconnected", error=None)
            for account in state["accounts"].values():
                if account.get("itemId") == item_id:
                    account["stale"] = True

        self._mutate("personal", operation)

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
        def operation(state):
            item = state["items"].get(item_id)
            if not item or not item.get("token"):
                raise StoreError("Connection not found.")
            if expected_cursor is not UNSET and item.get("cursor") != expected_cursor:
                raise StoreError("Sync cursor changed. Please retry.")
            provider = item.get("provider", "plaid")

            def owned_transaction(transaction: dict) -> bool:
                account = state["accounts"].get(transaction["accountId"])
                return (
                    account is not None
                    and account.get("itemId") == item_id
                    and transaction["source"] == provider
                )

            for account in state["accounts"].values():
                if account.get("itemId") == item_id:
                    account["stale"] = True
            for account in accounts or []:
                normalized_account = imported_account(account)
                if (
                    normalized_account["itemId"] != item_id
                    or normalized_account["source"] != provider
                ):
                    raise StoreError("Bank account belongs to another connection.")
                previous_account = state["accounts"].get(normalized_account["id"])
                if previous_account and previous_account.get("itemId") != item_id:
                    raise StoreError("Bank account belongs to another connection.")
                if (
                    previous_account
                    and previous_account.get("currency", "USD") != normalized_account["currency"]
                ):
                    raise StoreError("An existing account currency cannot change.")
                state["accounts"][normalized_account["id"]] = normalized_account
            for imported in changes.get("added", []) + changes.get("modified", []):
                validate_imported_transaction(imported)
                account = state["accounts"].get(imported["accountId"])
                if account is None:
                    raise StoreError("Bank account data missing.")
                if account.get("itemId") != item_id or imported["source"] != provider:
                    raise StoreError("Bank transaction belongs to another connection.")
                transaction_id = imported["id"]
                existing = state["transactions"].get(transaction_id)
                pending = state["transactions"].get(imported.get("pendingTransactionId"))
                if any(
                    not owned_transaction(previous)
                    for previous in (existing, pending)
                    if previous is not None
                ):
                    raise StoreError("Bank transaction belongs to another connection.")
                annotations = existing or pending or {}
                normalized_transaction = annotated_transaction(imported, annotations)
                category_id = normalized_transaction["categoryId"]
                if category_id is not None:
                    try:
                        child = ensure_child_category(state["categories"], category_id)
                        if child.get("currency", "USD") != account.get("currency", "USD"):
                            raise StoreError("Category currency must match the account currency.")
                    except StoreError:
                        if annotations.get("categoryEdited"):
                            raise
                        # A provider suggestion is optional and cannot cross currencies
                        # or introduce a parent/dangling category reference.
                        normalized_transaction["categoryId"] = None
                state["transactions"][transaction_id] = normalized_transaction
            for removed_id in changes.get("removed", []):
                bank_identifier(removed_id)
                transaction = state["transactions"].get(removed_id)
                if transaction is None:
                    continue  # Already absent tombstones remain harmless and replayable.
                if not owned_transaction(transaction):
                    raise StoreError("Bank transaction does not belong to this connection.")
                transaction["deleted"] = True
            item.update(
                cursor=cursor,
                lastSynced=datetime.now(timezone.utc).isoformat(),
                status="connected",
                error=None,
            )
            return {kind: len(changes.get(kind, [])) for kind in ("added", "modified", "removed")}

        return self._mutate("personal", operation)
