"""Typed categorization engine sessions and opaque cooperative continuations.

This module's child dispatcher receives complete data snapshots. Engine/model
objects stay in the child. Host namespace journaling is a separate adapter below
the worker transport, never a callback or native handle sent through IPC.
"""

import inspect
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from types import MappingProxyType
from uuid import uuid4

from .effect_store import encode
from .namespace_effects import NamespaceDraft
from .sdk import BudgetContext
from .wire import WireError, _value
from .worker_namespace import namespace_bundle, read_namespace

_TOKEN = re.compile(r"[a-f0-9]{32}\Z")
_MUTATIONS = frozenset({"save", "select", "delete", "rename"})
_COMMANDS = frozenset({"open", "index", "predict", "preview", "train", "step", "cancel", "save", "select", "delete", "rename", "release", "close"})


def _token(value):
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise WireError()
    return value


def _plain(value, *, limit=1024 * 1024):
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise WireError()
    _value(value)
    encode(value, limit)
    return value


def _progress(value):
    if (type(value) is not dict or value.keys() != {"stage", "progress"}
            or type(value["stage"]) is not str or not 1 <= len(value["stage"]) <= 200
            or type(value["progress"]) is not int or not 0 <= value["progress"] <= 100):
        raise WireError()
    return _plain(value, limit=4096)


def _close_iterator(iterator):
    callback = getattr(iterator, "close", None)
    if callback is not None:
        if not callable(callback):
            raise WireError()
        result = callback()
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise WireError()


class _Engine:
    def __init__(self, plugin, budget_id, workspace, settings):
        self.current = None
        self.iterator = None
        self.models = {}
        self.plugin = plugin
        self.workspace = workspace
        self.settings = _plain(settings)
        self.budget_id = budget_id
        self.engine = None

    def callback(self, operation, *values):
        if self.current is None:
            raise WireError()
        if operation != "save_state":
            values = (("demo/" if self.workspace == "demo" else "") + values[0], *values[1:])
        return getattr(self.current.context(), operation)(*values)

    def context(self, snapshot):
        return BudgetContext(self.budget_id, MappingProxyType(self.settings), MappingProxyType(snapshot.state),
                             partial(self.callback, "save_state"), partial(self.callback, "read_file"),
                             partial(self.callback, "write_file"), partial(self.callback, "delete_file"))

    def start(self, snapshot):
        engine = self.plugin.categorization.factory(self.context(snapshot))
        if inspect.isawaitable(engine):
            if inspect.iscoroutine(engine):
                engine.close()
            raise WireError()
        self.engine = engine
        if not isinstance(getattr(engine, "defaults", None), Mapping) or any(
            not callable(getattr(engine, name, None)) for name in ("index", "select", "delete", "rename", "train", "save", "predict")
        ) or (getattr(engine, "close", None) is not None and not callable(engine.close)):
            raise WireError()
        return {"defaults": _plain(dict(engine.defaults)), "preview": callable(getattr(engine, "preview", None))}

    def close(self):
        failed = False
        callbacks = ([partial(_close_iterator, self.iterator)] if self.iterator is not None else [])
        self.iterator = None
        self.models.clear()
        if callable(getattr(self.engine, "close", None)):
            callbacks.append(self.engine.close)
        for callback in callbacks:
            try:
                result = callback()
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    raise WireError()
            except Exception:
                failed = True
        if failed:
            raise WireError()

    def dispatch(self, command, values):
        engine = self.engine
        if command == "index" and not values:
            return _plain(engine.index())
        if command in {"predict", "preview", "train"}:
            required = {"rows", "parameters"} if command == "train" else {"rows"}
            if values.keys() != required or type(values["rows"]) is not list or len(values["rows"]) > 20000:
                raise WireError()
            _plain(values, limit=6 * 1024 * 1024)
            if command != "train":
                callback = getattr(engine, command, None)
                if not callable(callback):
                    raise WireError()
                output = callback(values["rows"])
                if type(output) not in {list, tuple} or len(output) > 20000:
                    raise WireError()
                return _plain(list(output))
            if self.iterator is not None or len(self.models) >= 2:
                raise WireError()
            candidate = engine.train(values["rows"], values["parameters"])
            if inspect.isawaitable(candidate):
                if inspect.iscoroutine(candidate):
                    candidate.close()
                raise WireError()
            self.iterator = iter(candidate)
            return None
        if command == "step" and not values:
            if self.iterator is None:
                raise WireError()
            try:
                progress = next(self.iterator)
            except StopIteration as completed:
                model = completed.value
            else:
                return {"complete": False, "progress": _progress(progress)}
            self.iterator = None
            token = uuid4().hex
            self.models[token] = model
            return {"complete": True, "model": token}
        if command == "cancel" and not values:
            if self.iterator is not None:
                iterator, self.iterator = self.iterator, None
                _close_iterator(iterator)
            return None
        if command in {"save", "release"}:
            if values.keys() != ({"model", "name"} if command == "save" else {"model"}):
                raise WireError()
            model = _token(values["model"])
            if model not in self.models:
                raise WireError()
            if command == "release":
                del self.models[model]
                return None
            if type(values["name"]) is not str or not 1 <= len(values["name"]) <= 100:
                raise WireError()
            return _plain(engine.save(self.models[model], values["name"]), limit=64 * 1024)
        if command in {"select", "delete", "rename"}:
            if values.keys() != ({"id", "name"} if command == "rename" else {"id"}):
                raise WireError()
            if type(values["id"]) is not str or len(values["id"]) > 200:
                raise WireError()
            arguments = (values["id"],)
            if command == "rename":
                if type(values["name"]) is not str or len(values["name"]) > 100:
                    raise WireError()
                arguments += (values["name"],)
            getattr(engine, command)(*arguments)
            return None
        if command == "close" and not values:
            self.close()
            return None
        raise WireError()


