"""Generation-bound report, branding, parser and categorization services.

Only explicit supported adapters participate; financial storage, bank services
and other resource families keep their existing runtime instances.
"""

import asyncio
from copy import deepcopy
from functools import partial
from threading import Lock
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from whab.budgets.runtime import RuntimeCandidate
from whab.services.categorization_runtime import live_categorizer

from .configuration import validate_selection
from .contracts import contract_for
from .interface_adapter import validate_interface_owners
from .runtime import PluginError, PluginRegistry, load_descriptors
from .worker_adapters import LIVE_CAPABILITIES, supports_live_plugin
from .worker_resources import capture_resources

_REPORTS = frozenset({"reporting.insights", "reporting.export"})
_METHODS = frozenset({
    "describe", "descriptors", "bank_plugins", "interface_plugins", "file_import_plugins",
    "storage_plugin", "ui_plugin", "settings", "settings_action", "bank_logo",
    "bank_logo_provider", "logo_image", "merchant_logo_image", "insights", "export",
    "parser_identity", "parser_requires_identity", "import_resource",
    "ui_templates",
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
            "branding_factory",
        ))
    )


class RegistryGeneration:
    """Replace optional hooks while retaining stable financial/provider services."""

    def __init__(self, registry, stable=None, workers=None, previous=None, resources_enabled=None):
        self.registry = registry
        validate_interface_owners(registry.descriptors())
        self.stable = stable or registry
        self.workers = dict(workers or {})
        self.resources_enabled = previous.resources_enabled if resources_enabled is None and previous else (True if resources_enabled is None else resources_enabled)
        self._parsers = {}
        self._resources = dict(previous._resources) if previous else {}
        self._legacy_tokens = dict(previous._legacy_tokens) if previous else {}
        for plugin in registry.file_import_plugins():
            before = previous._parsers.get(plugin.id) if previous else None
            token = before[1] if before and before[0] is plugin.file_import else uuid4().hex
            self._parsers[plugin.id] = plugin.file_import, token, supports_live_plugin(plugin)
            if not before or before[0] is not plugin.file_import:
                try:
                    resources = capture_resources(plugin.file_import) if self.resources_enabled else None
                except Exception:
                    raise PluginError("Import plugin UI resources are missing or invalid. Reinstall or disable the plugin.",
                                      code="missing_resources", plugin_id=plugin.id) from None
                if resources is not None:
                    self._resources[plugin.id, token] = resources
                    self._legacy_tokens.setdefault(plugin.id, token)
        # Old HTML keeps its exact public files or receives 404 after bounded
        # retention. It can never fall through to a newer parser's JavaScript.
        for identifier in {key[0] for key in self._resources}:
            keys = [key for key in self._resources if key[0] == identifier]
            while len(keys) > 8 or sum(sum(map(len, self._resources[key].files.values())) for key in keys) > 8 * 1024 * 1024:
                removable = next((key for key in keys if key != (identifier, self._parsers.get(identifier, (None, None))[1])), None)
                if removable is None:
                    break
                del self._resources[removable]
                keys.remove(removable)

    def describe(self):
        return self.registry.describe()

    def descriptors(self):
        return self.registry.descriptors()

    def storage_plugin(self, identifier):
        return self.registry.storage_plugin(identifier)

    def ui_plugin(self, identifier):
        return self.registry.ui_plugin(identifier)

    def ui_templates(self):
        """Bound public extension templates for the operation's visual worker."""
        from .worker_resources import capture_resource_paths

        templates = {}
        for plugin in self.bank_plugins():
            if plugin.bank_ui is not None:
                resources = capture_resource_paths(plugin.bank_ui.templates, plugin.bank_ui.static)
                templates.update({f"plugins/{plugin.id}/{name.removeprefix('templates/')}": raw.decode("utf-8")
                                  for name, raw in resources.files.items() if name.startswith("templates/")})
        for identifier, (_, token, _) in self._parsers.items():
            resources = self._resources.get((identifier, token))
            if resources is not None:
                templates.update({f"imports/{identifier}/{token}/{name.removeprefix('templates/')}": raw.decode("utf-8")
                                  for name, raw in resources.files.items() if name.startswith("templates/")})
        if len(templates) > 256 or sum(len(raw.encode("utf-8")) for raw in templates.values()) > 4 * 1024 * 1024:
            raise PluginError("The combined interface extension resources exceed their supported bounds.", code="missing_resources")
        return templates

    def insights(self, snapshot):
        return self.registry.insights(snapshot)

    def export(self, plugin_id, snapshot):
        return self.registry.export(plugin_id, snapshot)

    def file_import_plugins(self):
        return self.registry.file_import_plugins()

    def interface_plugins(self):
        return self.registry.interface_plugins()

    def parser_identity(self, plugin_id):
        parser = self._parsers.get(plugin_id)
        return parser[1] if parser else None

    def parser_requires_identity(self, plugin_id):
        parser = self._parsers.get(plugin_id)
        return bool(parser and parser[2])

    def import_resource(self, plugin_id, token, name):
        token = token or self._legacy_tokens.get(plugin_id)
        resources = self._resources.get((plugin_id, token))
        return resources.files.get(name) if resources else None

    def bank_logo(self, institution, *, website=None):
        return self.registry.bank_logo(institution, website=website)

    def bank_logo_provider(self, plugin_id=None):
        return self.registry.bank_logo_provider(plugin_id)

    def _live(self, plugin_id):
        return any(plugin.id == plugin_id and supports_live_plugin(plugin) for plugin in self.descriptors())

    def logo_image(self, plugin_id, key):
        if not any(plugin.id == plugin_id for plugin in self.descriptors()):
            return None
        return (self.registry if self._live(plugin_id) else self.stable).logo_image(plugin_id, key)

    def merchant_logo_image(self, plugin_id, key):
        if not any(plugin.id == plugin_id for plugin in self.descriptors()):
            return None
        return (self.registry if self._live(plugin_id) else self.stable).merchant_logo_image(plugin_id, key)

    def settings(self, plugin_id):
        if not any(plugin.id == plugin_id for plugin in self.descriptors()) or self._live(plugin_id):
            return self.registry.settings(plugin_id)
        return self.stable.settings(plugin_id)

    def settings_action(self, plugin_id, action_id):
        if not any(plugin.id == plugin_id for plugin in self.descriptors()) or self._live(plugin_id):
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
    """Validate supported changes and publish saved selections under the gate."""

    def __init__(self, runtime, required, entrypoints, persist, session=None, nonlive=(), home=None):
        self.runtime = runtime
        self.required = frozenset(required)
        self.entrypoints = entrypoints
        self.persist = persist
        self.session = session
        self.nonlive = frozenset(nonlive)
        self.home = home
        self.categorization = None
        self.prepare_interfaces = None
        self.visual_ids = frozenset()
        self.visual = None
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
        upgrades = set(LIVE_CAPABILITIES) - {"interface.ui"}
        if self.visual is not None:
            upgrades.add("interface.ui")
        families = set(upgrades)
        if self.categorization is not None:
            families.add("categorization")
        return {"families": sorted(families),
                "code_upgrade_families": sorted(upgrades) if self.home is not None else [],
                "host_only_families": [],
                "code_upgrades": self.home is not None, "runtime": self.runtime.status(),
                "workers": sorted(workers, key=lambda item: item["plugin"]), "operations": operations}

    def _candidate(self, selection, active, *, bind=False):
        selection = validate_selection(selection)
        if (self.required | self.visual_ids) & set(selection):
            raise PluginError("Storage and user interfaces remain selected by the host. Choose only optional plugins.", code="required_plugin")
        current = {plugin.id: plugin for plugin in active.descriptors()}
        unavailable = set(self.session.missing_plugins) if self.session else set()
        previous = (set(current) - self.required) | unavailable
        changed = previous ^ set(selection)
        added = tuple(sorted(set(selection) - set(current) - unavailable))
        loaded = {plugin.id: plugin for plugin in load_descriptors(added, self.entrypoints())}
        for identifier in sorted(changed):
            plugin = current.get(identifier) or loaded.get(identifier)
            if plugin is not None and plugin.ui is not None:
                raise PluginError("Use the dedicated visual interface controls to prepare browser views and resources.",
                                  code="required_plugin", plugin_id=identifier)
            if plugin is None or identifier in self.nonlive or not (supports_live_plugin(plugin) or (self.categorization is not None and live_categorizer(plugin))):
                raise PluginError(
                    "This plugin needs an available live capability adapter. Finish its active work or restart WHAB with a compatible plugin selection.",
                    code="restart_required", plugin_id=identifier,
                )
        # Injectable host backends need not have a discovery descriptor. Do
        # not invent an active storage plugin while changing their reports.
        selected = tuple(current[name] if name in current else loaded[name]
                         for name in sorted((self.required & set(current)) | set(selection))
                         if name not in unavailable)
        registry = PluginRegistry(selected)
        validate_interface_owners(registry.descriptors())
        if bind:
            registry = registry.bind_branding(added)
        if self.session:
            from whab.budgets.plugin_state import check_saved_states

            manifest = deepcopy(self.session.manifest)
            for record in manifest["plugins"]:
                record["enabled"] = record["id"] in self.required | set(selection)
            check_saved_states(manifest, registry.descriptors())
        return RegistryGeneration(registry, active.stable,
                                  {name: binding for name, binding in active.workers.items() if name in set(selection) | self.required}, previous=active)

    def _preflight(self, selection):
        # The scheduling request already owns a generation. This scope also
        # permits direct host callers to validate without retaining raw services.
        with self.runtime.lease() as lease:
            candidate = self._candidate(selection, lease.service("plugins"))
            if self.categorization is not None:
                self.categorization.preflight(lease.service("categorization"), candidate.descriptors())

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
            if active.services.get("visual") is not candidate.services.get("visual"):
                return False
            generation = active if chosen == "active" else candidate
            valid = await run_in_threadpool(validate_saved_generation, self, generation)
            if valid is True and self.categorization is not None:
                self.categorization.committed(generation)
            return valid

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
                        "plugin_id": plugin_id, "message": "The plugin upgrade failed. Inspect runtime authority and preserve the active environment before retrying.",
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
        def changed_owner():
            # Diagnostic IDs come from an immutable generation, without invoking
            # hooks or admission. Awaiting a thread here could reorder already
            # scheduled transitions before they reach the serial coordinator.
            active = self.runtime._active
            current_ids = {plugin.id for plugin in active.services["plugins"].descriptors()} - self.required
            changed = set(selection) - current_ids or current_ids - set(selection)
            return next(iter(changed)) if len(changed) == 1 else None

        async def prepare(preparation):
            if ticket:
                with self._lock:
                    self._tickets[ticket]["state"] = "running"
            active = preparation.active.services["plugins"]
            candidate = await run_in_threadpool(partial(self._candidate, selection, active, bind=True))
            if self.home is not None:
                from .worker_selection import restore_workers

                registry, bindings = await run_in_threadpool(restore_workers, self.home, candidate.registry, candidate.workers,
                                                            resource_owner=preparation.own)
                candidate = RegistryGeneration(registry, active.stable, bindings, previous=active)
                for name, binding in bindings.items():
                    if name not in active.workers:
                        preparation.own(binding.resource)
            for plugin in candidate.descriptors():
                if plugin.id not in {row.id for row in active.descriptors()} and plugin.id not in candidate.workers and plugin.bank_logos is not None and plugin.bank_logos.configure_cache is not None:
                    await run_in_threadpool(plugin.bank_logos.configure_cache, self.home / "cache" / "plugins" / plugin.id)
            removed = {id(binding.resource) for name, binding in active.workers.items() if name not in candidate.workers}
            resources = tuple(resource for resource in preparation.active.resources if id(resource) not in removed)
            resources += tuple(binding.resource for name, binding in candidate.workers.items() if name not in active.workers)
            services = {**preparation.active.services, "plugins": candidate}
            if self.prepare_interfaces is not None:
                interfaces = await run_in_threadpool(self.prepare_interfaces, candidate, preparation.active.services.get("interfaces"))
                previous = preparation.active.services.get("interfaces")
                if previous is not interfaces:
                    if interfaces.resource is not None:
                        preparation.own(interfaces.resource)
                    resources = tuple(resource for resource in resources if previous is None or resource is not previous.resource)
                    if interfaces.resource is not None:
                        resources += (interfaces.resource,)
                services["interfaces"] = interfaces
            return RuntimeCandidate(services, resources)

        async def commit(candidate, _number):
            registry = candidate.services["plugins"]
            await run_in_threadpool(self.persist, selection, registry.descriptors())
            if self.categorization is not None:
                self.categorization.committed(candidate)

        async def finalize(preparation, candidate):
            if self.categorization is None:
                return candidate
            return await run_in_threadpool(self.categorization.finalize, preparation, candidate)

        try:
            owner = changed_owner()
            generation = await self.runtime.transition(prepare, finalize=finalize, commit=commit, plugin_id=owner)
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
