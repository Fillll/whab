"""Fictional independent snapshot storage, using only the public WHAB SDK."""

from whab.plugins import Capability, Plugin, PluginContract


def create_storage(context):
    from .backend import SnapshotStorage

    return SnapshotStorage(context)


plugin = Plugin(
    id="example-storage",
    display_name="Example independent snapshot storage",
    storage=create_storage,
    storage_payload_paths=("snapshot-budget.json",),
    contract=PluginContract(
        provides=tuple(
            Capability(name)
            for name in (
                "receipts",
                "transfer.neutral",
                "transfer.prepare",
                "storage.authority",
                "storage.commands",
                "storage.worker",
            )
        ),
        authority="local",
        lifecycle="live",
    ),
)

PLUGIN_ID = plugin.id
