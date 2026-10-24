"""Explicit JSON storage selection; opening never substitutes another backend."""

from whab.plugins import Capability, Plugin, PluginContract, StorageContext


def create_storage(context: StorageContext):
    from .store import Store

    return Store(context.home / "data" / f"whab-{context.environment}.json",
                 budget_id=context.budget.budget_id if context.budget is not None else "default",
                 environment=context.environment, initial_role=context.initial_role)


plugin = Plugin(
    id="json", display_name="JSON", storage=create_storage,
    storage_payload_paths=("budget.json",),
    contract=PluginContract(
        provides=tuple(Capability(name) for name in (
            "receipts", "transfer.neutral", "transfer.prepare", "storage.authority", "storage.commands", "transfer.local", "storage.replica",
        )),
        authority="local", lifecycle="restart",
    ),
)
