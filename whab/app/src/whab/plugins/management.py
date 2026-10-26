"""Installed metadata, restart configuration and opt-in live report management."""

from copy import deepcopy
from importlib import metadata
from threading import Lock

from .configuration import read_selection, validate_selection, write_selection
from .contracts import contract_for
from .manifests import installed_manifest
from .runtime import PluginError, PluginRegistry, load_descriptors
from .sdk import ENTRYPOINT_GROUP, valid_plugin_name

# Registration identity is package metadata, so disabled plugins remain unloaded.
_BUNDLED_METADATA = {
    ("random-forest-categorization", "whab_plugin_random_forest_categorization.plugin:plugin"): ("automation", "Random forest categorization"),
    ("bank-logos", "whab_plugin_bank_logos.plugin:plugin"): ("interface", "Bank logos"),
    ("web", "whab_plugin_web.plugin:plugin"): ("interface", "WHAB web"),
    ("plaid", "whab_plugin_plaid.plugin:plugin"): ("ingestion", "Plaid"),
    ("ynab", "whab_plugin_ynab.plugin:plugin"): ("ingestion", "Import from YNAB ZIP file"),
    ("sqlite", "whab_plugin_sqlite.plugin:plugin"): ("storage", "SQLite"),
    ("json", "whab_plugin_json.plugin:plugin"): ("storage", "JSON"),
    ("client", "whab_plugin_client.plugin:plugin"): ("storage", "Remote budget client"),
    ("server", "whab_plugin_server.plugin:plugin"): ("interface", "Budget API server"),
    ("example", "whab_plugin_example:plugin"): ("example", "Month totals example"),
    ("example-branding", "whab_plugin_example_branding:plugin"): ("example", "Fictional branding"),
    ("example-file-import", "whab_plugin_example_file_import:plugin"): ("example", "Fictional account import"),
    ("example-categorization", "whab_plugin_example_categorization:plugin"): ("example", "Exact merchant teaching model"),
    ("example-api", "whab_plugin_example_api:plugin"): ("example", "Fictional API interface"),
    ("example-ui", "whab_plugin_example_ui:plugin"): ("example", "Fictional visual interface"),
    ("example-bank", "whab_plugin_example_bank:plugin"): ("example", "Offline fictional bank"),
    ("example-remote-client", "whab_plugin_example_remote_client:plugin"): ("example", "Fictional remote ledger client"),
    ("example-storage", "whab_plugin_example_storage:plugin"): ("example", "Example independent snapshot storage"),
}


def bundled_metadata(name, entries):
    """Resolve only unambiguous bundled registration identities."""
    if len(entries) == 1:
        return _BUNDLED_METADATA.get((name, getattr(entries[0], "value", None)))
    return None


def plugin_category(name, entries, description):
    """Use known registration metadata or active capabilities, never disabled imports."""
    known = bundled_metadata(name, entries)
    if known:
        return known[0]
    if description:
        if description.has_ui or description.has_bank_logos or "user_interface" in description.kinds:
            return "interface"
        if description.has_storage:
            return "storage"
        if description.has_bank or description.has_file_import:
            return "ingestion"
        if description.has_insights or description.has_export:
            return "insight"
    return "extension"


def installed_entrypoints():
    """Read package registration metadata without importing disabled plugin code."""
    try:
        return tuple(metadata.entry_points(group=ENTRYPOINT_GROUP))
    except Exception:
        raise PluginError(
            "Installed plugin metadata could not be read. Check your Python environment.",
            code="discovery_failed",
        ) from None


def package_details(entry):
    """Public package name/version only; never expose module targets or runtime config."""
    try:
        distribution = entry.dist
        name = distribution.metadata.get("Name", "") if distribution else ""
        version = distribution.version if distribution else ""
    except Exception:
        return "", ""
    return (
        name[:120] if type(name) is str else "",
        version[:80] if type(version) is str else "",
    )


