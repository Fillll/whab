"""Composition root: construct adapters and assemble the local application."""

import inspect
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader, PrefixLoader

from whab import __version__
from whab.budgets.host import BudgetDispatch, BudgetHost, BudgetRuntimeRequests
from whab.budgets.plugin_state import check_saved_states
from whab.budgets.runtime import BudgetRuntime
from whab.budgets.session import BudgetSession, TrackedStorage
from whab.budgets.storage_replicas import StorageReplicas
from whab.budgets.storage_runtime import StorageRuntime, open_saved_storage, saved_storage_selection
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
from whab.plugins.live import (
    LeasedPluginRegistry,
    LivePluginSelection,
    RegistryGeneration,
)
from whab.plugins.management import PluginManager, installed_entrypoints
from whab.plugins.storage import close_storage, open_storage
from whab.plugins.worker_adapters import supports_live_plugin
from whab.plugins.worker_selection import restore_workers
from whab.services.categorization import local_context, local_files
from whab.services.categorization_runtime import (
    CategorizationLifecycle,
    LeasedCategorization,
    live_categorizer,
)
from whab.services.receipts import ReceiptService
from whab.storage.bank import BankStore
from whab.web.errors import install_error_handlers
from whab.web.import_resources import ImportResourceLoader
from whab.web.import_resources import create_router as import_resources_router
from whab.web.middleware import install_middleware
from whab.web.routes.banks import create_router as banks_router
from whab.web.routes.file_imports import create_router as file_imports_router
from whab.web.routes.health import create_router as health_router
from whab.web.routes.ledger import create_router as ledger_router
from whab.web.routes.pages import create_router as pages_router
from whab.web.routes.plugins import create_router as plugins_router
from whab.web.routes.receipts import create_router as receipts_router


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
    budget_host = budget_host or BudgetHost(settings)
    plugins = plugins if plugins is not None else load_plugins(settings.enabled_plugins())
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
    if ui and (not (ui.templates / "base.html").is_file() or not ui.static.is_dir()):
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
    selected_storage = plugins.storage_plugin(settings.storage) if owned_store else None
    resolved_config = storage_config(settings, budget_session, selected_storage) if owned_store else {}
    if budget_session:
        budget_session.storage_config = resolved_config
    if owned_store:
        store = open_saved_storage(settings, budget_session,
            lambda plugin: storage_config(replace(settings, storage=plugin.id), budget_session, plugin))
        store = store or open_storage(
            selected_storage,
            StorageContext(
                home=budget_session.backend_home if budget_session else settings.home,
                environment=settings.plaid_env,
                config=resolved_config,
                budget=budget_session.context(settings.storage) if budget_session else None,
            ),
        )
    try:
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
        categorization_lifecycle, categorization, categorization_resource = None, None, None
        if not remote_authority:
            state_home = settings.home / "data" / "plugin-state" / settings.plaid_env
            context_factory = budget_session.context if budget_session else lambda identifier: local_context(
                state_home, service.budget_id, identifier, lock=store.lock,
                on_change=lambda: store.after_commit and store.after_commit())
            saved_ids = [record["id"] for record in budget_session.manifest["plugins"]
                         if any(f"plugins/{record['id']}/suggestions-{workspace}.json" in budget_session.files
                                for workspace in ("personal", "demo"))] if budget_session else [
                    path.name for path in (settings.home / "data" / "plugin-state" / settings.plaid_env / "plugin-data").glob("*")
                    if any((path / f"suggestions-{workspace}.json").is_file() for workspace in ("personal", "demo"))]
            snapshot = budget_session.files_for if budget_session else lambda identifier: local_files(state_home, service.budget_id, identifier)
            categorization_lifecycle = CategorizationLifecycle(service, context_factory, snapshot)
            categorization, categorization_resource = categorization_lifecycle.initial(plugins.descriptors(), saved_ids)
        bank_stores = {plugin.id: BankStore(store, plugin.id, suggestions_only=lambda: bool(service.categorization and service.categorization.enabled())) for plugin in descriptors}
    except Exception:
        if owned_store:
            close_storage(store)
        raise
    banks = {}
    worker_home = budget_session.storage_home if budget_session else settings.home
    try:
        plugins, worker_bindings = restore_workers(worker_home, plugins)
    except BaseException:
        if categorization is not None:
            try:
                categorization.close()
            except Exception:
                pass
        if owned_store:
            close_storage(store)
        raise
    runtime = BudgetRuntime(service.budget_id, {
        "plugins": RegistryGeneration(plugins, workers=worker_bindings, resources_enabled=bool(ui)), "ledger": service,
        "storage": store.store, "banks": banks, "categorization": categorization,
    }, tuple(binding.resource for binding in worker_bindings.values()) +
       ((categorization_resource,) if categorization_resource else ()))
    if categorization is not None:
        service.categorization = LeasedCategorization(runtime)
        store.after_ingestion = service.categorization.after_import
    registry = LeasedPluginRegistry(runtime)
    plugin_manager = PluginManager(settings, registry, budget_session=budget_session)
    live = LivePluginSelection(runtime, plugin_manager.required, installed_entrypoints,
                               plugin_manager.persist_live, budget_session, nonlive, worker_home)
    live.categorization = categorization_lifecycle
    plugin_manager.live = live
    storage_runtime = StorageRuntime(settings, budget_session, store, runtime, registry,
        lambda plugin: storage_config(replace(settings, storage=plugin.id), budget_session, plugin))
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
                    await live.close()
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
    # The inner lease also covers directly used child apps. BudgetDispatch pins
    # the same generation around every default/selected dispatch path.
    app.add_middleware(BudgetRuntimeRequests, runtime=runtime)
    templates = Jinja2Templates(directory=ui.templates) if ui else None
    loaders = {}
    for plugin in descriptors:
        if ui and plugin.bank_ui:
            loaders[plugin.id] = FileSystemLoader(plugin.bank_ui.templates)
            app.mount(
                f"/plugins/{plugin.id}/static",
                StaticFiles(directory=plugin.bank_ui.static),
                name=f"plugin-{plugin.id}",
            )
    if ui:
        templates.env.loader = ChoiceLoader(
            [
                templates.env.loader,
                PrefixLoader({"plugins": PrefixLoader(loaders)}),
                PrefixLoader({"imports": ImportResourceLoader(registry)}),
            ]
        )
        app.mount("/static", StaticFiles(directory=ui.static), name="static")
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
        remote=bool(plugins.interface_plugins()),
    )
    install_error_handlers(app)
    app.include_router(health_router())
    if ui:
        app.include_router(import_resources_router(registry))
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
            store, banks, bank_stores, registry, templates, settings.plaid_env, ui,
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
    storage_runtime.attach(app, plugin_manager, live)
    app.include_router(storage_runtime.router())
    app.state.storage_replicas = replicas
    app.include_router(replicas.router())
    app.state.bank_ui_available = any(plugin.id == "plaid" and plugin.bank_ui for plugin in descriptors)
    app.state.plugin_manager = plugin_manager
    app.state.storage_plugin = settings.storage if owned_store else None
    app.state.ui_plugin = ui_plugin.id if ui_plugin else None
    app.state.ledger_service = service
    app.state.budget_session = budget_session
    storage_contract = contract_for(selected_storage) if selected_storage else None
    storage_file_exports = bool(storage_contract and storage_contract.authority == "local"
                                and any(capability.name == "transfer.prepare" for capability in storage_contract.provides))
    app.include_router(budget_host.router(store, settings, budget_session, storage_config=resolved_config,
                                         storage_file_exports=storage_file_exports))
    for plugin in plugins.interface_plugins():
        try:
            app.include_router(plugin.interface.factory(InterfaceContext(
                service=service, config=plugin_config(settings, budget_session, plugin.id),
                environment=settings.plaid_env, budget_id=service.budget_id,
                budget=budget_session.context(plugin.id) if budget_session else None,
            )))
        except Exception:
            for binding in reversed(tuple(worker_bindings.values())):
                binding.worker.close()
            if owned_store:
                close_storage(store)
            raise PluginError("Interface plugin could not start. Check its configuration.", code="interface_startup_failed", plugin_id=plugin.id) from None
    if not _child:
        budget_host.register(app, budget_session)
        app.add_middleware(BudgetDispatch, host=budget_host)
    return app
