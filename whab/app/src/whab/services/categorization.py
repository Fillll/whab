"""Host-owned suggestion persistence and settings-bound cooperative training sessions."""

import json
import math
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock
from time import monotonic
from types import MappingProxyType
from uuid import UUID, uuid4

from whab.budgets.document import atomic_write
from whab.domain.categorization import candidates, fingerprint
from whab.domain.errors import StoreError
from whab.plugins import BudgetContext


def local_context(home, budget_id, plugin_id):
    """Device-local fallback for the original workspace; no runtime files enter Git."""
    root = home / "plugin-data" / plugin_id

    def path(name):
        parts = name.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise StoreError("Invalid plugin data path.")
        return root.joinpath(*parts)

    def read(name):
        target = path(name)
        return target.read_bytes() if target.exists() else None

    return BudgetContext(budget_id, MappingProxyType({}), MappingProxyType({}), lambda _: None,
                         read, lambda name, raw: atomic_write(path(name), raw),
                         lambda name: path(name).unlink(missing_ok=True))


class CategorizationService:
    """Models never receive a storage handle or unreviewed labeled examples."""

    def __init__(self, ledger, plugins, context_factory, saved_ids=()):
        self.ledger = ledger
        self.contexts = {key: context_factory(key) for key in set(saved_ids) | {p.id for p in plugins if p.categorization}}
        active = [p for p in plugins if p.categorization]
        if len(active) > 1:
            raise StoreError("Enable only one categorization plugin per budget.")
        self.engines = {p.id: p.categorization.factory(self.contexts[p.id]) for p in active}
        self.demo_engines = {}
        for plugin in active:
            context = self.contexts[plugin.id]
            scoped = replace(context,
                             read_file=lambda name, ctx=context: ctx.read_file("demo/" + name),
                             write_file=lambda name, raw, ctx=context: ctx.write_file("demo/" + name, raw),
                             delete_file=lambda name, ctx=context: ctx.delete_file("demo/" + name))
            self.demo_engines[plugin.id] = plugin.categorization.factory(scoped)
        self.runs = {}
        self.lock = RLock()
        self.error = None

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

    def predict(self, workspace):
        with self.ledger._lock, self.lock:
            for identifier, engine in (self.demo_engines if workspace == "demo" else self.engines).items():
                state = self.state(identifier, workspace)
                rows = [row for row in self.rows(workspace, False) if row["id"] not in state or state[row["id"]]["fingerprint"] != fingerprint(row)]
                categories = {row["id"]: row for row in self.ledger.categories(workspace)}
                for batch in range(0, len(rows), 100):
                    chunk = rows[batch:batch + 100]
                    by_id = {row["id"]: row for row in chunk}
                    for prediction in engine.predict(deepcopy(chunk)):
                        row = by_id.get(prediction.get("transactionId"))
                        category = categories.get(prediction.get("categoryId"))
                        score = prediction.get("score")
                        if row is None or category is None or category.get("currency", "USD") != row["currency"] or type(score) not in (float, int) or not math.isfinite(score) or not 0 <= score <= 1:
                            raise StoreError("Categorizer returned an invalid suggestion.")
                        state[row["id"]] = {**prediction, "id": str(uuid4()), "pluginId": identifier,
                                            "fingerprint": fingerprint(row), "createdAt": datetime.now(timezone.utc).isoformat()}
                # Prune obsolete rows, while preserving accepted provenance in the saved model history.
                all_ids = {row["id"] for row in self.ledger.transactions(workspace)}
                state = {key: value for key, value in state.items() if key in all_ids}
                self._save(identifier, workspace, state)
            self.error = None
        return {"suggested": len(self.suggestions(workspace))}

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
                        "categoryLabels": {row["id"]: row["name"] for row in self.ledger.categories(workspace)}}
            if action == "suggest":
                return self.predict(workspace)
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
