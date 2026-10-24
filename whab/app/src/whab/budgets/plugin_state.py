"""Saved-state preflight and detached upgrades, without active namespace writes."""

import copy
import inspect
from dataclasses import dataclass, field
from threading import RLock
from types import MappingProxyType

from whab.plugins import BudgetContext, PluginError
from whab.plugins.contracts import CapabilityError, contract_for, state_upgrade_path

from .container import encode_container, validate_file_name
from .document import portable_values, upgrade_manifest, validate_manifest


@dataclass(frozen=True, slots=True)
class StatePreparation:
    """Reviewable candidate data. The caller owns activation and durable commit."""

    manifest: dict = field(repr=False)
    files: dict[str, bytes] = field(repr=False)
    migrated: tuple[str, ...] = ()


class _StagedNamespace:
    """One migration edge's owner-bound callbacks, revoked when that edge ends."""

    def __init__(self, candidate, files, record):
        self._candidate = candidate
        self._files = files
        self._record = record
        self._owner = record["id"]
        self._lock = RLock()
        self._closed = False

    def _check_open(self):
        if self._closed:
            raise ValueError("This staged migration context has closed.")

    def _file_key(self, name):
        if type(name) is not str or name in {"settings.json", "state.json"}:
            raise ValueError("Reserved plugin metadata filename")
        return validate_file_name(f"plugins/{self._owner}/{name}")

    def save_state(self, value):
        with self._lock:
            self._check_open()
            if type(value) is not dict:
                raise ValueError("Plugin state must be an object")
            self._record["state"] = portable_values(value)

    def read_file(self, name):
        with self._lock:
            self._check_open()
            return self._files.get(self._file_key(name))

    def write_file(self, name, raw):
        with self._lock:
            self._check_open()
            if type(raw) is not bytes:
                raise ValueError("Plugin data must be bytes")
            self._files[self._file_key(name)] = raw
            encode_container(self._candidate, self._files)

    def delete_file(self, name):
        with self._lock:
            self._check_open()
            self._files.pop(self._file_key(name), None)

    def context(self):
        return BudgetContext(
            self._candidate["id"], MappingProxyType(copy.deepcopy(self._record["settings"])),
            MappingProxyType(copy.deepcopy(self._record["state"])), self.save_state,
            self.read_file, self.write_file, self.delete_file,
        )

    def close(self):
        # Wait for an admitted callback before revoking the namespace. A delayed
        # thread must never mutate state while the caller validates or copies it.
        with self._lock:
            self._closed = True


def _plans(manifest, plugins):
    records = {record["id"]: record for record in manifest["plugins"]}
    result = []
    for plugin in plugins:
        record = records.get(plugin.id)
        if record is None or not record["enabled"]:
            continue
        if record["apiVersion"] != plugin.api_version:
            raise PluginError(
                f"Plugin '{plugin.id}' saved state requires a different SDK version. Install a compatible host and plugin before enabling it.",
                code="unsupported_saved_sdk", plugin_id=plugin.id,
            )
        try:
            path = state_upgrade_path(contract_for(plugin), record.get("stateVersion", 1), plugin_id=plugin.id)
        except CapabilityError as error:
            raise PluginError(str(error), code=error.code, plugin_id=plugin.id) from None
        result.append((plugin, path))
    return result


def check_saved_states(manifest, plugins):
    """Reject incompatible state before binding or opening plugin factories.

    Startup never silently upgrades saved state. A declared path can be prepared
    explicitly, inspected, then committed by the budget transition coordinator.
    Disabled/unavailable plugin data remains opaque, even when its schema is newer.
    """
    for plugin, path in _plans(validate_manifest(manifest), plugins):
        if len(path) > 1:
            raise PluginError(
                f"Plugin '{plugin.id}' needs a saved-state upgrade from version {path[0]} to {path[-1]}. Preserve the budget and prepare that upgrade before activation.",
                code="state_upgrade_required", plugin_id=plugin.id,
            )


def stage_plugin_states(manifest, files, plugins):
    """Run declared migration edges using detached, namespace-scoped callbacks.

    The returned candidate changes no runtime file or active mapping. A migration
    can change its own state/data files; settings are read-only and other owners'
    bytes survive unchanged. Trusted Python can still access the OS independently.
    """
    candidate = upgrade_manifest(manifest)
    staged_files = dict(files)
    # Validate the entire input and every path before running migration code.
    encode_container(candidate, staged_files)
    plans = _plans(candidate, plugins)
    for plugin, path in plans:
        if len(path) > 1 and plugin.migrate_state is None:
            raise PluginError(
                f"Plugin '{plugin.id}' does not provide its declared saved-state upgrader. Install a compatible version or contact its author.",
                code="missing_state_migrator", plugin_id=plugin.id,
            )
    migrated = []
    for plugin, path in plans:
        if len(path) == 1:
            continue
        record = next(record for record in candidate["plugins"] if record["id"] == plugin.id)

        try:
            for source, target in zip(path, path[1:]):
                namespace = _StagedNamespace(candidate, staged_files, record)
                try:
                    result = plugin.migrate_state(namespace.context(), source, target)
                    if inspect.isawaitable(result):
                        if inspect.iscoroutine(result):
                            result.close()
                        raise ValueError("Saved-state migrations must be synchronous")
                    if result is not None:
                        raise ValueError("Saved-state migrations return None")
                finally:
                    namespace.close()
                record["stateVersion"] = target
                encode_container(candidate, staged_files)
        except Exception:
            raise PluginError(
                f"Plugin '{plugin.id}' could not prepare its saved-state upgrade. The active state is unchanged.",
                code="state_migration_failed", plugin_id=plugin.id,
            ) from None
        migrated.append(plugin.id)
    return StatePreparation(copy.deepcopy(candidate), dict(staged_files), tuple(migrated))
