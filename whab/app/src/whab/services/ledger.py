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

from whab.domain.accounts import bank_website_entry, normalize_account
from whab.domain.categories import category_tree
from whab.domain.dates import shift_month
from whab.domain.errors import StoreError
from whab.domain.money import cents, currency_code, currency_minor_units
from whab.domain.portable import ACCOUNT_FIELDS, validate_portable_budget
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
            "update_account": ({"accountId"}, {"type", "onBudget", "subtype", "name", "bankWebsite", "currency"}),
            "add_category": ({"name", "target"}, {"group", "parentId", "currency"}),
            "add_transaction": (
                {"accountId", "date", "merchant", "amount", "categoryId"}, {"note"},
            ),
            "edit_transaction": ({"transactionId"}, {"categoryId", "reviewed", "note"}),
            "assign": ({"categoryId", "month", "assigned"}, set()),
            "assign_next_month": ({"month", "currency", "assigned"}, set()),
            "set_rollover_mode": ({"month", "currency", "mode"}, set()),
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
        if "note" in payload and (type(payload["note"]) is not str or len(payload["note"]) > 1000):
            raise StoreError("Invalid note.")
        if operation == "add_account":
            normalize_account(payload)
        if operation == "set_rollover_mode" and (
            type(payload["mode"]) is not str or payload["mode"] not in {"category", "balance"}
        ):
            raise StoreError("Rollover mode must be category or balance.")
        if operation == "update_account":
            if not payload.keys() & {"type", "onBudget", "subtype", "name", "bankWebsite", "currency"}:
                raise StoreError("No account changes provided.")
            if "bankWebsite" in payload:
                payload["bankWebsite"] = bank_website_entry(payload["bankWebsite"])
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
        if operation == "edit_transaction":
            if not payload.keys() & {"categoryId", "reviewed", "note"}:
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
                    **({"note": payload["note"]} if "note" in payload else {}),
                )
            elif operation == "assign_next_month":
                assign_next = getattr(self._store, "assign_next_month", None)
                if not callable(assign_next):
                    raise StoreError("This storage plugin does not support next-month funding.")
                data = assign_next(
                    workspace, payload["month"], payload["currency"], payload["assigned"],
                )
            elif operation == "set_rollover_mode":
                set_mode = getattr(self._store, "set_rollover_mode", None)
                if not callable(set_mode):
                    raise StoreError("This storage plugin does not support monthly rollover settings.")
                data = set_mode(workspace, payload["month"], payload["currency"], payload["mode"])
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

    def transaction_register(
        self, workspace: str, month: str | None, currency: str, account_id: str | None = None,
        *, account_ids: list[str] | tuple[str, ...] | None = None,
        start_date: str | None = None, end_date: str | None = None,
    ) -> dict:
        """Read a scoped register without calculating unrelated envelope histories.

        Optional backend row selection avoids loading other accounts' histories.
        Existing backends retain compatibility through their public read methods.
        Every read remains serialized with commands; no financial page cache is used.
        """
        self._workspace(workspace)
        if month is not None:
            _month(month)
        currency = currency_code(currency)
        if account_id is not None:
            _identifier(account_id)
        if account_ids is not None:
            if type(account_ids) not in (list, tuple) or len(account_ids) > 500:
                raise StoreError("Invalid account selection.")
            for identifier in account_ids:
                _identifier(identifier)
            account_ids = tuple(dict.fromkeys(account_ids))
        for value in (start_date, end_date):
            if value is not None:
                validate_date(value)
        if start_date is not None and end_date is not None and start_date > end_date:
            raise StoreError("Start date must not be after end date.")
        with self._lock:
            read = getattr(self._store, "transaction_register", None)
            filtered_read = getattr(self._store, "transaction_register_filtered", None)
            if callable(filtered_read) and (account_ids is not None or start_date is not None or end_date is not None):
                selected = account_ids
                if account_ids is None and account_id is not None:
                    known = self._store.accounts(workspace)
                    selected = (account_id,) if any(
                        row["id"] == account_id and row.get("currency", "USD") == currency for row in known
                    ) else None
                data = filtered_read(workspace, start_date, end_date, currency, selected)
            elif callable(read):
                data = read(workspace, month, currency, account_id if account_ids is None else None)
            else:
                data = {
                    "accounts": self._store.accounts(workspace),
                    "categories": self._store.categories(workspace),
                    "transactions": self._store.transactions(workspace, month),
                }
            accounts = _public(data["accounts"])
            categories = _public(data["categories"])
            currencies = sorted({"USD", currency, *(
                row.get("currency", "USD") for row in accounts + categories
            )})
            accounts = [row for row in accounts if row.get("currency", "USD") == currency]
            categories = [row for row in categories if row.get("currency", "USD") == currency]
            available_account_ids = {row["id"] for row in accounts}
            if account_id not in available_account_ids:
                account_id = None
            transactions = [
                row for row in _public(data["transactions"])
                if row["accountId"] in available_account_ids
                and (account_ids is None or row["accountId"] in account_ids)
                and (account_ids is not None or account_id is None or row["accountId"] == account_id)
                and (month is None or row["date"].startswith(month))
                and (start_date is None or row["date"] >= start_date)
                and (end_date is None or row["date"] <= end_date)
            ]
            return {
                "currency": currency, "currencyMinorUnits": currency_minor_units(currency),
                "currencies": currencies, "accounts": accounts, "categories": categories,
                "categoryGroups": category_tree(categories), "transactions": transactions,
            }

    def workspace_periods(self, workspace: str, month: str, count: int) -> list[dict]:
        """Read consecutive core projections under the budget's shared command lock.

        Backends retain the single-month workspace contract. Each projection
        therefore uses the same financial rules for local and remote storage.
        """
        self._workspace(workspace)
        _month(month)
        if type(count) is not int or not 1 <= count <= 12:
            raise StoreError("Period count must be an integer between 1 and 12.")
        year, number = map(int, month.split("-"))
        remaining = (2200 - year) * 12 + (12 - number) + 1
        count = min(count, remaining)
        with self._lock:
            return [
                _public(self._store.workspace(workspace, shift_month(month, offset)))
                for offset in range(count)
            ]

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

    def account_settings(self, workspace, account_id):
        """Return one public account and a history-aware currency edit capability."""
        _identifier(account_id)
        if callable(getattr(self._store, "export_budget", None)):
            snapshot = self.query("portable", workspace)["data"]
        else:
            # Basic metadata remains available on a backend without portable
            # history. Missing proof of emptiness never enables currency edits.
            snapshot = {"accounts": self.accounts(workspace), "transactions": None}
        account = next((row for row in snapshot["accounts"] if row["id"] == account_id), None)
        if account is None:
            raise StoreError("Account not found.")
        account = {key: value for key, value in account.items()
                   if key in ACCOUNT_FIELDS | {"bankWebsite", "nameOverridden"}}
        return {"account": account, "currencyEditable": snapshot["transactions"] is not None and not any(
            row["accountId"] == account_id for row in snapshot["transactions"]
        )}

    def add_category(self, workspace, category):
        return self._local_command("add_category", workspace, category)

    def add_transaction(self, workspace, transaction, source="manual"):
        if source != "manual":
            raise StoreError("Public commands only create manual transactions.")
        return self._local_command("add_transaction", workspace, transaction)

    def edit_transaction(self, workspace, transaction_id, category_id=UNSET, reviewed=None, note=UNSET):
        payload = {"transactionId": transaction_id}
        if category_id is not UNSET:
            payload["categoryId"] = category_id
        if reviewed is not None:
            payload["reviewed"] = reviewed
        if note is not UNSET:
            payload["note"] = note
        return self._local_command("edit_transaction", workspace, payload)

    def assign(self, workspace, category_id, month, assigned):
        return self._local_command("assign", workspace, {
            "categoryId": category_id, "month": month, "assigned": assigned,
        })

    def assign_next_month(self, workspace, month, currency, assigned):
        return self._local_command("assign_next_month", workspace, {
            "month": month, "currency": currency, "assigned": assigned,
        })

    def set_rollover_mode(self, workspace, month, currency, mode):
        return self._local_command("set_rollover_mode", workspace, {
            "month": month, "currency": currency, "mode": mode,
        })
