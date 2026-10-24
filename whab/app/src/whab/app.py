"""Composition root: construct adapters and assemble the local application."""

import inspect
from collections.abc import Mapping
from contextlib import asynccontextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from whab import __version__
from whab.budgets.host import BudgetDispatch, BudgetHost, BudgetRuntimeRequests
from whab.budgets.plugin_state import check_saved_states
from whab.budgets.runtime import BudgetRuntime, RuntimeResource
from whab.budgets.session import BudgetSession, TrackedStorage
from whab.budgets.storage_replicas import StorageReplicas
from whab.budgets.storage_runtime import StorageRuntime, open_saved_storage, saved_storage_selection
from whab.budgets.storage_upgrade import StorageBinding, StorageUpgrade, storage_worker_descriptor
from whab.config import Settings
from whab.plugins import (
    BankContext,
    InterfaceContext,
    LedgerService,
    PluginError,
    StorageContext,
    load_plugins,
)
from whab.plugins.contracts import contract_for
from whab.plugins.interface_adapter import validate_interface_owners
from whab.plugins.live import (
    LeasedPluginRegistry,
    LivePluginSelection,
    RegistryGeneration,
)
from whab.plugins.management import PluginManager, installed_entrypoints
from whab.plugins.runtime import PluginRegistry
from whab.plugins.storage import close_storage, open_storage
from whab.plugins.ui_resources import UIResourceArchive
from whab.plugins.ui_runtime import (
    VisualController,
    apply_visual_choice,
    read_visual_choice,
    restore_visual_worker,
)
from whab.plugins.ui_selection import UISelectionStore
from whab.plugins.worker_adapters import supports_live_plugin
from whab.plugins.worker_selection import restore_workers
from whab.services.categorization import local_context, local_files
from whab.services.categorization_runtime import (
    CategorizationLifecycle,
    LeasedCategorization,
    live_categorizer,
)
from whab.services.namespace_effects import NamespaceNotifications
from whab.services.receipts import ReceiptService
from whab.storage.bank import BankStore
from whab.web.errors import install_error_handlers
from whab.web.import_resources import create_router as import_resources_router
from whab.web.interface_adapter import create_remote_router, prepare_interfaces
from whab.web.middleware import RequireHTTPS, install_middleware
from whab.web.routes.banks import create_router as banks_router
from whab.web.routes.file_imports import create_router as file_imports_router
from whab.web.routes.health import create_router as health_router
from whab.web.routes.ledger import create_router as ledger_router
from whab.web.routes.pages import create_router as pages_router
from whab.web.routes.plugins import create_router as plugins_router
from whab.web.routes.receipts import create_router as receipts_router
from whab.web.routes.ui_views import create_router as ui_views_router
from whab.web.routes.visual_ui import create_router as visual_ui_router
from whab.web.visual_interface import LeasedVisualUI, VisualGeneration, VisualTemplates
from whab.web.visual_interface import create_resource_router as visual_resources_router

_STARTUP_PENDING = {}
_STARTUP_PENDING_LOCK = RLock()


def retry_startup_cleanup(home, budget_id, environment):
    """Retry retained initial owners without opening another backend or child."""
    key = str(Path(home).resolve()), budget_id, environment
    with _STARTUP_PENDING_LOCK:
        owners = tuple(_STARTUP_PENDING.get(key, {}).values())
    for owner in owners:
        owner.retry()
    with _STARTUP_PENDING_LOCK:
        # Another already-starting constructor may have registered a failure
        # during this pass. Never approve a new startup while any owner remains.
        return not _STARTUP_PENDING.get(key)


