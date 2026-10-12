"""Local household ledger using SQLite and atomic ledger/cursor commits.

The schema remains compatible with the original IHAB prototype. All changes
commit under BEGIN IMMEDIATE; user edits cannot be lost while a sync publishes.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

MAX_CENTS = 1_000_000_000_000
MAX_SAFE_INTEGER = 9_007_199_254_740_991
UNSET = object()
CATEGORY_TEMPLATE = [
    ("Rent", "Home", 260000),
    ("Groceries", "Everyday", 85000),
    ("Dining", "Everyday", 35000),
    ("Transport", "Everyday", 25000),
    ("Utilities", "Home", 30000),
    ("Health", "Life", 20000),
    ("Fun", "Life", 20000),
    ("Travel", "Goals", 50000),
    ("Shopping", "Life", 25000),
]


class StoreError(ValueError):
    """Safe, intentionally authored error suitable for the local API."""


def today() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def current_month() -> str:
    return today()[:7]


def shift_month(month: str, offset: int) -> str:
    year, number = map(int, month.split("-"))
    serial = year * 12 + number - 1 + offset
    return f"{serial // 12:04d}-{serial % 12 + 1:02d}"


def cents(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or abs(value) > MAX_CENTS:
        raise StoreError("Money must be integer USD cents within the supported range.")
    return value


def dollars_to_cents(value: Any) -> int:
    try:
        decimal = Decimal(str(value))
        if not decimal.is_finite():
            raise ValueError
        return cents(int((decimal * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
    except (ValueError, TypeError, ArithmeticError):
        raise StoreError("Invalid bank amount.") from None


def transfer_category(transaction: dict) -> bool:
    category = transaction.get("personal_finance_category") or {}
    return (
        category.get("primary") in {"TRANSFER_IN", "TRANSFER_OUT"}
        or category.get("detailed") == "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"
    )


def suggest_category(transaction: dict) -> str | None:
    category = transaction.get("personal_finance_category") or {}
    primary, detailed = category.get("primary", ""), category.get("detailed", "")
    if transfer_category(transaction) or primary == "INCOME":
        return None
    for keyword, result in [
        ("GROCERIES", "groceries"),
        ("FOOD_AND_DRINK", "dining"),
        ("RENT", "rent"),
        ("UTILITIES", "utilities"),
        ("TRANSPORTATION", "transport"),
        ("MEDICAL", "health"),
        ("ENTERTAINMENT", "fun"),
        ("TRAVEL", "travel"),
        ("GENERAL_MERCHANDISE", "shopping"),
    ]:
        if keyword in primary or keyword in detailed:
            return result
    return None


def _date(value: str) -> None:
    try:
        if (
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
            or datetime.strptime(value, "%Y-%m-%d").date().isoformat() != value
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise StoreError("Invalid date.") from None


def _month(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(
        r"(?:19|20|21|22)\d{2}-(?:0[1-9]|1[0-2])", value
    ):
        raise StoreError("Invalid month.")


def _label(value: str, limit: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise StoreError("Invalid name.")
    return value.strip()


def _new_state(workspace: str, month: str) -> dict:
    state = {
        "_id": workspace,
        "revision": 0,
        "schemaVersion": 1,
        "budgetStartMonth": shift_month(month, -5) if workspace == "demo" else month,
        "accounts": {},
        "categories": {},
        "transactions": {},
        "budget": {},
        "items": {},
    }
    state["categories"] = {
        name.lower(): {"id": name.lower(), "name": name, "group": group, "target": target}
        for name, group, target in CATEGORY_TEMPLATE
    }
    if workspace != "demo":
        return state
    for account_id, name, kind, balance, mask in [
        ("checking", "Everyday checking", "depository", 700000, "4821"),
        ("savings", "Family savings", "depository", 240000, "9012"),
        ("credit", "Rewards card", "credit", 0, "3389"),
    ]:
        state["accounts"][account_id] = {
            "id": account_id,
            "name": name,
            "institution": "Demo Bank",
            "type": kind,
            "balance": balance,
            "source": "demo",
            "mask": mask,
            "stale": False,
        }
    merchants = [
        ("rent", "Monthly rent", 260000),
        ("groceries", "Whole Foods Market", 62000),
        ("dining", "The neighborhood café", 23500),
        ("transport", "BART & gas", 14500),
        ("utilities", "Power and internet", 27500),
        ("health", "Family pharmacy", 13000),
        ("fun", "Movie night", 8200),
        ("shopping", "Family essentials", 18000),
    ]
    for offset in range(-5, 1):
        seed_month = shift_month(month, offset)
        state["budget"][seed_month] = {
            name.lower(): target for name, _, target in CATEGORY_TEMPLATE
        }
        rows = [(f"salary-{seed_month}", "Family paychecks", -820000, None, 1)]
        rows += [
            (
                f"{category}-{seed_month}",
                merchant,
                amount
                if offset == 0
                else int(Decimal(amount) * (Decimal(1) + Decimal(offset) * Decimal(".025"))),
                category,
                min(index + 2, int(today()[8:]) if offset == 0 else 20),
            )
            for index, (category, merchant, amount) in enumerate(merchants)
        ]
        for transaction_id, merchant, amount, category, day in rows:
            state["transactions"][transaction_id] = {
                "id": transaction_id,
                "accountId": "checking",
                "date": f"{seed_month}-{day:02d}",
                "merchant": merchant,
                "amount": amount,
                "categoryId": category,
                "pending": False,
                "reviewed": True,
                "source": "demo",
                "note": "",
                "isTransfer": False,
                "inferredTransfer": False,
                "categoryEdited": False,
                "deleted": False,
            }
            state["accounts"]["checking"]["balance"] -= amount
    state["transactions"]["pending-demo"] = {
        "id": "pending-demo",
        "accountId": "credit",
        "date": today(),
        "merchant": "Weekend groceries",
        "amount": 6425,
        "categoryId": "groceries",
        "pending": True,
        "reviewed": False,
        "source": "demo",
        "note": "",
        "isTransfer": False,
        "inferredTransfer": False,
        "categoryEdited": False,
        "deleted": False,
    }
    return state


class Store:
    def __init__(
        self, database_path: str | Path = "data/ihab-sandbox.sqlite", *, month: str | None = None
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
        self.db.executescript("""
            PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS meta(workspace TEXT PRIMARY KEY,opening_cash INTEGER NOT NULL DEFAULT 0,baseline_ready INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS accounts(id TEXT NOT NULL,workspace TEXT NOT NULL,name TEXT NOT NULL,institution TEXT NOT NULL,type TEXT NOT NULL,balance INTEGER NOT NULL,source TEXT NOT NULL,item_id TEXT,mask TEXT,PRIMARY KEY(workspace,id));
            CREATE TABLE IF NOT EXISTS categories(id TEXT NOT NULL,workspace TEXT NOT NULL,name TEXT NOT NULL,group_name TEXT NOT NULL,target INTEGER NOT NULL,PRIMARY KEY(workspace,id));
            CREATE TABLE IF NOT EXISTS transactions(id TEXT NOT NULL,workspace TEXT NOT NULL,account_id TEXT NOT NULL,date TEXT NOT NULL,merchant TEXT NOT NULL,amount INTEGER NOT NULL,category_id TEXT,pending INTEGER NOT NULL DEFAULT 0,reviewed INTEGER NOT NULL DEFAULT 0,source TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',is_transfer INTEGER NOT NULL DEFAULT 0,category_edited INTEGER NOT NULL DEFAULT 0,deleted INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(workspace,id));
            CREATE TABLE IF NOT EXISTS budget(workspace TEXT NOT NULL,month TEXT NOT NULL,category_id TEXT NOT NULL,assigned INTEGER NOT NULL,PRIMARY KEY(workspace,month,category_id));
            CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY,token TEXT,institution_name TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'connected',last_synced TEXT,error TEXT,cursor TEXT,baseline_ready INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS tx_month ON transactions(workspace,date);
        """)
        migrations = {
            "accounts": {
                "active": "INTEGER NOT NULL DEFAULT 1",
                "balance_unknown": "INTEGER NOT NULL DEFAULT 0",
            },
            "transactions": {"inferred_transfer": "INTEGER NOT NULL DEFAULT 0"},
            "meta": {"budget_start_month": "TEXT"},
        }
        for table, columns in migrations.items():
            present = {row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")}
            for name, declaration in columns.items():
                if name not in present:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
                    if name == "inferred_transfer":
                        self.db.execute(
                            "UPDATE transactions SET inferred_transfer=is_transfer WHERE source='plaid' AND category_edited=0"
                        )
        selected_month = month or current_month()
        _month(selected_month)
        self.db.execute(
            "UPDATE meta SET budget_start_month=CASE WHEN workspace='demo' THEN COALESCE((SELECT MIN(substr(date,1,7)) FROM transactions WHERE workspace='demo'),?) ELSE COALESCE((SELECT MIN(month) FROM budget WHERE workspace='personal'),?) END WHERE budget_start_month IS NULL",
            (shift_month(selected_month, -5), selected_month),
        )
        for workspace in ("demo", "personal"):
            with self._lock:
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    if not self.db.execute(
                        "SELECT 1 FROM meta WHERE workspace=?", (workspace,)
                    ).fetchone():
                        self._write_state(_new_state(workspace, selected_month))
                    self.db.execute("COMMIT")
                except Exception:
                    self.db.execute("ROLLBACK")
                    raise

    def close(self) -> None:
        with self._lock:
            self.db.close()

    def state(self, workspace: str) -> dict:
        if not isinstance(workspace, str) or workspace not in {"demo", "personal"}:
            raise StoreError("Invalid workspace.")
        with self._lock:
            # A read transaction makes the account/ledger/cursor snapshot coherent
            # even if another process commits a sync between these SELECTs.
            own_transaction = not self.db.in_transaction
            if own_transaction:
                self.db.execute("BEGIN")
            try:
                result = self._read_state(workspace)
                if own_transaction:
                    self.db.execute("COMMIT")
                return result
            except Exception:
                if own_transaction:
                    self.db.execute("ROLLBACK")
                raise

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
                "source": row["source"],
                "stale": row["source"] == "plaid" and not bool(row["active"]),
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
            }
        for row in self.db.execute(
            "SELECT * FROM transactions WHERE workspace=? ORDER BY rowid", (workspace,)
        ):
            state["transactions"][row["id"]] = {
                "id": row["id"],
                "accountId": row["account_id"],
                "date": row["date"],
                "merchant": row["merchant"],
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
                }
        return state

    def _write_state(self, state: dict) -> None:
        workspace = state["_id"]
        self.db.execute(
            "INSERT INTO meta(workspace,budget_start_month) VALUES(?,?) ON CONFLICT(workspace) DO UPDATE SET budget_start_month=excluded.budget_start_month",
            (workspace, state["budgetStartMonth"]),
        )
        self.db.executemany(
            "INSERT INTO accounts(id,workspace,name,institution,type,balance,source,item_id,mask,active,balance_unknown) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET name=excluded.name,institution=excluded.institution,type=excluded.type,balance=excluded.balance,source=excluded.source,item_id=excluded.item_id,mask=excluded.mask,active=excluded.active,balance_unknown=excluded.balance_unknown",
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
                )
                for a in state["accounts"].values()
            ],
        )
        self.db.executemany(
            "INSERT INTO categories(id,workspace,name,group_name,target) VALUES(?,?,?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET name=excluded.name,group_name=excluded.group_name,target=excluded.target",
            [
                (c["id"], workspace, c["name"], c["group"], c["target"])
                for c in state["categories"].values()
            ],
        )
        self.db.executemany(
            "INSERT INTO transactions(id,workspace,account_id,date,merchant,amount,category_id,pending,reviewed,source,note,is_transfer,category_edited,deleted,inferred_transfer) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET account_id=excluded.account_id,date=excluded.date,merchant=excluded.merchant,amount=excluded.amount,category_id=excluded.category_id,pending=excluded.pending,reviewed=excluded.reviewed,source=excluded.source,note=excluded.note,is_transfer=excluded.is_transfer,category_edited=excluded.category_edited,deleted=excluded.deleted,inferred_transfer=excluded.inferred_transfer",
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
                "INSERT INTO items(id,token,institution_name,status,last_synced,error,cursor) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET token=excluded.token,institution_name=excluded.institution_name,status=excluded.status,last_synced=excluded.last_synced,error=excluded.error,cursor=excluded.cursor",
                [
                    (
                        item["id"],
                        item.get("token"),
                        item["institutionName"],
                        item["status"],
                        item.get("lastSynced"),
                        item.get("error"),
                        item.get("cursor"),
                    )
                    for item in state["items"].values()
                ],
            )

    def _mutate(self, workspace: str, operation: Callable[[dict], Any]) -> Any:
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                state = self.state(workspace)
                result = operation(state)
                self._write_state(state)
                self.db.execute("COMMIT")
                return result
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    @staticmethod
    def _public_transaction(transaction: dict) -> dict:
        return {
            key: transaction.get(key)
            for key in (
                "id",
                "accountId",
                "date",
                "merchant",
                "amount",
                "categoryId",
                "pending",
                "reviewed",
                "source",
                "note",
                "isTransfer",
                "inferredTransfer",
            )
        }

    def accounts(self, workspace: str) -> list[dict]:
        return list(self.state(workspace)["accounts"].values())

    def categories(self, workspace: str) -> list[dict]:
        return list(self.state(workspace)["categories"].values())

    def transactions(self, workspace: str, month: str | None = None) -> list[dict]:
        rows = [
            self._public_transaction(t)
            for t in self.state(workspace)["transactions"].values()
            if not t.get("deleted") and (not month or t["date"].startswith(month))
        ]
        return sorted(rows, key=lambda t: (t["date"], t["id"]), reverse=True)

    @staticmethod
    def _workspace(state: dict, month: str) -> dict:
        _month(month)
        transactions = [
            Store._public_transaction(t)
            for t in state["transactions"].values()
            if not t.get("deleted")
        ]
        transactions.sort(key=lambda t: (t["date"], t["id"]), reverse=True)
        cleared = [t for t in transactions if not t["pending"] and not t["isTransfer"]]
        accounts = list(state["accounts"].values())

        def income_for(rows):
            return -sum(
                t["amount"]
                for t in rows
                if t["amount"] < 0
                and t["categoryId"] is None
                and state["accounts"].get(t["accountId"], {}).get("type") != "credit"
            )

        def spend_for(rows):
            return sum(t["amount"] for t in rows if t["amount"] > 0 or t["categoryId"] is not None)

        start = state["budgetStartMonth"]
        active = [t for t in cleared if t["date"].startswith(month)]
        prior = [t for t in cleared if start <= t["date"][:7] < month]
        budget = []
        for category in state["categories"].values():
            category_id = category["id"]
            assigned = state["budget"].get(month, {}).get(category_id, 0) if month >= start else 0
            carryover = sum(
                rows.get(category_id, 0)
                for m, rows in state["budget"].items()
                if start <= m < month
            ) - sum(t["amount"] for t in prior if t["categoryId"] == category_id)
            spent = (
                sum(t["amount"] for t in active if t["categoryId"] == category_id)
                if month >= start
                else 0
            )
            budget.append(
                {
                    "categoryId": category_id,
                    "assigned": assigned,
                    "spent": spent,
                    "carryover": carryover,
                    "available": carryover + assigned - spent,
                    "target": category["target"],
                }
            )
        later = [t for t in transactions if not t["pending"] and t["date"][:7] > month]

        def balance_at_month(account):
            return account["balance"] + (-1 if account["type"] == "credit" else 1) * sum(
                t["amount"] for t in later if t["accountId"] == account["id"]
            )

        cash = sum(
            balance_at_month(a)
            for a in accounts
            if a["type"] == "depository" and not a.get("stale")
        )
        debt = sum(max(0, balance_at_month(a)) for a in accounts if a["type"] == "credit")
        ready = cash - debt - sum(max(0, row["available"]) for row in budget)
        if any(a.get("balanceUnknown") and a["type"] == "credit" for a in accounts):
            ready = min(0, ready)
        summary = {
            "income": income_for(active),
            "spent": spend_for(active),
            "assigned": sum(b["assigned"] for b in budget),
            "available": sum(b["available"] for b in budget),
            "readyToAssign": ready,
        }
        if any(abs(value) > MAX_SAFE_INTEGER for value in summary.values()):
            raise StoreError("Ledger totals exceed the supported range.")
        trend = []
        for offset in range(-5, 1):
            m = shift_month(month, offset)
            rows = [t for t in cleared if t["date"].startswith(m)]
            trend.append({"month": m, "income": income_for(rows), "spent": spend_for(rows)})
        return {
            "month": month,
            "budgetStartMonth": start,
            "accounts": accounts,
            "categories": list(state["categories"].values()),
            "transactions": [t for t in transactions if t["date"].startswith(month)],
            "budget": budget,
            "summary": summary,
            "trend": trend,
        }

    def workspace(self, workspace: str, month: str | None = None) -> dict:
        return self._workspace(self.state(workspace), month or current_month())

    def add_account(self, workspace: str, account: dict) -> str:
        account_id = str(uuid4())
        name = _label(account["name"])
        kind, balance = account["type"], cents(account["balance"])
        if kind not in {"depository", "credit"}:
            raise StoreError("Invalid account type.")

        def operation(state):
            state["accounts"][account_id] = {
                "id": account_id,
                "name": name,
                "institution": "Manual account",
                "type": kind,
                "balance": balance,
                "source": "manual",
                "stale": False,
            }
            return account_id

        return self._mutate(workspace, operation)

    def add_category(self, workspace: str, category: dict) -> str:
        category_id = str(uuid4())
        name, group, target = (
            _label(category["name"]),
            _label(category["group"]),
            cents(category["target"]),
        )
        if target < 0:
            raise StoreError("Target cannot be negative.")

        def operation(state):
            state["categories"][category_id] = {
                "id": category_id,
                "name": name,
                "group": group,
                "target": target,
            }
            return category_id

        return self._mutate(workspace, operation)

    def add_transaction(self, workspace: str, transaction: dict, source: str = "manual") -> str:
        transaction_id = transaction.get("id") or str(uuid4())
        amount = cents(transaction["amount"])
        _date(transaction["date"])
        merchant = _label(transaction["merchant"])
        note = transaction.get("note", "")
        if not isinstance(note, str) or len(note) > 1000:
            raise StoreError("Invalid note.")

        def operation(state):
            account = state["accounts"].get(transaction["accountId"])
            if not account:
                raise StoreError("Account not found.")
            if source == "manual" and account["source"] == "plaid":
                raise StoreError(
                    "Use a manual account for manual entries; bank imports cannot yet reconcile them."
                )
            category = transaction.get("categoryId")
            if category is not None and category not in state["categories"]:
                raise StoreError("Category not found.")
            if transaction_id in state["transactions"]:
                raise StoreError("Transaction already exists.")
            state["transactions"][transaction_id] = {
                "id": transaction_id,
                "accountId": account["id"],
                "date": transaction["date"],
                "merchant": merchant,
                "amount": amount,
                "categoryId": category,
                "pending": False,
                "reviewed": source == "demo",
                "source": source,
                "note": note,
                "isTransfer": False,
                "inferredTransfer": False,
                "categoryEdited": source == "manual",
                "deleted": False,
            }
            account["balance"] = cents(
                account["balance"] + (amount if account["type"] == "credit" else -amount)
            )
            return transaction_id

        return self._mutate(workspace, operation)

    def edit_transaction(
        self,
        workspace: str,
        transaction_id: str,
        category_id: Any = UNSET,
        reviewed: bool | None = None,
    ) -> None:
        def operation(state):
            transaction = state["transactions"].get(transaction_id)
            if not transaction or transaction.get("deleted"):
                raise StoreError("Transaction not found.")
            if category_id is not UNSET:
                if category_id is not None and category_id not in state["categories"]:
                    raise StoreError("Category not found.")
                transaction.update(
                    categoryId=category_id,
                    categoryEdited=True,
                    isTransfer=False
                    if category_id is not None
                    else transaction.get("inferredTransfer", False),
                )
            if reviewed is not None:
                if not isinstance(reviewed, bool):
                    raise StoreError("Invalid review status.")
                transaction["reviewed"] = reviewed

        self._mutate(workspace, operation)

    def assign(self, workspace: str, category_id: str, month: str, assigned: int) -> None:
        _month(month)
        cents(assigned)
        if assigned < 0:
            raise StoreError("Assignment cannot be negative.")

        def operation(state):
            if category_id not in state["categories"]:
                raise StoreError("Category not found.")
            if month < state["budgetStartMonth"]:
                raise StoreError(
                    "This month is before the budget start month. Historical data remains available in analytics."
                )
            previous = state["budget"].get(month, {}).get(category_id, 0)
            as_of = min(month, current_month())
            available = self._workspace(state, as_of)["summary"]["readyToAssign"]
            future = sum(sum(rows.values()) for m, rows in state["budget"].items() if m > as_of)
            if assigned > previous and assigned - previous > available - future:
                raise StoreError("Assignment exceeds available money.")
            state["budget"].setdefault(month, {})[category_id] = assigned

        self._mutate(workspace, operation)

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

    def save_item(self, item_id: str, encrypted_token: str, institution_name: str) -> None:
        def operation(state):
            previous = state["items"].get(item_id, {})
            state["items"][item_id] = {
                "id": item_id,
                "token": encrypted_token,
                "institutionName": _label(institution_name),
                "status": "connected",
                "lastSynced": previous.get("lastSynced"),
                "error": None,
                "cursor": previous.get("cursor"),
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
        changes: dict,
        cursor: str | None,
        accounts: list[dict] | None = None,
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
            for account in state["accounts"].values():
                if account.get("itemId") == item_id:
                    account["stale"] = True
            for bank_account in accounts or []:
                balances = bank_account.get("balances") or {}
                if balances.get("iso_currency_code") not in {None, "USD"} or balances.get(
                    "unofficial_currency_code"
                ):
                    raise StoreError("Only USD bank accounts are supported.")
                value = balances.get("current")
                if value is None and bank_account.get("type") == "depository":
                    value = balances.get("available")
                unknown = value is None
                account_id = bank_account["account_id"]
                state["accounts"][account_id] = {
                    "id": account_id,
                    "name": (bank_account.get("name") or "Bank account")[:200],
                    "institution": item["institutionName"],
                    "type": bank_account.get("type", "depository"),
                    "balance": 0 if unknown else dollars_to_cents(value),
                    "source": "plaid",
                    "itemId": item_id,
                    "mask": bank_account.get("mask") or "",
                    "stale": unknown,
                    "balanceUnknown": unknown,
                }
            for imported in changes.get("added", []) + changes.get("modified", []):
                if imported["account_id"] not in state["accounts"]:
                    raise StoreError("Bank account data missing.")
                _date(imported["date"])
                transaction_id = imported["transaction_id"]
                annotations = (
                    state["transactions"].get(transaction_id)
                    or state["transactions"].get(imported.get("pending_transaction_id"))
                    or {}
                )
                category = (
                    annotations.get("categoryId")
                    if annotations.get("categoryEdited")
                    else suggest_category(imported)
                )
                inferred = transfer_category(imported)
                state["transactions"][transaction_id] = {
                    "id": transaction_id,
                    "accountId": imported["account_id"],
                    "date": imported["date"],
                    "merchant": (
                        imported.get("merchant_name") or imported.get("name") or "Bank transaction"
                    )[:200],
                    "amount": dollars_to_cents(imported["amount"]),
                    "categoryId": category,
                    "pending": bool(imported.get("pending")),
                    "reviewed": bool(annotations.get("reviewed")),
                    "source": "plaid",
                    "note": annotations.get("note", ""),
                    "isTransfer": False
                    if annotations.get("categoryEdited") and category is not None
                    else inferred,
                    "inferredTransfer": inferred,
                    "categoryEdited": bool(annotations.get("categoryEdited")),
                    "deleted": False,
                }
            for removed in changes.get("removed", []):
                transaction = state["transactions"].get(removed["transaction_id"])
                if transaction:
                    transaction["deleted"] = True
            item.update(
                cursor=cursor,
                lastSynced=datetime.now(timezone.utc).isoformat(),
                status="connected",
                error=None,
            )
            return {kind: len(changes.get(kind, [])) for kind in ("added", "modified", "removed")}

        return self._mutate("personal", operation)
