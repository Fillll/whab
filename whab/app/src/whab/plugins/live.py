"""Host integration for live selection of explicitly stateless report plugins.

The running environment is unchanged: this enables/disables selected installed
report hooks, not plugin code upgrades or worker isolation. All other families
retain their existing instances and restart-bound configuration behavior.
"""

import asyncio
from copy import deepcopy
from functools import partial
from threading import Lock
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from whab.budgets.runtime import RuntimeCandidate

from .configuration import validate_selection
from .contracts import contract_for
from .runtime import PluginError, PluginRegistry, load_descriptors

_REPORTS = frozenset({"reporting.insights", "reporting.export"})
_METHODS = frozenset({
    "describe", "descriptors", "bank_plugins", "interface_plugins", "file_import_plugins",
    "storage_plugin", "ui_plugin", "settings", "settings_action", "bank_logo",
    "bank_logo_provider", "logo_image", "merchant_logo_image", "insights", "export",
})


def live_report(plugin):
    """Explicit support excludes binders, saved state, settings and other hooks."""
    contract = contract_for(plugin)
    return (
        plugin.contract is not None and contract.lifecycle == "live"
        and (plugin.insights is not None or plugin.export is not None)
        and {capability.name for capability in contract.provides} <= _REPORTS
        and contract.state_version == 1 and not contract.state_upgrades
        and all(getattr(plugin, name) is None for name in (
            "bind", "settings", "migrate_state", "budget_connection", "categorization",
            "bank", "bank_ui", "storage", "ui", "bank_logos", "file_import",
            "merchant_logos", "interface",
        ))
    )


class RegistryGeneration:
    """Replace report hooks while retaining unchanged providers and their caches."""

    def __init__(self, registry, stable=None, workers=None):
        self.registry = registry
        self.stable = stable or registry
        self.workers = dict(workers or {})

    def describe(self):
        return self.registry.describe()

    def descriptors(self):
        return self.registry.descriptors()

    def storage_plugin(self, identifier):
        return self.registry.storage_plugin(identifier)

    def insights(self, snapshot):
        return self.registry.insights(snapshot)

    def export(self, plugin_id, snapshot):
        return self.registry.export(plugin_id, snapshot)

    def settings(self, plugin_id):
        if not any(plugin.id == plugin_id for plugin in self.descriptors()) or any(plugin.id == plugin_id and live_report(plugin) for plugin in self.descriptors()):
            return self.registry.settings(plugin_id)
        return self.stable.settings(plugin_id)

    def settings_action(self, plugin_id, action_id):
        if not any(plugin.id == plugin_id for plugin in self.descriptors()) or any(plugin.id == plugin_id and live_report(plugin) for plugin in self.descriptors()):
            return self.registry.settings_action(plugin_id, action_id)
        return self.stable.settings_action(plugin_id, action_id)

    def __getattr__(self, name):
        if name not in _METHODS:
            raise AttributeError(name)
        return getattr(self.stable, name)


class LeasedPluginRegistry:
    """Never cache a bound method from a replaceable registry generation."""

    def __init__(self, runtime):
        self._handle = runtime.handle("plugins")

    def __getattr__(self, name):
        if name not in _METHODS:
            raise AttributeError(name)
        return partial(self._handle.call, name)


