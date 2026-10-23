"""Controlled visual handover, distinct from optional report/API selection."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from time import monotonic
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from whab.budgets.runtime import RuntimeCandidate
from whab.web.visual_interface import VisualGeneration

from .environments import prepare_environment
from .live import RegistryGeneration
from .runtime import PluginError, PluginRegistry, load_descriptors
from .sdk import valid_plugin_name
from .ui_resources import UIResourceArchive
from .ui_selection import UISelectionStore
from .ui_views import BrowserViews
from .ui_worker import readiness_arguments, supports_live_visual
from .worker_selection import _start, reopen_environment


def _error(code, identifier=None):
    messages = {
        "ui_selection_conflict": "The visual selection changed. Prepare and review a fresh interface change.",
        "ui_transition_required": "This interface requires restart. Choose an interface with supported browser participation for live changes.",
        "ui_preview_unavailable": "This interface preview is unavailable or expired. Prepare a fresh preview.",
        "ui_recovery_required": "Validate the visual interface's durable selection before resuming this budget.",
    }
    return PluginError(messages[code], code=code, plugin_id=identifier)


def worker_record(binding):
    return ({"environment": binding.environment.identifier, "distribution": binding.environment.distribution,
             "version": binding.environment.version} if binding else None)


def read_visual_choice(home, budget_id, environment, initial):
    """Read before loading descriptors; a missing choice retains initial WHAB_UI."""
    path = Path(home) / "visual-ui" / "visual-selection.json"
    if not path.exists() and not path.is_symlink():
        return None
    with UISelectionStore(home, budget_id, environment, initial or None) as selected:
        return selected.snapshot()


def apply_visual_choice(settings, choice):
    if choice is None:
        return settings
    return replace(settings, ui=choice["selection"] or "",
                   plugins=tuple(name for name in settings.plugins if name not in choice["known"]))


def restore_visual_worker(home, registry, choice, *, worker_owner=None):
    """The single UI decision owns this exact worker; never read optional refs."""
    if not choice or choice["worker"] is None:
        return registry, {}
    identifier, reference = choice["selection"], choice["worker"]
    template = registry.ui_plugin(identifier)
    prepared = reopen_environment(Path(home) / "plugin-workers" / "environments", identifier, reference["environment"])
    if (prepared.distribution, prepared.version) != (reference["distribution"], reference["version"]):
        raise _error("ui_recovery_required", identifier)
    proxy, binding = _start(template, prepared, home, worker_owner=worker_owner)
    try:
        visual = VisualGeneration.prepare(proxy, UIResourceArchive(home), code_identity=prepared.identifier)
        if visual.generation != choice["generation"]:
            raise _error("ui_recovery_required", identifier)
        return PluginRegistry(tuple(proxy if plugin.id == identifier else plugin for plugin in registry.descriptors())), {identifier: binding}
    except BaseException:
        binding.worker.close()
        raise


class VisualController:
    """Preparation stays admitted so browser autosaves can finish before draining."""

    def __init__(self, runtime, selection, home, entrypoints, manager, live, *, session=None, browser_timeout=10, clock=monotonic):
        self.runtime, self.selection, self.home = runtime, selection, Path(home)
        self.entrypoints, self.manager, self.live, self.session = entrypoints, manager, live, session
        self.browser_timeout, self.clock = browser_timeout, clock
        self.previews, self.operations, self.preview_operations, self.tasks = {}, {}, {}, set()
        self._uncertain = None
        self.on_change = None
        self.closed = False
        visual = runtime._active.services["visual"]
        current = selection.snapshot()
        if current["selection"] != visual.plugin_id:
            raise _error("ui_recovery_required", visual.plugin_id)
        binding = runtime._active.services["plugins"].workers.get(visual.plugin_id)
        if current["worker"] != worker_record(binding):
            raise _error("ui_recovery_required", visual.plugin_id)
        if current["generation"] != visual.generation:
            if current["worker"] is not None:
                raise _error("ui_recovery_required", visual.plugin_id)
            # An explicit application restart may install a new native bootstrap;
            # retain old public resources but never replace a saved worker here.
            selection.publish({"id": str(uuid4()), "selection": visual.plugin_id, "generation": visual.generation,
                               "worker": None}, selection.revision(current))
            current = selection.snapshot()
        self.configured = current
        self.views = BrowserViews(visual.generation)
        self._configure(current)

    def _configure(self, record):
        with self.manager._lock:
            self.manager.settings = apply_visual_choice(self.manager.settings, record)
            self.manager.required = tuple(name for name in (self.manager.settings.storage, record["selection"]) if name)
            self.manager.visual_known = frozenset(record["known"])
            self.live.required = frozenset(self.manager.required)
            self.live.visual_ids = frozenset(record["known"])
        self.configured = deepcopy(record)
        if self.on_change is not None:
            self.on_change(record["selection"])

    def status(self):
        visual = self.runtime._active.services["visual"]
        binding = self.runtime._active.services["plugins"].workers.get(visual.plugin_id)
        return {"active": {"plugin_id": visual.plugin_id, "resource_generation": visual.generation,
                           "worker": worker_record(binding), "view_protocol": visual.ui.view_protocol if visual.ui else None},
                "configured": {"plugin_id": self.configured["selection"], "resource_generation": self.configured["generation"]},
                "selection_revision": self.selection.revision(self.configured), "runtime": self.runtime.status(),
                "browser_preparation": deepcopy(self.views.pending), "operations": deepcopy(list(self.operations.values())),
                "decisions": [{"id": row["id"], "plugin_id": row["selection"], "resource_generation": row["generation"]}
                              for row in self.configured["decisions"]]}

    def _template(self, identifier):
        if identifier is None:
            return None
        if not valid_plugin_name(identifier):
            raise _error("ui_transition_required")
        plugin, = load_descriptors((identifier,), self.entrypoints())
        if not supports_live_visual(plugin):
            raise _error("ui_transition_required", identifier)
        return plugin

    async def preview(self, identifier, *, approvals=(), use_installed=False):
        if self.closed:
            raise _error("ui_preview_unavailable")
        if type(use_installed) is not bool or type(approvals) is not tuple or identifier is None and approvals:
            raise _error("ui_transition_required", identifier)
        async with self.runtime.request() as lease:
            active = lease.service("visual")
            if active.ui is not None and active.ui.view_protocol != 1:
                raise _error("ui_transition_required", active.plugin_id)
        template = await run_in_threadpool(self._template, identifier)
        async with self.runtime.request() as lease:
            registry = lease.service("plugins")
            PluginRegistry(tuple(plugin for plugin in registry.descriptors() if plugin.ui is None) + ((template,) if template else ()))
        record = await run_in_threadpool(self.selection.snapshot)
        now = self.clock()
        self.previews = {key: plan for key, plan in self.previews.items() if now - plan["created"] <= 300}
        if len(self.previews) >= 32:
            raise _error("ui_preview_unavailable", identifier)
        key = str(uuid4())
        self.previews[key] = {"created": now, "selection": identifier, "approvals": approvals,
                              "use_installed": use_installed, "revision": self.selection.revision(record)}
        return {"preview_id": key, "plugin_id": identifier, "code": "approved-wheels" if approvals else
                "installed" if use_installed or record["workers"].get(identifier) is None else "saved-worker",
                "selection_revision": self.selection.revision(record), "requires_saved_views": True}

    def schedule(self, preview_id):
        if self.closed:
            raise _error("ui_preview_unavailable")
        if preview_id in self.preview_operations:
            return self.preview_operations[preview_id]
        plan = self.previews.pop(preview_id, None)
        if plan is None or self.clock() - plan["created"] > 300:
            raise _error("ui_preview_unavailable")
        if sum(row["state"] in {"queued", "running"} for row in self.operations.values()) >= 8:
            raise _error("ui_preview_unavailable")
        identifier = str(uuid4())
        self.operations[identifier] = {"id": identifier, "preview_id": preview_id,
                                      "plugin_id": plan["selection"], "state": "queued", "error": None}
        self.preview_operations[preview_id] = identifier
        while len(self.operations) > 32:
            removable = next((key for key, row in self.operations.items() if row["state"] not in {"queued", "running"}), None)
            if removable is None:
                break
            self.operations.pop(removable)
            self.preview_operations = {key: value for key, value in self.preview_operations.items() if value != removable}

        async def run():
            self.operations[identifier]["state"] = "running"
            try:
                generation = await self.activate(plan, identifier)
            except (Exception, asyncio.CancelledError) as error:
                self.operations[identifier].update(state="failed", error={
                    "code": getattr(error, "code", "ui_transition_failed"), "plugin_id": plan["selection"],
                    "message": "The interface change did not finish. Inspect its runtime status and preserve the selected resources before retrying.",
                })
                raise
            self.operations[identifier].update(state="complete", generation=generation)
            return generation

        task = self.runtime.schedule(run())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return identifier

    async def activate(self, plan, operation_id):
        current = await run_in_threadpool(self.selection.snapshot)
        if self.selection.revision(current) != plan["revision"]:
            raise _error("ui_selection_conflict", plan["selection"])
        proposal = None

        async def prepare(preparation):
            nonlocal proposal
            record = await run_in_threadpool(self.selection.snapshot)
            if self.selection.revision(record) != plan["revision"]:
                raise _error("ui_selection_conflict", plan["selection"])
            active = preparation.active.services["plugins"]
            template = await run_in_threadpool(self._template, plan["selection"])
            binding, proxy = None, template
            if template is not None:
                from .worker_selection import PreparationWorkers

                probes = PreparationWorkers(preparation, template.id)
                reference = record["workers"].get(template.id) if not plan["use_installed"] else None
                if plan["approvals"]:
                    prepared = await run_in_threadpool(prepare_environment, self.home / "plugin-workers" / "environments", template.id,
                                                      plan["approvals"], worker_owner=probes.own, worker_retired=probes.retired)
                elif reference is not None:
                    prepared = await run_in_threadpool(reopen_environment, self.home / "plugin-workers" / "environments", template.id, reference["environment"])
                    if (prepared.distribution, prepared.version) != (reference["distribution"], reference["version"]):
                        raise _error("ui_recovery_required", template.id)
                else:
                    prepared = None
                if prepared is not None:
                    proxy, binding = await run_in_threadpool(_start, template, prepared, self.home,
                                                            resource_owner=preparation.own)
                    preparation.own(binding.resource)
            previous_ui = {plugin.id for plugin in active.descriptors() if plugin.ui is not None}
            descriptors = tuple(plugin for plugin in active.descriptors() if plugin.ui is None) + ((proxy,) if proxy else ())
            registry = PluginRegistry(descriptors)
            if self.session:
                from whab.budgets.plugin_state import check_saved_states

                check_saved_states(self.session.manifest, registry.descriptors())
            workers = {name: value for name, value in active.workers.items() if name not in previous_ui}
            if binding is not None:
                workers[proxy.id] = binding
            candidate = RegistryGeneration(registry, active.stable, workers, previous=active, resources_enabled=proxy is not None)
            await run_in_threadpool(candidate.ui_templates)
            visual = await run_in_threadpool(VisualGeneration.prepare, proxy, UIResourceArchive(self.home),
                                            code_identity=binding.environment.identifier if binding else None)
            if visual.ui is not None:
                from .ui_render import render_page

                for page in ("budget", "transactions", "accounts", "insights"):
                    if visual.ui.render is not None:
                        await run_in_threadpool(visual.ui.render, readiness_arguments(page))
                    else:
                        await run_in_threadpool(render_page, visual.ui, readiness_arguments(page))
            proposal = await self.views.prepare(visual.generation, timeout=self.browser_timeout)
            removed = {id(value.resource) for name, value in active.workers.items() if name in previous_ui}
            resources = tuple(value for value in preparation.active.resources if id(value) not in removed)
            if binding is not None:
                resources += (binding.resource,)
            return RuntimeCandidate({**preparation.active.services, "plugins": candidate, "visual": visual}, resources)

        async def finalize(_preparation, candidate):
            self.views.validate(proposal)
            return candidate

        async def commit(candidate, _number):
            visual = candidate.services["visual"]
            binding = candidate.services["plugins"].workers.get(visual.plugin_id)
            decision = {"id": operation_id, "selection": visual.plugin_id, "generation": visual.generation, "worker": worker_record(binding)}
            await run_in_threadpool(self.selection.publish, decision, plan["revision"])
            record = await run_in_threadpool(self.selection.snapshot)
            self._configure(record)
            self.views.publish(proposal)

        try:
            return await self.runtime.transition(prepare, finalize=finalize, commit=commit, plugin_id=plan["selection"])
        except BaseException:
            if self.runtime.status()["health"] == "recovery_required":
                self._uncertain = proposal
            elif proposal is not None:
                self.views.abort(proposal)
            raise

    async def recover(self, authority):
        if self._uncertain is None:
            raise _error("ui_recovery_required")

        async def validate(chosen, active, candidate):
            if active.services.get("visual") is candidate.services.get("visual"):
                return False
            generation = active if chosen == "active" else candidate
            visual = generation.services["visual"]
            record = await run_in_threadpool(self.selection.recover)
            binding = generation.services["plugins"].workers.get(visual.plugin_id)
            if (record["selection"], record["generation"], record["worker"]) != (visual.plugin_id, visual.generation, worker_record(binding)):
                return False
            if visual.bundle:
                loaded = await run_in_threadpool(UIResourceArchive(self.home).load, visual.generation)
                if loaded.encoded() != visual.bundle.encoded() or binding is not None and not binding.worker.healthy:
                    return False
            self._configure(record)
            if chosen == "candidate":
                if self.views.pending is not None:
                    self.views.publish(self._uncertain)
                elif self.views.generation != visual.generation:
                    return False
            else:
                self.views.abort(self._uncertain)
            return True

        try:
            return await self.runtime.recover(authority, validate)
        finally:
            if self.runtime.status()["health"] != "recovery_required":
                self._uncertain = None

    async def settle(self):
        self.closed = True
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)

    async def close(self):
        await self.settle()
        self.selection.close()
