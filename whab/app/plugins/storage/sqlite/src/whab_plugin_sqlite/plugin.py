"""SQLite storage descriptor; backend construction occurs only in its factory."""

from contextlib import closing

from whab.domain.errors import StoreError
from whab.plugins import Capability, Plugin, PluginContract, StorageBackend, StorageContext


def create_storage(context: StorageContext) -> StorageBackend:
    import sqlite3

    from .store import Store

    directory = context.home / "data"
    path = directory / f"whab-{context.environment}.sqlite"
    existing = sorted(directory.glob(f"*-{context.environment}.sqlite"))
    if len(existing) > 1:
        raise StoreError(
            "Multiple SQLite ledgers match this environment. Keep only the intended ledger "
            "in the runtime data directory before starting WHAB."
        )
    if existing and existing[0] != path:
        path = existing[0]
        # Validate base columns before Store can apply additive migrations.
        required_columns = {
            "meta": {"workspace", "opening_cash", "baseline_ready"},
            "accounts": {
                "id",
                "workspace",
                "name",
                "institution",
                "type",
                "balance",
                "source",
                "item_id",
                "mask",
            },
            "categories": {"id", "workspace", "name", "group_name", "target"},
            "transactions": {
                "id",
                "workspace",
                "account_id",
                "date",
                "merchant",
                "amount",
                "category_id",
                "pending",
                "reviewed",
                "source",
                "note",
                "is_transfer",
                "category_edited",
                "deleted",
            },
            "budget": {"workspace", "month", "category_id", "assigned"},
            "items": {
                "id",
                "token",
                "institution_name",
                "status",
                "last_synced",
                "error",
                "cursor",
                "baseline_ready",
            },
        }
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                for table, required in required_columns.items():
                    columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                    if not required <= columns:
                        raise StoreError("The existing SQLite file is not a WHAB ledger.")
        except sqlite3.Error:
            raise StoreError(
                "The existing SQLite file could not be opened as a WHAB ledger."
            ) from None
    return Store(path, budget_id=context.budget.budget_id if context.budget is not None else "default",
                 environment=context.environment, initial_role=context.initial_role,
                 read_only=context.read_only)


plugin = Plugin(
    id="sqlite", display_name="SQLite", storage=create_storage,
    storage_payload_paths=("budget.sqlite",),
    contract=PluginContract(
        provides=tuple(Capability(name) for name in (
            "receipts", "transfer.neutral", "transfer.prepare", "storage.authority", "storage.commands", "transfer.local", "storage.replica", "storage.worker",
        )), authority="local", lifecycle="live",
    ),
)