class LivePluginSelection:
    """Validate report-only changes and publish saved selections under the gate."""

    def __init__(self, runtime, required, entrypoints, persist, session=None, nonlive=(), home=None):
        self.runtime = runtime
        self.required = frozenset(required)
        self.entrypoints = entrypoints
        self.persist = persist
        self.session = session
        self.nonlive = frozenset(nonlive)
        self.home = home
        self.initial_selection = frozenset(plugin.id for plugin in runtime._active.services["plugins"].descriptors()) - self.required
        self._lock = Lock()
        self._tickets = {}
        self._tasks = set()

    def status(self):
        with self._lock:
            operations = [dict(ticket) for ticket in self._tickets.values()]
        workers = []
        # Status must remain available while admission is gated. The immutable
        # active generation is safe to inspect here; no hook is invoked.
        for identifier, binding in self.runtime._active.services["plugins"].workers.items():
            workers.append({"plugin": identifier, "version": binding.environment.version,
                            "distribution": binding.environment.distribution,
                            "environment": binding.environment.identifier,
                            "health": "ready" if binding.worker.healthy else "failed"})
        return {"families": ["reporting.insights", "reporting.export"],
                "code_upgrades": self.home is not None, "runtime": self.runtime.status(),
                "workers": sorted(workers, key=lambda item: item["plugin"]), "operations": operations}

    def _candidate(self, selection, active):
        selection = validate_selection(selection)
        if self.required & set(selection):
            raise PluginError("Storage and user interfaces remain selected by the host. Choose only optional plugins.", code="required_plugin")
        current = {plugin.id: plugin for plugin in active.descriptors()}
        unavailable = set(self.session.missing_plugins) if self.session else set()
        previous = (set(current) - self.required) | unavailable
        changed = previous ^ set(selection)
        added = tuple(sorted(set(selection) - set(current) - unavailable))
        loaded = {plugin.id: plugin for plugin in load_descriptors(added, self.entrypoints())}
        for identifier in sorted(changed):
            plugin = current.get(identifier) or loaded.get(identifier)
            if plugin is None or identifier in self.nonlive or not live_report(plugin):
                raise PluginError(
                    "This change requires a restart. Live selection currently supports only explicitly live, stateless reporting/export plugins without settings or binders.",
                    code="restart_required", plugin_id=identifier,
                )
        # Injectable host backends need not have a discovery descriptor. Do
        # not invent an active storage plugin while changing their reports.
        selected = tuple(current[name] if name in current else loaded[name]
                         for name in sorted((self.required & set(current)) | set(selection))
                         if name not in unavailable)
        registry = PluginRegistry(selected)
        if self.session:
            from whab.budgets.plugin_state import check_saved_states

            manifest = deepcopy(self.session.manifest)
            for record in manifest["plugins"]:
                record["enabled"] = record["id"] in self.required | set(selection)
            check_saved_states(manifest, registry.descriptors())
        return RegistryGeneration(registry, active.stable,
                                  {name: binding for name, binding in active.workers.items() if name in selection})

    def _preflight(self, selection):
        # The scheduling request already owns a generation. This scope also
        # permits direct host callers to validate without retaining raw services.
        with self.runtime.lease() as lease:
            self._candidate(selection, lease.service("plugins"))

    def preflight(self, selection):
        self._preflight(validate_selection(selection))

    def schedule(self, selection, *, validated=False):
        selection = validate_selection(selection)
        if not validated:
            self._preflight(selection)
        identifier = str(uuid4())
        with self._lock:
            if sum(ticket["state"] in {"queued", "running"} for ticket in self._tickets.values()) >= 32:
                raise PluginError("Too many live transitions are pending. Wait for them to finish before retrying.", code="transition_busy")
            self._tickets[identifier] = {"id": identifier, "state": "queued", "error": None}
            while len(self._tickets) > 32:
                oldest = next(iter(self._tickets))
                if self._tickets[oldest]["state"] in {"queued", "running"}:
                    break
                self._tickets.pop(oldest)
        task = self.runtime.schedule(self._activate(selection, identifier))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return identifier

    async def activate(self, selection):
        """Awaitable host API, called outside an admitted budget operation."""
        selection = validate_selection(selection)
        await run_in_threadpool(self._preflight, selection)
        return await self._activate(selection)

    async def upgrade(self, plugin_id, approvals):
        from .worker_selection import upgrade_report

        if self.home is None:
            raise PluginError("This runtime cannot persist worker environments.", code="restart_required", plugin_id=plugin_id)
        return await upgrade_report(self, self.home, plugin_id, approvals)

    async def recover(self, authority):
        from .worker_selection import validate_saved_generation

        async def validate(chosen, active, candidate):
            generation = active if chosen == "active" else candidate
            return await run_in_threadpool(validate_saved_generation, self, generation)

        # Recovery itself cannot hold a request lease from the gated budget.
        task = self.runtime.schedule(self.runtime.recover(authority, validate))
        return await asyncio.shield(task)

    def schedule_upgrade(self, plugin_id, approvals):
        identifier = str(uuid4())
        with self._lock:
            if sum(ticket["state"] in {"queued", "running"} for ticket in self._tickets.values()) >= 32:
                raise PluginError("Too many live transitions are pending.", code="transition_busy", plugin_id=plugin_id)
            self._tickets[identifier] = {"id": identifier, "state": "queued", "error": None}

            for previous in tuple(self._tickets):
                if len(self._tickets) <= 32:
                    break
                if self._tickets[previous]["state"] not in {"queued", "running"}:
                    self._tickets.pop(previous)

        async def run():
            with self._lock:
                self._tickets[identifier]["state"] = "running"
            try:
                result = await self.upgrade(plugin_id, approvals)
            except (Exception, asyncio.CancelledError) as error:
                with self._lock:
                    self._tickets[identifier].update(state="failed", error={
                        "code": error.code if isinstance(error, PluginError) else "worker_upgrade_failed",
                        "plugin_id": plugin_id, "message": "The report upgrade failed. Inspect runtime authority and preserve the active environment before retrying.",
                    })
                raise
            with self._lock:
                self._tickets[identifier]["state"] = "complete"
            return result

        task = self.runtime.schedule(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return identifier

    async def _activate(self, selection, ticket=None):
        async def prepare(preparation):
            if ticket:
                with self._lock:
                    self._tickets[ticket]["state"] = "running"
            active = preparation.active.services["plugins"]
            candidate = await run_in_threadpool(self._candidate, selection, active)
            if self.home is not None:
                from .worker_selection import restore_workers

                registry, bindings = await run_in_threadpool(restore_workers, self.home, candidate.registry, candidate.workers)
                candidate = RegistryGeneration(registry, active.stable, bindings)
                for name, binding in bindings.items():
                    if name not in active.workers:
                        preparation.own(binding.resource)
            removed = {id(binding.resource) for name, binding in active.workers.items() if name not in candidate.workers}
            resources = tuple(resource for resource in preparation.active.resources if id(resource) not in removed)
            resources += tuple(binding.resource for name, binding in candidate.workers.items() if name not in active.workers)
            return RuntimeCandidate({**preparation.active.services, "plugins": candidate}, resources)

        async def commit(candidate, _number):
            registry = candidate.services["plugins"]
            await run_in_threadpool(self.persist, selection, registry.descriptors())

        try:
            generation = await self.runtime.transition(prepare, commit=commit)
        except (PluginError, asyncio.CancelledError) as error:
            if ticket:
                diagnostic = ({"code": error.code, "plugin_id": error.plugin_id, "message": str(error)}
                              if isinstance(error, PluginError) else
                              {"code": "transition_interrupted", "plugin_id": None,
                               "message": "The runtime transition did not finish. Inspect runtime status before retrying."})
                with self._lock:
                    self._tickets[ticket].update(state="failed", error=diagnostic)
            raise
        if ticket:
            with self._lock:
                self._tickets[ticket]["state"] = "complete"
        return generation

    async def close(self):
        # Accepted transitions belong to the host, including after their HTTP
        # caller disconnected. Settle them before retiring host services.
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        await self.runtime.shutdown()
