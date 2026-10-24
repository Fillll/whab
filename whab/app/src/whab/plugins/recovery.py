"""Minimal local startup repair without importing plugin entry-point targets."""

import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from threading import RLock
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from whab import __version__
from whab.budgets.document import MAX_MANIFEST_BYTES, _json, digest
from whab.budgets.session import BudgetSession
from whab.config import Settings
from whab.web.middleware import install_middleware

from .configuration import MAX_SELECTION_BYTES, validate_selection, write_selection
from .management import installed_entrypoints, package_details
from .sdk import valid_plugin_name

_MESSAGES = {
    "invalid_request": "Use the current recovery revision and valid optional plugin IDs.",
    "revision_conflict": "Recovery settings changed. Refresh before applying another action.",
    "required_plugin": "Storage and the selected interface are required. Repair their installed packages before retrying startup.",
    "selection_unreadable": "The saved optional plugin selection is invalid. Archive and clear it explicitly to continue.",
    "budget_unreadable": "This budget's metadata could not be validated. Preserve its files before repairing the budget.",
    "metadata_unreadable": "Installed package metadata could not be read. Repair this Python installation before retrying.",
    "worker_reference_invalid": "A saved report-worker reference is invalid. Preserve its environment and explicitly archive the reference if it should no longer be used.",
    "write_failed": "The recovery change could not be confirmed. Refresh and inspect its preserved records before retrying.",
    "startup_failed": "The full application could not start. Recovery remains available; repair or disable the failing optional plugin and retry.",
    "recovery_closed": "The recovery listener has stopped.",
    "already_active": "The full application is already active. Use its plugin settings for further changes.",
    "shutdown_failed": "The application could not confirm cleanup. Preserve this runtime and inspect its plugin recovery controls.",
    "unsupported_platform": "Recovery changes require a local POSIX file lock on this platform.",
}


class RecoveryError(ValueError):
    def __init__(self, code, *, plugin_id=None):
        super().__init__(_MESSAGES[code])
        self.code = code
        self.plugin_id = plugin_id if valid_plugin_name(plugin_id) else None

    def public(self):
        return {"code": self.code, "message": str(self), "plugin_id": self.plugin_id}


def _safe_path(home, path):
    home, path = Path(home).resolve(), Path(path)
    if not path.is_relative_to(home):
        raise RecoveryError("write_failed")
    if ".." in path.relative_to(home).parts:
        raise RecoveryError("write_failed")
    current = home
    for part in path.relative_to(home).parts:
        current /= part
        if current.is_symlink():
            raise RecoveryError("write_failed")
    return path


def _read(home, path, limit):
    path = _safe_path(home, path)
    try:
        with path.open("rb") as source:
            raw = source.read(limit + 1)
    except FileNotFoundError:
        return None
    if len(raw) > limit:
        raise RecoveryError("write_failed")
    return raw


