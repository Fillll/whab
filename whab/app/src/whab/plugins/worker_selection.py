"""Host-owned activation and durable device-local references for report workers."""

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from starlette.concurrency import run_in_threadpool

from whab.budgets.runtime import RuntimeCandidate, RuntimeResource

from .environments import ApprovedWheel, prepare_environment
from .live import RegistryGeneration, live_report
from .report_worker import ReportWorker
from .runtime import PluginError, PluginRegistry
from .sdk import PluginContract, valid_plugin_name

_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _read(path, limit=1024 * 1024):
    if path.is_symlink():
        raise ValueError()
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ValueError()
    return json.loads(raw, object_pairs_hook=_pairs)


def _error(plugin_id):
    return PluginError("The saved report-worker environment could not be validated. Preserve its files and prepare an approved replacement.",
                       code="worker_environment_unavailable", plugin_id=plugin_id)


def reopen_environment(root, plugin_id, identifier):
    """Validate a content-addressed receipt before any interpreter executes."""
    try:
        if not valid_plugin_name(plugin_id) or type(identifier) is not str or not _HASH.fullmatch(identifier):
            raise ValueError()
        root = Path(root)
        path = root / identifier
        if path.is_symlink():
            raise ValueError()
        ready = _read(path / "ready.json")
        if type(ready) is not dict or ready.get("plugin") != plugin_id or ready.get("identifier") != identifier:
            raise ValueError()
        manifest = {key: value for key, value in ready.items() if key not in {"identifier", "installation_sha256"}}
        if hashlib.sha256(_json(manifest)).hexdigest() != identifier:
            raise ValueError()
        if type(ready["wheels"]) is not list or not 1 <= len(ready["wheels"]) <= 128:
            raise ValueError()
        approvals = []
        for wheel in ready["wheels"]:
            name = wheel["filename"]
            if type(name) is not str or Path(name).name != name or "\\" in name:
                raise ValueError()
            approvals.append(ApprovedWheel(path / "wheels" / name, wheel["sha256"]))
        prepared = prepare_environment(root, plugin_id, tuple(approvals))
        if prepared.identifier != identifier:
            raise ValueError()
        return prepared
    except Exception:
        raise _error(plugin_id) from None


def save_reference(home, prepared):
    """One flushed atomic reference is the report-code activation decision."""
    directory = Path(home) / "plugin-workers" / "active"
    # Environment preparation already owns and flushes plugin-workers. Flush
    # each reference directory before a successful decision can be reported.
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for parent in (Path(home), directory.parent, directory):
        descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    raw = _json({"format": 1, "plugin": prepared.plugin_id, "environment": prepared.identifier,
                 "distribution": prepared.distribution, "version": prepared.version})
    descriptor, temporary = tempfile.mkstemp(prefix=".worker-", dir=directory)
    try:
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as target:
                target.write(raw)
                target.flush()
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, directory / (prepared.plugin_id + ".json"))
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


