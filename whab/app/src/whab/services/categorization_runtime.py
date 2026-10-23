"""Host-owned categorizer generations and read-only candidate namespaces.

An installed categorizer is trusted Python. Revocable callbacks prevent an old
adapter from using host namespace access after retirement; they are not an OS
security boundary. A candidate factory cannot publish files or state.
"""

from copy import deepcopy
from dataclasses import replace
from functools import partial
from importlib import metadata
from threading import RLock
from types import MappingProxyType

from starlette.concurrency import run_in_threadpool

from whab.budgets.container import MAX_CONTAINER_FILES, validate_file_name
from whab.budgets.document import MAX_BUNDLE_BYTES, portable_values, validate_manifest
from whab.budgets.runtime import RuntimeCandidate, RuntimeResource
from whab.domain.errors import StoreError
from whab.plugins.runtime import PluginError
from whab.plugins.sdk import valid_plugin_name

from .categorization import CategorizationService


def live_categorizer(plugin):
    """The current adapter supports one explicit, independently scoped engine."""
    from whab.plugins.worker_adapters import supports_live_plugin

    return plugin.categorization is not None and supports_live_plugin(plugin)


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

    def __init__(self, ledger, context_factory, snapshot, *, worker_home=None, environment="sandbox", materialization=None):
        self.ledger, self.context_factory, self.snapshot = ledger, context_factory, snapshot
        self.worker_home, self.environment, self.materialization = worker_home, environment, materialization
        self._effects = {}

    def _namespace(self, owner):
        from whab.plugins.namespace_effects import NamespaceScope

        from .namespace_effects import NamespaceEffects

        if owner not in self._effects:
            self._effects[owner] = NamespaceEffects(
                self.worker_home / "plugin-workers" / "namespace-effects" / self.environment / owner,
                NamespaceScope(self.ledger.budget_id, self.environment, owner),
                lambda: self.context_factory(owner), lambda: self.snapshot(owner),
                lock=self.ledger._lock, materialization=self.materialization)
        return self._effects[owner]

    def _service(self, plugins, saved_ids=(), *, staged=False):
        from whab.plugins import Categorization
        from whab.plugins.categorization_worker import WorkerCategorizationFactory
        from whab.plugins.report_worker import ReportWorker

        factories, accesses, namespaces = [], {}, {}
        selected = []
        try:
            for plugin in plugins:
                factory = plugin.categorization.factory if plugin.categorization else None
                if self.worker_home is not None and live_categorizer(plugin):
                    if not isinstance(factory, WorkerCategorizationFactory):
                        entries = tuple(entry for entry in metadata.entry_points(group="whab.plugins")
                                        if entry.name == plugin.id and getattr(entry, "dist", None) is not None)
                        if entries:
                            entry, = entries
                            worker = ReportWorker(plugin.id, expected_distribution=entry.dist.metadata["Name"], expected_version=entry.dist.version)
                            factory = WorkerCategorizationFactory(worker, owns_worker=True)
                    if isinstance(factory, WorkerCategorizationFactory):
                        # Own an installed child before journal open/recovery can
                        # fail; candidate worker bindings have their own owner.
                        factories.append(factory)
                        manager = self._namespace(plugin.id)
                        manager.recover()
                        accesses[plugin.id] = manager.context(writable=not staged)
                        factory = factory.bind(manager, accesses[plugin.id])
                        factories[-1] = factory
                        plugin = replace(plugin, categorization=Categorization(factory))
                selected.append(plugin)

            def context(owner):
                if self.worker_home is not None and owner in self._effects:
                    if owner not in accesses:
                        accesses[owner] = self._namespace(owner).context(writable=not staged)
                    return accesses[owner].context()
                base = self.context_factory(owner)
                if staged:
                    namespace = ReadinessNamespace(owner, base, self.snapshot(owner))
                    namespaces[owner] = namespace
                    return namespace.context()
                return base

            service = CategorizationService(self.ledger, selected, context, saved_ids)
            service._worker_factories = tuple(factories)
            service._namespace_access = tuple(accesses.values())
            service._readiness_namespaces = tuple(namespaces.values())
            return service
        except BaseException:
            for factory in factories:
                try:
                    factory.close()
                except Exception:
                    pass
            for access in (*accesses.values(), *namespaces.values()):
                access.close()
            raise

    def save_plugin_data(self, session, owner, *, settings=None, state=None):
        """Keep accepted host state edits in a retained journal, even if disabled.

        Configuration remains host-owned. Validate the complete manifest before
        the namespace decision, then defer its checkpoint until the optional
        settings save also finishes. Native materialization uses the original
        context factory, so it never re-enters this route-level interception.
        """
        manager = self._effects.get(owner)
        if manager is None or state is None:
            return False
        if session.id != self.ledger.budget_id:
            raise StoreError("Plugin state belongs to another budget.")
        with self.ledger._lock, self.materialization():
            updated = deepcopy(session.manifest)
            record = next(row for row in updated["plugins"] if row["id"] == owner)
            for key, value in (("settings", settings), ("state", state)):
                if value is not None:
                    if type(value) is not dict:
                        raise StoreError("Plugin settings and state must be objects.")
                    record[key] = portable_values(value)
            validate_manifest(updated)
            from whab.plugins.namespace_effects import NamespaceSnapshot
            manager.execute("host-state-edit", {"state": record["state"]},
                lambda before: (NamespaceSnapshot(record["state"], dict(before.files)), None), mutating=True)
            if settings is not None:
                session.save_plugin_data(owner, settings=record["settings"])
        return True

    def close(self):
        for manager in self._effects.values():
            manager.close()
        self._effects.clear()

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
        if self.worker_home is not None:
            directory = self.worker_home / "plugin-workers" / "namespace-effects" / self.environment
            owners = []
            if directory.exists():
                for child in directory.iterdir():
                    if len(owners) >= 100 or not valid_plugin_name(child.name) or child.is_symlink() or not child.is_dir():
                        raise StoreError("Invalid saved categorization namespace.")
                    owners.append(child.name)
            for owner in sorted(owners):
                self._namespace(owner).recover()
        service = self._service(plugins, saved_ids)
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
        service = None

        async def close():
            try:
                if service is not None:
                    await run_in_threadpool(service.close)
            finally:
                if service is not None:
                    for namespace in getattr(service, "_readiness_namespaces", ()):
                        namespace.close()

        # Own callbacks before a factory can retain them or fail part way through.
        owner = descriptor.id if descriptor else active.descriptor.id
        resource = preparation.own(RuntimeResource(owner, close))

        service = self._service(plugins, tuple(active.contexts), staged=True)
        service.descriptor = descriptor
        service.runtime_resource = resource
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
        for access in getattr(service, "_namespace_access", ()):
            if not access.writable:
                access.activate()


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
