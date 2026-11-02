"""Every installed plugin is discovered and executed in an owned child process."""

import asyncio
import hashlib
import os
import subprocess
import sys
import weakref
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from functools import partial, wraps
from pathlib import Path
from threading import RLock

from .process_environment import process_environment
from .process_rpc import Peer
from .wire import WireError

_STARTUP = ContextVar("whab_plugin_process_startup", default=None)
_DISCOVERY = ContextVar("whab_plugin_process_discovery", default=())
_PENDING = {}
_PENDING_LOCK = RLock()


@contextmanager
def discovery_scope():
    owners = []
    token = _DISCOVERY.set((*_DISCOVERY.get(), owners))
    try:
        yield
    except BaseException:
        for owner in reversed(owners):
            owner.close()
        raise
    finally:
        _DISCOVERY.reset(token)


def set_process_scope(home, budget_id, environment):
    startup = _STARTUP.get()
    if startup is not None:
        startup["scope"] = str(Path(home).resolve()), budget_id, environment


def retry_process_cleanup(home, budget_id, environment):
    key = str(Path(home).resolve()), budget_id, environment
    with _PENDING_LOCK:
        owners = [owner for owner in _PENDING.values() if owner.scope == key]
    for owner in owners:
        owner.close()
    with _PENDING_LOCK:
        return not any(owner.scope == key for owner in _PENDING.values())


def managed_plugin_startup(function):
    """Own every discovery child before application startup can fail."""
    @wraps(function)
    def run(*args, **kwargs):
        acquired = []
        token = _STARTUP.set({"processes": acquired, "scope": None})
        try:
            app = function(*args, **kwargs)
            active = app.state.budget_runtime._active.services["plugins"].descriptors()
            retained = {id(plugin._process) for plugin in active if plugin._process is not None}
            for process in acquired:
                if id(process) not in retained:
                    process.close()
            return app
        except BaseException:
            for process in reversed(acquired):
                process.close()
            raise
        finally:
            _STARTUP.reset(token)

    return run


def plugin_environment(plugin_id):
    """Explicit installed declarations, including public dependency configuration.

    The ordinary process allowlist remains unchanged. This opt-in declaration
    preserves machine-local configuration used by a plugin's public API without
    inheriting arbitrary caller settings or the selected budget password alias.
    """
    from importlib import metadata

    from .manifests import installed_manifest

    entries = {}
    for entry in metadata.entry_points(group="whab.plugins"):
        entries.setdefault(entry.name, []).append(entry)
    visited, pending, names = set(), [plugin_id], set()
    while pending:
        identifier = pending.pop()
        if identifier in visited:
            continue
        visited.add(identifier)
        matches = entries.get(identifier, ())
        if len(matches) != 1:
            raise WireError()
        manifest = installed_manifest(matches[0])
        if manifest is not None:
            names.update(manifest.get("process_config", ()))
            pending.extend(row["id"] for row in manifest["dependencies"])
    values = process_environment()
    excluded = os.environ.get("WHAB_BUDGET_PASSWORD_ENV")
    for name in names:
        if name != excluded and name in os.environ and len(os.environ[name]) <= 8192:
            values[name] = os.environ[name]
    return values