def validate_saved_generation(live, generation):
    """Confirm durable report selection/version before reopening admission."""
    from whab.budgets.document import validate_manifest

    from .configuration import read_selection, write_selection

    registry = generation.services["plugins"]
    selected = {plugin.id for plugin in registry.descriptors()} - live.required
    if live.session:
        manifest = validate_manifest(_read(live.session.storage_home / "manifest.json"))
        if manifest["id"] != live.session.id or manifest["storage"] != live.session.manifest["storage"]:
            return False
        configured = {record["id"] for record in manifest["plugins"] if record["enabled"]} - live.required
        selected |= set(live.session.missing_plugins)
    else:
        saved = read_selection(live.home)
        configured = set(live.initial_selection if saved is None else saved)
        manifest = None
    if configured != selected:
        return False
    for plugin_id in selected:
        reference = Path(live.home) / "plugin-workers" / "active" / (plugin_id + ".json")
        binding = registry.workers.get(plugin_id)
        if binding is None:
            if reference.exists() or reference.is_symlink():
                return False
        else:
            expected = {"format": 1, "plugin": plugin_id, "environment": binding.environment.identifier,
                        "distribution": binding.environment.distribution, "version": binding.environment.version}
            if _read(reference, 16384) != expected or not binding.worker.healthy:
                return False
            reopen_environment(Path(live.home) / "plugin-workers" / "environments", plugin_id, binding.environment.identifier)
            # Reflush the exact validated decision. A previous response may
            # have been lost after replacement but before directory fsync.
            save_reference(live.home, binding.environment)
    if manifest is not None:
        live.session.manifest = manifest
        live.session.checkpoint()
    else:
        write_selection(live.home, tuple(sorted(configured)))
    descriptor = os.open(live.home, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return True


@dataclass(frozen=True)
class WorkerBinding:
    environment: object
    worker: object
    resource: RuntimeResource


def _start(template, prepared):
    if not live_report(template):
        raise PluginError("Only explicitly live stateless reports support this worker adapter.",
                          code="restart_required", plugin_id=template.id)
    worker = ReportWorker(template.id, python=prepared.python, expected_distribution=prepared.distribution,
                          expected_version=prepared.version)
    if not worker.info["live"]:
        worker.close()
        raise PluginError("The candidate report does not declare a compatible live lifecycle.",
                          code="restart_required", plugin_id=template.id)

    async def close():
        await run_in_threadpool(worker.close)
        if worker.cleanup_pending:
            raise PluginError("The report worker still has pending cleanup.", code="worker_cleanup_pending", plugin_id=template.id)

    binding = WorkerBinding(prepared, worker, RuntimeResource(template.id, close))
    proxy = replace(template, insights=worker.insights if worker.info["insights"] else None,
                    export=worker.export if worker.info["export"] else None,
                    contract=PluginContract(lifecycle="live"))
    return proxy, binding


def restore_workers(home, registry, existing=None):
    """Restore selected approved versions; failure never silently selects host code."""
    existing = existing or {}
    selected, bindings = [], {}
    try:
        for plugin in registry.descriptors():
            if plugin.id in existing:
                selected.append(plugin)
                bindings[plugin.id] = existing[plugin.id]
                continue
            reference = Path(home) / "plugin-workers" / "active" / (plugin.id + ".json")
            if reference.exists() or reference.is_symlink():
                try:
                    value = _read(reference, 16384)
                    if type(value) is not dict or set(value) != {"format", "plugin", "environment", "distribution", "version"} or type(value["format"]) is not int or value["format"] != 1 or value["plugin"] != plugin.id:
                        raise ValueError()
                    prepared = reopen_environment(Path(home) / "plugin-workers" / "environments", plugin.id, value["environment"])
                    if (prepared.distribution, prepared.version) != (value["distribution"], value["version"]):
                        raise ValueError()
                    plugin, binding = _start(plugin, prepared)
                    bindings[plugin.id] = binding
                except Exception:
                    raise _error(plugin.id) from None
            selected.append(plugin)
        return PluginRegistry(tuple(selected)), bindings
    except BaseException:
        for binding in reversed(tuple(bindings.values())):
            if all(binding is not previous for previous in existing.values()):
                binding.worker.close()
        raise


async def upgrade_report(live, home, plugin_id, approvals):
    """Prepare a new immutable version while the current generation stays usable."""
    async def prepare(preparation):
        active = preparation.active.services["plugins"]
        template = next((plugin for plugin in active.descriptors() if plugin.id == plugin_id), None)
        if template is None or not live_report(template):
            raise PluginError("Enable an explicitly live stateless report before upgrading it.",
                              code="restart_required", plugin_id=plugin_id)
        prepared = await run_in_threadpool(prepare_environment, Path(home) / "plugin-workers" / "environments",
                                          plugin_id, approvals)
        proxy, binding = await run_in_threadpool(_start, template, prepared)
        preparation.own(binding.resource)
        registry = PluginRegistry(tuple(proxy if plugin.id == plugin_id else plugin for plugin in active.descriptors()))
        bindings = {**active.workers, plugin_id: binding}
        candidate = RegistryGeneration(registry, active.stable, bindings)
        previous = active.workers.get(plugin_id)
        resources = tuple(resource for resource in preparation.active.resources
                          if previous is None or resource is not previous.resource)
        return RuntimeCandidate({**preparation.active.services, "plugins": candidate}, (*resources, binding.resource))

    async def commit(candidate, _number):
        binding = candidate.services["plugins"].workers[plugin_id]
        await run_in_threadpool(save_reference, home, binding.environment)

    return await live.runtime.transition(prepare, commit=commit, plugin_id=plugin_id)