class _StartupResources:
    """Synchronous ownership until an unstarted app transfers to its lifespan.

    Register initial objects' synchronous release callbacks, never async runtime
    close methods. Cleanup preserves the construction exception and retains failed
    owners privately at process scope; successful owners are not closed again on
    retry. A discarded construction exception cannot discard cleanup ownership.
    """

    def __init__(self, home, budget_id, environment):
        self.callbacks, self.pending, self.workers = {}, {}, {}
        self.key = str(Path(home).resolve()), budget_id, environment
        self.lock = RLock()

    def own(self, owner, close=None):
        self.callbacks.setdefault(id(owner), (owner, close or owner.close))
        return owner

    def worker(self, worker, *, own=True):
        self.workers[id(worker)] = worker
        if own:
            self.own(worker)

    def close(self, error):
        callbacks, self.callbacks = self.callbacks, {}
        for worker in self.workers.values():
            # No admitted work exists yet. Bound an unresponsive child's shutdown
            # RPC before its existing owned-process-group kill/reap path runs.
            timeout = getattr(worker, "timeout", None)
            if type(timeout) in (int, float) and timeout > 1:
                worker.timeout = 1
        for identity, (owner, close) in reversed(callbacks.items()):
            try:
                result = close()
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    raise TypeError("Startup cleanup requires a synchronous release callback.")
            except BaseException:
                self.pending[identity] = owner, close
        for identity, worker in self.workers.items():
            if getattr(worker, "cleanup_pending", False):
                self.pending[identity] = worker, worker.close
        self.workers = {identity: worker for identity, worker in self.workers.items()
                        if getattr(worker, "cleanup_pending", False)}
        if self.pending:
            # No underlying exception text or private owner enters diagnostics.
            # Keep cleanup ownership available to a caller retaining the original
            # exception, even if bounded child cleanup needs another attempt.
            try:
                error._whab_startup_cleanup = self
            except BaseException:
                pass
            with _STARTUP_PENDING_LOCK:
                _STARTUP_PENDING.setdefault(self.key, {})[id(self)] = self
        else:
            with _STARTUP_PENDING_LOCK:
                owners = _STARTUP_PENDING.get(self.key)
                if owners is not None:
                    owners.pop(id(self), None)
                    if not owners:
                        _STARTUP_PENDING.pop(self.key)

    def retry(self):
        with self.lock:
            self.callbacks, self.pending = self.pending, {}
            error = RuntimeError("Startup resource cleanup remains incomplete.")
            self.close(error)
            return not self.pending

    def release(self):
        self.callbacks.clear()
        self.workers.clear()


def plugin_config(settings, session, plugin_id, *, remote_authority=False):
    """Portable public settings plus machine-local credentials for one plugin."""
    if remote_authority:
        # A data file must never choose the destination of this machine's bearer
        # credential. Bind the URL, credential and remote budget together locally.
        return settings.plugin_configuration(plugin_id)
    prefix = plugin_id.upper().replace("-", "_") + "_"
    record = session.plugin_record(plugin_id) if session else None
    public = {
        key: value for key, value in record["settings"].items()
        if key.startswith(prefix) and type(value) is str
        and not any(word in key.upper() for word in ("TOKEN", "SECRET", "KEY", "PASSWORD", "AUTHORIZATION"))
    } if record else {}
    return {**public, **settings.plugin_configuration(plugin_id)}


def _approved_profile(profile, public):
    """Compare connection identities without changing a credential's origin."""
    resolved = dict(profile)
    for key, expected in public.items():
        actual = profile.get(key)
        if key.endswith("_URL") and expected.startswith("https://"):
            try:
                if type(actual) is not str or any(character.isspace() for character in actual):
                    return None
                parsed = urlsplit(actual)
                if (
                    parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or parsed.path not in {"", "/"}
                    or parsed.query or parsed.fragment
                ):
                    return None
                parsed.port
                actual = str(httpx.URL(actual).copy_with(path=""))
            except (ValueError, httpx.InvalidURL):
                return None
        if actual != expected:
            return None
        resolved[key] = expected
    return resolved


def storage_config(settings, session, plugin):
    """Bind a data-only connection to one complete approved credential profile."""
    device = settings.storage_configuration()
    if not session or plugin.budget_connection is None:
        return plugin_config(settings, session, plugin.id, remote_authority=contract_for(plugin).authority == "remote")
    files = session.files_for(plugin.id)
    if not files:
        return device
    public = dict(plugin.budget_connection(files))
    credentials = session.credentials.get(plugin.id)
    if credentials is not None:
        resolved = _approved_profile(credentials, public)
        if resolved is None:
            raise PluginError("The unlocked credentials belong to a different server or budget.", code="invalid_configuration", plugin_id=plugin.id)
        return resolved
    resolved = _approved_profile(device, public)
    if resolved is None:
        raise PluginError("Configure credentials for this exact server and budget, or unlock credentials included in the budget file.", code="invalid_configuration", plugin_id=plugin.id)
    return resolved


