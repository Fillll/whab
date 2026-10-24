"""One saved currency-scoped exact-merchant teaching model; suggestions only."""

import json
from uuid import UUID, uuid4

from whab.plugins import Categorization, Plugin, PluginContract, StoreError


class Engine:
    defaults = {}

    def __init__(self, context):
        self.context = context

    def _read(self):
        raw = self.context.read_file("model.json")
        if raw is None:
            return None
        try:
            if type(raw) is not bytes or len(raw) > 2 * 1024 * 1024:
                raise ValueError()
            model = json.loads(raw)
            if type(model) is not dict or set(model) != {"format", "id", "name", "active", "lookup"} or type(model["format"]) is not int or model["format"] != 1 or type(model["active"]) is not bool:
                raise ValueError()
            if str(UUID(model["id"])) != model["id"] or type(model["name"]) is not str or not 1 <= len(model["name"]) <= 100:
                raise ValueError()
            if type(model["lookup"]) is not dict or len(model["lookup"]) > 1000 or any(type(key) is not str or len(key) > 2100 or type(value) is not str or not 1 <= len(value) <= 200 for key, value in model["lookup"].items()):
                raise ValueError()
            return model
        except (ValueError, TypeError, AttributeError):
            raise StoreError("Saved teaching model is damaged; preserve the budget before restoring it.") from None

    def _write(self, model):
        self.context.write_file("model.json", json.dumps(model, allow_nan=False).encode())

    def _selected(self, identifier):
        model = self._read()
        if model is None or model["id"] != identifier:
            raise StoreError("Choose the saved teaching model.")
        return model

    def index(self):
        model = self._read()
        if model is None:
            return {"active": None, "models": []}
        return {"active": model["id"] if model["active"] else None,
                "models": [{"id": model["id"], "name": model["name"]}]}

    def train(self, rows, parameters):
        if parameters or len(rows) > 1000:
            raise StoreError("Use no parameters and at most 1000 reviewed teaching rows.")
        lookup = {}
        for row in rows:
            if row.get("reviewed") is True and row.get("categoryId") and not row.get("pending") and not row.get("isTransfer"):
                lookup[row["currency"] + ":" + row["merchant"].casefold()] = row["categoryId"]
        yield {"stage": "Learned reviewed merchants", "progress": 50}
        return {"format": 1, "id": str(uuid4()), "lookup": lookup}

    def save(self, model, name):
        if self._read() is not None or type(name) is not str or not 1 <= len(name) <= 100:
            raise StoreError("Delete the prior teaching model and choose a bounded name first.")
        self._write({**model, "name": name, "active": False})
        return self.index()["models"][0]

    def select(self, identifier):
        model = self._selected(identifier)
        model["active"] = True
        self._write(model)

    def rename(self, identifier, name):
        model = self._selected(identifier)
        if type(name) is not str or not 1 <= len(name) <= 100:
            raise StoreError("Choose a bounded model name.")
        model["name"] = name
        self._write(model)

    def delete(self, identifier):
        self._selected(identifier)
        self.context.delete_file("model.json")

    def predict(self, rows):
        model = self._read()
        if model is None or not model["active"]:
            return []
        return [{"transactionId": row["id"], "categoryId": model["lookup"][key], "modelId": model["id"], "score": 1.0}
                for row in rows if not row.get("reviewed") and not row.get("categoryId")
                and (key := row["currency"] + ":" + row["merchant"].casefold()) in model["lookup"]]


plugin = Plugin("example-categorization", "Exact merchant teaching model", categorization=Categorization(Engine),
                contract=PluginContract(lifecycle="restart"))
