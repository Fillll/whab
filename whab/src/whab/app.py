"""Composition root: construct adapters and assemble the local application."""

import inspect
from collections.abc import Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader, PrefixLoader

from whab import __version__
from whab.config import Settings
from whab.plugins import BankContext, PluginError, StorageContext, load_plugins
from whab.plugins.management import PluginManager
from whab.plugins.storage import close_storage, open_storage
from whab.storage.bank import BankStore
from whab.web.errors import install_error_handlers
from whab.web.middleware import install_middleware
from whab.web.routes.banks import create_router as banks_router
from whab.web.routes.file_imports import create_router as file_imports_router
from whab.web.routes.ledger import create_router as ledger_router
from whab.web.routes.pages import create_router as pages_router
from whab.web.routes.plugins import create_router as plugins_router


def create_app(store=None, settings=None, plugins=None, bank_services=None):
    settings = settings or Settings.load()
    plugins = plugins if plugins is not None else load_plugins(settings.enabled_plugins())
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
                home=settings.home,
                environment=settings.plaid_env,
                config=settings.storage_configuration(),
            ),
        )
    try:
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
            yield
        finally:
            # Close every service even if another plugin's cleanup raises.
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
        },
    )
    install_error_handlers(app)
    app.include_router(ledger_router(store))
    app.include_router(file_imports_router(store, plugins))
    for plugin in descriptors:
        app.include_router(banks_router(plugin.id, banks, bank_stores[plugin.id]))
        if plugin.id == "plaid":
            app.include_router(
                banks_router(plugin.id, banks, bank_stores[plugin.id], prefix="/api/plaid")
            )
    plugin_manager = PluginManager(settings, plugins)
    app.include_router(plugins_router(store, plugins, plugin_manager))
    app.include_router(
        pages_router(
            store, banks, bank_stores, plugins, templates, settings.plaid_env, ui,
            plugin_manager=plugin_manager,
        )
    )
    app.state.store = store
    app.state.banks = banks
    app.state.plugins = plugins
    app.state.plugin_manager = plugin_manager
    app.state.storage_plugin = settings.storage if owned_store else None
    app.state.ui_plugin = ui_plugin.id if ui_plugin else None
    return app
