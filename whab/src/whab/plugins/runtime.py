"""Explicit entry-point loading and validated hook execution for trusted plugins."""

from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import fields
from importlib import metadata
from threading import Lock
from time import monotonic

from .sdk import (
    API_VERSION,
    ENTRYPOINT_GROUP,
    AccountSnapshot,
    BudgetSnapshot,
    CategorySnapshot,
    ExportTable,
    Insight,
    LogoImage,
    Plugin,
    PluginDescription,
    PluginInsightResult,
    TransactionSnapshot,
    valid_plugin_name,
)


class PluginError(ValueError):
    """Authored, actionable error safe to display; never includes plugin exception text."""

    def __init__(self, message: str, *, code: str, plugin_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.plugin_id = plugin_id


def _copy_record(value: object, record_type: type):
    if type(value) is not record_type:
        raise ValueError("Snapshot contains an unsupported record type.")
    return record_type(**{field.name: getattr(value, field.name) for field in fields(record_type)})


def copy_snapshot(snapshot: BudgetSnapshot) -> BudgetSnapshot:
    """Make fresh recursively immutable records, retaining exactly one workspace/month."""
    if type(snapshot) is not BudgetSnapshot:
        raise ValueError("Plugin snapshot must be a BudgetSnapshot.")
    return BudgetSnapshot(
        workspace=snapshot.workspace,
        month=snapshot.month,
        accounts=tuple(_copy_record(row, AccountSnapshot) for row in snapshot.accounts),
        categories=tuple(_copy_record(row, CategorySnapshot) for row in snapshot.categories),
        transactions=tuple(_copy_record(row, TransactionSnapshot) for row in snapshot.transactions),
        currency=snapshot.currency,
    )


class PluginRegistry:
    """A fixed set of enabled plugins; host code owns rendering and CSV downloads."""

    def __init__(self, plugins: tuple[Plugin, ...] = ()) -> None:
        self._plugins = plugins
        self._logo_cache = OrderedDict()
        self._logo_lock = Lock()
        self._logo_fetch_locks = tuple(Lock() for _ in range(16))

    def descriptors(self) -> tuple[Plugin, ...]:
        """Return immutable loaded descriptors for composition without invoking hooks."""
        return self._plugins

    def describe(self) -> tuple[PluginDescription, ...]:
        return tuple(
            PluginDescription(
                id=plugin.id,
                display_name=plugin.display_name,
                api_version=plugin.api_version,
                has_insights=plugin.insights is not None,
                has_export=plugin.export is not None,
                has_bank=plugin.bank is not None,
                has_storage=plugin.storage is not None,
                has_ui=plugin.ui is not None,
                has_bank_logos=plugin.bank_logos is not None,
                kinds=tuple(
                    name
                    for name, enabled in (
                        ("user_interface", plugin.ui is not None),
                        ("storage", plugin.storage is not None),
                        ("data_ingestion", plugin.bank is not None),
                        ("reporting", plugin.insights is not None or plugin.export is not None),
                        ("branding", plugin.bank_logos is not None),
                    )
                    if enabled
                ),
            )
            for plugin in self._plugins
        )

    def bank_plugins(self) -> tuple[Plugin, ...]:
        """Return enabled descriptors; the host supplies scoped contexts and owns lifecycle."""
        return tuple(plugin for plugin in self._plugins if plugin.bank is not None)

    def bank_logo(self, institution: str) -> tuple[str, str] | None:
        """First enabled match wins. Only institution text reaches resolver hooks."""
        if type(institution) is not str or not institution or len(institution) > 200:
            return None
        for plugin in self._plugins:
            if plugin.bank_logos is None:
                continue
            try:
                key = plugin.bank_logos.resolve(institution)
                if valid_plugin_name(key):
                    return plugin.id, key
            except Exception:
                pass  # Optional branding must never break a budget page or leak errors.
        return None

    def logo_image(self, plugin_id: str, key: str) -> LogoImage | None:
        """Bounded, single-flight icon cache; failures retry after five minutes."""
        if not valid_plugin_name(plugin_id) or not valid_plugin_name(key):
            return None
        plugin = next((p for p in self._plugins if p.id == plugin_id), None)
        if plugin is None or plugin.bank_logos is None:
            return None
        cache_key = (plugin_id, key)
        with self._logo_lock:
            cached = self._logo_cache.get(cache_key)
            if cached is not None and cached[0] > monotonic():
                self._logo_cache.move_to_end(cache_key)
                return cached[1]
        # A bounded set of fetch locks coalesces requests for one bank without
        # holding the cache lock during network I/O or delaying cached icons.
        with self._logo_fetch_locks[hash(cache_key) % len(self._logo_fetch_locks)]:
            with self._logo_lock:
                cached = self._logo_cache.get(cache_key)
                if cached is not None and cached[0] > monotonic():
                    self._logo_cache.move_to_end(cache_key)
                    return cached[1]
            try:
                result = plugin.bank_logos.fetch(key)
                image = _copy_record(result, LogoImage) if result is not None else None
            except Exception:
                image = None
            with self._logo_lock:
                self._logo_cache[cache_key] = (monotonic() + (86400 if image else 300), image)
                self._logo_cache.move_to_end(cache_key)
                while len(self._logo_cache) > 128:
                    self._logo_cache.popitem(last=False)
            return image

    def storage_plugin(self, plugin_id: str) -> Plugin:
        """Resolve one selected backend descriptor without constructing its storage."""
        if not valid_plugin_name(plugin_id):
            raise PluginError(
                "WHAB_STORAGE must name a lowercase storage plugin.",
                code="invalid_storage_configuration",
            )
        storage = tuple(plugin for plugin in self._plugins if plugin.storage is not None)
        if len(storage) > 1:
            raise PluginError(
                "Multiple storage plugins are enabled. Keep only WHAB_STORAGE's selected backend.",
                code="multiple_storage_plugins",
                plugin_id=plugin_id,
            )
        selected = next((plugin for plugin in self._plugins if plugin.id == plugin_id), None)
        if selected is None:
            raise PluginError(
                f"Storage plugin '{plugin_id}' is not enabled. Install it and select it with WHAB_STORAGE.",
                code="missing_storage_plugin",
                plugin_id=plugin_id,
            )
        if selected.storage is None:
            raise PluginError(
                f"Plugin '{plugin_id}' does not provide storage. Select a storage-capable plugin with WHAB_STORAGE.",
                code="invalid_storage_plugin",
                plugin_id=plugin_id,
            )
        return selected

    def ui_plugin(self, plugin_id: str) -> Plugin:
        """Resolve the selected browser interface without loading unselected UI code."""
        if not valid_plugin_name(plugin_id):
            raise PluginError(
                "WHAB_UI must name a lowercase user interface plugin, or be empty for headless mode.",
                code="invalid_ui_configuration",
            )
        interfaces = tuple(plugin for plugin in self._plugins if plugin.ui is not None)
        if len(interfaces) > 1:
            raise PluginError(
                "Multiple user interface plugins are enabled. Keep only WHAB_UI's selected interface.",
                code="multiple_ui_plugins",
                plugin_id=plugin_id,
            )
        selected = next((plugin for plugin in self._plugins if plugin.id == plugin_id), None)
        if selected is None:
            raise PluginError(
                f"User interface plugin '{plugin_id}' is not enabled. Install it and select it with WHAB_UI.",
                code="missing_ui_plugin",
                plugin_id=plugin_id,
            )
        if selected.ui is None:
            raise PluginError(
                f"Plugin '{plugin_id}' does not provide a user interface. Select a UI-capable plugin with WHAB_UI.",
                code="invalid_ui_plugin",
                plugin_id=plugin_id,
            )
        return selected

    def insights(self, snapshot: BudgetSnapshot) -> tuple[PluginInsightResult, ...]:
        results = []
        for plugin in self._plugins:
            if plugin.insights is None:
                continue
            try:
                output = plugin.insights(copy_snapshot(snapshot))
                if type(output) is not tuple or len(output) > 50:
                    raise ValueError("Insight output must be a tuple of at most 50 records.")
                insights = tuple(_copy_record(row, Insight) for row in output)
            except Exception:
                results.append(
                    PluginInsightResult(
                        plugin_id=plugin.id,
                        display_name=plugin.display_name,
                        insights=(),
                        error="Plugin insights could not be generated. Disable the plugin or contact its author.",
                    )
                )
            else:
                results.append(PluginInsightResult(plugin.id, plugin.display_name, insights))
        return tuple(results)

    def export(self, plugin_id: str, snapshot: BudgetSnapshot) -> ExportTable:
        if not valid_plugin_name(plugin_id):
            raise PluginError("Choose an enabled plugin export.", code="unavailable")
        plugin = next((plugin for plugin in self._plugins if plugin.id == plugin_id), None)
        if plugin is None or plugin.export is None:
            raise PluginError(
                "This plugin export is not enabled. Check WHAB_PLUGINS and restart WHAB.",
                code="unavailable",
                plugin_id=plugin_id,
            )
        try:
            output = plugin.export(copy_snapshot(snapshot))
            return _copy_record(output, ExportTable)
        except Exception:
            raise PluginError(
                "Plugin export could not be generated. Disable the plugin or contact its author.",
                code="hook_failed",
                plugin_id=plugin.id,
            ) from None


def load_plugins(
    enabled: tuple[str, ...],
    entrypoints: Sequence[metadata.EntryPoint] | None = None,
) -> PluginRegistry:
    """Load named whab.plugins descriptors only; an empty tuple imports no plugins."""
    if type(enabled) is not tuple or any(not valid_plugin_name(name) for name in enabled):
        raise PluginError(
            "WHAB_PLUGINS must contain comma-separated lowercase plugin names.",
            code="invalid_configuration",
        )
    if len(set(enabled)) != len(enabled):
        raise PluginError(
            "WHAB_PLUGINS contains a duplicate name. Keep each enabled plugin name once.",
            code="duplicate_configuration",
        )
    if not enabled:
        return PluginRegistry()
    if entrypoints is None:
        try:
            entrypoints = metadata.entry_points(group=ENTRYPOINT_GROUP)
        except Exception:
            raise PluginError(
                "Installed plugin metadata could not be read. Check your Python environment.",
                code="discovery_failed",
            ) from None

    selected = []
    # Resolve every configured name before importing any code: never choose an ambiguous plugin.
    for name in enabled:
        matches = [
            entry for entry in entrypoints if entry.group == ENTRYPOINT_GROUP and entry.name == name
        ]
        if not matches:
            raise PluginError(
                f"Plugin '{name}' is not installed. Install its package or remove it from WHAB_PLUGINS.",
                code="missing",
                plugin_id=name,
            )
        if len(matches) != 1:
            raise PluginError(
                f"Plugin '{name}' is registered more than once. Remove the duplicate installed package.",
                code="duplicate_entrypoint",
                plugin_id=name,
            )
        selected.append((name, matches[0]))

    plugins = []
    for name, entry in selected:
        try:
            descriptor = entry.load()
        except Exception:
            raise PluginError(
                f"Plugin '{name}' could not be loaded. Check its installation or disable it.",
                code="load_failed",
                plugin_id=name,
            ) from None
        if type(descriptor) is not Plugin:
            raise PluginError(
                f"Plugin '{name}' must export a WHAB Plugin descriptor. Contact its author.",
                code="invalid_descriptor",
                plugin_id=name,
            )
        # Revalidate even a descriptor created by bypassing frozen-dataclass safeguards.
        try:
            descriptor = _copy_record(descriptor, Plugin)
        except Exception:
            raise PluginError(
                f"Plugin '{name}' has an invalid descriptor. Contact its author.",
                code="invalid_descriptor",
                plugin_id=name,
            ) from None
        if descriptor.id != name:
            raise PluginError(
                f"Plugin '{name}' must use the same descriptor ID and entry-point name.",
                code="id_mismatch",
                plugin_id=name,
            )
        if descriptor.api_version != API_VERSION:
            raise PluginError(
                f"Plugin '{name}' requires an unsupported API version. Install an API v1 plugin or disable it.",
                code="incompatible_api",
                plugin_id=name,
            )
        plugins.append(descriptor)
    return PluginRegistry(tuple(plugins))
