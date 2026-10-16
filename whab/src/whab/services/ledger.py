"""Validated public ledger operations, without banking credentials or transport code.

One service serializes reads and commands for a budget. Revisions are opaque,
process-specific capabilities: a restarted server rejects an old revision rather
than risking duplicate writes after losing its in-memory idempotency receipts.
"""

from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from hashlib import sha256
from json import dumps
from threading import RLock
from uuid import uuid4

from whab.domain.accounts import normalize_account
from whab.domain.errors import StoreError
from whab.domain.money import cents, currency_code
from whab.domain.portable import validate_portable_budget
from whab.domain.transactions import UNSET
from whab.domain.validation import validate_date, validate_label, validate_month


class RevisionConflict(StoreError):
    """The caller must refresh its view before submitting another command."""


class RequestConflict(StoreError):
    """An idempotency key was reused for a different command."""


def _identifier(value: object) -> None:
    if type(value) is not str or not 1 <= len(value) <= 200 or "\x00" in value:
        raise StoreError("Invalid identifier.")


def _month(value: str) -> None:
    validate_month(value)
    if not "1900-01" <= value <= "2200-12":
        raise StoreError("Month must be between 1900-01 and 2200-12.")


def _public(value):
    """Defense in depth for backend projections: never serialize private bank state."""
    private = {"token", "accesstoken", "encryptedtoken", "cursor", "encryptionkey", "items"}
    if isinstance(value, dict):
        return {
            key: _public(row) for key, row in value.items()
            if key.lower().replace("_", "") not in private
        }
    if isinstance(value, list):
        return [_public(row) for row in value]
    return value


