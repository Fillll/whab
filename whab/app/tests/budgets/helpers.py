"""Test-only inspection of SQLite payloads; production container code stays opaque."""

from whab_sqlite import Store

from whab.budgets.container import decode_container, encode_container


def decode_budget(raw):
    container = decode_container(raw)
    if container.legacy_ledger is not None:
        return container.manifest, container.legacy_ledger
    prefix = "plugins/sqlite/"
    files = {name[len(prefix):]: value for name, value in container.files.items() if name.startswith(prefix)}
    store = Store(":memory:", month="2026-10")
    try:
        store.restore_budget_files("personal", files)
        return container.manifest, store.export_budget()
    finally:
        store.close()


def encode_sqlite_budget(manifest, ledger):
    store = Store(":memory:", month=ledger["budgetStartMonth"])
    try:
        store.restore_budget("personal", ledger)
        files = {f"plugins/sqlite/{name}": raw for name, raw in store.export_budget_files().items()}
        return encode_container(manifest, files)
    finally:
        store.close()