class CategorizationDispatcher:
    """Child-owned engines; two independently scoped workspace continuations."""

    def __init__(self, plugin):
        self.plugin = plugin
        self.engines = {}

    def call(self, arguments, payload):
        if type(arguments) is not dict or arguments.keys() != {"engine", "command", "values", "namespace", "context"}:
            raise WireError()
        command, values = arguments["command"], arguments["values"]
        if type(command) is not str or command not in _COMMANDS or type(values) is not dict:
            raise WireError()
        snapshot = read_namespace(arguments["namespace"], payload)
        token = arguments["engine"]
        created = command == "open"
        if created:
            context = arguments["context"]
            if (token is not None or values or type(context) is not dict or context.keys() != {"budget", "workspace", "settings"}
                    or type(context["budget"]) is not str or not 1 <= len(context["budget"]) <= 200
                    or type(context["workspace"]) is not str or context["workspace"] not in {"personal", "demo"} or type(context["settings"]) is not dict
                    or len(self.engines) >= 2):
                raise WireError()
            token = uuid4().hex
            engine = _Engine(self.plugin, context["budget"], context["workspace"], context["settings"])
        else:
            _token(token)
            if arguments["context"] is not None or token not in self.engines:
                raise WireError()
            engine = self.engines[token]
        draft = NamespaceDraft(BudgetContext(engine.budget_id, engine.settings, snapshot.state, lambda _: None), snapshot,
                               writable=command in _MUTATIONS)
        engine.current = draft
        try:
            output = engine.start(snapshot) if created else engine.dispatch(command, values)
            result = draft.snapshot
            if created:
                self.engines[token] = engine
            elif command == "close":
                del self.engines[token]
            namespace, raw = namespace_bundle(result)
            return {"engine": token, "result": output, "namespace": namespace}, raw
        except BaseException:
            if created:
                try:
                    engine.close()
                except Exception:
                    pass
            raise
        finally:
            engine.current = None
            draft.close()

    def close(self):
        failed = False
        for engine in self.engines.values():
            try:
                engine.close()
            except Exception:
                failed = True
        self.engines.clear()
        if failed:
            raise WireError()


@dataclass(frozen=True, slots=True)
class TrainedModelToken:
    identifier: str

    def __post_init__(self):
        _token(self.identifier)