def create_app(store=None, settings=None, plugins=None, bank_services=None, *, budget_session=None, budget_host=None, _child=False):
    settings = settings or Settings.load()
    if plugins is not None:
        validate_interface_owners(plugins.descriptors())
    if budget_session is None and settings.budget_path:
        budget_session = BudgetSession.open(settings.home, settings.plaid_env, settings.budget_path, password=settings.budget_password)
    if budget_session is None and store is None:
        authority = saved_storage_selection(settings.home, "default", settings.plaid_env)
        if authority:
            authority[0].check_key(settings.encryption_key)
            settings = replace(settings, storage=authority[2]["plugin_id"],
                               plugins=tuple(name for name in settings.plugins if name != settings.storage))
    if budget_session:
        if budget_session.storage_authority:
            budget_session.storage_authority[0].check_key(settings.encryption_key)
        installed = {entry.name for entry in installed_entrypoints()}
        budget_session.missing_plugins = [name for name in budget_session.selection if name not in installed]
        settings = replace(settings, storage=budget_session.manifest["storage"], plugins=tuple(name for name in budget_session.selection if name in installed))
    worker_home = budget_session.storage_home if budget_session else settings.home
    if not retry_startup_cleanup(worker_home, budget_session.id if budget_session else "default", settings.plaid_env):
        raise PluginError("This budget's previous startup resources are still closing. Preserve its files and retry after cleanup finishes.",
                          code="startup_cleanup_pending")
    visual_choice = read_visual_choice(worker_home, budget_session.id if budget_session else "default",
                                       settings.plaid_env, settings.ui)
    settings = apply_visual_choice(settings, visual_choice)
    budget_host = budget_host or BudgetHost(settings)
    plugins = plugins if plugins is not None else load_plugins(settings.enabled_plugins())
    validate_interface_owners(plugins.descriptors())
    nonlive = {plugin.id for plugin in plugins.descriptors() if not (supports_live_plugin(plugin) or live_categorizer(plugin))}
    if budget_session:
        check_saved_states(budget_session.manifest, plugins.descriptors())
        plugins = plugins.bind_budget(budget_session.context)
    plugins = plugins.bind_branding()
    ui_plugin = plugins.ui_plugin(settings.ui) if settings.ui else None
    ui = ui_plugin.ui if ui_plugin else None
    if not ui and any(description.has_ui for description in plugins.describe()):
        raise PluginError(
            "Remove user interface plugins from WHAB_PLUGINS when WHAB_UI is empty.",
            code="invalid_configuration",
        )
    if ui and ui.resources is None and (not (ui.templates / "base.html").is_file() or not ui.static.is_dir()):
        raise PluginError(
            "User interface resources are missing. Reinstall the selected interface.",
            code="missing_resources",
            plugin_id=ui_plugin.id,
        )
    descriptors = plugins.bank_plugins()
    storage_descriptor = next((plugin for plugin in plugins.descriptors() if plugin.id == settings.storage and plugin.storage is not None), None)
    remote_authority = bool(storage_descriptor and contract_for(storage_descriptor).authority == "remote")
    if remote_authority and descriptors:
        raise PluginError("Bank ingestion belongs on the server. Disable bank plugins in a client budget.", code="invalid_configuration")
    for plugin in descriptors:
        if ui and plugin.bank_ui and (
            not (plugin.bank_ui.templates / "accounts.html").is_file()
            or not (plugin.bank_ui.static / "connect.js").is_file()
        ):
            raise PluginError(
                "Bank plugin UI resources are missing. Reinstall or disable the plugin.",
                code="missing_resources",
                plugin_id=plugin.id,
            )
    import_descriptors = plugins.file_import_plugins()
    for plugin in import_descriptors:
        file_import = plugin.file_import
        if ui and file_import.templates is not None and (
            not (file_import.templates / "accounts.html").is_file()
            or not (file_import.static / "import.js").is_file()
        ):
            raise PluginError(
                "Import plugin UI resources are missing. Reinstall or disable the plugin.",
                code="missing_resources",
                plugin_id=plugin.id,
            )
    plugins.configure_logo_caches(budget_session.storage_home if budget_session else settings.home)
    owned_store = store is None
    startup = _StartupResources(worker_home, budget_session.id if budget_session else "default", settings.plaid_env)
    budget_id = budget_session.id if budget_session else "default"
    storage_upgrade = None

    def own_storage_worker(worker):
        if storage_upgrade is None:
            startup.worker(worker)
        else:
            storage_upgrade.own_worker(worker)

    def own_storage_proxy(backend):
        if storage_upgrade is None:
            startup.own(backend)
        else:
            storage_upgrade.own_proxy(backend)

    def descriptor_for(plugin):
        if storage_upgrade is not None and plugin.id == storage_upgrade.plugin_id:
            return runtime._active.services["plugins"].registry.storage_plugin(plugin.id)
        return storage_worker_descriptor(worker_home, budget_id, settings.plaid_env, plugin,
                                         worker_owner=own_storage_worker, storage_owner=own_storage_proxy)

    try:
        selected_storage = plugins.storage_plugin(settings.storage) if owned_store else None
        if owned_store:
            selected_storage = descriptor_for(selected_storage)
            plugins = PluginRegistry(tuple(selected_storage if plugin.id == selected_storage.id else plugin
                                           for plugin in plugins.descriptors()))
        resolved_config = storage_config(settings, budget_session, selected_storage) if owned_store else {}
        if budget_session:
            budget_session.storage_config = resolved_config
        if owned_store:
            store = open_saved_storage(settings, budget_session,
                lambda plugin: storage_config(replace(settings, storage=plugin.id), budget_session, plugin), descriptor_for)
            store = store or open_storage(selected_storage, StorageContext(
                home=budget_session.backend_home if budget_session else settings.home, environment=settings.plaid_env,
                config=resolved_config, budget=budget_session.context(settings.storage) if budget_session else None))
            startup.own(store)
    except BaseException as error:
        startup.close(error)
        raise
    categorization_lifecycle, categorization, categorization_resource = None, None, None
    worker_bindings, banks = {}, {}
    storage_binding = None
    try:
        startup_batch = budget_session.namespace_materialization() if budget_session else nullcontext()
        with startup_batch:
            if budget_session:
                budget_session.storage_payload_paths = selected_storage.storage_payload_paths if selected_storage else ()
                budget_session.bind(store)
                for path in settings.copy_paths:
                    target = path.resolve()
                    if not any(item["path"] == str(target) for item in budget_session.destinations):
                        budget_session.add_copy(path.stem, str(target))
            store = TrackedStorage(store, budget_session)
            store.storage_plugin_id = settings.storage if owned_store else None
            service = LedgerService(store, budget_id=budget_session.id if budget_session else "default", lock=store.lock)
            store.on_mutation = service.invalidate_revision
            plugins, worker_bindings = restore_visual_worker(worker_home, plugins, visual_choice, worker_owner=startup.worker)
            for binding in worker_bindings.values():
                startup.worker(binding.worker)
            prepared_storage = getattr(store.store, "prepared_environment", None)
            if prepared_storage is not None:
                storage_binding = StorageBinding(prepared_storage, store.store)
                worker_bindings[settings.storage] = storage_binding
            plugins, worker_bindings = restore_workers(worker_home, plugins, worker_bindings, worker_owner=startup.worker)
            for binding in worker_bindings.values():
                startup.worker(binding.worker)
            ui_plugin = plugins.ui_plugin(settings.ui) if settings.ui else None
            ui = ui_plugin.ui if ui_plugin else None
            notifications = NamespaceNotifications(store.lock, lambda: store.after_commit and store.after_commit())
            if not remote_authority:
                state_home = settings.home / "data" / "plugin-state" / settings.plaid_env
                context_factory = budget_session.context if budget_session else lambda identifier: local_context(
                    state_home, service.budget_id, identifier, lock=store.lock, on_change=notifications.changed)
                saved_ids = [record["id"] for record in budget_session.manifest["plugins"]
                             if any(f"plugins/{record['id']}/suggestions-{workspace}.json" in budget_session.files
                                    for workspace in ("personal", "demo"))] if budget_session else [
                        path.name for path in (settings.home / "data" / "plugin-state" / settings.plaid_env / "plugin-data").glob("*")
                        if any((path / f"suggestions-{workspace}.json").is_file() for workspace in ("personal", "demo"))]
                snapshot = budget_session.files_for if budget_session else lambda identifier: local_files(state_home, service.budget_id, identifier)
                categorization_lifecycle = CategorizationLifecycle(service, context_factory, snapshot,
                    worker_home=worker_home, environment=settings.plaid_env,
                    materialization=budget_session.namespace_materialization if budget_session else notifications.materialization)
                startup.own(categorization_lifecycle)
                categorization, categorization_resource = categorization_lifecycle.initial(plugins.descriptors(), saved_ids)
                startup.own(categorization)
                for factory in getattr(categorization, "_worker_factories", ()):
                    startup.worker(factory.worker, own=False)
            bank_stores = {plugin.id: BankStore(store, plugin.id, suggestions_only=lambda: bool(service.categorization and service.categorization.enabled())) for plugin in descriptors}
    except BaseException as error:
        startup.close(error)
        raise
    def interface_context(identifier):
        # A portable budget may never choose this machine's bearer credential,
        # even when an adapter uses an unconventional configuration-key name.
        return InterfaceContext(service=service, config=settings.plugin_configuration(identifier),
            environment=settings.plaid_env, budget_id=service.budget_id,
            budget=budget_session.context(identifier) if budget_session else None)

    try:
        interfaces = prepare_interfaces(plugins, interface_context)
        if interfaces.binding is not None:
            startup.own(interfaces.binding)
    except BaseException as error:
        startup.close(error)
        raise
    visual_selection = None
    try:
        visual = VisualGeneration.prepare(ui_plugin, UIResourceArchive(worker_home),
            code_identity=worker_bindings[ui_plugin.id].environment.identifier
            if ui_plugin is not None and ui_plugin.id in worker_bindings else None)
        visual_selection = startup.own(UISelectionStore(worker_home, service.budget_id, settings.plaid_env, settings.ui or None))
    except BaseException as error:
        startup.close(error)
        raise

    async def close_visual_selection():
        await run_in_threadpool(visual_selection.close)

    async def close_namespaces():
        if categorization_lifecycle is not None:
            await run_in_threadpool(categorization_lifecycle.close)

    async def close_storage_upgrade():
        if storage_upgrade is not None:
            await storage_upgrade.close()

    try:
        namespace_resources = ((RuntimeResource("categorization-namespaces", close_namespaces),)
                               if categorization_lifecycle is not None else ())
        storage_resources = ((RuntimeResource(settings.storage, close_storage_upgrade),)
                             if owned_store and not remote_authority else ())
        runtime = BudgetRuntime(service.budget_id, {
            "plugins": RegistryGeneration(plugins, workers=worker_bindings, resources_enabled=bool(ui)), "ledger": service,
            "storage": store.store, "banks": banks, "categorization": categorization, "interfaces": interfaces, "visual": visual,
        }, (RuntimeResource("visual-selection", close_visual_selection), *namespace_resources, *storage_resources,
            *tuple(binding.resource for binding in worker_bindings.values()),
            *((categorization_resource,) if categorization_resource else ()),
            *((interfaces.resource,) if interfaces.resource is not None else ())))
        if categorization is not None:
            service.categorization = LeasedCategorization(runtime)
            store.after_ingestion = service.categorization.after_import
        registry = LeasedPluginRegistry(runtime)
        plugin_manager = PluginManager(settings, registry, budget_session=budget_session)
        live = LivePluginSelection(runtime, plugin_manager.required, installed_entrypoints,
                                   plugin_manager.persist_live, budget_session, nonlive, worker_home)
        live.categorization = categorization_lifecycle
        live.prepare_interfaces = lambda registry, previous: prepare_interfaces(registry, interface_context, previous)
        plugin_manager.live = live
        visual_controller = VisualController(runtime, visual_selection, worker_home, installed_entrypoints,
                                             plugin_manager, live, session=budget_session)
        live.visual = visual_controller
        storage_runtime = StorageRuntime(settings, budget_session, store, runtime, registry,
            lambda plugin: storage_config(replace(settings, storage=plugin.id), budget_session, plugin))
        storage_runtime.descriptor_for = descriptor_for
        if owned_store and not remote_authority:
            selected = saved_storage_selection(worker_home, budget_id, settings.plaid_env)
            backend_home = selected[0].home(selected[2]) if selected else (budget_session.backend_home if budget_session else settings.home)
            storage_upgrade = StorageUpgrade(worker_home, runtime, store,
                StorageContext(backend_home, settings.plaid_env, resolved_config,
                    budget_session.context(settings.storage) if budget_session else None,
                    expected_instance_id=selected[1]["authority_instance_id"] if selected else None),
                registry, check=storage_runtime._source, binding=storage_binding,
                on_create=lambda owner: startup.own(owner, owner.close_sync))
            live.storage = storage_upgrade
        replicas = StorageReplicas(storage_runtime)

        @asynccontextmanager
        async def lifespan(_app):
            try:
                for plugin in descriptors:
                    injected = (bank_services or {}).get(plugin.id)
                    if injected is not None:
                        banks[plugin.id] = injected
                    else:
                        try:
                            banks[plugin.id] = plugin.bank(
                                BankContext(
                                    store=bank_stores[plugin.id],
                                    config=settings.bank_configuration(plugin.id),
                                    environment=settings.plaid_env,
                                    encryption_key=settings.encryption_key,
                                    budget=budget_session.context(plugin.id) if budget_session else None,
                                    background_scope=runtime.background,
                                )
                            )
                            service = banks[plugin.id]
                            if inspect.iscoroutine(service):
                                service.close()
                            if (
                                inspect.isawaitable(service)
                                or not isinstance(getattr(service, "config", None), Mapping)
                                or any(
                                    not callable(getattr(service, name, None))
                                    for name in (
                                        "link_token",
                                        "exchange",
                                        "sync",
                                        "disconnect",
                                        "webhook",
                                        "close",
                                    )
                                )
                            ):
                                raise ValueError("Invalid bank service")
                        except Exception:
                            raise PluginError(
                                "Bank plugin could not start. Check its configuration or disable it.",
                                code="bank_startup_failed",
                                plugin_id=plugin.id,
                            ) from None
                if not _child:
                    await budget_host.remember_current()
                await replicas.start()
                yield
            finally:
                # Close every service even if another plugin's cleanup raises.
                try:
                    try:
                        if not _child:
                            await budget_host.close()
                    finally:
                        await replicas.close()
                        await storage_runtime.close()
                        try:
                            await visual_controller.settle()
                            await live.close()
                        finally:
                            try:
                                await close_storage_upgrade()
                            finally:
                                if categorization_lifecycle is not None:
                                    categorization_lifecycle.close()
                finally:
                    try:
                        for bank in reversed(tuple(banks.values())):
                            try:
                                await bank.close()
                            except Exception:
                                pass
                    finally:
                        banks.clear()
                        if owned_store:
                            close_storage(store)

        app = FastAPI(
            title="WHAB",
            version=__version__,
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
            lifespan=lifespan,
        )
        visual_ui = LeasedVisualUI(runtime)
        templates = VisualTemplates(runtime, registry)
        for plugin in descriptors:
            if plugin.bank_ui and plugin.bank_ui.static.is_dir():
                app.mount(
                    f"/plugins/{plugin.id}/static",
                    StaticFiles(directory=plugin.bank_ui.static),
                    name=f"plugin-{plugin.id}",
                )
        app.include_router(visual_resources_router(worker_home, runtime))
        webhook_paths = {f"/api/banks/{p.id}/webhook" for p in descriptors}
        if "plaid" in bank_stores:
            webhook_paths.add("/api/plaid/webhook")  # Existing documented callback URL.
        install_middleware(
            app,
            settings.port,
            webhook_paths,
            file_import_paths={
                f"/api/plugins/{plugin.id}/import/{action}"
                for plugin in import_descriptors
                for action in ("preview", "commit", "new/preview", "new/commit")
            } | {"/api/budget/open"},
            remote=lambda: runtime.handle("interfaces").call("enabled"),
        )
        # Guards and routing share the same generation, including direct child apps.
        # This pure ASGI lease lasts through the complete response body.
        app.add_middleware(BudgetRuntimeRequests, runtime=runtime)
        install_error_handlers(app)
        app.include_router(health_router())
        app.include_router(import_resources_router(registry))
        app.include_router(ui_views_router(lambda: visual_controller.views))
        app.include_router(visual_ui_router(visual_controller))
        app.include_router(ledger_router(service))
        receipt_service = ReceiptService(store, store.lock)
        app.include_router(receipts_router(receipt_service))
        app.state.receipt_service = receipt_service
        app.include_router(file_imports_router(
            store, registry, budget_host, budget_session.id if budget_session else "default",
        ))
        for plugin in descriptors:
            app.include_router(banks_router(plugin.id, banks, bank_stores[plugin.id]))
            if plugin.id == "plaid":
                app.include_router(
                    banks_router(plugin.id, banks, bank_stores[plugin.id], prefix="/api/plaid")
                )
        app.include_router(plugins_router(store, registry, plugin_manager))
        app.include_router(
            pages_router(
                store, banks, bank_stores, registry, templates, settings.plaid_env, visual_ui,
                plugin_manager=plugin_manager,
                budget_name=budget_session.name if budget_session else "My budget",
                service=service,
            )
        )
        app.state.store = store.store
        app.state.tracked_store = store
        app.state.banks = banks
        app.state.plugins = registry
        app.state.budget_runtime = runtime
        app.state.live_plugins = live
        app.state.storage_runtime = storage_runtime
        app.state.storage_upgrade = storage_upgrade
        if storage_upgrade is not None:
            storage_upgrade.activated = lambda _registry: setattr(app.state, "store", store.store)
        storage_runtime.attach(app, plugin_manager, live)
        app.include_router(storage_runtime.router())
        app.state.storage_replicas = replicas
        app.include_router(replicas.router())
        app.state.bank_ui_available = any(plugin.id == "plaid" and plugin.bank_ui for plugin in descriptors)
        app.state.plugin_manager = plugin_manager
        app.state.storage_plugin = settings.storage if owned_store else None
        app.state.ui_plugin = ui_plugin.id if ui_plugin else None
        app.state.visual_controller = visual_controller
        visual_controller.on_change = lambda identifier: setattr(app.state, "ui_plugin", identifier)
        app.state.ledger_service = service
        app.state.budget_session = budget_session
        storage_contract = contract_for(selected_storage) if selected_storage else None
        storage_file_exports = bool(storage_contract and storage_contract.authority == "local"
                                    and any(capability.name == "transfer.prepare" for capability in storage_contract.provides))
        app.include_router(budget_host.router(store, settings, budget_session, storage_config=resolved_config,
                                             storage_file_exports=storage_file_exports,
                                             namespace_effects=categorization_lifecycle))
        app.include_router(create_remote_router(
            lambda: runtime.handle("interfaces").call("current"),
            lambda: runtime.handle("interfaces").call("legacy_routes"),
        ))
        for plugin in plugins.interface_plugins():
            if plugin.interface.factory is None:
                continue
            try:
                app.include_router(plugin.interface.factory(InterfaceContext(
                    service=service, config=plugin_config(settings, budget_session, plugin.id),
                    environment=settings.plaid_env, budget_id=service.budget_id,
                    budget=budget_session.context(plugin.id) if budget_session else None,
                )))
            except Exception:
                raise PluginError("Interface plugin could not start. Check its configuration.", code="interface_startup_failed", plugin_id=plugin.id) from None
        if not _child:
            budget_host.register(app, budget_session)
            app.add_middleware(BudgetDispatch, host=budget_host)
        app.add_middleware(RequireHTTPS)
        startup.release()
        return app
    except BaseException as error:
        startup.close(error)
        raise
