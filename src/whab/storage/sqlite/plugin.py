"""SQLite storage descriptor; backend construction occurs only in its factory."""

from whab.plugins import Plugin, StorageBackend, StorageContext


def create_storage(context: StorageContext) -> StorageBackend:
    from .store import Store

    return Store(context.home / "data" / f"ihab-{context.environment}.sqlite")


plugin = Plugin(id="sqlite", display_name="SQLite", storage=create_storage)