class WorkerCategorizationFactory:
    """Host-only factory marker; its callbacks/worker never cross IPC."""

    def __init__(self, worker, effects=None, *, owns_worker=False, access=None):
        self.worker, self.effects, self.owns_worker = worker, effects, owns_worker
        self.access = access

    def bind(self, effects, access):
        return type(self)(self.worker, effects, owns_worker=self.owns_worker, access=access)

    def __call__(self, context):
        raise WireError()  # The host must explicitly bind the workspace.

    def create(self, context, workspace):
        if self.effects is None or workspace not in {"personal", "demo"}:
            raise WireError()
        return WorkerCategorizer(self, context, workspace)

    def close(self):
        if self.owns_worker:
            self.worker.close()
            if self.worker.cleanup_pending:
                raise WireError()


class WorkerCategorizer:
    """Public engine facade; all code effects commit through NamespaceEffects."""

    def __init__(self, factory, context, workspace):
        self.factory, self.context, self.workspace = factory, context, workspace
        self.token, self.closed, self._preview = None, False, False
        self.last_snapshot = factory.effects.snapshot
        info = self._call("open", {})
        if type(info) is not dict or info.keys() != {"defaults", "preview"} or type(info["defaults"]) is not dict or type(info["preview"]) is not bool:
            factory.worker.close()
            raise WireError()
        self.defaults, self._preview = info["defaults"], info["preview"]
        # Preserve the public optional-preview feature test used by the host.
        if not self._preview:
            self.preview = None

    def _call(self, command, values):
        if self.closed:
            raise WireError()
        if self.factory.access is not None:
            self.factory.access._check(command in _MUTATIONS)
        effects = self.factory.effects
        arguments = {"engine": self.token, "command": command, "values": values,
                     "context": {"budget": self.context.budget_id, "workspace": self.workspace,
                                 "settings": dict(self.context.settings)} if command == "open" else None}

        def invoke(snapshot):
            token, output, changed = self.factory.worker.category(arguments, snapshot)
            if command == "open":
                self.token = token
            self.last_snapshot = changed
            return changed, output

        return effects.execute("category:" + self.workspace + ":" + command,
                               {"command": command, "values": values, "workspace": self.workspace},
                               invoke, mutating=command in _MUTATIONS)

    def index(self):
        return self._call("index", {})

    def predict(self, rows):
        return self._call("predict", {"rows": rows})

    def preview(self, rows):
        return self._call("preview", {"rows": rows})

    def train(self, rows, parameters):
        self._call("train", {"rows": rows, "parameters": parameters})
        finished = False
        try:
            while True:
                result = self._call("step", {})
                if type(result) is not dict or type(result.get("complete")) is not bool:
                    raise WireError()
                if result["complete"]:
                    if result.keys() != {"complete", "model"}:
                        raise WireError()
                    finished = True
                    return TrainedModelToken(result["model"])
                if result.keys() != {"complete", "progress"} or type(result["progress"]) is not dict:
                    raise WireError()
                yield _progress(result["progress"])
        finally:
            if not finished and self.factory.worker.healthy:
                self._call("cancel", {})

    def save(self, model, name):
        if type(model) is not TrainedModelToken:
            raise WireError()
        try:
            result = self._call("save", {"model": model.identifier, "name": name})
        except BaseException:
            if self.factory.effects.pending is None and self.factory.worker.healthy:
                try:
                    self._call("release", {"model": model.identifier})
                except Exception:
                    self.factory.worker.close()
            raise
        try:
            self._call("release", {"model": model.identifier})
        except Exception:
            # The authoritative model save succeeded; cleanup cannot undo it.
            self.factory.worker.close()
        return result

    def select(self, identifier):
        return self._call("select", {"id": identifier})

    def delete(self, identifier):
        return self._call("delete", {"id": identifier})

    def rename(self, identifier, name):
        return self._call("rename", {"id": identifier, "name": name})

    def close(self):
        if self.closed:
            return
        try:
            if self.factory.worker.healthy:
                self.factory.worker.category({"engine": self.token, "command": "close", "values": {}, "context": None}, self.last_snapshot)
        finally:
            self.closed = True
