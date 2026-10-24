"""Host-owned activation and durable references for approved optional workers."""

import hashlib
import json
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from starlette.concurrency import run_in_threadpool

from whab.budgets.runtime import RuntimeCandidate, RuntimeResource

from .environments import ApprovedWheel, _lock, prepare_environment
from .live import RegistryGeneration
from .report_worker import ReportWorker
from .runtime import PluginError, PluginRegistry
from .sdk import (
    BankLogos,
    BankUI,
    Categorization,
    FileImport,
    Interface,
    MerchantLogos,
    PluginAction,
    PluginSettings,
    RemoteLedgerClient,
    UserInterface,
    valid_plugin_name,
)
from .ui_worker import proxy_context
from .worker_adapters import supports_live_plugin

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
    return PluginError("The saved plugin-worker environment could not be validated. Preserve its files and prepare an approved replacement.",
                       code="worker_environment_unavailable", plugin_id=plugin_id)


def reopen_environment(root, plugin_id, identifier, *, worker_owner=None):
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
        if ready.get("format") == 1 and "family" not in ready:
            family = "optional"
        elif ready.get("format") == 2 and ready.get("family") == "storage":
            family = "storage"
        else:
            raise ValueError()
        prepared = prepare_environment(root, plugin_id, tuple(approvals), family=family,
                                       **({"worker_owner": worker_owner} if worker_owner else {}))
        if prepared.identifier != identifier:
            raise ValueError()
        return prepared
    except Exception:
        raise _error(plugin_id) from None


@contextmanager
def _reference_lock(home):
    home = Path(home)
    directory = home
    for name in ("plugin-workers", "locks"):
        directory /= name
        if directory.is_symlink():
            raise ValueError()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for path in (home, directory.parent, directory):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    with _lock(directory / "references.lock", time.monotonic() + 30):
        yield


def _reference_record(prepared):
    return {"format": 1, "plugin": prepared.plugin_id, "environment": prepared.identifier,
            "distribution": prepared.distribution, "version": prepared.version}


def save_reference(home, prepared):
    """Publish one flushed activation decision under the reference writer lock."""
    with _reference_lock(home):
        _save_reference(home, prepared)


def _reference_directory(home):
    directory = Path(home)
    for name in ("plugin-workers", "active"):
        directory /= name
        if directory.is_symlink():
            raise ValueError()
    return directory


def _save_reference(home, prepared):
    directory = _reference_directory(home)
    # Environment preparation already owns and flushes plugin-workers. Flush
    # each reference directory before a successful decision can be reported.
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for parent in (Path(home), directory.parent, directory):
        descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    raw = _json(_reference_record(prepared))
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