def _flush(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _flush_tree(home, directory):
    home, directory = Path(home).resolve(), Path(directory)
    while directory.is_relative_to(home):
        _flush(directory)
        if directory == home:
            return
        directory = directory.parent


@contextmanager
def _decision_lock(home):
    try:
        import fcntl
    except ImportError:
        raise RecoveryError("unsupported_platform") from None
    home = Path(home).resolve()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _safe_path(home, home / ".plugin-recovery.lock")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RecoveryError("revision_conflict") from None
        yield
    finally:
        os.close(descriptor)


def _archive(home, directory, name, raw):
    directory = _safe_path(home, directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    identifier = f"{name}-{digest(raw)}-{uuid4().hex}.json"
    path = directory / identifier
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())
    _flush_tree(home, directory)
    return identifier


def _selection(settings):
    raw = _read(settings.home, settings.home / "plugins.json", 1024 * 1024)
    if raw is not None:
        if len(raw) > MAX_SELECTION_BYTES:
            raise RecoveryError("selection_unreadable")
        # The ordinary reader validates its exact schema/IDs. Duplicate fields
        # are refused before a metadata-only repair ever replaces this record.
        value = _json(raw)
        if (type(value) is not dict or set(value) != {"version", "plugins"}
                or type(value["version"]) is not int or value["version"] != 1
                or type(value["plugins"]) is not list):
            raise RecoveryError("selection_unreadable")
        return validate_selection(tuple(value["plugins"])), raw
    return validate_selection(settings.plugins), None


def _worker_reference(raw, plugin_id):
    value = _json(raw)
    if (type(value) is not dict or set(value) != {"format", "plugin", "environment", "distribution", "version"}
            or type(value["format"]) is not int or value["format"] != 1 or value["plugin"] != plugin_id
            or type(value["environment"]) is not str or len(value["environment"]) != 64
            or any(char not in "0123456789abcdef" for char in value["environment"])
            or any(type(value[key]) is not str or not 1 <= len(value[key]) <= 120 for key in ("distribution", "version"))):
        raise ValueError()
    return {"distribution": value["distribution"], "version": value["version"], "environment": value["environment"]}


class RecoveryController:
    """Static inspection and explicit compare-and-set startup repair decisions."""

    def __init__(self, settings):
        self.settings = settings
        self._lock = RLock()

    def inspect(self):
        with self._lock:
            errors, fingerprints = [], []
            session = None
            root = self.settings.home
            required = {name for name in (self.settings.storage, self.settings.ui) if name}
            if self.settings.budget_path:
                try:
                    session = BudgetSession.open(self.settings.home, self.settings.plaid_env,
                                                 self.settings.budget_path, password=self.settings.budget_password)
                    root = session.storage_home
                    _safe_path(self.settings.home, root)
                    configured = session.selection
                    required = {name for name in (session.manifest["storage"], self.settings.ui) if name}
                    fingerprints.append(session._file_digest)
                    for name in ("source.json", "manifest.json"):
                        raw = _read(self.settings.home, root / name, MAX_MANIFEST_BYTES + 1024)
                        fingerprints.append(None if raw is None else digest(raw))
                except Exception:
                    errors.append(RecoveryError("budget_unreadable").public())
                    configured = ()
                    session = None
            else:
                try:
                    configured, raw = _selection(self.settings)
                    fingerprints.append(None if raw is None else digest(raw))
                except Exception:
                    errors.append(RecoveryError("selection_unreadable").public())
                    configured = ()
                    try:
                        raw = _read(self.settings.home, self.settings.home / "plugins.json", 1024 * 1024)
                        fingerprints.append(None if raw is None else digest(raw))
                    except Exception:
                        fingerprints.append("unreadable")
            installed = {}
            try:
                for entry in installed_entrypoints():
                    if valid_plugin_name(entry.name):
                        installed.setdefault(entry.name, []).append(entry)
            except Exception:
                errors.append(RecoveryError("metadata_unreadable").public())
            workers, references = [], {}
            try:
                directory = _safe_path(self.settings.home, root / "plugin-workers" / "active")
                paths = sorted(directory.glob("*.json"))
                if len(paths) > 100:
                    raise ValueError()
                for path in paths:
                    if not valid_plugin_name(path.stem):
                        raise ValueError()
                    raw = _read(self.settings.home, path, 16384)
                    if raw is None:
                        continue
                    references[path.stem] = raw
                    details = {}
                    try:
                        details = _worker_reference(raw, path.stem)
                        status = "recorded"
                    except Exception:
                        status = "invalid"
                        errors.append(RecoveryError("worker_reference_invalid", plugin_id=path.stem).public())
                    workers.append({"plugin_id": path.stem, "status": status, **details})
            except Exception:
                errors.append(RecoveryError("worker_reference_invalid").public())
            catalog = []
            for name in sorted(set(installed) | set(configured) | required):
                entries = installed.get(name, ())
                distribution, version = package_details(entries[0]) if len(entries) == 1 else ("", "")
                catalog.append({"id": name, "distribution": distribution, "version": version,
                                "installed": len(entries) == 1, "duplicate": len(entries) > 1,
                                "enabled": name in configured or name in required, "required": name in required})
            identity = [sorted(required), list(configured), fingerprints, catalog,
                        sorted((name, digest(raw)) for name, raw in references.items())]
            revision = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            public = {"mode": "recovery", "host_version": __version__, "revision": revision,
                      "plugins": catalog, "configured": list(configured), "workers": workers, "errors": errors,
                      "can_clear_selection": not self.settings.budget_path and any(error["code"] == "selection_unreadable" for error in errors)}
            return public, session, root, references

    def _current(self, revision):
        if type(revision) is not str or len(revision) != 64:
            raise RecoveryError("invalid_request")
        inspected = self.inspect()
        if inspected[0]["revision"] != revision:
            raise RecoveryError("revision_conflict")
        return inspected

    def disable(self, revision, plugins):
        if type(plugins) is not list:
            raise RecoveryError("invalid_request")
        try:
            disabled = validate_selection(tuple(plugins))
        except Exception:
            raise RecoveryError("invalid_request") from None
        with self._lock, _decision_lock(self.settings.home):
            public, session, root, _ = self._current(revision)
            if any(error["code"] in {"budget_unreadable", "selection_unreadable"} for error in public["errors"]):
                raise RecoveryError("budget_unreadable" if self.settings.budget_path else "selection_unreadable")
            required = {plugin["id"] for plugin in public["plugins"] if plugin["required"]}
            if set(disabled) & required:
                raise RecoveryError("required_plugin")
            if not set(disabled) <= set(public["configured"]):
                raise RecoveryError("invalid_request")
            selection = tuple(name for name in public["configured"] if name not in disabled)
            try:
                if session:
                    session.save_selection(selection)
                else:
                    write_selection(self.settings.home, selection)
                _flush_tree(self.settings.home, root)
            except Exception:
                raise RecoveryError("write_failed") from None
            return self.inspect()[0]

    def clear_invalid(self, revision):
        with self._lock, _decision_lock(self.settings.home):
            public, _, _, _ = self._current(revision)
            if not public["can_clear_selection"]:
                raise RecoveryError("invalid_request")
            try:
                raw = _read(self.settings.home, self.settings.home / "plugins.json", 1024 * 1024)
                archive = None if raw is None else _archive(self.settings.home, self.settings.home / "plugin-recovery-archive", "selection", raw)
                self._current(revision)
                write_selection(self.settings.home, ())
                _flush(self.settings.home)
            except RecoveryError:
                raise
            except Exception:
                raise RecoveryError("write_failed") from None
            return {**self.inspect()[0], "archive": archive}

    def reset_worker(self, revision, plugin_id):
        if not valid_plugin_name(plugin_id):
            raise RecoveryError("invalid_request")
        with self._lock, _decision_lock(self.settings.home):
            public, _, root, references = self._current(revision)
            if any(error["code"] == "budget_unreadable" for error in public["errors"]):
                raise RecoveryError("budget_unreadable")
            if plugin_id not in references:
                raise RecoveryError("invalid_request")
            try:
                _archive(self.settings.home, root / "plugin-workers" / "recovery-archive", plugin_id, references[plugin_id])
                path = _safe_path(self.settings.home, root / "plugin-workers" / "active" / (plugin_id + ".json"))
                # Recheck immediately before removing the exact archived decision.
                if _read(self.settings.home, path, 16384) != references[plugin_id]:
                    raise RecoveryError("revision_conflict")
                path.unlink()
                _flush(path.parent)
            except RecoveryError:
                raise
            except Exception:
                raise RecoveryError("write_failed") from None
            return self.inspect()[0]


_PAGE = r"""<!doctype html>
<html lang="en">
<meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>WHAB plugin recovery</title>
<style>
body{font:17px system-ui;max-width:760px;margin:7vh auto;padding:24px;color:#172723;background:#f6f7f3}
h1{font-size:32px}li{margin:14px 0}button{font:inherit;margin:12px 8px 12px 0;padding:10px 15px;cursor:pointer}
#status{white-space:pre-wrap}small{display:block;color:#51645c}
</style>
<h1>Plugin recovery</h1>
<p>Inspect installed packages and repair optional startup selections.
Budget views become available when the full application starts successfully.</p>
<p id="status" role="status">Reading local metadata…</p>
<ul id="plugins"></ul><button id="disable" disabled>Disable checked plugins</button>
<button id="clear" hidden disabled>Archive and clear invalid selection</button>
<h2>Saved report workers</h2><ul id="workers"></ul>
<button id="retry" disabled>Retry full startup</button>
<script>
let state;
const status = document.querySelector('#status');
async function call(path, data) {
  const options = data ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)} : {};
  const response = await fetch(path, options);
  const value = await response.json();
  if (!response.ok) throw Error(value.error.message);
  return value;
}
async function refresh() {
  state = await call('/api/recovery');
  const errors = [...state.errors, ...(state.startup_error ? [state.startup_error] : [])];
  status.textContent = errors.map(error => error.message).join('\n') || 'No budget is active in recovery mode.';
  const list = document.querySelector('#plugins');
  list.replaceChildren();
  for (const plugin of state.plugins) {
    const row = document.createElement('li'), label = document.createElement('label');
    const box = document.createElement('input'), detail = document.createElement('small');
    box.type = 'checkbox'; box.value = plugin.id; box.disabled = plugin.required || !plugin.enabled;
    label.append(box, document.createTextNode(' ' + plugin.id + (plugin.required ? ' (required)' : '')));
    detail.textContent = plugin.distribution + ' ' + plugin.version +
      (plugin.duplicate ? ' — duplicate registration' : plugin.installed ? '' : ' — unavailable');
    row.append(label, detail); list.append(row);
  }
  document.querySelector('#clear').hidden = !state.can_clear_selection;
  document.querySelector('#clear').disabled = state.starting;
  document.querySelector('#disable').disabled = state.starting;
  document.querySelector('#retry').disabled = state.starting || state.errors.some(
    error => error.code === 'budget_unreadable' || error.code === 'selection_unreadable'
  );
  const workers = document.querySelector('#workers');
  workers.replaceChildren();
  for (const worker of state.workers) {
    const row = document.createElement('li'), button = document.createElement('button');
    row.append(document.createTextNode(worker.plugin_id + ' — ' + worker.status + ' '));
    button.textContent = 'Archive worker reference';
    button.disabled = state.starting;
    button.onclick = () => act('/api/recovery/workers/' + encodeURIComponent(worker.plugin_id) + '/reset');
    row.append(button); workers.append(row);
  }
}
async function act(path, extra = {}) {
  try { await call(path, {revision:state.revision, ...extra}); await refresh(); }
  catch (error) { status.textContent = error.message; }
}
document.querySelector('#disable').onclick = () => act('/api/recovery/disable', {
  plugins:[...document.querySelectorAll('#plugins input:checked')].map(input => input.value)
});
document.querySelector('#clear').onclick = () => act('/api/recovery/selection/clear-invalid');
document.querySelector('#retry').onclick = async () => {
  status.textContent = 'Preparing the full application…';
  try { await call('/api/recovery/retry', {revision:state.revision}); location.href = '/'; }
  catch (error) { await refresh(); status.textContent = error.message; }
};
refresh().catch(error => status.textContent = error.message);
</script></html>"""

class RecoveryApplication:
    """Own one listener; publish the full candidate only after its lifespan starts."""

    def __init__(self, settings, *, factory=None):
        self.settings, self.factory = settings, factory
        self.controller = RecoveryController(settings)
        self._candidate = self._context = self._retry_task = None
        self._closed = False
        self._serial = asyncio.Lock()
        self.error = None

        @asynccontextmanager
        async def lifespan(_):
            try:
                yield
            finally:
                self._closed = True
                if self._retry_task is not None:
                    await asyncio.gather(self._retry_task, return_exceptions=True)
                if self._context is not None:
                    try:
                        await self._context.__aexit__(None, None, None)
                    except Exception:
                        raise RecoveryError("shutdown_failed") from None

        shell = self.shell = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
        install_middleware(shell, settings.port)

        @shell.exception_handler(RecoveryError)
        async def safe_error(_, error):
            return JSONResponse({"error": error.public()}, status_code=409 if error.code == "revision_conflict" else 400)

        @shell.get("/", response_class=HTMLResponse)
        async def page():
            return HTMLResponse(_PAGE, headers={"Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"})

        @shell.get("/api/recovery")
        async def status():
            result = await run_in_threadpool(self.controller.inspect)
            return {**result[0], "startup_error": self.error,
                    "starting": self._retry_task is not None and not self._retry_task.done()}

        async def body(request, keys):
            try:
                value = _json(await request.body())
            except Exception:
                raise RecoveryError("invalid_request") from None
            if type(value) is not dict or set(value) != keys:
                raise RecoveryError("invalid_request")
            return value

        @shell.post("/api/recovery/disable")
        async def disable(request: Request):
            value = await body(request, {"revision", "plugins"})
            async with self._serial:
                self._inactive()
                return await run_in_threadpool(self.controller.disable, value["revision"], value["plugins"])

        @shell.post("/api/recovery/selection/clear-invalid")
        async def clear_invalid(request: Request):
            value = await body(request, {"revision"})
            async with self._serial:
                self._inactive()
                return await run_in_threadpool(self.controller.clear_invalid, value["revision"])

        @shell.post("/api/recovery/workers/{plugin_id}/reset")
        async def reset_worker(plugin_id: str, request: Request):
            value = await body(request, {"revision"})
            async with self._serial:
                self._inactive()
                return await run_in_threadpool(self.controller.reset_worker, value["revision"], plugin_id)

        @shell.post("/api/recovery/retry")
        async def retry(request: Request):
            value = await body(request, {"revision"})
            if self._retry_task is not None and not self._retry_task.done():
                raise RecoveryError("revision_conflict")
            self._retry_task = asyncio.create_task(self._retry(value["revision"]))
            self._retry_task.add_done_callback(self._observed)
            return await asyncio.shield(self._retry_task)

    @staticmethod
    def _observed(task):
        if not task.cancelled():
            task.exception()

    def _inactive(self):
        if self._closed:
            raise RecoveryError("recovery_closed")
        if self._candidate is not None:
            raise RecoveryError("already_active")

    async def _retry(self, revision):
        async with self._serial:
            self._inactive()
            with _decision_lock(self.settings.home):
                public, session, _, _ = await run_in_threadpool(self.controller._current, revision)
                if any(error["code"] in {"selection_unreadable", "budget_unreadable"} for error in public["errors"]):
                    raise RecoveryError("budget_unreadable" if self.settings.budget_path else "selection_unreadable")
                from dataclasses import replace

                factory = self.factory
                if factory is None:
                    from whab.app import create_app

                    factory = create_app
                settings = replace(self.settings, plugins=tuple(public["configured"]), copy_paths=())
                try:
                    candidate = await run_in_threadpool(factory, settings=settings, budget_session=session)
                    context = candidate.router.lifespan_context(candidate)
                    await context.__aenter__()
                except Exception as error:
                    safe = RecoveryError("startup_failed", plugin_id=getattr(error, "plugin_id", None))
                    self.error = safe.public()
                    raise safe from None
                self._candidate, self._context = candidate, context
                self.error = None
                return {"started": True}

    async def __call__(self, scope, receive, send):
        target = self.shell if scope["type"] == "lifespan" or self._candidate is None else self._candidate
        await target(scope, receive, send)


def create_app(settings=None):
    """Uvicorn factory; no selected plugin code executes until explicit retry."""
    return RecoveryApplication(settings or Settings.load(read_plugin_selection=False))
