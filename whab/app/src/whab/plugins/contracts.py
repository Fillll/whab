"""Versioned runtime capabilities, independent of Python package resolution.

Resolution reads immutable descriptors only. It never invokes plugin hooks,
binders, factories, migrations, or package installers.
"""

from __future__ import annotations

import heapq
import re
from collections import deque
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sdk import Plugin

_NAME = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
# These are contracts consumed by this host, independently of requirements
# between plugins. Unknown names remain available for extensions to negotiate.
_HOST_CONTRACTS = frozenset({
    "ledger.read", "ledger.mutate", "provider.state", "provider.sync", "receipts",
    "transfer.neutral", "transfer.prepare", "transfer.local", "storage.authority", "storage.commands", "storage.replica",
    "interface.ui", "interface.api",
    "reporting.insights", "reporting.export", "ingestion.file", "ingestion.bank",
    "branding.bank", "branding.merchant", "categorization",
    "lifecycle.background",
})


class CapabilityError(ValueError):
    """Authored resolution error, safe to display without underlying exceptions."""

    def __init__(self, message: str, *, code: str, plugin_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.plugin_id = plugin_id


def _name(value: object) -> None:
    if type(value) is not str or len(value) > 100 or _NAME.fullmatch(value) is None:
        raise ValueError("Capability names must be lowercase identifiers of at most 100 characters.")


def _version(value: object) -> None:
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError("Contract versions must be integers from 1 through 65535.")


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    version: int = 1
    composition: str = "additive"

    def __post_init__(self):
        _name(self.name)
        _version(self.version)
        if type(self.composition) is not str or self.composition not in {"singleton", "additive"}:
            raise ValueError("Capability composition must be singleton or additive.")
        if self.name == "ledger.mutate" and self.composition != "singleton":
            raise ValueError("The authoritative ledger writer must be a singleton capability.")


@dataclass(frozen=True, slots=True)
class Requirement:
    name: str
    min_version: int = 1
    max_version: int | None = None

    def __post_init__(self):
        _name(self.name)
        _version(self.min_version)
        if self.max_version is not None:
            _version(self.max_version)
            if self.max_version < self.min_version:
                raise ValueError("A capability version range must include at least one version.")

    def accepts(self, version: int) -> bool:
        _version(version)
        return self.min_version <= version and (self.max_version is None or version <= self.max_version)


@dataclass(frozen=True, slots=True)
class PluginContract:
    provides: tuple[Capability, ...] = ()
    requires: tuple[Requirement, ...] = ()
    conflicts: tuple[str, ...] = ()
    lifecycle: str = "restart"
    authority: str = "local"
    state_version: int = 1
    state_upgrades: tuple[tuple[int, int], ...] = ()

    def __post_init__(self):
        for records, expected in ((self.provides, Capability), (self.requires, Requirement)):
            if type(records) is not tuple or len(records) > 100 or any(type(row) is not expected for row in records):
                raise ValueError("Capability declarations must be bounded tuples of SDK records.")
            names = []
            for row in records:
                row.__post_init__()
                names.append(row.name)
            if len(names) != len(set(names)):
                raise ValueError("Capability names must be unique within each declaration.")
        if type(self.conflicts) is not tuple or len(self.conflicts) > 100:
            raise ValueError("Capability conflicts must be a bounded tuple of names.")
        for name in self.conflicts:
            _name(name)
        if len(self.conflicts) != len(set(self.conflicts)):
            raise ValueError("Capability conflicts must be unique.")
        if set(self.conflicts) & {row.name for row in self.provides}:
            raise ValueError("A plugin cannot conflict with its own capabilities.")
        if type(self.lifecycle) is not str or self.lifecycle not in {"restart", "live"}:
            raise ValueError("Lifecycle support must be restart or live.")
        if type(self.authority) is not str or self.authority not in {"local", "remote", "unknown"}:
            raise ValueError("Authority must be local, remote, or unknown.")
        _version(self.state_version)
        if type(self.state_upgrades) is not tuple or len(self.state_upgrades) > 100:
            raise ValueError("State upgrades must be a bounded tuple of version pairs.")
        for edge in self.state_upgrades:
            if type(edge) is not tuple or len(edge) != 2:
                raise ValueError("Each state upgrade must be a pair of versions.")
            start, end = edge
            _version(start)
            _version(end)
            if not start < end <= self.state_version:
                raise ValueError("State upgrades must increase toward the supported state version.")
        if len(self.state_upgrades) != len(set(self.state_upgrades)):
            raise ValueError("State upgrade paths must be unique.")


def contract_for(plugin: Plugin) -> PluginContract:
    """Return validated metadata, adapting API v1 hooks as restart-only legacy code.

    Hook capabilities are always included; explicit metadata may version those
    contracts and add capabilities, but cannot hide a ledger writer or UI.
    Legacy storage authority is unknown unless its connection-reader hook
    identifies remote storage. Legacy provider capabilities are compatibility
    declarations, not conformance evidence. Optional backend features are never
    guessed by opening a backend. ``plugin.contract is None`` retains legacy status.
    """
    if plugin.contract is not None:
        if type(plugin.contract) is not PluginContract:
            raise ValueError("Plugin contract must use the public PluginContract record.")
        plugin.contract.__post_init__()
    authority = plugin.contract.authority if plugin.contract is not None else (
        "remote" if plugin.budget_connection is not None else "unknown"
    )
    provides = []
    for attribute, name, composition in (
        ("storage", "ledger.read", "singleton"),
        ("storage", "ledger.mutate", "singleton"),
        ("ui", "interface.ui", "singleton"),
        ("interface", "interface.api", "additive"),
        ("insights", "reporting.insights", "additive"),
        ("export", "reporting.export", "additive"),
        ("bank", "ingestion.bank", "additive"),
        ("file_import", "ingestion.file", "additive"),
        ("bank_logos", "branding.bank", "additive"),
        ("merchant_logos", "branding.merchant", "additive"),
        ("categorization", "categorization", "additive"),
    ):
        if getattr(plugin, attribute) is not None:
            provides.append(Capability(name, composition=composition))
    if plugin.storage is not None and authority != "remote":
        provides.extend((
            Capability("provider.state", composition="singleton"),
            Capability("provider.sync", composition="singleton"),
        ))
    if plugin.contract is None:
        return PluginContract(provides=tuple(provides), authority=authority)
    mandatory = {row.name: row for row in provides}
    for row in plugin.contract.provides:
        if row.name in mandatory and mandatory[row.name].composition != row.composition:
            raise ValueError("Declared composition must preserve the host's hook capability contract.")
        mandatory[row.name] = row
    return replace(plugin.contract, provides=tuple(mandatory[name] for name in sorted(mandatory)))


def state_upgrade_path(
    contract: PluginContract, saved_version: int, *, plugin_id: str | None = None,
) -> tuple[int, ...]:
    """Return the shortest declared path, including source and target versions.

    This is compatibility evidence only; the host must stage and execute every
    migration before publishing upgraded state. A path never mutates state.
    """
    if type(contract) is not PluginContract:
        raise ValueError("Saved-state compatibility requires a PluginContract.")
    contract.__post_init__()
    _version(saved_version)
    pending = deque([(saved_version,)])
    visited = {saved_version}
    while pending:
        path = pending.popleft()
        if path[-1] == contract.state_version:
            return path
        for start, end in sorted(contract.state_upgrades):
            if start == path[-1] and end not in visited:
                visited.add(end)
                pending.append((*path, end))
    raise CapabilityError(
        "Saved plugin state is newer than supported or has no declared upgrade path. "
        "Install a compatible plugin version; preserve the existing budget and plugin state.",
        code="unsupported_state", plugin_id=plugin_id,
    )


def resolve_plugins(plugins: tuple[Plugin, ...]) -> tuple[Plugin, ...]:
    """Validate a complete selection and order dependencies before consumers.

    Every selected provider of a required additive capability must satisfy its
    accepted version range. Its consumers run after all such providers. Sorting
    by plugin ID resolves otherwise independent peers consistently.
    """
    from .sdk import API_VERSION, Plugin, valid_plugin_name

    if type(plugins) is not tuple or len(plugins) > 100:
        raise CapabilityError("Select at most 100 plugin descriptors.", code="invalid_contract")
    by_id = {}
    contracts = {}
    for plugin in plugins:
        if type(plugin) is not Plugin or not valid_plugin_name(plugin.id):
            raise CapabilityError("The plugin selection contains an invalid descriptor.", code="invalid_contract")
        if plugin.id in by_id:
            raise CapabilityError("The plugin selection contains a duplicate plugin ID.", code="duplicate_plugin", plugin_id=plugin.id)
        by_id[plugin.id] = plugin
        try:
            plugin.__post_init__()
            contracts[plugin.id] = contract_for(plugin)
        except (ValueError, TypeError, AttributeError):
            raise CapabilityError("Plugin capability metadata is invalid. Contact its author.", code="invalid_contract", plugin_id=plugin.id) from None
        if plugin.api_version != API_VERSION:
            raise CapabilityError(
                "Plugin requires an unsupported SDK API version. Install a compatible plugin or upgrade WHAB.",
                code="incompatible_api", plugin_id=plugin.id,
            )
        for capability in contracts[plugin.id].provides:
            if capability.name in _HOST_CONTRACTS and capability.version != 1:
                raise CapabilityError(
                    f"Plugin capability '{capability.name}' requires an unsupported host contract version. "
                    "This WHAB host supports version 1; install a compatible plugin or upgrade WHAB.",
                    code="incompatible_host_contract", plugin_id=plugin.id,
                )

    providers = {}
    for identifier in sorted(contracts):
        for capability in contracts[identifier].provides:
            providers.setdefault(capability.name, []).append((identifier, capability))
    for name, matches in sorted(providers.items()):
        compositions = {row.composition for _, row in matches}
        if len(compositions) != 1:
            raise CapabilityError("Selected providers disagree on capability composition. Choose compatible plugins.", code="incompatible_composition", plugin_id=matches[0][0])
        if len(matches) > 1 and "singleton" in compositions:
            raise CapabilityError("Multiple plugins provide a singleton capability. Select one authoritative provider.", code="conflicting_providers", plugin_id=matches[0][0])

    dependencies = {identifier: set() for identifier in by_id}
    for identifier in sorted(contracts):
        contract = contracts[identifier]
        if any(name in providers for name in contract.conflicts):
            raise CapabilityError("The selected plugin conflicts with another selected capability. Change the selection.", code="capability_conflict", plugin_id=identifier)
        for requirement in contract.requires:
            matches = providers.get(requirement.name, ())
            if not matches:
                raise CapabilityError("A required plugin capability is missing. Install and select a compatible provider.", code="missing_requirement", plugin_id=identifier)
            if any(not requirement.accepts(capability.version) for _, capability in matches):
                raise CapabilityError("A required capability version is incompatible. Select providers within the accepted contract range.", code="incompatible_requirement", plugin_id=identifier)
            dependencies[identifier].update(provider for provider, _ in matches if provider != identifier)

    ready = [identifier for identifier, required in dependencies.items() if not required]
    heapq.heapify(ready)
    ordered = []
    while ready:
        identifier = heapq.heappop(ready)
        ordered.append(by_id[identifier])
        for consumer in sorted(dependencies):
            if identifier in dependencies[consumer]:
                dependencies[consumer].remove(identifier)
                if not dependencies[consumer]:
                    heapq.heappush(ready, consumer)
    if len(ordered) != len(plugins):
        identifier = min(name for name, required in dependencies.items() if required)
        raise CapabilityError("Selected plugin capabilities contain a dependency cycle. Remove the cycle before activation.", code="dependency_cycle", plugin_id=identifier)
    return tuple(ordered)
