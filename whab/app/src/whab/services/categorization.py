"""Host-owned suggestion persistence and settings-bound cooperative training sessions."""

import inspect
import json
import math
import os
import stat
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock
from time import monotonic
from types import MappingProxyType
from uuid import UUID, uuid4

from whab.budgets.container import MAX_CONTAINER_FILES, validate_file_name
from whab.budgets.document import MAX_BUNDLE_BYTES, atomic_write
from whab.domain.categorization import candidates, fingerprint
from whab.domain.errors import StoreError
from whab.plugins import BudgetContext
from whab.plugins.runtime import PluginError
from whab.plugins.sdk import valid_plugin_name


def _local_root(home, budget_id, plugin_id):
    """Keep legacy default files in place; isolate every other budget by identity."""
    if not valid_plugin_name(budget_id) or not valid_plugin_name(plugin_id):
        raise StoreError("Invalid budget or plugin data namespace.")
    base = home.resolve()
    root = (base if budget_id == "default" else base / "budgets" / budget_id) / "plugin-data" / plugin_id
    current = base
    for part in root.relative_to(base).parts:
        current = current / part
        if current.is_symlink():
            raise StoreError("Invalid plugin data path.")
    return root


def local_files(home, budget_id, plugin_id):
    """Read a complete bounded namespace without following plugin symlinks."""
    root = _local_root(home, budget_id, plugin_id)
    result, size = {}, 0
    if root.exists() and not root.is_dir():
        raise StoreError("The categorization namespace is not a directory.")
    pending, entries = ([root] if root.exists() else []), 0
    while pending:
        with os.scandir(pending.pop()) as children:
            for child in children:
                entries += 1
                if entries > MAX_CONTAINER_FILES * 2:
                    raise StoreError("The categorization namespace exceeds its supported size.")
                if child.is_symlink():
                    raise StoreError("Plugin data contains an unsupported symbolic link.")
                if child.is_dir(follow_symlinks=False):
                    pending.append(root / os.path.relpath(child.path, root))
                    continue
                if not stat.S_ISREG(child.stat(follow_symlinks=False).st_mode):
                    raise StoreError("Plugin data contains an unsupported file type.")
                name = os.path.relpath(child.path, root).replace(os.sep, "/")
                validate_file_name(f"plugins/{plugin_id}/{name}")
                remaining = MAX_BUNDLE_BYTES - size
                if len(result) >= MAX_CONTAINER_FILES or child.stat(follow_symlinks=False).st_size > remaining:
                    raise StoreError("The categorization namespace exceeds its supported size.")
                with open(child.path, "rb") as source:
                    raw = source.read(remaining + 1)
                size += len(raw)
                if size > MAX_BUNDLE_BYTES:
                    raise StoreError("The categorization namespace exceeds its supported size.")
                result[name] = raw
    return result


def local_context(home, budget_id, plugin_id, *, lock=None, on_change=None):
    """Budget callbacks share the host mutation lock when one is supplied."""
    root = _local_root(home, budget_id, plugin_id)
    lock = lock if lock is not None else RLock()

    def path(name):
        _local_root(home, budget_id, plugin_id)
        if type(name) is not str:
            raise StoreError("Invalid plugin data path.")
        validate_file_name(f"plugins/{plugin_id}/{name}")
        target = root.joinpath(*name.split("/"))
        current = root
        for part in target.relative_to(root).parts:
            current = current / part
            if current.is_symlink():
                raise StoreError("Invalid plugin data path.")
        return target

    def read(name):
        with lock:
            target = path(name)
            return target.read_bytes() if target.exists() else None

    def changed():
        if on_change is not None:
            try:
                on_change()
            except Exception:
                # The primary namespace save succeeded. Copy failures remain
                # observable through the owning copy coordinator.
                pass

    def write(name, raw):
        with lock:
            if type(raw) is not bytes:
                raise StoreError("Plugin files must contain bytes.")
            atomic_write(path(name), raw)
            changed()

    def delete(name):
        with lock:
            path(name).unlink(missing_ok=True)
            changed()

    return BudgetContext(budget_id, MappingProxyType({}), MappingProxyType({}), lambda _: None,
                         read, write, delete)


