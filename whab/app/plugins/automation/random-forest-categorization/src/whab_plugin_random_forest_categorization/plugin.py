"""Budget-scoped random forests. Each training step runs only on a settings request."""

import json
from collections import Counter
from datetime import datetime, timezone
from uuid import UUID, uuid4

import sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction import DictVectorizer

from whab.plugins import Categorization, Plugin, StoreError

from .model import DEFAULTS, decode, encode, features, metrics, parameters, score, split, tokens


class Engine:
    defaults = DEFAULTS

    def __init__(self, context):
        self.context = context

    def index(self):
        raw = self.context.read_file("models.json")
        if not raw:
            return {"active": None, "models": []}
        try:
            index = json.loads(raw)
            if type(index) is not dict or type(index["models"]) is not list or len(index["models"]) > 20:
                raise StoreError()
            ids = [row["id"] for row in index["models"]]
            if any(type(key) is not str or str(UUID(key)) != key for key in ids) or len(set(ids)) != len(ids) or index["active"] is not None and index["active"] not in ids:
                raise StoreError()
            return index
        except (ValueError, KeyError, TypeError, AttributeError):
            raise StoreError("Saved model list is damaged. Preserve the budget and restore a valid copy.") from None

    def _index(self, index):
        self.context.write_file("models.json", json.dumps(index).encode())

    def select(self, identifier):
        index = self.index()
        if identifier not in {row["id"] for row in index["models"]}:
            raise StoreError("Choose a saved model.")
        self.load(identifier)
        index["active"] = identifier
        self._index(index)

    def rename(self, identifier, name):
        index = self.index()
        for row in index["models"]:
            if row["id"] == identifier:
                row["name"] = name
                self._index(index)
                return
        raise StoreError("Choose a saved model.")

    def delete(self, identifier):
        index = self.index()
        if identifier not in {row["id"] for row in index["models"]}:
            raise StoreError("Choose a saved model.")
        index["models"] = [row for row in index["models"] if row["id"] != identifier]
        if index["active"] == identifier:
            index["active"] = None
        self._index(index)
        self.context.delete_file(f"models/{identifier}.json")

    def load(self, identifier):
        # IDs must be from the index; never interpret user input as a path.
        if identifier not in {row["id"] for row in self.index()["models"]}:
            raise StoreError("Choose a saved model.")
        return decode(self.context.read_file(f"models/{identifier}.json"))

    def train(self, rows, values):
        try:
            config = parameters(values)
        except ValueError as error:
            raise StoreError(str(error)) from None
        rows = [row for row in rows if row["reviewed"] and row["categoryId"] and
                (not config["start"] or row["date"] >= config["start"]) and
                (not config["end"] or row["date"] <= config["end"])]
        if len(rows) > 20000:
            raise StoreError("Limit the training history to at most 20,000 reviewed transactions.")
        try:
            train, validation, test = split(rows)
        except ValueError as error:
            raise StoreError(str(error)) from None
        yield {"stage": "Building features", "progress": 5}
        counts = Counter(token for row in train for token in tokens(row["merchant"]))
        vocabulary = sorted(counts, key=lambda key: (-counts[key], key))[:config["vocabulary"]]
        vectorizer = DictVectorizer()
        x = vectorizer.fit_transform([features(row, set(vocabulary)) for row in train])
        forest = RandomForestClassifier(n_estimators=1, max_depth=config["depth"], min_samples_leaf=config["leaf"],
                                        class_weight={key: len(train) / (len(Counter(r["categoryId"] for r in train)) * count) for key, count in Counter(r["categoryId"] for r in train).items()} if config["balanced"] else None,
                                        random_state=42, n_jobs=1, warm_start=True)
        for count in range(0, config["trees"], 10):
            forest.n_estimators = min(count + 10, config["trees"])
            forest.fit(x, [row["categoryId"] for row in train])
            yield {"stage": f"Training trees {forest.n_estimators} / {config['trees']}",
                   "progress": 5 + int(80 * forest.n_estimators / config["trees"])}
        yield {"stage": "Evaluating reviewed transactions", "progress": 90}
        model = {"format": 2, "id": str(uuid4()), "parameters": config, "sklearn": sklearn.__version__,
                 "trainedAt": datetime.now(timezone.utc).isoformat(), "vocabulary": vocabulary,
                 "features": list(vectorizer.get_feature_names_out()), "classes": list(forest.classes_),
                 "forest": [{"left": tree.tree_.children_left.tolist(), "right": tree.tree_.children_right.tolist(),
                             "feature": tree.tree_.feature.tolist(), "threshold": tree.tree_.threshold.tolist(),
                             "value": [[[int(index), float(weights[index])] for index in weights.nonzero()[0]] if left == -1 else []
                                       for left, weights in zip(tree.tree_.children_left, tree.tree_.value[:, 0, :])]} for tree in forest.estimators_],
                 "partitions": {key: {"count": len(part), "start": part[0]["date"], "end": part[-1]["date"]}
                                for key, part in (("train", train), ("validation", validation), ("test", test))}}
        model["validation"] = metrics(model, validation)
        model["test"] = metrics(model, test)
        yield {"stage": "Ready to save", "progress": 98}
        return model

    def save(self, model, name):
        index = self.index()
        if len(index["models"]) >= 20:
            raise StoreError("Delete an old model before saving another (maximum 20).")
        try:
            raw = encode(model)
        except ValueError as error:
            raise StoreError(str(error)) from None
        self.context.write_file(f"models/{model['id']}.json", raw)
        record = {key: value for key, value in model.items() if key not in {"forest", "features", "vocabulary", "classes", "format"}}
        record["name"] = name
        index["models"].append(record)
        self._index(index)
        return record

    def predict(self, rows):
        return self._predict(rows, False)

    def preview(self, rows):
        """Explicit manual guesses do not change the saved automatic cutoff."""
        if not self.index()["active"]:
            raise StoreError("Choose Use model before previewing low-score guesses.")
        return self._predict(rows, True)

    def _predict(self, rows, include_low_scores):
        identifier = self.index()["active"]
        if not identifier or not rows:
            return []
        model = self.load(identifier)
        probabilities = score(model, rows)
        return [{"transactionId": row["id"], "categoryId": model["classes"][int(p.argmax())],
                 "score": float(p.max()), "modelId": identifier,
                 "belowCutoff": bool(p.max() < model["parameters"]["threshold"])}
                for row, p in zip(rows, probabilities) if include_low_scores or p.max() >= model["parameters"]["threshold"]]


plugin = Plugin(id="random-forest-categorization", display_name="Random forest categorization",
                categorization=Categorization(factory=Engine))
