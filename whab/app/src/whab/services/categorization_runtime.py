"""Host-owned categorizer generations and read-only candidate namespaces.

An installed categorizer is trusted Python. Revocable callbacks prevent an old
adapter from using host namespace access after retirement; they are not an OS
security boundary. A candidate factory cannot publish files or state.
"""

from copy import deepcopy
from dataclasses import replace
from functools import partial
from threading import RLock
from types import MappingProxyType

from starlette.concurrency import run_in_threadpool

from whab.budgets.container import MAX_CONTAINER_FILES, validate_file_name
from whab.budgets.document import MAX_BUNDLE_BYTES
from whab.budgets.runtime import RuntimeCandidate, RuntimeResource
from whab.domain.errors import StoreError
from whab.plugins.contracts import contract_for
from whab.plugins.runtime import PluginError
from whab.plugins.sdk import valid_plugin_name

from .categorization import CategorizationService


def live_categorizer(plugin):
    """The current adapter supports one explicit, independently scoped engine."""
    contract = contract_for(plugin)
    return (
        plugin.contract is not None and contract.lifecycle == "live"
        and plugin.categorization is not None
        and getattr(plugin, "branding_factory", None) is None
        and {record.name for record in contract.provides} <= {"categorization"}
        and not contract.state_upgrades
        and all(getattr(plugin, name) is None for name in (
            "bind", "settings", "migrate_state", "budget_connection", "insights", "export",
            "bank", "bank_ui", "storage", "ui", "bank_logos", "file_import",
            "merchant_logos", "interface",
        ))
    )


class ReadinessNamespace:
    """A detached read snapshot until commit; live callbacks after activation."""

    def __init__(self, owner, base, files):
        if not valid_plugin_name(owner) or type(files) is not dict or len(files) > MAX_CONTAINER_FILES:
            raise StoreError("The categorization namespace exceeds its supported size.")
        for name, raw in files.items():
            if name in {"state.json", "settings.json"} or type(raw) is not bytes:
                raise StoreError("The categorization namespace is invalid.")
            validate_file_name(f"plugins/{owner}/{name}")
        if sum(len(raw) for raw in files.values()) > MAX_BUNDLE_BYTES:
            raise StoreError("The categorization namespace exceeds its supported size.")
        self.owner, self.base = owner, base
        self.files = dict(files)
        self.lock = RLock()
        self.mode = "staged"

    def _name(self, name):
        if type(name) is not str or name in {"state.json", "settings.json"}:
            raise StoreError("Choose a data file within this plugin's budget namespace.")
        validate_file_name(f"plugins/{self.owner}/{name}")
        return name

    def call(self, operation, *arguments):
        with self.lock:
            if self.mode == "closed":
                raise StoreError("This categorization generation has retired.")
            if operation != "save_state":
                self._name(arguments[0])
            if self.mode == "staged":
                if operation == "read_file":
                    return self.files.get(arguments[0])
                raise StoreError("A candidate categorizer cannot change active saved models during preparation.")
            return getattr(self.base, operation)(*arguments)

    def context(self):
        return replace(
            self.base, settings=MappingProxyType(deepcopy(dict(self.base.settings))),
            state=MappingProxyType(deepcopy(dict(self.base.state))),
            save_state=partial(self.call, "save_state"),
            read_file=partial(self.call, "read_file"),
            write_file=partial(self.call, "write_file"),
            delete_file=partial(self.call, "delete_file"),
        )

    def activate(self):
        with self.lock:
            if self.mode != "staged":
                raise StoreError("The candidate categorization namespace is unavailable.")
            self.mode = "active"
            self.files.clear()

    def close(self):
        with self.lock:
            self.mode = "closed"
            self.files.clear()


def _descriptor(plugins):
    active = tuple(plugin for plugin in plugins if plugin.categorization)
    if len(active) > 1:
        raise StoreError("Enable only one categorization plugin per budget.")
    return active[0] if active else None