class PluginManager:
    """Ordinary saves configure startup; explicit transitions apply live reports."""

    def __init__(self, settings, plugins, budget_session=None, live=None):
        self.settings = settings
        self.plugins = plugins
        self.budget_session = budget_session
        self.live = live
        self.required = tuple(dict.fromkeys(name for name in (settings.storage, settings.ui) if name))
        self.visual_known = frozenset()
        self._lock = Lock()

    def _read_selection(self):
        selected = self.budget_session.selection if self.budget_session else read_selection(self.settings.home)
        return tuple(name for name in selected if name not in self.visual_known) if selected is not None else None

    def projection(self):
        """Safe template/API data; a bad file can be repaired through an explicit save."""
        descriptions = {item.id: item for item in self.plugins.describe()}
        active = tuple(sorted(name for name in descriptions if name not in self.required))
        configured = tuple(name for name in self.settings.plugins if name not in self.required)
        errors = []
        try:
            saved = self._read_selection()
            if saved is not None:
                configured = tuple(name for name in saved if name not in self.required)
        except PluginError as error:
            errors.append(str(error))
        configured = tuple(sorted(configured))
        try:
            entries = installed_entrypoints()
        except PluginError as error:
            entries = ()
            errors.append(str(error))
        installed = {}
        for entry in entries:
            if entry.group == ENTRYPOINT_GROUP and type(entry.name) is str:
                installed.setdefault(entry.name, []).append(entry)
        names = sorted(set(installed) | set(descriptions) | set(configured) | set(self.required))
        catalog = []
        for name in names:
            description = descriptions.get(name)
            matches = installed.get(name, [])
            required = name in self.required
            reason = None
            if not valid_plugin_name(name):
                reason = "Invalid plugin registration name. Reinstall its package."
            elif len(matches) > 1:
                reason = "Registered more than once. Remove the duplicate installed package."
            elif not matches:
                reason = "Not installed in this Python environment."
            elif required:
                reason = "Required by the selected storage or user interface."
            elif description and (description.has_storage or description.has_ui):
                reason = "Storage and user interfaces are selected with WHAB_STORAGE and WHAB_UI."
            distribution, version = package_details(matches[0]) if len(matches) == 1 else ("", "")
            known = bundled_metadata(name, matches)
            manifest = None
            if len(matches) == 1:
                try:
                    manifest = installed_manifest(matches[0])
                except PluginError as error:
                    reason = str(error)
            display_name = known[1] if known else name
            catalog.append({
                "id": name,
                "display_name": description.display_name if description else display_name,
                "kinds": list(description.kinds) if description else [],
                "category": manifest["category"] if manifest else plugin_category(name, matches, description),
                "installed": bool(matches) or description is not None,
                "dependencies": manifest["dependencies"] if manifest else [],
                "distribution": distribution,
                "version": version,
                "compatibility": {
                    "legacy": description.legacy,
                    "lifecycle": description.lifecycle,
                    "authority": description.authority,
                    "state_version": description.state_version,
                    "capabilities": [{"name": item.name, "version": item.version, "composition": item.composition} for item in description.capabilities],
                    "requirements": [{"name": item.name, "min_version": item.min_version, "max_version": item.max_version} for item in description.requirements],
                } if description else None,
                "active": name in descriptions,
                "enabled": required or name in configured,
                "required": required,
                "manageable": reason is None,
                "reason": reason,
            })
        return {
            "plugins": catalog,
            "active": list(active),
            "configured": list(configured),
            "restart_required": active != configured,
            "error": " ".join(errors) or None,
            **({"live": self.live.status()} if self.live else {}),
        }

    def persist_live(self, selection, descriptors):
        """Called only after generation admission is gated and drained."""
        with self._lock:
            if self.budget_session:
                self.budget_session.save_selection(selection, state_versions={
                    plugin.id: contract_for(plugin).state_version for plugin in descriptors
                })
            else:
                write_selection(self.settings.home, selection)

    def preflight_live(self, selection):
        if self.live is None:
            raise PluginError("This host has no live runtime coordinator. Save the selection and restart.", code="restart_required")
        self.live.preflight(selection)

    def schedule_live(self, selection, *, validated=False):
        if self.live is None:
            raise PluginError("This host has no live runtime coordinator. Save the selection and restart.", code="restart_required")
        return self.live.schedule(selection, validated=validated)

    def save(self, selection):
        """Validate descriptors and required capabilities; do not open/close any service."""
        selection = validate_selection(selection)
        if any(name in self.required for name in selection):
            raise PluginError(
                "Storage and user interface plugins are required; choose only optional plugins here.",
                code="required_plugin",
            )
        with self._lock:
            # Preserve unavailable optional requirements carried by an opened budget;
            # they must not prevent configuring the installed capabilities.
            unavailable = set(self.budget_session.missing_plugins) if self.budget_session else set()
            optional = load_descriptors(tuple(name for name in selection if name not in unavailable), installed_entrypoints())
            # Reuse already validated mandatory descriptors. In particular, do not
            # reimport custom/injected UI or storage code to toggle a bank/report.
            required = tuple(
                plugin for plugin in self.plugins.descriptors() if plugin.id in self.required
            )
            missing = tuple(name for name in self.required if not any(p.id == name for p in required))
            if missing:
                required += load_descriptors(missing, installed_entrypoints())
            proposed = PluginRegistry((*optional, *required))
            proposed.storage_plugin(self.settings.storage)
            selected_storage = proposed.storage_plugin(self.settings.storage)
            if contract_for(selected_storage).authority == "remote" and proposed.bank_plugins():
                raise PluginError("Bank ingestion belongs on the server. Disable bank plugins in a client budget.", code="invalid_configuration", plugin_id=selected_storage.id)
            if self.settings.ui:
                proposed.ui_plugin(self.settings.ui)
            elif any(item.has_ui for item in proposed.describe()):
                raise PluginError(
                    "User interface plugins cannot be enabled when WHAB_UI is empty.",
                    code="invalid_configuration",
                )
            if self.settings.ui:
                for plugin in (item for item in optional if item.file_import is not None):
                    capability = plugin.file_import
                    if capability.templates is not None and (
                        not (capability.templates / "accounts.html").is_file()
                        or not (capability.static / "import.js").is_file()
                    ):
                        raise PluginError(
                            "Import plugin UI resources are missing. Reinstall or disable the plugin.",
                            code="missing_resources",
                            plugin_id=plugin.id,
                        )
                for plugin in (item for item in optional if item.bank is not None):
                    if plugin.bank_ui and (
                        not (plugin.bank_ui.templates / "accounts.html").is_file()
                        or not (plugin.bank_ui.static / "connect.js").is_file()
                    ):
                        raise PluginError(
                            "Bank plugin UI resources are missing. Reinstall or disable the plugin.",
                            code="missing_resources",
                            plugin_id=plugin.id,
                        )
            if self.budget_session:
                from whab.budgets.plugin_state import check_saved_states

                candidate = deepcopy(self.budget_session.manifest)
                selected_ids = {plugin.id for plugin in proposed.descriptors()}
                for record in candidate["plugins"]:
                    record["enabled"] = record["id"] in selected_ids
                check_saved_states(candidate, proposed.descriptors())
                self.budget_session.save_selection(selection, state_versions={plugin.id: contract_for(plugin).state_version for plugin in proposed.descriptors()})
            else:
                write_selection(self.settings.home, selection)
        return self.projection()
