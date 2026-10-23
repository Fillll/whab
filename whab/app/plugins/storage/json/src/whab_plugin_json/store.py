"""Durable full-file JSON storage, independent of every other storage plugin."""

import copy
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from whab.domain import commands
from whab.domain.accounts import normalize_account
from whab.domain.bank_sync import merge_sync
from whab.domain.budget import calculate_workspace, calculate_workspace_periods
from whab.domain.categories import create_category, public_categories
from whab.domain.dates import current_month
from whab.domain.errors import StoreError
from whab.domain.file_imports import validate_workspace_import
from whab.domain.imports import bank_identifier
from whab.domain.ledger_state import (
    initial_state,
    portable_snapshot,
    require_pristine,
    restore_snapshot,
)
from whab.domain.receipts import receipt_media_type, validate_receipt_selection
from whab.domain.transactions import UNSET, transaction_rows, validate_review_batch
from whab.domain.validation import validate_label, validate_month

from . import codec
from .durability import (
    CommitUncertain,
    ExternalChange,
    WriterLock,
    ensure_durable_directory,
    fingerprint,
    flush_directory,
    read_file,
)


class Store:
    """One exclusive writer with detached validated candidates and durable evidence.

    ``commit_hook`` is a deterministic observation/fault barrier for synthetic
    tests. It receives boundary names only, never ledger records or credentials.
    Synchronous methods must be called on the host's existing blocking-I/O pool.
    """

    def __init__(self, path, *, month=None, commit_hook=None):
        self.path = Path(path).absolute()
        ensure_durable_directory(self.path.parent)
        self._lock = RLock()
        self._closed = False
        self._uncertain = None
        self._changed = False
        self._hook = commit_hook
        self._writer = WriterLock(self.path.with_name(self.path.name + ".lock"))
        try:
            raw = read_file(self.path)
            self._disk_hash = fingerprint(raw) if raw is not None else None
            self._disk_identity = self._identity() if raw is not None else None
            if raw is not None:
                self._data = codec.decode(raw)
            else:
                selected = month or current_month()
                validate_month(selected)
                self._data = {
                    "formatVersion": 1, "generation": 0, "operationId": str(uuid4()),
                    "authorityEpoch": str(uuid4()), "workspaces": {
                        name: initial_state(name, selected) for name in ("personal", "demo")
                    },
                }
                self._commit(self._data)
        except BaseException:
            self._writer.close()
            self._closed = True
            raise

    def close(self):
        with self._lock:
            if not self._closed:
                self._writer.close()
                self._closed = True

    def _boundary(self, name):
        if self._hook is not None:
            self._hook(name)

    def _identity(self):
        metadata = self.path.lstat()
        return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns

    def _check_current(self):
        if self._closed:
            raise StoreError("This JSON backend has been closed.")
        if self._uncertain is not None:
            raise CommitUncertain(*self._uncertain)
        if self._changed:
            raise ExternalChange("JSON storage changed outside this writer; preserve both revisions before reopening.")
        try:
            self._writer.check()
            raw = read_file(self.path)
            actual = fingerprint(raw) if raw is not None else None
            identity = self._identity() if raw is not None else None
            if actual != self._disk_hash or identity != self._disk_identity:
                raise ExternalChange("JSON storage changed outside this writer; preserve both revisions before reopening.")
        except (OSError, StoreError):
            self._changed = True
            raise ExternalChange("JSON storage or its lock changed outside this writer; preserve it before reopening.") from None

    def commit_evidence(self):
        """Operation/generation evidence contains no financial or credential payload."""
        with self._lock:
            if self._uncertain is not None:
                operation, generation = self._uncertain
                return {"status": "uncertain", "operationId": operation, "generation": generation,
                        "authorityEpoch": self._data["authorityEpoch"]}
            self._check_current()
            return {"status": "committed", **{
                name: self._data[name] for name in ("operationId", "generation", "authorityEpoch")
            }}

    def _commit(self, candidate):
        self._check_current()
        raw, candidate = codec.encode(candidate)
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".whab-json-", dir=self.path.parent)
        except OSError:
            raise StoreError("JSON storage could not create its candidate file; the previous generation remains authoritative.") from None
        attempted = False
        try:
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as target:
                    target.write(raw)
                    target.flush()
                    os.fsync(target.fileno())
            finally:
                os.close(descriptor)
            self._boundary("temporary_flushed")
            self._check_current()
            self._boundary("before_replace")
            self._check_current()
            attempted = True
            os.replace(temporary, self.path)
            self._boundary("replaced")
            flush_directory(self.path.parent)
            self._boundary("directory_flushed")
            self._data = candidate
            self._disk_hash = fingerprint(raw)
            self._disk_identity = self._identity()
            self._boundary("published")
        except BaseException as error:
            if attempted:
                self._uncertain = candidate["operationId"], candidate["generation"]
                if isinstance(error, Exception):
                    raise CommitUncertain(*self._uncertain) from None
            elif isinstance(error, ExternalChange):
                raise
            elif isinstance(error, Exception):
                raise StoreError("JSON storage could not save its candidate; the previous generation remains authoritative.") from None
            raise
        finally:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _workspace_name(workspace):
        if type(workspace) is not str or workspace not in {"personal", "demo"}:
            raise StoreError("Invalid workspace.")

    def _read(self, workspace):
        self._workspace_name(workspace)
        with self._lock:
            self._check_current()
            return copy.deepcopy(self._data["workspaces"][workspace])

    def _mutate(self, workspace, operation, *, write_if=None):
        self._workspace_name(workspace)
        with self._lock:
            self._check_current()
            candidate = copy.deepcopy(self._data)
            result = operation(candidate["workspaces"][workspace])
            if write_if is None or write_if(result):
                candidate["generation"] += 1
                candidate["operationId"] = str(uuid4())
                self._commit(candidate)
            return result

    def accounts(self, workspace):
        return [normalize_account({"balanceUnknown": False, "stale": False, **record})
                for record in self._read(workspace)["accounts"].values()]

    def categories(self, workspace):
        return public_categories(self._read(workspace)["categories"])

    def transactions(self, workspace, month=None):
        if month is not None:
            validate_month(month)
        return transaction_rows(self._read(workspace), month)

    def workspace(self, workspace, month=None):
        selected = month or current_month()
        validate_month(selected)
        return calculate_workspace(self._read(workspace), selected, as_of_month=current_month())

    def workspace_periods(self, workspace, month, count):
        return calculate_workspace_periods(self._read(workspace), month, count, as_of_month=current_month())

    def add_account(self, workspace, account):
        identifier = str(uuid4())
        return self._mutate(workspace, lambda state: commands.add_account(state, identifier, account))

    def update_account(self, workspace, account_id, changes):
        return self._mutate(workspace, lambda state: commands.update_account(state, account_id, changes))

    def add_category(self, workspace, category):
        if type(category) is not dict or category.keys() - {"name", "group", "target", "parentId", "currency"}:
            raise StoreError("Categories support exactly two levels.")
        identifier = str(uuid4())

        def operation(state):
            state["categories"][identifier] = create_category(
                identifier, category["name"], category.get("group"), category.get("target", 0),
                categories=state["categories"], parent_id=category.get("parentId"), currency=category.get("currency", "USD"),
            )
            return identifier

        return self._mutate(workspace, operation)

    def add_transaction(self, workspace, transaction, source="manual"):
        identifier = transaction.get("id") or str(uuid4())
        return self._mutate(workspace, lambda state: commands.add_transaction(state, identifier, transaction, source))

    def edit_transaction(self, workspace, transaction_id, category_id=UNSET, reviewed=None, note=UNSET):
        return self._mutate(workspace, lambda state: commands.edit_transaction(state, transaction_id, category_id, reviewed, note))

    def review_transactions(self, workspace, transaction_ids, reviewed):
        selected = validate_review_batch(transaction_ids, reviewed)

        def operation(state):
            for identifier in selected:
                commands.edit_transaction(state, identifier, reviewed=reviewed)

        return self._mutate(workspace, operation)

    def assign(self, workspace, category_id, month, assigned):
        return self._mutate(workspace, lambda state: commands.assign(state, category_id, month, assigned, as_of_month=current_month()))

    def assign_next_month(self, workspace, month, currency, assigned):
        return self._mutate(workspace, lambda state: commands.assign_next_month(state, month, currency, assigned, as_of_month=current_month()))

    def set_rollover_mode(self, workspace, month, currency, mode):
        return self._mutate(workspace, lambda state: commands.set_rollover_mode(state, month, currency, mode, as_of_month=current_month()))

    def items(self):
        return [{key: item[key] for key in ("id", "institutionName", "status", "lastSynced", "error")}
                for item in self._read("personal")["items"].values() if item["token"] is not None]

    def get_item(self, item_id):
        return self._read("personal")["items"].get(item_id)

    def save_item(self, item_id, encrypted_token, institution_name, *, provider_id="plaid"):
        bank_identifier(item_id, limit=4096)
        if type(provider_id) is not str or re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", provider_id) is None or len(provider_id) > 64 or provider_id in {"manual", "demo"}:
            raise StoreError("Invalid bank provider.")
        if type(encrypted_token) is not str or not encrypted_token:
            raise StoreError("Bank persistence requires an encrypted token record.")

        def operation(state):
            previous = state["items"].get(item_id, {})
            if previous and previous["provider"] != provider_id:
                raise StoreError("Connection belongs to another bank provider.")
            state["items"][item_id] = {
                "id": item_id, "token": encrypted_token, "institutionName": validate_label(institution_name),
                "status": "connected", "lastSynced": previous.get("lastSynced"), "error": None,
                "cursor": previous.get("cursor"), "provider": provider_id,
            }

        return self._mutate("personal", operation)

    def set_item_status(self, item_id, status, error):
        def operation(state):
            item = state["items"].get(item_id)
            if item is not None and item["token"] is not None:
                item.update(status=status, error=error)

        return self._mutate("personal", operation)

    def disconnect_item(self, item_id):
        def operation(state):
            item = state["items"].get(item_id)
            if item is None or item["token"] is None:
                raise StoreError("Connection not found.")
            item.update(token=None, cursor=None, status="disconnected", error=None)
            for account in state["accounts"].values():
                if account.get("itemId") == item_id:
                    account["stale"] = True

        return self._mutate("personal", operation)

    def apply_sync(self, item_id, changes, cursor, accounts=None, historical_complete=True, *, expected_cursor=UNSET):
        if type(historical_complete) is not bool:
            raise StoreError("Historical completion must be a boolean.")
        return self._mutate("personal", lambda state: merge_sync(
            state, item_id, changes, cursor, [] if accounts is None else accounts,
            expected_cursor=expected_cursor, synced_at=datetime.now(timezone.utc).isoformat(),
        ))

    @staticmethod
    def _receipt_target(state, transaction_id):
        transaction = state["transactions"].get(transaction_id)
        if transaction is None or transaction["deleted"]:
            raise StoreError("Transaction not found.")

    def receipt_transaction_exists(self, workspace, transaction_id):
        self._workspace_name(workspace)
        if type(transaction_id) is not str:
            return False
        transaction = self._read(workspace)["transactions"].get(transaction_id)
        return transaction is not None and not transaction["deleted"]

    def receipt_ids(self, workspace):
        state = self._read(workspace)
        return sorted(identifier for identifier in state["receipts"] if not state["transactions"][identifier]["deleted"])

    def receipt_ids_for(self, workspace, transaction_ids):
        selected = validate_receipt_selection(transaction_ids)
        return sorted(set(self.receipt_ids(workspace)) & set(selected))

    def get_receipt(self, workspace, transaction_id):
        state = self._read(workspace)
        self._receipt_target(state, transaction_id)
        return state["receipts"].get(transaction_id)

    def save_receipt(self, workspace, transaction_id, raw):
        receipt_media_type(raw)

        def operation(state):
            self._receipt_target(state, transaction_id)
            state["receipts"][transaction_id] = raw
            codec.export_files(state)

        return self._mutate(workspace, operation)

    def delete_receipt(self, workspace, transaction_id):
        def operation(state):
            self._receipt_target(state, transaction_id)
            state["receipts"].pop(transaction_id, None)

        return self._mutate(workspace, operation)

    def export_budget(self, workspace="personal"):
        return portable_snapshot(self._read(workspace))

    def restore_budget(self, workspace, data):
        if workspace != "personal":
            raise StoreError("Portable budgets require the personal workspace.")

        def operation(state):
            require_pristine(state)
            restore_snapshot(state, data)

        return self._mutate(workspace, operation)

    def export_budget_files(self, workspace="personal"):
        return codec.export_files(self._read(workspace))

    def restore_budget_files(self, workspace, files):
        if workspace != "personal":
            raise StoreError("Portable budgets require the personal workspace.")
        if not isinstance(files, Mapping):
            raise StoreError("JSON budget files must be a mapping.")
        restored = codec.read_files(dict(files))

        def operation(state):
            require_pristine(state)
            state.clear()
            state.update(copy.deepcopy(restored))

        return self._mutate(workspace, operation)

    def prepare_budget_files(self, workspace, files, *, existing=False):
        if workspace != "personal" or type(existing) is not bool or not isinstance(files, Mapping):
            raise StoreError("JSON budget preparation requires personal files and a boolean cache flag.")
        restored = codec.read_files(dict(files))
        with self._lock:
            current = self._read("personal")
            if existing:
                try:
                    require_pristine(current)
                except StoreError:
                    return
            if codec.equivalent(current, restored):
                return
            self.restore_budget_files(workspace, files)

    @staticmethod
    def _import_identifiers(workspace, plugin_id, fingerprint):
        if workspace != "personal":
            raise StoreError("Workspace imports require the personal workspace.")
        bank_identifier(plugin_id, limit=64)
        bank_identifier(fingerprint)

    @staticmethod
    def _import_status(state, plugin_id, fingerprint):
        previous = state["importReceipt"]
        if previous is not None:
            if previous["pluginId"] != plugin_id or previous["fingerprint"] != fingerprint:
                raise StoreError("This workspace already received a different file import.")
            return {"counts": previous["counts"].copy(), "alreadyImported": True}
        require_pristine(state)
        return None

    def preview_workspace_import(self, workspace, plugin_id, fingerprint):
        try:
            self._import_identifiers(workspace, plugin_id, fingerprint)
            previous = self._import_status(self._read(workspace), plugin_id, fingerprint)
            return {"canImport": True, "reason": "", "alreadyImported": previous is not None}
        except StoreError as error:
            return {"canImport": False, "reason": str(error), "alreadyImported": False}

    def import_workspace(self, workspace, plugin_id, fingerprint, data):
        self._import_identifiers(workspace, plugin_id, fingerprint)

        def operation(state):
            imported = validate_workspace_import(data)
            previous = self._import_status(state, plugin_id, fingerprint)
            if previous is not None:
                return previous
            counts = {key: len(getattr(imported, key)) for key in ("accounts", "categories", "transactions", "assignments")}
            restore_snapshot(state, {
                "formatVersion": 1, "budgetStartMonth": imported.budget_start_month,
                "accounts": [{**record, "currency": imported.currency} for record in imported.accounts],
                "categories": [{**record, "currency": imported.currency} for record in imported.categories],
                "transactions": [{**record, "merchantLogo": None} for record in imported.transactions],
                "assignments": list(imported.assignments),
            }, mark_external_stale=False)
            state["importReceipt"] = {"pluginId": plugin_id, "fingerprint": fingerprint, "counts": counts}
            return {"counts": counts.copy(), "alreadyImported": False}

        return self._mutate(workspace, operation, write_if=lambda result: not result["alreadyImported"])
