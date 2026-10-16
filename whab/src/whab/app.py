"""Composition root: construct adapters and assemble the local application."""

import inspect
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import replace

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader, PrefixLoader

from whab import __version__
from whab.budgets.host import BudgetDispatch, BudgetHost
from whab.budgets.session import BudgetSession, TrackedStorage
from whab.config import Settings
from whab.plugins import (
    BankContext,
    InterfaceContext,
    LedgerService,
    PluginError,
    StorageContext,
    load_plugins,
)
from whab.plugins.management import PluginManager, installed_entrypoints
from whab.plugins.storage import close_storage, open_storage
from whab.storage.bank import BankStore
from whab.web.errors import install_error_handlers
from whab.web.middleware import install_middleware
from whab.web.routes.banks import create_router as banks_router
from whab.web.routes.file_imports import create_router as file_imports_router
from whab.web.routes.ledger import create_router as ledger_router
from whab.web.routes.pages import create_router as pages_router
from whab.web.routes.plugins import create_router as plugins_router


def plugin_config(settings, session, plugin_id):
    """Portable public settings plus machine-local credentials for one plugin."""
    if plugin_id == "client":
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


def create_app(store=None, settings=None, plugins=None, bank_services=None, *, budget_session=None, budget_host=None, _child=False):
    settings = settings or Settings.load()
    if budget_session is None and settings.budget_path:
        budget_session = BudgetSession.open(settings.home, settings.plaid_env, settings.budget_path)
    if budget_session:
        installed = {entry.name for entry in installed_entrypoints()}
        budget_session.missing_plugins = [name for name in budget_session.selection if name not in installed]
        settings = replace(settings, storage=budget_session.manifest["storage"], plugins=tuple(name for name in budget_session.selection if name in installed))
    budget_host = budget_host or BudgetHost(settings)
    plugins = plugins if plugins is not None else load_plugins(settings.enabled_plugins())
    if budget_session:
        plugins = plugins.bind_budget(budget_session.context)
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
    if settings.storage == "client" and descriptors:
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
    plugins.configure_logo_caches(settings.home)
    owned_store = store is None
    if owned_store:
        store = open_storage(
            plugins.storage_plugin(settings.storage),
            StorageContext(
                home=budget_session.storage_home if budget_session else settings.home,
                environment=settings.plaid_env,
                config=plugin_config(settings, budget_session, settings.storage),
                budget=budget_session.context(settings.storage) if budget_session else None,
            ),
        )
    try:
        if budget_session:
            budget_session.bind(store)
            for path in settings.copy_paths:
                target = path.resolve()
                if not any(item["path"] == str(target) for item in budget_session.destinations):
                    budget_session.add_copy(path.stem, str(target))
        store = TrackedStorage(store, budget_session)
        service = LedgerService(store, budget_id=budget_session.id if budget_session else "default", lock=store.lock)
        store.on_mutation = service.invalidate_revision
        bank_stores = {plugin.id: BankStore(store, plugin.id) for plugin in descriptors}
    except Exception:
        if owned_store:
            close_storage(store)
        raise
    banks = {}

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
            yield
        finally:
            # Close every service even if another plugin's cleanup raises.
            try:
                if not _child:
                    await budget_host.close()
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
    templates = Jinja2Templates(directory=ui.templates) if ui else None
    loaders = {}
    import_loaders = {}
    for plugin in descriptors:
        if ui and plugin.bank_ui:
            loaders[plugin.id] = FileSystemLoader(plugin.bank_ui.templates)
            app.mount(
                f"/plugins/{plugin.id}/static",
                StaticFiles(directory=plugin.bank_ui.static),
                name=f"plugin-{plugin.id}",
            )
    for plugin in import_descriptors:
        file_import = plugin.file_import
        if ui and file_import.templates is not None:
            import_loaders[plugin.id] = FileSystemLoader(file_import.templates)
            app.mount(
                f"/imports/{plugin.id}/static",
                StaticFiles(directory=file_import.static),
                name=f"import-{plugin.id}",
            )
    if ui:
        templates.env.loader = ChoiceLoader(
            [
                templates.env.loader,
                PrefixLoader({"plugins": PrefixLoader(loaders)}),
                PrefixLoader({"imports": PrefixLoader(import_loaders)}),
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
            for action in ("preview", "commit")
        } | {"/api/budget/open"},
        remote=bool(plugins.interface_plugins()),
    )
    install_error_handlers(app)
    app.include_router(ledger_router(service))
    app.include_router(file_imports_router(store, plugins))
    for plugin in descriptors:
        app.include_router(banks_router(plugin.id, banks, bank_stores[plugin.id]))
        if plugin.id == "plaid":
            app.include_router(
                banks_router(plugin.id, banks, bank_stores[plugin.id], prefix="/api/plaid")
            )
    plugin_manager = PluginManager(settings, plugins, budget_session=budget_session)
    app.include_router(plugins_router(store, plugins, plugin_manager))
    app.include_router(
        pages_router(
            store, banks, bank_stores, plugins, templates, settings.plaid_env, ui,
            plugin_manager=plugin_manager,
            budget_name=budget_session.name if budget_session else "My budget",
        )
    )
    app.state.store = store.store
    app.state.tracked_store = store
    app.state.banks = banks
    app.state.plugins = plugins
    app.state.plugin_manager = plugin_manager
    app.state.storage_plugin = settings.storage if owned_store else None
    app.state.ui_plugin = ui_plugin.id if ui_plugin else None
    app.state.ledger_service = service
    app.state.budget_session = budget_session
    app.include_router(budget_host.router(store, settings, budget_session))
    for plugin in plugins.interface_plugins():
        try:
            app.include_router(plugin.interface.factory(InterfaceContext(
                service=service, config=plugin_config(settings, budget_session, plugin.id),
                environment=settings.plaid_env, budget_id=service.budget_id,
                budget=budget_session.context(plugin.id) if budget_session else None,
            )))
        except Exception:
            if owned_store:
                close_storage(store)
            raise PluginError("Interface plugin could not start. Check its configuration.", code="interface_startup_failed", plugin_id=plugin.id) from None
    if not _child:
        budget_host.register(app, budget_session)
        app.add_middleware(BudgetDispatch, host=budget_host)
    return app
