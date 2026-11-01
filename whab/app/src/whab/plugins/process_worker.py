"""Every installed plugin is discovered and executed in an owned child process."""

import asyncio
import hashlib
import os
import subprocess
import sys
import weakref
from dataclasses import replace
from functools import partial
from pathlib import Path

from .process_environment import process_environment
from .process_rpc import Peer
from .wire import WireError


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
        from .sdk import valid_plugin_name

        if not valid_plugin_name(plugin_id):
            raise ValueError("Choose a valid plugin identity.")
        self.plugin_id, self.timeout = plugin_id, timeout
        self.dependencies = ()
        self._process = self._retiring = self.peer = None
        self._resource = None
        try:
            self._process = subprocess.Popen(
                [str(python), "-I", "-B", "-m", "whab.plugins.process_worker", plugin_id],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=plugin_environment(plugin_id), bufsize=0, start_new_session=True,
            )
            self.peer = Peer(self._process.stdout, self._process.stdin, timeout=timeout)
            self.peer.codec.owner = weakref.ref(self)
            from .process_capabilities import prepare_record

            self.peer.codec.prepare_record = partial(prepare_record, owner=weakref.ref(self))
            self._finalizer = weakref.finalize(self, _dispose, self._process, self.peer)
            result = self.peer.call("root", "describe")
            if type(result) is not dict or set(result) != {"plugin", "pid", "code"} or result["pid"] != self._process.pid:
                raise WireError()
            self.code_identity = result["code"]
            self._descriptor = self.adapt(result["plugin"])
            if self._descriptor.id != plugin_id:
                raise WireError()
            from importlib import metadata

            from .manifests import installed_manifest

            entry, = (entry for entry in metadata.entry_points(group="whab.plugins") if entry.name == plugin_id)
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
        from .ui_worker import proxy_context

        if type(plugin) is not Plugin or plugin.id != self.plugin_id:
            raise WireError()
        updates = {"_process": self}
        if plugin.interface is not None and plugin.interface.factory is not None:
            factory = plugin.interface.factory
            updates["interface"] = Interface(factory=lambda context: self.interface(context, factory),
                                             local_settings=plugin.interface.local_settings)
        if plugin.ui is not None:
            updates["ui"] = replace(plugin.ui, build_context=proxy_context)
        if plugin.storage is not None and type(plugin.storage) is not RemoteLedgerClient:
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
        return StorageWorkerProxy(StorageWorker(self.plugin_id), context, root)

    def interface(self, context, factory):
        from contextlib import asynccontextmanager

        from fastapi import APIRouter, Request, Response
        from starlette.concurrency import run_in_threadpool

        descriptions = self.peer.call("root", "interface.open", (context, factory))
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
            result = await run_in_threadpool(self.peer.call, "root", "interface.http", ({
                "method": request.method, "path": request.url.path,
                "query": request.scope.get("query_string", b""),
                "headers": tuple((key, value) for key, value in request.scope["headers"]
                                 if key.lower() in {b"content-type", b"accept"}),
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
            for stream in (process.stdin, process.stdout):
                stream.close()


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

            entries = [entry for entry in metadata.entry_points(group="whab.plugins") if entry.name == self.plugin_id]
            entry = entries[0]
            digest.update((entry.value + "\n" + entry.dist.version).encode())
            spec = util.find_spec(entry.value.split(":")[0].split(".")[0])
            for location in spec.submodule_search_locations or ():
                for path in sorted(Path(location).rglob("*.py")):
                    if not path.is_symlink() and path.stat().st_size <= 2 * 1024 * 1024:
                        digest.update(path.relative_to(location).as_posix().encode())
                        digest.update(path.read_bytes())
            return {"plugin": normalize(self.plugin), "pid": os.getpid(), "code": digest.hexdigest()}
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
        return [{"path": route.path, "methods": tuple(sorted(route.methods)), "name": route.name}
                for route in router.routes]

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
