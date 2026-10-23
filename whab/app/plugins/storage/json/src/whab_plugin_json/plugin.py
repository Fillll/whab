"""Explicit JSON storage selection; opening never substitutes another backend."""

from whab.plugins import Capability, Plugin, PluginContract, StorageContext


def create_storage(context: StorageContext):
    from .store import Store

    return Store(context.home / "data" / f"whab-{context.environment}.json")


plugin = Plugin(
    id="json", display_name="JSON", storage=create_storage,
    contract=PluginContract(
        provides=(Capability("receipts"), Capability("transfer.neutral"), Capability("transfer.prepare")),
        authority="local", lifecycle="restart",
    ),
)
