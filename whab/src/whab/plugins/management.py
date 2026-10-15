"""Installed-plugin metadata and restart-bound configuration, without factory execution."""

from importlib import metadata
from threading import Lock

from .configuration import read_selection, validate_selection, write_selection
from .runtime import PluginError, PluginRegistry, load_plugins
from .sdk import ENTRYPOINT_GROUP, valid_plugin_name


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
    """The running registry stays fixed; explicit saves configure the next startup."""

    def __init__(self, settings, plugins):
        self.settings = settings
        self.plugins = plugins
        self.required = tuple(dict.fromkeys(name for name in (settings.storage, settings.ui) if name))
        self._lock = Lock()

    def projection(self):
        """Safe template/API data; a bad file can be repaired through an explicit save."""
        descriptions = {item.id: item for item in self.plugins.describe()}
        active = tuple(name for name in descriptions if name not in self.required)
        configured = tuple(name for name in self.settings.plugins if name not in self.required)
        errors = []
        try:
            saved = read_selection(self.settings.home)
            if saved is not None:
                configured = tuple(name for name in saved if name not in self.required)
        except PluginError as error:
            errors.append(str(error))
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
            catalog.append({
                "id": name,
                "display_name": description.display_name if description else name,
                "kinds": list(description.kinds) if description else [],
                "distribution": distribution,
                "version": version,
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
        }

    def save(self, selection):
        """Validate descriptors and required capabilities; do not open/close any service."""
        selection = validate_selection(selection)
        if any(name in self.required for name in selection):
            raise PluginError(
                "Storage and user interface plugins are required; choose only optional plugins here.",
                code="required_plugin",
            )
        with self._lock:
            optional = load_plugins(selection, installed_entrypoints())
            # Reuse already validated mandatory descriptors. In particular, do not
            # reimport custom/injected UI or storage code to toggle a bank/report.
            required = tuple(
                plugin for plugin in self.plugins.descriptors() if plugin.id in self.required
            )
            missing = tuple(name for name in self.required if not any(p.id == name for p in required))
            if missing:
                required += load_plugins(missing, installed_entrypoints()).descriptors()
            proposed = PluginRegistry((*optional.descriptors(), *required))
            proposed.storage_plugin(self.settings.storage)
            if self.settings.ui:
                proposed.ui_plugin(self.settings.ui)
            elif any(item.has_ui for item in proposed.describe()):
                raise PluginError(
                    "User interface plugins cannot be enabled when WHAB_UI is empty.",
                    code="invalid_configuration",
                )
            if self.settings.ui:
                for plugin in optional.file_import_plugins():
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
                for plugin in optional.bank_plugins():
                    if plugin.bank_ui and (
                        not (plugin.bank_ui.templates / "accounts.html").is_file()
                        or not (plugin.bank_ui.static / "connect.js").is_file()
                    ):
                        raise PluginError(
                            "Bank plugin UI resources are missing. Reinstall or disable the plugin.",
                            code="missing_resources",
                            plugin_id=plugin.id,
                        )
            write_selection(self.settings.home, selection)
        return self.projection()
