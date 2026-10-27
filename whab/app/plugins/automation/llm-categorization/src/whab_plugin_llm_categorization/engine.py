"""Budget-scoped prompt profiles and strict, provisional model suggestions."""

import json
import math
from datetime import datetime, timezone
from uuid import uuid4

from whab.plugins import StoreError

from . import providers
from .private import PROVIDERS, Vault, document, identifier

DEFAULTS = {"provider": "openai", "model": "", "threshold": 0.8, "examples": 20, "automatic": False}


def text(value, limit):
    return type(value) is str and 0 < len(value.strip()) <= limit and not any(ord(c) < 32 for c in value)


def settings(values):
    config = {**DEFAULTS, **values}
    categories = config.get("categories")
    if (set(config) != {*DEFAULTS, "categories", "consent"} or config["consent"] is not True
            or type(config["provider"]) is not str or config["provider"] not in PROVIDERS or not text(config["model"], 200)
            or type(config["threshold"]) not in (int, float) or not 0.8 <= config["threshold"] <= 1
            or type(config["examples"]) is not int or not 0 <= config["examples"] <= 50
            or type(config["automatic"]) is not bool or type(categories) is not list or not 1 <= len(categories) <= 500):
        raise StoreError("Choose a provider, model, categories, cutoff of at least 0.8 and consent to send transaction data.")
    ids = set()
    for row in categories:
        if (type(row) is not dict or set(row) != {"id", "name", "currency"}
                or not text(row["id"], 200) or row["id"] in ids or not text(row["name"], 200)
                or not text(row["currency"], 3) or len(row["currency"]) != 3):
            raise StoreError("Choose valid, distinct budget categories with currencies.")
        ids.add(row["id"])
    return config


def transaction(row, key):
    # Never send account identifiers, dates, notes, receipts, balances or bank data.
    return {"transactionId": key, "merchant": str(row["merchant"])[:200],
            "amount": row["amount"], "currency": row["currency"]}


class Engine:
    defaults = DEFAULTS

    def __init__(self, context):
        self.context = context
        self.vault = Vault(context)

    def _read(self):
        raw = self.context.read_file("profiles.json")
        saved = document(raw) if raw else {"active": None, "models": []}
        try:
            if (set(saved) != {"active", "models"} or type(saved["models"]) is not list or len(saved["models"]) > 20
                    or len({row["id"] for row in saved["models"]}) != len(saved["models"])):
                raise ValueError()
            for row in saved["models"]:
                if (not identifier(row["id"]) or not text(row["name"], 100) or type(row["examples"]) is not list
                        or len(row["examples"]) > 50 or not text(row["trainedAt"], 80)):
                    raise ValueError()
                settings(row["parameters"])
            if saved["active"] is not None and saved["active"] not in {row["id"] for row in saved["models"]}:
                raise ValueError()
            return saved
        except (ValueError, TypeError, KeyError):
            raise StoreError("Saved LLM profiles are invalid. Preserve this budget before restoring a valid copy.") from None

    def _write(self, saved):
        self.context.write_file("profiles.json", json.dumps(saved, allow_nan=False).encode())

    @staticmethod
    def public(row):
        return {key: value for key, value in row.items() if key != "examples"}

    def index(self):
        saved = self._read()
        return {"active": saved["active"], "models": [self.public(row) for row in saved["models"]]}

    def train(self, rows, parameters):
        config = settings(parameters)
        providers.credential(self.vault, config["provider"])
        categories = {row["id"]: row["currency"] for row in config["categories"]}
        eligible = [row for row in rows if row.get("reviewed") is True and not row.get("pending")
                    and not row.get("isTransfer") and not row.get("inferredTransfer") and not row.get("splits")
                    and categories.get(row.get("categoryId")) == row.get("currency")]
        eligible.sort(key=lambda row: row["date"], reverse=True)
        examples = [{**transaction(row, str(index)), "categoryId": row["categoryId"]}
                    for index, row in enumerate(eligible[:config["examples"]])]
        yield {"stage": "Prepared prompt profile; no transactions sent", "progress": 90}
        return {"id": str(uuid4()), "trainedAt": datetime.now(timezone.utc).isoformat(),
                "parameters": config, "examples": examples}

    def save(self, model, name):
        saved = self._read()
        if len(saved["models"]) >= 20 or not text(name, 100):
            raise StoreError("Choose a profile name and keep at most 20 saved profiles.")
        row = {**model, "name": name.strip()}
        saved["models"].append(row)
        self._write(saved)
        return self.public(row)

    def _selected(self, key):
        saved = self._read()
        row = next((row for row in saved["models"] if row["id"] == key), None)
        if row is None:
            raise StoreError("Choose a saved LLM profile from this budget.")
        return saved, row

    def select(self, key):
        saved, row = self._selected(key)
        providers.credential(self.vault, row["parameters"]["provider"])
        saved["active"] = key
        self._write(saved)

    def rename(self, key, name):
        saved, row = self._selected(key)
        if not text(name, 100):
            raise StoreError("Choose a profile name of at most 100 characters.")
        row["name"] = name.strip()
        self._write(saved)

    def delete(self, key):
        saved, row = self._selected(key)
        saved["models"].remove(row)
        if saved["active"] == key:
            saved["active"] = None
        self._write(saved)

    def predict(self, rows):
        return self._predict(rows, False)

    def preview(self, rows):
        return self._predict(rows, True)

    def _predict(self, rows, manual):
        saved = self._read()
        if not saved["active"] or not rows:
            return []
        _, profile = self._selected(saved["active"])
        config = profile["parameters"]
        if not manual and not config["automatic"]:
            return []
        eligible = [row for row in rows if not row.get("reviewed") and not row.get("categoryId")
                    and not row.get("pending") and not row.get("isTransfer") and not row.get("inferredTransfer") and not row.get("splits")]
        results = []
        for offset in range(0, len(eligible), 20):
            chunk = eligible[offset:offset + 20]
            by_id = {str(index): row for index, row in enumerate(chunk)}
            payload = {"categories": config["categories"], "reviewedExamples": profile["examples"],
                       "transactions": [transaction(row, key) for key, row in by_id.items()]}
            value = providers.complete(self.vault, config["provider"], config["model"], payload)
            try:
                parsed = json.loads(value)
                if type(parsed) is not dict or set(parsed) != {"suggestions"} or type(parsed["suggestions"]) is not list or len(parsed["suggestions"]) > len(chunk):
                    raise ValueError()
                seen = set()
                categories = {row["id"]: row["currency"] for row in config["categories"]}
                for item in parsed["suggestions"]:
                    if type(item) is not dict or set(item) != {"transactionId", "categoryId", "score"}:
                        raise ValueError()
                    key, category, score = item["transactionId"], item["categoryId"], item["score"]
                    row = by_id.get(key)
                    if (row is None or key in seen or categories.get(category) != row["currency"]
                            or type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1):
                        raise ValueError()
                    seen.add(key)
                    if score >= config["threshold"]:
                        results.append({"transactionId": row["id"], "categoryId": category,
                                        "score": score, "modelId": profile["id"]})
            except (ValueError, TypeError, KeyError):
                raise StoreError("The model returned invalid category suggestions. Nothing from this request was applied.") from None
        return results
