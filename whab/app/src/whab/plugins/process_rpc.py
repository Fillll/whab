"""Private, duplex SDK capability calls with reentrant callback dispatch.

One reader owns the pipe. Callbacks in an existing call chain execute on its
waiting thread, preserving the host's reentrant financial lock. Independent
background callers use bounded, thread-affine dispatchers. No Python code,
traceback, import path, backend attribute or pickle is accepted on the wire.
"""

import asyncio
import inspect
import os
import queue
import selectors
import threading
import weakref
from contextvars import Context, ContextVar
from time import monotonic
from uuid import uuid4

from whab.domain.errors import StoreError

from .process_wire import HEADER, Codec, lengths
from .storage_proxy import _MUTATIONS
from .storage_worker_codec import CONTROL_WRITES, METHODS
from .wire import WireError

_REQUEST = ContextVar("whab_plugin_process_request", default=None)
_KINDS = {
    "call": ({"call"}, set(), set()),
    "service": ({"query", "command", "external_mutation", "register_feature", "feature_adapter"},
                {"operation_lock", "budget_id", "revision"}, set()),
    "feature": ({"query", "command", "snapshot", "budget"}, {"context", "ai"}, {"ai"}),
    "jobs": ({"require_idle", "status", "start", "stop", "close"}, {"engine_factory"}, set()),
    "labels": ({"configure", "classify", "write", "report", "override", "label", "put"}, {"saved", "context"}, set()),
    "engine": ({"index", "select", "delete", "rename", "train", "save", "predict", "preview", "close"}, {"defaults"}, set()),
    "provider": ({"connection_token", "exchange_token", "read_sync", "read_investments", "remove_token", "webhook_event", "close"}, {"config"}, set()),
    "bank": ({"link_token", "exchange", "sync", "disconnect", "webhook", "close"}, {"config"}, set()),
    "lock": ({"enter", "exit"}, set(), set()),
    "iterator": ({"next", "close"}, set(), set()),
    "storage": (set(METHODS) | set(_MUTATIONS) | {"close"}, set(), set()),
    "bankstore": ({"items", "get_item", "save_item", "set_item_status", "disconnect_item", "item_identity", "apply_sync"}, set(), set()),
    "opaque": (set(), set(), set()),
}


class OpaqueModel:
    """Keep a completed legacy engine model exclusively in its child."""

    def __init__(self, value):
        self.value = value


class ProcessUnavailable(StoreError):
    def __init__(self):
        super().__init__("The plugin process is unavailable. Check its status before retrying this operation.")


class Capability:
    def __init__(self, peer, identifier, kind, methods, attributes, setters, asynchronous):
        object.__setattr__(self, "_peer", peer)
        owner = getattr(peer.codec, "owner", None)
        object.__setattr__(self, "_owner", owner() if owner is not None else None)
        object.__setattr__(self, "_identifier", identifier)
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_methods", frozenset(methods))
        object.__setattr__(self, "_attributes", frozenset(attributes))
        object.__setattr__(self, "_setters", frozenset(setters))
        object.__setattr__(self, "_asynchronous", frozenset(asynchronous))
        object.__setattr__(self, "_leases", [1])
        weakref.finalize(self, peer.release_remote, identifier, self._leases)

    def __call__(self, *args, **kwargs):
        if "call" not in self._methods:
            raise TypeError("This SDK capability is not callable.")
        return self._peer.call(self._identifier, "call", args, kwargs)

    def __getattr__(self, name):
        if name in self._attributes:
            return self._peer.call(self._identifier, "get", (name,))
        if name not in self._methods:
            raise AttributeError(name)
        if name in self._asynchronous:
            async def invoke(*args, **kwargs):
                return await asyncio.to_thread(self._peer.call, self._identifier, name, args, kwargs)
        else:
            def invoke(*args, **kwargs):
                return self._peer.call(self._identifier, name, args, kwargs)
        return invoke

    def __setattr__(self, name, value):
        if name not in self._setters:
            raise AttributeError(name)
        self._peer.call(self._identifier, "set", (name, value))

    def __enter__(self):
        return self._peer.call(self._identifier, "enter")

    def __exit__(self, *_):
        self._peer.call(self._identifier, "exit")

    def __iter__(self):
        return self

    def __next__(self):
        return self._peer.call(self._identifier, "next")


class AsyncCapability(Capability):
    async def __call__(self, *args, **kwargs):
        return await asyncio.to_thread(super().__call__, *args, **kwargs)