def _reference_bytes(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError()
        raw = os.read(descriptor, 16385)
        if not raw or len(raw) > 16384:
            raise ValueError()
        return raw
    finally:
        os.close(descriptor)


def worker_reference_revision(home, plugin_id):
    """Read a bounded opaque CAS revision, without executing the saved worker."""
    try:
        if not valid_plugin_name(plugin_id):
            raise ValueError()
        directory = _reference_directory(home)
        return hashlib.sha256(_reference_bytes(directory / (plugin_id + ".json"))).hexdigest()
    except Exception:
        raise _error(plugin_id) from None


def _preserve_reference(home, plugin_id, revision, raw):
    directory = Path(home) / "plugin-workers"
    for name in ("history", plugin_id):
        directory /= name
        if directory.is_symlink():
            raise ValueError()
        directory.mkdir(mode=0o700, exist_ok=True)
        descriptor = os.open(directory.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    target = directory / (revision + ".json")
    descriptor, temporary = tempfile.mkstemp(prefix=".reference-", dir=directory)
    try:
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as saved:
                saved.write(raw)
                saved.flush()
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if _reference_bytes(target) != raw:
                raise ValueError()
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


def replace_saved_worker(home, plugin_id, approvals, *, expected_reference_sha256):
    """Explicitly repair a CLOSED or recovery-gated budget's worker selection.

    The caller owns the admission barrier: never invoke this on a running,
    admitted budget. Only a fresh approved current-host environment is published;
    prior immutable environments and the exact old reference are preserved.
    """
    try:
        if not valid_plugin_name(plugin_id) or type(expected_reference_sha256) is not str or _HASH.fullmatch(expected_reference_sha256) is None:
            raise ValueError()
        home = Path(home)
        prepared = prepare_environment(home / "plugin-workers" / "environments", plugin_id, approvals)
        with _reference_lock(home):
            reference = _reference_directory(home) / (plugin_id + ".json")
            current = _reference_bytes(reference)
            if hashlib.sha256(current).hexdigest() != expected_reference_sha256:
                history = home / "plugin-workers" / "history"
                if history.is_symlink() or (history / plugin_id).is_symlink():
                    raise ValueError()
                history = history / plugin_id / (expected_reference_sha256 + ".json")
                if current != _json(_reference_record(prepared)) or hashlib.sha256(_reference_bytes(history)).hexdigest() != expected_reference_sha256:
                    raise PluginError("The saved worker reference changed. Inspect it again before replacing it.",
                                      code="worker_reference_conflict", plugin_id=plugin_id)
            else:
                _preserve_reference(home, plugin_id, expected_reference_sha256, current)
            _save_reference(home, prepared)
        return prepared
    except PluginError:
        raise
    except Exception:
        raise PluginError("The approved worker replacement could not be published. Preserve its files and inspect the saved reference before retrying.",
                          code="worker_replacement_failed", plugin_id=plugin_id) from None


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


async def _close_worker(worker, plugin_id):
    await run_in_threadpool(worker.close)
    if worker.cleanup_pending:
        raise PluginError("The report worker still has pending cleanup.", code="worker_cleanup_pending", plugin_id=plugin_id)


class PreparationWorkers:
    """Own environment probes from allocation until confirmed synchronous exit."""

    def __init__(self, preparation, plugin_id):
        self.preparation, self.plugin_id, self.resources = preparation, plugin_id, {}

    def own(self, worker):
        async def close():
            await _close_worker(worker, self.plugin_id)
        resource = RuntimeResource(self.plugin_id, close)
        self.resources[id(worker)] = resource
        self.preparation.own(resource)

    def retired(self, worker):
        if worker.cleanup_pending or worker.healthy:
            raise _error(self.plugin_id)
        self.preparation.release(self.resources.pop(id(worker)))


def _start(template, prepared, home=None, *, worker_owner=None, resource_owner=None):
    from .bank_adapter import BankAdapter
    from .categorization_worker import WorkerCategorizationFactory
    from .provider_worker import WorkerBankFactory

    if not supports_live_plugin(template):
        raise PluginError("This optional plugin does not declare a supported live adapter.",
                          code="restart_required", plugin_id=template.id)
    worker = None

    async def close():
        if worker is not None:
            await _close_worker(worker, template.id)

    resource = RuntimeResource(template.id, close)
    if resource_owner is not None:
        resource_owner(resource)

    def created(allocated):
        nonlocal worker
        worker = allocated
        if worker_owner is not None:
            worker_owner(allocated)

    worker = ReportWorker(template.id, python=prepared.python, expected_distribution=prepared.distribution,
                          expected_version=prepared.version,
                          **({"on_create": created} if worker_owner or resource_owner else {}))
    try:
        if not worker.info["live"]:
            raise PluginError("The candidate does not declare a compatible live lifecycle.",
                              code="restart_required", plugin_id=template.id)
        adapters = worker.info["adapters"]
        bank = adapters["bank_logos"]
        if bank is not None and bank["cache"]:
            if home is None:
                raise _error(template.id)
            worker.configure_cache(Path(home) / "cache" / "plugins" / template.id / "environments" / prepared.identifier)
        resources = worker.resources() if adapters["file_import"] is not None else None
        if adapters["file_import"] is not None and (resources is not None) != adapters["file_import"]["ui"]:
            raise _error(template.id)
        visual = None
        if adapters["ui"] is not None:
            visual = UserInterface(None, None, proxy_context, resources=worker.visual_resources(),
                                   render=worker.render_ui, **adapters["ui"])
        bank_resources = worker.provider_resources() if adapters["bank"] is not None and adapters["bank"]["ui"] else None
        proxy = replace(template, bank=BankAdapter(WorkerBankFactory(worker, code_identity=prepared.identifier)) if adapters["bank"] is not None else None,
                        bank_ui=BankUI(resources=bank_resources) if bank_resources is not None else None, insights=worker.insights if worker.info["insights"] else None,
                        export=worker.export if worker.info["export"] else None,
                        bank_logos=None if bank is None else BankLogos(
                            worker.resolve, worker.fetch, configure_cache=worker.configure_cache if bank["cache"] else None,
                            resolve_domain=partial(worker.resolve, domain=True) if bank["domain"] else None,
                            provider_name=bank["provider_name"], provider_url=bank["provider_url"],
                        ),
                        merchant_logos=MerchantLogos(partial(worker.fetch, merchant=True)) if adapters["merchant_logos"] else None,
                        file_import=FileImport(worker.parse, resources=resources) if adapters["file_import"] is not None else None,
                        categorization=Categorization(WorkerCategorizationFactory(worker)) if adapters["categorization"] else None,
                        settings=None if adapters["settings"] is None else PluginSettings(
                            lambda: worker.settings()["insights"],
                            tuple(PluginAction(**row) for row in adapters["settings"]["actions"]), worker.settings_action,
                        ), interface=Interface(adapter=worker.interface()) if adapters["interface"] is not None else None,
                        ui=visual, storage=worker.remote_client() if adapters["remote_client"] is not None else None,
                        contract=worker.contract)
        if not supports_live_plugin(proxy):
            raise _error(template.id)
    except BaseException:
        worker.close()
        raise

    binding = WorkerBinding(prepared, worker, resource)
    return proxy, binding


def restore_workers(home, registry, existing=None, *, worker_owner=None, resource_owner=None):
    """Restore selected approved versions; failure never silently selects host code."""
    existing = existing or {}
    selected, bindings = [], {}
    try:
        for plugin in registry.descriptors():
            if plugin.id in existing:
                selected.append(plugin)
                bindings[plugin.id] = existing[plugin.id]
                continue
            if plugin.ui is not None or type(plugin.storage) is RemoteLedgerClient:
                # Visual and remote authorities have their separate exact decisions.
                selected.append(plugin)
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
                    plugin, binding = _start(plugin, prepared, home, worker_owner=worker_owner, resource_owner=resource_owner)
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
        if template is None or template.ui is not None or not supports_live_plugin(template) or (template.bank is not None and getattr(live, "banks", None) is None):
            raise PluginError("Enable an explicitly live supported optional adapter before upgrading it.",
                              code="restart_required", plugin_id=plugin_id)
        if template.categorization is not None:
            preparation.active.services["categorization"].ensure_idle()
        probes = PreparationWorkers(preparation, plugin_id)
        if template.bank is not None and getattr(live, "banks", None) is not None:
            live.banks.preflight(preparation.active.services["banks"], (), changed={plugin_id})
        prepared = await run_in_threadpool(prepare_environment, Path(home) / "plugin-workers" / "environments",
                                          plugin_id, approvals, worker_owner=probes.own, worker_retired=probes.retired)
        proxy, binding = await run_in_threadpool(_start, template, prepared, home, resource_owner=preparation.own)
        preparation.own(binding.resource)
        registry = PluginRegistry(tuple(proxy if plugin.id == plugin_id else plugin for plugin in active.descriptors()))
        bindings = {**active.workers, plugin_id: binding}
        candidate = RegistryGeneration(registry, active.stable, bindings, previous=active)
        previous = active.workers.get(plugin_id)
        resources = tuple(resource for resource in preparation.active.resources
                          if previous is None or resource is not previous.resource)
        resources = (*resources, binding.resource)
        services = {**preparation.active.services, "plugins": candidate}
        if live.prepare_interfaces is not None:
            previous_interface = preparation.active.services.get("interfaces")
            interfaces = await run_in_threadpool(live.prepare_interfaces, candidate, previous_interface)
            if interfaces is not previous_interface:
                resources = tuple(resource for resource in resources if previous_interface is None or resource is not previous_interface.resource)
                if interfaces.resource is not None:
                    preparation.own(interfaces.resource)
                    resources += (interfaces.resource,)
            services["interfaces"] = interfaces
        return RuntimeCandidate(services, resources)

    async def commit(candidate, _number):
        binding = candidate.services["plugins"].workers[plugin_id]
        await run_in_threadpool(save_reference, home, binding.environment)
        if live.categorization is not None:
            live.categorization.committed(candidate)

    async def finalize(preparation, candidate):
        if live.categorization is not None:
            candidate = await run_in_threadpool(live.categorization.finalize, preparation, candidate)
        if getattr(live, 'banks', None) is not None:
            candidate = await run_in_threadpool(live.banks.finalize, preparation, candidate)
        return candidate

    return await live.runtime.transition(prepare, finalize=finalize, commit=commit, plugin_id=plugin_id)