class CategorizationLifecycle:
    """Transition extension: preflight, drained finalization, then activation.

    ``snapshot(id)`` is owned by the host and returns the complete namespace for
    this budget/environment. Factories see only detached read-only callbacks.
    ``committed`` must run after the host's durable selection decision, before
    publishing its RuntimeCandidate. No model migration is implied.
    """

    def __init__(self, ledger, context_factory, snapshot):
        self.ledger, self.context_factory, self.snapshot = ledger, context_factory, snapshot

    @staticmethod
    def resource(service, owner):
        async def close():
            try:
                await run_in_threadpool(service.close)
            finally:
                for namespace in getattr(service, "_readiness_namespaces", ()):
                    namespace.close()

        resource = RuntimeResource(owner, close)
        service.runtime_resource = resource
        return resource

    def initial(self, plugins, saved_ids=()):
        service = CategorizationService(self.ledger, plugins, self.context_factory, saved_ids)
        service.descriptor = _descriptor(plugins)
        resource = self.resource(service, service.descriptor.id) if service.descriptor else None
        return service, resource

    @staticmethod
    def preflight(active, plugins):
        descriptor = _descriptor(plugins)
        if active.descriptor is not descriptor:
            active.ensure_idle()

    def finalize(self, preparation, candidate):
        active = preparation.active.services["categorization"]
        plugins = candidate.services["plugins"].descriptors()
        descriptor = _descriptor(plugins)
        self.preflight(active, plugins)
        if active.descriptor is descriptor:
            return candidate
        namespaces = {}
        service = None

        async def close():
            try:
                if service is not None:
                    await run_in_threadpool(service.close)
            finally:
                for namespace in namespaces.values():
                    namespace.close()

        # Own callbacks before a factory can retain them or fail part way through.
        owner = descriptor.id if descriptor else active.descriptor.id
        resource = preparation.own(RuntimeResource(owner, close))

        def context(identifier):
            base = self.context_factory(identifier)
            if descriptor is not None and identifier == descriptor.id:
                namespace = ReadinessNamespace(identifier, base, self.snapshot(identifier))
                namespaces[identifier] = namespace
                return namespace.context()
            return base

        service = CategorizationService(self.ledger, plugins, context, tuple(active.contexts))
        service.descriptor = descriptor
        service.runtime_resource = resource
        service._readiness_namespaces = tuple(namespaces.values())
        try:
            service.ready()
        except Exception:
            raise PluginError("The candidate categorizer could not read its saved models. The active categorizer is unchanged.",
                              code="categorization_readiness_failed", plugin_id=owner) from None
        resources = tuple(item for item in candidate.resources
                          if item is not getattr(active, "runtime_resource", None)) + (resource,)
        return RuntimeCandidate({**candidate.services, "categorization": service}, resources)

    @staticmethod
    def committed(candidate):
        service = candidate.services["categorization"]
        for namespace in getattr(service, "_readiness_namespaces", ()):
            if namespace.mode == "staged":
                namespace.activate()


class LeasedCategorization:
    """Stable ledger callbacks resolve the engine inside each runtime lease."""

    _METHODS = frozenset({"enabled", "predict", "after_import", "suggestions", "verify", "accepted", "action", "state", "_save"})

    def __init__(self, runtime):
        self._runtime = runtime
        self._handle = runtime.handle("categorization")

    def __getattr__(self, name):
        if name in self._METHODS:
            return partial(self._handle.call, name)
        # Host diagnostics/tests may inspect the currently active service. Public
        # operations use only the leased methods above, never these engine refs.
        if name in {"engines", "demo_engines", "contexts", "runs"}:
            with self._runtime.lease() as lease:
                return getattr(lease.service("categorization"), name)
        raise AttributeError(name)

    @property
    def error(self):
        with self._runtime.lease() as lease:
            return lease.service("categorization").error

    @error.setter
    def error(self, value):
        with self._runtime.lease() as lease:
            lease.service("categorization").error = value