class StorageCapability(Capability):
    """Legacy public storage methods, fenced after an uncertain write response."""

    def __init__(self, *args):
        super().__init__(*args)
        object.__setattr__(self, "_fenced", False)
        object.__setattr__(self, "_lock", threading.RLock())

    @property
    def uncertain(self):
        return self._fenced

    @property
    def lock(self):
        return self._lock

    def __getattr__(self, name):
        callback = super().__getattr__(name)
        if name not in set(_MUTATIONS) | CONTROL_WRITES | {"execute_operation"}:
            return callback

        def mutate(*args, **kwargs):
            from .runtime import PluginError

            if self._fenced:
                raise PluginError("The storage write outcome is unknown. Reopen and reconcile this budget before retrying.",
                                  code="storage_write_unknown")
            try:
                return callback(*args, **kwargs)
            except (StoreError, WireError):
                object.__setattr__(self, "_fenced", True)
                if self._owner is not None:
                    self._owner.storage_fault(self._owner.plugin_id)
                raise PluginError("The storage write outcome is unknown. Reopen and reconcile this budget before retrying.",
                                  code="storage_write_unknown") from None

        return mutate


class Peer:
    def __init__(self, source, target, *, timeout=60, root=None):
        self.source, self.target, self.timeout = source, target, timeout
        self.root = root
        self.codec = Codec(self.export, self.resolve)
        self._write_lock, self._state_lock = threading.Lock(), threading.RLock()
        self._pending, self._exports, self._identities, self._dispatchers = {}, {}, {}, {}
        self._remote = weakref.WeakValueDictionary()
        self._threads = []
        self._closed = threading.Event()
        self._local = threading.local()
        self._loop = None
        self._reader = threading.Thread(target=self._read, daemon=True, name="whab-plugin-pipe", context=Context())
        self._reader.start()

    def export(self, value):
        if isinstance(value, Capability) and value._peer is self:
            return {"back": value._identifier}
        if type(value) is OpaqueModel:
            kind = "opaque"
        elif isinstance(value, Capability):
            kind = value._kind
        elif callable(value):
            kind = "call"
        elif callable(getattr(value, "register_feature", None)):
            kind = "service"
        elif callable(getattr(value, "snapshot", None)) and hasattr(value, "ai"):
            kind = "feature"
        elif callable(getattr(value, "require_idle", None)):
            kind = "jobs"
        elif callable(getattr(value, "label", None)) and hasattr(value, "saved"):
            kind = "labels"
        elif callable(getattr(value, "predict", None)) and hasattr(value, "defaults"):
            kind = "engine"
        elif callable(getattr(value, "connection_token", None)):
            kind = "provider"
        elif callable(getattr(value, "link_token", None)):
            kind = "bank"
        elif callable(getattr(value, "accounts", None)) and callable(getattr(value, "categories", None)):
            kind = "storage"
        elif callable(getattr(value, "items", None)) and callable(getattr(value, "apply_sync", None)):
            kind = "bankstore"
        elif hasattr(value, "__next__"):
            kind = "iterator"
        elif hasattr(value, "__enter__") and hasattr(value, "__exit__"):
            kind = "lock"
        else:
            raise WireError()
        allowed, attributes, setters = _KINDS[kind]
        methods = {name for name in allowed if (kind in {"lock", "iterator"} or name == "call"
                   or callable(getattr(value, name, None)))}
        asynchronous = {name for name in methods if inspect.iscoroutinefunction(value if name == "call" else getattr(value, name, None))}
        if isinstance(value, Capability):
            methods, attributes, setters, asynchronous = value._methods, value._attributes, value._setters, value._asynchronous
        with self._state_lock:
            identifier = self._identities.get(id(value))
            if identifier is None:
                if len(self._exports) >= 16384:
                    raise WireError()
                identifier = uuid4().hex
                self._identities[id(value)] = identifier
                self._exports[identifier] = [value, kind, frozenset(methods), frozenset(attributes), frozenset(setters), 0]
            self._exports[identifier][5] += 1
        return {"id": identifier, "kind": kind, "methods": sorted(methods), "attributes": sorted(attributes),
                "setters": sorted(setters), "async": sorted(asynchronous)}

    def resolve(self, value):
        if type(value) is not dict:
            raise WireError()
        if set(value) == {"back"}:
            with self._state_lock:
                entry = self._exports.get(value["back"])
                if entry is None:
                    raise WireError()
                return entry[0].value if entry[1] == "opaque" else entry[0]
        if set(value) != {"id", "kind", "methods", "attributes", "setters", "async"}:
            raise WireError()
        identifier, kind = value["id"], value["kind"]
        if type(identifier) is not str or len(identifier) != 32 or type(kind) is not str or kind not in _KINDS:
            raise WireError()
        for name in ("methods", "attributes", "setters", "async"):
            if type(value[name]) is not list or any(type(item) is not str for item in value[name]) or len(set(value[name])) != len(value[name]):
                raise WireError()
        methods, attributes, setters = _KINDS[kind]
        if (set(value["methods"]) - methods or set(value["attributes"]) - attributes
                or set(value["setters"]) - setters or set(value["async"]) - set(value["methods"])):
            raise WireError()
        cls = StorageCapability if kind == "storage" else (AsyncCapability if "call" in value["async"] else Capability)
        with self._state_lock:
            previous = self._remote.get(identifier)
            if previous is not None:
                if (previous._kind != kind or previous._methods != set(value["methods"])
                        or previous._attributes != set(value["attributes"]) or previous._setters != set(value["setters"])
                        or previous._asynchronous != set(value["async"])):
                    raise WireError()
                previous._leases[0] += 1
                return previous
            result = cls(self, identifier, kind, value["methods"], value["attributes"], value["setters"], value["async"])
            self._remote[identifier] = result
            return result

    def _send(self, value):
        deadline = monotonic() + self.timeout
        raw = self.codec.encode(value)
        if not self._write_lock.acquire(timeout=max(0, deadline - monotonic())):
            self.fail()
            raise ProcessUnavailable()
        try:
            if self._closed.is_set():
                raise ProcessUnavailable()
            if callable(getattr(self.target, "send_frame", None)):
                self.target.send_frame(raw, timeout=max(0, deadline - monotonic()))
                return
            descriptor = self.target.fileno()
            os.set_blocking(descriptor, False)
            with selectors.DefaultSelector() as selector:
                selector.register(descriptor, selectors.EVENT_WRITE)
                offset = 0
                while offset < len(raw):
                    remaining = deadline - monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        self.fail()
                        raise ProcessUnavailable()
                    try:
                        offset += os.write(descriptor, raw[offset:offset + 65536])
                    except BlockingIOError:
                        continue
        finally:
            self._write_lock.release()

    def release_remote(self, identifier, leases):
        try:
            self._send({"kind": "release", "id": identifier, "count": leases[0]})
        except (OSError, ValueError):
            pass

    def _read_exact(self, size):
        value = bytearray()
        while len(value) < size:
            part = self.source.read(min(65536, size - len(value)))
            if not part:
                raise EOFError()
            value.extend(part)
        return bytes(value)

    def _read(self):
        try:
            while not self._closed.is_set():
                header = self._read_exact(HEADER.size)
                metadata, body = lengths(header)
                value = self.codec.decode(header + self._read_exact(metadata + body))
                try:
                    self._receive(value)
                finally:
                    # A descriptor/callback can retain its process owner. Do
                    # not keep the last frame alive while waiting on an idle pipe.
                    del value
        except (Exception, BaseExceptionGroup):
            self.fail()

    def _receive(self, value):
        if type(value) is not dict or type(value.get("kind")) is not str:
            raise WireError()
        kind = value["kind"]
        if kind == "release" and set(value) == {"kind", "id", "count"}:
            with self._state_lock:
                entry = self._exports.get(value["id"])
                if entry is None or type(value["count"]) is not int or not 1 <= value["count"] <= entry[5]:
                    raise WireError()
                entry[5] -= value["count"]
                if entry[5] == 0:
                    self._identities.pop(id(entry[0]), None)
                    del self._exports[value["id"]]
            return
        if kind in {"result", "error"} and set(value) == {"kind", "id", "value"}:
            with self._state_lock:
                pending = self._pending.get(value["id"])
            if pending is None:
                raise WireError()
            pending.put(value)
            return
        if (kind != "call" or set(value) != {"kind", "id", "parent", "thread", "target", "operation", "args", "kwargs"}
                or any(type(value[name]) is not str or not 1 <= len(value[name]) <= 100
                       for name in ("id", "thread", "target", "operation"))
                or value["parent"] is not None and type(value["parent"]) is not str
                or type(value["args"]) is not tuple or type(value["kwargs"]) is not dict):
            raise WireError()
        with self._state_lock:
            pending = self._pending.get(value["parent"])
            if pending is None:
                key = value["thread"]
                pending = self._dispatchers.get(key)
                if pending is None:
                    if len(self._dispatchers) >= 128:
                        raise WireError()
                    pending = queue.Queue(maxsize=128)
                    self._dispatchers[key] = pending
                    thread = threading.Thread(target=self._dispatch_loop, args=(pending,), daemon=True,
                                              name="whab-plugin-sdk", context=Context())
                    self._threads.append(thread)
                    thread.start()
        pending.put_nowait(value)

    def _dispatch_loop(self, pending):
        try:
            while not self._closed.is_set() and (value := pending.get()) is not None:
                try:
                    self._dispatch(value)
                finally:
                    del value
        finally:
            self._unwind(0)

    def _unwind(self, count):
        entered = getattr(self._local, "entered", [])
        while len(entered) > count:
            context = entered.pop()
            try:
                context.__exit__(None, None, None)
            except Exception:
                pass

    def _await(self, value):
        if not inspect.isawaitable(value):
            return value
        with self._state_lock:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                threading.Thread(target=self._loop.run_forever, daemon=True, name="whab-plugin-async", context=Context()).start()
        return asyncio.run_coroutine_threadsafe(value, self._loop).result(self.timeout)

    def _dispatch(self, value):
        token = _REQUEST.set(value["id"])
        try:
            if self._closed.is_set():
                raise ProcessUnavailable()
            if value["target"] == "root":
                if self.root is None:
                    raise WireError()
                result = self.root(value["operation"], *value["args"], **value["kwargs"])
            else:
                with self._state_lock:
                    entry = self._exports.get(value["target"])
                    if entry is None:
                        raise WireError()
                    target, kind, methods, attributes, setters, _ = entry
                operation, args, kwargs = value["operation"], value["args"], value["kwargs"]
                if operation == "get" and len(args) == 1 and not kwargs and args[0] in attributes:
                    result = getattr(target, args[0])
                elif operation == "set" and len(args) == 2 and not kwargs and args[0] in setters:
                    setattr(target, *args)
                    result = None
                elif operation not in methods:
                    raise WireError()
                elif kind == "call":
                    result = target(*args, **kwargs)
                elif kind == "lock":
                    if args or kwargs:
                        raise WireError()
                    if not hasattr(self._local, "entered"):
                        self._local.entered = []
                    if operation == "enter":
                        target.__enter__()
                        self._local.entered.append(target)
                    elif not self._local.entered or self._local.entered[-1] is not target:
                        raise WireError()
                    else:
                        self._local.entered.pop().__exit__(None, None, None)
                    result = None
                elif kind == "iterator" and operation == "next":
                    result = next(target)
                else:
                    result = getattr(target, operation)(*args, **kwargs)
            self._send({"kind": "result", "id": value["id"], "value": self._await(result)})
        except StopIteration as error:
            model = OpaqueModel(error.value) if value["operation"] == "next" and error.value is not None else error.value
            self._send({"kind": "error", "id": value["id"], "value": {"code": "iterator_end", "data": model}})
        except StoreError as error:
            self._send({"kind": "error", "id": value["id"], "value": {"code": "rejected", "data": str(error)[:2000]}})
        except Exception:
            try:
                self._send({"kind": "error", "id": value["id"], "value": {"code": "failed", "data": None}})
            except Exception:
                self.fail()
        finally:
            _REQUEST.reset(token)

    def call(self, target, operation, args=(), kwargs=None):
        identifier = uuid4().hex
        pending = queue.Queue(maxsize=128)
        count = len(getattr(self._local, "entered", []))
        with self._state_lock:
            if self._closed.is_set() or len(self._pending) >= 128:
                raise ProcessUnavailable()
            self._pending[identifier] = pending
        deadline = monotonic() + self.timeout
        try:
            self._send({"kind": "call", "id": identifier, "parent": _REQUEST.get(),
                        "thread": str(threading.get_ident()), "target": target, "operation": operation,
                        "args": tuple(args), "kwargs": dict(kwargs or {})})
            while True:
                if self._closed.is_set():
                    raise ProcessUnavailable()
                try:
                    value = pending.get(timeout=max(0, deadline - monotonic()))
                except queue.Empty:
                    self.fail()
                    raise ProcessUnavailable() from None
                if value is None:
                    raise ProcessUnavailable()
                if value["kind"] == "call":
                    self._dispatch(value)
                    continue
                if value["kind"] == "result":
                    return value["value"]
                error = value["value"]
                if type(error) is not dict or set(error) != {"code", "data"}:
                    raise WireError()
                if error["code"] == "iterator_end":
                    raise StopIteration(error["data"])
                if error["code"] == "rejected" and type(error["data"]) is str:
                    raise StoreError(error["data"])
                raise ProcessUnavailable()
        except (OSError, EOFError):
            self.fail()
            raise ProcessUnavailable() from None
        finally:
            with self._state_lock:
                self._pending.pop(identifier, None)
            self._unwind(count)

    def fail(self):
        with self._state_lock:
            self._closed.set()
            for pending in (*self._pending.values(), *self._dispatchers.values()):
                try:
                    pending.put_nowait(None)
                except queue.Full:
                    pass
        if callable(getattr(self.source, "send_frame", None)):
            self.source.close()

    def close(self):
        self.fail()
        with self._state_lock:
            self._exports.clear()
            self._identities.clear()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        deadline = monotonic() + 1
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(max(0, deadline - monotonic()))

    @property
    def cleanup_pending(self):
        return any(thread.is_alive() for thread in self._threads)
