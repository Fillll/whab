"""Host lifecycle for a selected synchronous storage plugin."""

import inspect

from .runtime import PluginError
from .sdk import StorageBackend


def close_storage(backend):
    """Cleanup must not mask startup errors or expose backend exception details."""
    try:
        result = backend.close()
        if inspect.iscoroutine(result):
            result.close()
    except Exception:
        pass


def open_storage(plugin, context):
    backend = None
    try:
        backend = plugin.storage(context)
        if inspect.isawaitable(backend):
            if inspect.iscoroutine(backend):
                backend.close()
            raise ValueError("Storage factories must be synchronous")
        for name, member in vars(StorageBackend).items():
            if name.startswith("_") or not callable(member):
                continue
            implementation = getattr(backend, name, None)
            if not callable(implementation) or inspect.iscoroutinefunction(implementation):
                raise ValueError("Invalid storage backend")
        return backend
    except Exception:
        if backend is not None and not inspect.isawaitable(backend):
            close_storage(backend)
        raise PluginError(
            "Storage plugin could not start. Check its configuration and installation.",
            code="storage_startup_failed",
            plugin_id=plugin.id,
        ) from None