class PluginProcess:
    def __init__(self, plugin_id, *, python=sys.executable, timeout=60):
        from importlib import metadata

        from whab import __version__

        from .sdk import API_VERSION, valid_plugin_name

        if not valid_plugin_name(plugin_id) or type(timeout) not in {int, float} or not 0 < timeout <= 60:
            raise ValueError("Choose a valid plugin identity and a timeout within 60 seconds.")
        self.plugin_id, self.timeout = plugin_id, timeout
        self.scope = _STARTUP.get()["scope"] if _STARTUP.get() is not None else None
        self.dependencies = ()
        self._process = self._retiring = self.peer = None
        self._resource = None
        self.openapi = None
        try:
            entry, = (entry for entry in metadata.entry_points(group="whab.plugins") if entry.name == plugin_id)
            distribution = getattr(entry, "dist", None)
            self._process = subprocess.Popen(
                [str(python), "-I", "-B", "-m", "whab.plugins.process_worker", plugin_id],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=plugin_environment(plugin_id), bufsize=0, start_new_session=True,
            )
            acquired = _STARTUP.get()
            if acquired is not None:
                acquired["processes"].append(self)
            for owners in _DISCOVERY.get():
                owners.append(self)
            self.peer = Peer(self._process.stdout, self._process.stdin, timeout=timeout)
            self.peer.codec.owner = weakref.ref(self)
            from .process_capabilities import prepare_record

            self.peer.codec.prepare_record = partial(prepare_record, owner=weakref.ref(self))
            self._finalizer = weakref.finalize(self, _dispose, self._process, self.peer)
            result = self.peer.call("root", "describe")
            if (type(result) is not dict or set(result) != {"plugin", "pid", "code", "host", "sdk", "distribution", "version"}
                    or type(result["pid"]) is not int or result["pid"] != self._process.pid
                    or result["host"] != __version__ or type(result["sdk"]) is not int or result["sdk"] != API_VERSION
                    or type(result["code"]) is not str or len(result["code"]) != 64
                    or any(character not in "0123456789abcdef" for character in result["code"])
                    or any(type(result[name]) is not str or not 1 <= len(result[name]) <= 200 for name in ("distribution", "version"))
                    or distribution is not None and (result["distribution"] != distribution.metadata["Name"]
                                                     or result["version"] != distribution.version)):
                raise WireError()
            self.info = {key: result[key] for key in ("host", "sdk", "distribution", "version")}
            self.code_identity = result["code"]
            self._descriptor = self.adapt(result["plugin"])
            if self._descriptor.id != plugin_id:
                raise WireError()
            from .manifests import installed_manifest

            manifest = installed_manifest(entry)
            self.dependencies = tuple(row["id"] for row in manifest["dependencies"]) if manifest else ()
        except BaseException:
            self.close()
            raise

    @property
    def descriptor(self):
        # The process must not retain its own descriptor: unused discovery
        # results then release their child immediately, including failed previews.
        result, self._descriptor = self._descriptor, None
        return result

    @property
    def resource(self):
        from starlette.concurrency import run_in_threadpool

        from whab.budgets.runtime import RuntimeResource

        if self._resource is None:
            owner = weakref.ref(self)

            async def close():
                process = owner()
                if process is not None:
                    await run_in_threadpool(process.close)
                    if process.cleanup_pending:
                        raise ProcessLookupError("Plugin cleanup remains pending.")

            self._resource = RuntimeResource(self.plugin_id, close, plugin_process=True)
        return self._resource

    @property
    def healthy(self):
        return self._process is not None and self._process.poll() is None and not self.peer._closed.is_set()

    @property
    def cleanup_pending(self):
        return self._retiring is not None

    def adapt(self, plugin):
        from .sdk import Interface, Plugin, RemoteLedgerClient
        from .storage_worker import supports_storage_worker
        from .ui_worker import proxy_context

        if type(plugin) is not Plugin or plugin.id != self.plugin_id:
            raise WireError()
        updates = {"_process": self}
        if plugin.interface is not None and plugin.interface.factory is not None:
            factory = plugin.interface.factory
            updates["interface"] = Interface(factory=lambda context: self.interface(context, factory, plugin.interface.local_settings),
                                             local_settings=plugin.interface.local_settings)
        if plugin.ui is not None:
            updates["ui"] = replace(plugin.ui, build_context=proxy_context)
        if plugin.storage is not None and type(plugin.storage) is not RemoteLedgerClient and supports_storage_worker(plugin):
            updates["storage"] = self.storage
        if plugin.bind is not None:
            original = plugin.bind
            updates["bind"] = lambda context: self.adapt(original(context))
        if plugin.branding_factory is not None:
            original_branding = plugin.branding_factory
            updates["branding_factory"] = lambda: self.adapt(original_branding())
        return replace(plugin, **updates)

    def storage(self, context):
        from .storage_proxy import StorageWorkerProxy
        from .storage_worker import StorageWorker

        # Keep the native financial receipts/fencing protocol authoritative.
        root = getattr(self, "home", context.home) / "plugin-workers" / "storage-intents"
        worker_owner = getattr(self, "worker_owner", None)
        worker = StorageWorker(self.plugin_id, **({"on_create": worker_owner} if worker_owner else {}))
        return StorageWorkerProxy(worker, context, root, on_unknown=self.storage_fault,
                                  on_create=getattr(self, "storage_owner", None))

    def storage_fault(self, plugin_id):
        runtime = getattr(self, "runtime", None)
        if runtime is not None:
            registry = runtime._active.services["plugins"]
            active = next((plugin for plugin in registry.descriptors() if plugin.id == plugin_id), None)
            if active is not None and active._process is self:
                runtime.fence_storage(plugin_id)

    def interface(self, context, factory, local_settings):
        from contextlib import asynccontextmanager

        from fastapi import APIRouter, Request, Response
        from starlette.concurrency import run_in_threadpool

        prepared = self.peer.call("root", "interface.open", (context, factory))
        if type(prepared) is not dict or set(prepared) != {"routes", "schema"} or type(prepared["schema"]) is not dict:
            raise WireError()
        descriptions = prepared["routes"]
        self.openapi = prepared["schema"]
        if type(descriptions) is not list or len(descriptions) > 256:
            raise WireError()

        @asynccontextmanager
        async def lifespan(_app):
            try:
                yield
            finally:
                if self.healthy:
                    await run_in_threadpool(self.peer.call, "root", "interface.close")

        router = APIRouter(lifespan=lifespan)

        async def forward(request: Request):
            raw = await request.body()
            if len(raw) > 1024 * 1024:
                return Response(status_code=413)
            headers = {b"content-type", b"accept"} | ({b"authorization"} if not local_settings else set())
            result = await run_in_threadpool(self.peer.call, "root", "interface.http", ({
                "method": request.method, "path": request.url.path,
                "query": request.scope.get("query_string", b""),
                "headers": tuple((key, value) for key, value in request.scope["headers"]
                                 if key.lower() in headers),
                "body": raw,
            },))
            if (type(result) is not dict or set(result) != {"status", "headers", "body"}
                    or type(result["status"]) is not int or not 100 <= result["status"] <= 599
                    or type(result["body"]) is not bytes or len(result["body"]) > 16 * 1024 * 1024):
                raise WireError()
            response = Response(content=result["body"], status_code=result["status"])
            response.raw_headers = list(result["headers"])
            return response

        for description in descriptions:
            if (type(description) is not dict or set(description) != {"path", "methods", "name"}
                    or type(description["path"]) is not str or not description["path"].startswith("/")
                    or type(description["methods"]) is not tuple or not description["methods"]
                    or set(description["methods"]) - {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
                    or type(description["name"]) is not str):
                raise WireError()
            router.add_api_route(description["path"], forward, methods=list(description["methods"]),
                                 name=description["name"], include_in_schema=False)
        return router

    def close(self):
        from .report_worker import _stop_process_group

        process = self._process or self._retiring
        self._process, self._retiring = None, None
        if process is None:
            return
        if getattr(self, "_finalizer", None) is not None:
            self._finalizer.detach()
        try:
            _stop_process_group(process)
        except (OSError, subprocess.TimeoutExpired):
            self._retiring = process
        finally:
            if self.peer is not None:
                self.peer.close()
                if self.peer.cleanup_pending:
                    self._retiring = process
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except OSError:
                    self._retiring = process
            with _PENDING_LOCK:
                if self._retiring is not None:
                    _PENDING[id(self)] = self
                else:
                    _PENDING.pop(id(self), None)


def _dispose(process, peer):
    from .report_worker import _stop_process_group

    try:
        _stop_process_group(process)
    except (OSError, subprocess.TimeoutExpired):
        pass
    finally:
        peer.close()
        for stream in (process.stdin, process.stdout):
            stream.close()


def interface_documentation(app, plugins):
    """Merge data-only child OpenAPI with collision-free component names."""
    from copy import deepcopy

    document = deepcopy(app.openapi())
    for plugin in plugins:
        owner = plugin._process
        if owner is None or owner.openapi is None:
            continue
        child = deepcopy(owner.openapi)
        schemas = child.get("components", {}).get("schemas", {})
        names = {name: plugin.id.replace("-", "_") + "_" + name for name in schemas}

        def references(value):
            if type(value) is dict:
                for key, item in value.items():
                    if key == "$ref" and type(item) is str and item.startswith("#/components/schemas/"):
                        name = item.removeprefix("#/components/schemas/")
                        value[key] = "#/components/schemas/" + names.get(name, name)
                    else:
                        references(item)
            elif type(value) is list:
                for item in value:
                    references(item)

        references(child)
        document["paths"].update(child.get("paths", {}))
        document.setdefault("components", {}).setdefault("schemas", {}).update(
            {names[name]: schema for name, schema in schemas.items()})
    return document


def normalize(plugin):
    """Capture version-coherent resources and keep rendering code in the child."""
    from .sdk import BankUI, FileImport
    from .ui_render import render_page
    from .ui_worker import snapshot_ui
    from .worker_resources import capture_bank_resources, capture_resources

    updates = {}
    if plugin.ui is not None:
        ui = snapshot_ui(plugin.ui)
        updates["ui"] = replace(ui, render=ui.render or (lambda arguments: render_page(ui, arguments)))
    if plugin.bank_ui is not None:
        updates["bank_ui"] = BankUI(resources=capture_bank_resources(plugin.bank_ui))
    if plugin.file_import is not None and (plugin.file_import.resources is not None or plugin.file_import.templates is not None):
        updates["file_import"] = FileImport(plugin.file_import.parse, resources=capture_resources(plugin.file_import))
    if plugin.bind is not None:
        original = plugin.bind
        updates["bind"] = lambda context: normalize(original(context))
    if plugin.branding_factory is not None:
        branding = plugin.branding_factory
        updates["branding_factory"] = lambda: normalize(branding())
    return replace(plugin, **updates)


class Worker:
    def __init__(self, plugin_id):
        self.plugin_id, self.plugin = plugin_id, None
        self.app = self.lifespan = None

    def __call__(self, operation, *args):
        if operation == "describe" and not args and self.plugin is None:
            from .runtime import load_descriptors

            self.plugin = load_descriptors((self.plugin_id,), _worker=True)[0]
            digest = hashlib.sha256()
            from importlib import metadata, util

            from whab import __version__
            from whab.web.visual_interface import _source_paths

            entries = [entry for entry in metadata.entry_points(group="whab.plugins") if entry.name == self.plugin_id]
            entry = entries[0]
            digest.update((__version__ + "\n" + entry.value + "\n" + entry.dist.version).encode())
            spec = util.find_spec(entry.value.split(":")[0].split(".")[0])
            total = 0
            for location in spec.submodule_search_locations or ():
                for path in _source_paths(Path(location)):
                    raw = path.read_bytes() if path.stat().st_size <= 2 * 1024 * 1024 else None
                    if raw is None:
                        raise WireError()
                    total += len(raw)
                    if total > 10 * 1024 * 1024:
                        raise WireError()
                    digest.update(path.relative_to(location).as_posix().encode())
                    digest.update(raw)
            from .sdk import API_VERSION

            return {"plugin": normalize(self.plugin), "pid": os.getpid(), "code": digest.hexdigest(),
                    "host": __version__, "sdk": API_VERSION, "distribution": entry.dist.metadata["Name"],
                    "version": entry.dist.version}
        if operation == "interface.open" and len(args) == 2 and self.app is None:
            return self.open_interface(*args)
        if operation == "interface.http" and len(args) == 1 and self.app is not None:
            return self.http(args[0])
        if operation == "interface.close" and not args:
            return self.close_interface()
        raise WireError()

    async def open_interface(self, context, factory):
        from fastapi import FastAPI

        from whab.web.errors import install_error_handlers

        if self.plugin.interface is None or self.plugin.interface.factory is None:
            raise WireError()
        router = factory(context)
        if self.plugin.interface.local_settings:
            prefix = "/api/plugin-settings/" + self.plugin.id
            if any(route.path != prefix and not route.path.startswith(prefix + "/") for route in router.routes):
                raise WireError()
        self.app = FastAPI()
        install_error_handlers(self.app)
        self.app.include_router(router)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        return {"routes": [{"path": route.path, "methods": tuple(sorted(route.methods)), "name": route.name}
                           for route in router.routes], "schema": self.app.openapi()}

    async def http(self, value):
        if (type(value) is not dict or set(value) != {"method", "path", "query", "headers", "body"}
                or type(value["body"]) is not bytes or len(value["body"]) > 1024 * 1024):
            raise WireError()
        messages = []
        received = False

        async def receive():
            nonlocal received
            if received:
                await asyncio.Future()
            received = True
            return {"type": "http.request", "body": value["body"], "more_body": False}

        async def send(message):
            messages.append(message)
            if sum(len(item.get("body", b"")) for item in messages) > 16 * 1024 * 1024:
                raise WireError()

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "scheme": "https",
                 "method": value["method"], "path": value["path"], "raw_path": value["path"].encode(),
                 "query_string": value["query"], "root_path": "", "headers": list(value["headers"]),
                 "server": ("localhost", 443), "client": ("127.0.0.1", 0)}
        await self.app(scope, receive, send)
        start = messages[0]
        return {"status": start["status"], "headers": tuple(start["headers"]),
                "body": b"".join(item.get("body", b"") for item in messages[1:])}

    async def close_interface(self):
        if self.lifespan is not None:
            lifespan, self.lifespan = self.lifespan, None
            await lifespan.__aexit__(None, None, None)


def main():
    # Reserve the original stdout pipe before any plugin import. Plugin prints,
    # native writes and diagnostics cannot corrupt protocol frames or leak data.
    target = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, sys.stdout.fileno())
    os.close(null)
    worker = Worker(sys.argv[1])
    peer = Peer(sys.stdin.buffer, target, root=worker)
    try:
        peer._closed.wait()
    finally:
        peer.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