class CategorizationService:
    """Models never receive a storage handle or unreviewed labeled examples."""

    def __init__(self, ledger, plugins, context_factory, saved_ids=()):
        self.ledger = ledger
        self.contexts = {key: context_factory(key) for key in set(saved_ids) | {p.id for p in plugins if p.categorization}}
        if any(context.budget_id != ledger.budget_id for context in self.contexts.values()):
            raise StoreError("Categorization data belongs to another budget.")
        active = [p for p in plugins if p.categorization]
        if len(active) > 1:
            raise StoreError("Enable only one categorization plugin per budget.")
        self.engines = {}
        self.demo_engines = {}
        self.runs = {}
        self.lock = RLock()
        self.error = None
        self._closed = False
        self._engine_contexts = []
        try:
            for plugin in active:
                context = self.contexts[plugin.id]
                personal = _EngineContext(context)
                self._engine_contexts.append(personal)
                self.engines[plugin.id] = self._engine(plugin, personal.context())
                scoped = replace(context,
                                 read_file=lambda name, ctx=context: ctx.read_file("demo/" + name),
                                 write_file=lambda name, raw, ctx=context: ctx.write_file("demo/" + name, raw),
                                 delete_file=lambda name, ctx=context: ctx.delete_file("demo/" + name))
                demo = _EngineContext(scoped)
                self._engine_contexts.append(demo)
                self.demo_engines[plugin.id] = self._engine(plugin, demo.context())
        except Exception:
            try:
                self.close()
            except Exception:
                pass
            raise PluginError("The categorization plugin could not start. Its saved models are unchanged.",
                              code="categorization_startup_failed", plugin_id=plugin.id) from None

    @staticmethod
    def _engine(plugin, context):
        engine = plugin.categorization.factory(context)
        if inspect.isawaitable(engine):
            if inspect.iscoroutine(engine):
                engine.close()
            raise ValueError("Categorization factories must be synchronous")
        if not isinstance(getattr(engine, "defaults", None), Mapping) or any(
            not callable(getattr(engine, name, None))
            for name in ("index", "select", "delete", "rename", "train", "save", "predict")
        ) or (getattr(engine, "close", None) is not None and not callable(engine.close)):
            # Even a malformed engine may own a resource. Do not retain it when
            # validation fails; asynchronous engines are outside this contract.
            close = getattr(engine, "close", None)
            if callable(close):
                result = close()
                if inspect.iscoroutine(result):
                    result.close()
            raise ValueError("Invalid categorization engine")
        return engine

    def enabled(self):
        return bool(self.engines)

    def ensure_idle(self):
        """An unsaved cooperative continuation must finish or be canceled first."""
        from whab.budgets.runtime import RuntimeTransitionError

        with self.lock:
            if self.runs:
                owner = next(iter(self.runs.values()))["pluginId"]
                raise RuntimeTransitionError("continuation_busy", owner)

    def ready(self):
        """Read saved model indexes without changing their namespace."""
        for engine in (*self.engines.values(), *self.demo_engines.values()):
            value = engine.index()
            if type(value) is not dict or type(value.get("models")) is not list:
                raise ValueError("Invalid categorization model index")
            # Indexes/settings remain bounded, finite data across both adapters.
            if len(json.dumps({"defaults": dict(engine.defaults), "index": value}, allow_nan=False).encode()) > 1024 * 1024:
                raise ValueError("Categorization metadata exceeds its limit")
        return True

    def close(self):
        """Revoke delayed callbacks and close every engine/continuation once."""
        with self.lock:
            if self._closed:
                return
            self._closed = True
            for context in self._engine_contexts:
                context.close()
            callbacks = [run["iterator"].close for run in self.runs.values()]
            self.runs.clear()
            seen = set()
            for engine in (*self.demo_engines.values(), *self.engines.values()):
                if id(engine) not in seen and callable(getattr(engine, "close", None)):
                    callbacks.append(engine.close)
                    seen.add(id(engine))
            failed = False
            for close in callbacks:
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        if inspect.iscoroutine(result):
                            result.close()
                        raise ValueError("Categorization cleanup must be synchronous")
                except Exception:
                    failed = True
            if failed:
                raise StoreError("A retired categorization resource could not close.") from None

    def rows(self, workspace, reviewed, transactions=None):
        return candidates(self.ledger.transactions(workspace) if transactions is None else transactions, self.ledger.accounts(workspace),
                          self.ledger.categories(workspace), reviewed=reviewed)

    def state(self, plugin_id, workspace):
        raw = self.contexts[plugin_id].read_file(f"suggestions-{workspace}.json")
        try:
            state = json.loads(raw) if raw else {}
            if type(state) is not dict or any(type(row) is not dict or not {"id", "categoryId", "fingerprint", "modelId", "pluginId", "createdAt"} <= row.keys() for row in state.values()):
                raise ValueError()
            return state
        except (ValueError, TypeError):
            raise StoreError("Saved suggestions are damaged. Preserve the budget before restoring a valid copy.") from None

    def _save(self, plugin_id, workspace, state):
        self.contexts[plugin_id].write_file(f"suggestions-{workspace}.json", json.dumps(state).encode())

    def suggestions(self, workspace, transactions=None):
        rows = {row["id"]: row for row in self.rows(workspace, False, transactions)}
        categories = {row["id"]: row for row in self.ledger.categories(workspace)}
        result = {}
        for plugin_id in self.contexts:
            try:
                state = self.state(plugin_id, workspace)
            except StoreError:
                self.error = "Some saved suggestions are unavailable. Restore a valid budget copy."
                continue
            for identifier, suggestion in state.items():
                row = rows.get(identifier)
                category = categories.get(suggestion.get("categoryId"))
                if row and category and not suggestion.get("dismissed") and not suggestion.get("accepted") and suggestion["fingerprint"] == fingerprint(row) and category.get("currency", "USD") == row["currency"]:
                    result[identifier] = suggestion
        return result

    def predict(self, workspace, *, preview=False):
        with self.ledger._lock, self.lock:
            self._check_open()
            for identifier, engine in (self.demo_engines if workspace == "demo" else self.engines).items():
                predict = getattr(engine, "preview", None) if preview else engine.predict
                if not callable(predict):
                    raise StoreError("This categorization plugin does not support low-score previews. Update it first.")
                state = self.state(identifier, workspace)
                rows = [row for row in self.rows(workspace, False) if row["id"] not in state or state[row["id"]]["fingerprint"] != fingerprint(row)]
                categories = {row["id"]: row for row in self.ledger.categories(workspace)}
                for batch in range(0, len(rows), 100):
                    chunk = rows[batch:batch + 100]
                    by_id = {row["id"]: row for row in chunk}
                    for prediction in predict(deepcopy(chunk)):
                        row = by_id.get(prediction.get("transactionId"))
                        category = categories.get(prediction.get("categoryId"))
                        score = prediction.get("score")
                        if row is None or category is None or category.get("currency", "USD") != row["currency"] or type(score) not in (float, int) or not math.isfinite(score) or not 0 <= score <= 1 or type(prediction.get("belowCutoff", False)) is not bool:
                            raise StoreError("Categorizer returned an invalid suggestion.")
                        state[row["id"]] = {**prediction, "id": str(uuid4()), "pluginId": identifier,
                                            "fingerprint": fingerprint(row), "createdAt": datetime.now(timezone.utc).isoformat()}
                # Prune obsolete rows, while preserving accepted provenance in the saved model history.
                all_ids = {row["id"] for row in self.ledger.transactions(workspace)}
                state = {key: value for key, value in state.items() if key in all_ids}
                self._save(identifier, workspace, state)
            self.error = None
        suggestions = self.suggestions(workspace)
        return {"suggested": len(suggestions), "lowScore": sum(row.get("belowCutoff", False) for row in suggestions.values())}

    def after_import(self):
        try:
            self.predict("personal")
        except Exception:
            self.error = "Import completed. Category suggestions could not be generated; retry from plugin settings."

    def verify(self, workspace, transaction_id, suggestion_id):
        suggestion = self.suggestions(workspace).get(transaction_id)
        if not suggestion or suggestion["id"] != suggestion_id:
            raise StoreError("Suggestion changed. Refresh this transaction before reviewing it.")
        return suggestion

    def accepted(self, workspace, transaction_id, suggestion, category_id):
        """Keep provenance after the authoritative atomic category/review commit."""
        try:
            state = self.state(suggestion["pluginId"], workspace)
            state[transaction_id] = {**suggestion, "accepted": True, "acceptedCategoryId": category_id,
                                     "acceptedAt": datetime.now(timezone.utc).isoformat()}
            self._save(suggestion["pluginId"], workspace, state)
        except Exception:
            self.error = "Review saved. Its suggestion history could not be updated."

    def action(self, workspace, values):
        with self.ledger._lock, self.lock:
            self._check_open()
            action = values["action"]
            identifier = values.get("pluginId")
            if action == "dismiss":
                suggestion = self.verify(workspace, values["transactionId"], values["suggestionId"])
                state = self.state(suggestion["pluginId"], workspace)
                state[values["transactionId"]] = {**suggestion, "dismissed": True}
                self._save(suggestion["pluginId"], workspace, state)
                return {"ok": True}
            if identifier not in self.engines:
                raise StoreError("Enable a compatible categorization plugin on the budget host first.")
            engine = (self.demo_engines if workspace == "demo" else self.engines)[identifier]
            if action == "status":
                return {"defaults": engine.defaults, **engine.index(), "eligible": len(self.rows(workspace, True)), "error": self.error,
                        "previewAvailable": callable(getattr(engine, "preview", None)),
                        "categoryLabels": {row["id"]: row["name"] for row in self.ledger.categories(workspace)}}
            if action in {"suggest", "preview"}:
                return self.predict(workspace, preview=action == "preview")
            if action in {"select", "delete", "rename"}:
                model_id = values.get("modelId", "")
                if len(model_id) != 36 or str(UUID(model_id)) != model_id:
                    raise StoreError("Choose a saved model.")
                if action == "rename":
                    engine.rename(model_id, values.get("name", "Random forest"))
                else:
                    getattr(engine, action)(model_id)
                return {"ok": True}
            now = monotonic()
            for token in list(self.runs):
                if self.runs[token]["expires"] < now:
                    self.runs.pop(token)["iterator"].close()
            if action == "train":
                if self.runs:
                    raise StoreError("Finish or cancel the current training session first.")
                token = str(uuid4())
                self.runs[token] = {"pluginId": identifier, "workspace": workspace, "expires": now + 20,
                                    "name": str(values.get("name") or "Random forest")[:100],
                                    "iterator": engine.train(deepcopy(self.rows(workspace, True)), values.get("parameters", {}))}
                return {"session": token, "stage": "Preparing reviewed transactions", "progress": 0}
            token = values.get("session")
            run = self.runs.get(token)
            if not run or run["pluginId"] != identifier or run["workspace"] != workspace:
                raise StoreError("Training session expired. Start training again in settings.")
            if action == "cancel":
                self.runs.pop(token)["iterator"].close()
                return {"canceled": True}
            if action != "step":
                raise StoreError("Unknown categorization action.")
            run["expires"] = now + 20
            try:
                return {"session": token, **next(run["iterator"])}
            except StopIteration as completed:
                trained = completed.value
            except Exception:
                self.runs.pop(token)["iterator"].close()
                raise
            # Publish outside the StopIteration handler: save failures must not chain
            # the generator's returned model (including private vocabulary) into logs.
            self.runs.pop(token)
            model = engine.save(trained, run["name"])
            return {"complete": True, "model": model, "progress": 100, "stage": "Training complete"}

    def _check_open(self):
        if self._closed:
            raise StoreError("This categorization generation has retired. Refresh before continuing.")


class _EngineContext:
    """Engine callbacks expire on retirement, including callbacks kept by threads."""

    def __init__(self, context):
        self.base = context
        self.lock = RLock()
        self.closed = False

    def call(self, callback, *arguments):
        with self.lock:
            if self.closed:
                raise StoreError("This categorization generation has retired.")
            return callback(*arguments)

    def context(self):
        return replace(self.base,
                       save_state=lambda value: self.call(self.base.save_state, value),
                       read_file=lambda name: self.call(self.base.read_file, name),
                       write_file=lambda name, raw: self.call(self.base.write_file, name, raw),
                       delete_file=lambda name: self.call(self.base.delete_file, name))

    def close(self):
        with self.lock:
            self.closed = True