class LedgerService:
    """The public, budget-scoped command boundary over a selected storage backend."""

    def __init__(
        self, store, *, budget_id: str = "default", revision: str | None = None,
        on_commit: Callable[[], None] | None = None, lock=None,
    ):
        _identifier(budget_id)
        self.budget_id = budget_id
        self._store = store
        self._revision = revision or str(uuid4())
        self._receipts = OrderedDict()
        self._lock = lock if lock is not None else RLock()
        self._on_commit = on_commit

    @property
    def revision(self) -> str:
        with self._lock:
            return self._revision

    def invalidate_revision(self) -> None:
        """Invalidate remote readers after a host-managed mutation under the shared lock."""
        with self._lock:
            self._revision = str(uuid4())

    def external_mutation(self, operation: Callable[[], object]):
        """Serialize trusted ingestion with public commands and invalidate stale readers.

        The host may wrap its private bank/import store operations here. Those
        capabilities never become public command names or remote methods.
        """
        with self._lock:
            result = operation()
            self._revision = str(uuid4())
            if self._on_commit is not None:
                self._on_commit()
            return result

    @staticmethod
    def _workspace(workspace: str) -> None:
        if type(workspace) is not str or workspace not in {"demo", "personal"}:
            raise StoreError("Invalid workspace.")

    def query(self, operation: str, workspace: str, month: str | None = None) -> dict:
        self._workspace(workspace)
        if month is not None:
            _month(month)
        with self._lock:
            if operation == "portable":
                exporter = getattr(self._store, "export_budget", None)
                if exporter is None:
                    raise StoreError("This storage plugin does not support portable budgets.")
                data = validate_portable_budget(exporter(workspace))
            elif operation in {"accounts", "categories"}:
                data = getattr(self._store, operation)(workspace)
            elif operation in {"transactions", "workspace"}:
                data = getattr(self._store, operation)(workspace, month)
            else:
                raise StoreError("Unknown ledger query.")
            return {"revision": self._revision, "data": _public(data)}

    @staticmethod
    def _validate(operation: str, payload: dict) -> dict:
        fields = {
            "add_account": ({"name", "type", "balance"}, {"currency", "onBudget", "subtype"}),
            "update_account": ({"accountId"}, {"type", "onBudget", "subtype"}),
            "add_category": ({"name", "target"}, {"group", "parentId", "currency"}),
            "add_transaction": (
                {"accountId", "date", "merchant", "amount", "categoryId"}, {"note"},
            ),
            "edit_transaction": ({"transactionId"}, {"categoryId", "reviewed"}),
            "assign": ({"categoryId", "month", "assigned"}, set()),
        }
        if type(operation) is not str or operation not in fields:
            raise StoreError("Unknown ledger command.")
        required, optional = fields[operation]
        if type(payload) is not dict or not required <= payload.keys() or payload.keys() - required - optional:
            raise StoreError("Invalid ledger command fields.")
        payload = deepcopy(payload)
        for name in ("name", "merchant", "group"):
            if name in payload and payload[name] is not None:
                payload[name] = validate_label(payload[name])
        for name in ("accountId", "transactionId", "parentId", "categoryId"):
            if name in payload and payload[name] is not None:
                _identifier(payload[name])
        if "currency" in payload:
            currency_code(payload["currency"])
        for name in ("balance", "target", "amount", "assigned"):
            if name in payload:
                cents(payload[name])
        for name in ("target", "assigned"):
            if name in payload and payload[name] < 0:
                raise StoreError("Targets and assignments cannot be negative.")
        if operation == "add_account":
            normalize_account(payload)
        if operation == "update_account":
            if not payload.keys() & {"type", "onBudget", "subtype"}:
                raise StoreError("No account changes provided.")
            if "type" in payload:
                normalize_account(payload)
            if "onBudget" in payload and type(payload["onBudget"]) is not bool:
                raise StoreError("Invalid account budget scope.")
            if "subtype" in payload and payload["subtype"] is not None and type(payload["subtype"]) is not str:
                raise StoreError("Invalid account subtype.")
        if operation == "add_transaction":
            validate_date(payload["date"])
            if not "1900-01-01" <= payload["date"] <= "2200-12-31":
                raise StoreError("Transaction date must be between 1900-01-01 and 2200-12-31.")
            if not payload["amount"]:
                raise StoreError("Amount must not be zero.")
            if type(payload.get("note", "")) is not str or len(payload.get("note", "")) > 1000:
                raise StoreError("Invalid note.")
        if operation == "edit_transaction":
            if not payload.keys() & {"categoryId", "reviewed"}:
                raise StoreError("No transaction changes provided.")
            if "reviewed" in payload and type(payload["reviewed"]) is not bool:
                raise StoreError("Invalid review status.")
        if "month" in payload:
            _month(payload["month"])
        return payload

    def command(
        self, operation: str, workspace: str, payload: dict, *,
        request_id: str, expected_revision: str,
    ) -> dict:
        self._workspace(workspace)
        _identifier(request_id)
        _identifier(expected_revision)
        payload = self._validate(operation, payload)
        fingerprint = sha256(dumps(
            [operation, workspace, payload, expected_revision], sort_keys=True,
        ).encode()).hexdigest()
        with self._lock:
            if request_id in self._receipts:
                previous, response = self._receipts[request_id]
                if previous != fingerprint:
                    raise RequestConflict("Request ID already belongs to another command.")
                return deepcopy(response)
            if expected_revision != self._revision:
                raise RevisionConflict("Budget changed. Refresh before making this change.")
            if operation in {"add_account", "add_category", "add_transaction"}:
                data = getattr(self._store, operation)(workspace, payload)
            elif operation == "update_account":
                update = getattr(self._store, "update_account", None)
                if not callable(update):
                    raise StoreError("This storage plugin does not support account classification changes.")
                data = update(
                    workspace, payload["accountId"],
                    {key: value for key, value in payload.items() if key != "accountId"},
                )
            elif operation == "edit_transaction":
                data = self._store.edit_transaction(
                    workspace, payload["transactionId"],
                    category_id=payload.get("categoryId", UNSET), reviewed=payload.get("reviewed"),
                )
            else:
                data = self._store.assign(
                    workspace, payload["categoryId"], payload["month"], payload["assigned"],
                )
            self._revision = str(uuid4())
            response = {"revision": self._revision, "data": data}
            self._receipts[request_id] = (fingerprint, deepcopy(response))
            if len(self._receipts) > 1024:
                self._receipts.popitem(last=False)
            if self._on_commit is not None:
                self._on_commit()
            return response

    def accounts(self, workspace):
        return self.query("accounts", workspace)["data"]

    def categories(self, workspace):
        return self.query("categories", workspace)["data"]

    def transactions(self, workspace, month=None):
        return self.query("transactions", workspace, month)["data"]

    def workspace(self, workspace, month=None):
        return self.query("workspace", workspace, month)["data"]

    def _local_command(self, operation, workspace, payload):
        with self._lock:
            return self.command(
                operation, workspace, payload, request_id=str(uuid4()),
                expected_revision=self._revision,
            )["data"]

    def add_account(self, workspace, account):
        return self._local_command("add_account", workspace, account)

    def update_account(self, workspace, account_id, changes):
        return self._local_command("update_account", workspace, {**changes, "accountId": account_id})

    def add_category(self, workspace, category):
        return self._local_command("add_category", workspace, category)

    def add_transaction(self, workspace, transaction, source="manual"):
        if source != "manual":
            raise StoreError("Public commands only create manual transactions.")
        return self._local_command("add_transaction", workspace, transaction)

    def edit_transaction(self, workspace, transaction_id, category_id=UNSET, reviewed=None):
        payload = {"transactionId": transaction_id}
        if category_id is not UNSET:
            payload["categoryId"] = category_id
        if reviewed is not None:
            payload["reviewed"] = reviewed
        return self._local_command("edit_transaction", workspace, payload)

    def assign(self, workspace, category_id, month, assigned):
        return self._local_command("assign", workspace, {
            "categoryId": category_id, "month": month, "assigned": assigned,
        })
