"""Deterministic features and a data-only forest format; no executable deserialization."""

import calendar
import json
import math
import re
from datetime import date

import numpy as np
from sklearn.feature_extraction import DictVectorizer
from sklearn.metrics import accuracy_score, precision_recall_fscore_support

LIMIT = 16 * 1024 * 1024
DEFAULTS = {"trees": 300, "depth": 20, "leaf": 2, "vocabulary": 1500,
            "threshold": 0.8, "balanced": False, "start": "", "end": ""}


def parameters(values):
    if type(values) is not dict or values.keys() - DEFAULTS.keys():
        raise ValueError("Unknown model parameters.")
    result = {**DEFAULTS, **values}
    for key, low, high in (("trees", 10, 500), ("depth", 2, 24), ("leaf", 1, 100), ("vocabulary", 50, 5000)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f"Invalid {key} parameter ({low}–{high}).")
    if type(result["balanced"]) is not bool or type(result["threshold"]) not in (float, int) or not 0 <= result["threshold"] <= 1:
        raise ValueError("Choose a score cutoff between 0 and 1 and a valid class weighting.")
    for key in ("start", "end"):
        if type(result[key]) is not str or result[key] and date.fromisoformat(result[key]).isoformat() != result[key]:
            raise ValueError("Use valid history dates.")
    if result["start"] and result["end"] and result["start"] > result["end"]:
        raise ValueError("History start must not follow its end.")
    return result


def tokens(payee):
    normalized = " ".join(re.findall(r"[^\W_]+", payee.casefold()))
    words = normalized.split()
    # Long numeric references vary across payments; meaningful short numbers survive.
    words = [word for word in words if not (word.isdigit() and len(word) >= 5)]
    name = " ".join(words)
    return {"payee:" + name, *("word:" + word for word in words),
            *("pair:" + a + " " + b for a, b in zip(words, words[1:])),
            *("char:" + name[i:i + n] for n in (3, 4, 5) for i in range(len(name) - n + 1))}


def features(row, vocabulary):
    day = date.fromisoformat(row["date"])
    return {**{key: 1 for key in tokens(row["merchant"]) if key in vocabulary},
            "account:" + row["accountId"]: 1, "type:" + row["accountType"]: 1,
            "subtype:" + row["accountSubtype"]: 1, "currency:" + row["currency"]: 1,
            "amount": abs(row["amount"]), "direction": (row["amount"] > 0) - (row["amount"] < 0),
            "day": day.day, "weekday": day.weekday(), "month": day.month,
            "monthEnd": calendar.monthrange(day.year, day.month)[1] - day.day}


def split(rows):
    rows = sorted(rows, key=lambda row: (row["date"], row["id"]))
    dates = sorted({row["date"] for row in rows})
    if len(dates) < 3:
        raise ValueError("Review categorized transactions on at least three distinct dates first.")
    first = dates[max(1, int(len(dates) * .6))]
    second = dates[min(len(dates) - 1, max(2, int(len(dates) * .8)))]
    train = [row for row in rows if row["date"] < first]
    validation = [row for row in rows if first <= row["date"] < second]
    test = [row for row in rows if row["date"] >= second]
    if len({row["categoryId"] for row in train}) < 2:
        raise ValueError("Review at least two categories in the older training history first.")
    return train, validation, test


def score(model, rows):
    vocabulary = set(model["vocabulary"])
    matrix = DictVectorizer()
    matrix.feature_names_ = model["features"]
    matrix.vocabulary_ = {key: index for index, key in enumerate(model["features"])}
    values = matrix.transform([features(row, vocabulary) for row in rows]).toarray().astype(np.float32)
    probabilities = []
    for row in values:
        votes = np.zeros(len(model["classes"]))
        for tree in model["forest"]:
            node = 0
            while tree["left"][node] != -1:
                node = tree["left"][node] if row[tree["feature"][node]] <= tree["threshold"][node] else tree["right"][node]
            leaf = tree["value"][node]
            votes += np.asarray(leaf) / sum(leaf)
        probabilities.append(votes / len(model["forest"]))
    return np.asarray(probabilities)


def metrics(model, rows):
    probabilities = score(model, rows)
    labels = [row["categoryId"] for row in rows]
    predictions = [model["classes"][index] for index in probabilities.argmax(axis=1)]
    offered = probabilities.max(axis=1) >= model["parameters"]["threshold"]
    correct = [a == b for a, b in zip(labels, predictions)]
    classes = sorted(set(labels) | set(model["classes"]))
    precision, recall, f1, support = precision_recall_fscore_support(labels, predictions, labels=classes, zero_division=0)
    return {"count": len(rows), "accuracy": float(accuracy_score(labels, predictions)),
            "macroF1": float(np.mean(f1)), "coverage": float(np.mean(offered)),
            "suggestionPrecision": float(np.mean(np.asarray(correct)[offered])) if offered.any() else None,
            "categories": [{"id": key, "precision": float(p), "recall": float(r), "f1": float(f), "count": int(n)}
                           for key, p, r, f, n in zip(classes, precision, recall, f1, support)]}


def encode(model):
    raw = json.dumps(model, allow_nan=False, separators=(",", ":")).encode()
    if len(raw) > LIMIT:
        raise ValueError("Model is too large. Reduce trees, depth, or vocabulary.")
    return raw


def decode(raw):
    try:
        if not raw or len(raw) > LIMIT:
            raise ValueError()
        model = json.loads(raw)
        if model["format"] != 1 or len(model["features"]) > 20000 or not 2 <= len(model["classes"]) <= 1000:
            raise ValueError()
        parameters(model["parameters"])
        if not all(type(value) is str and len(value) <= 1000 for value in model["features"] + model["vocabulary"] + model["classes"]):
            raise ValueError()
        if not 1 <= len(model["forest"]) <= 500:
            raise ValueError()
        for tree in model["forest"]:
            size = len(tree["left"])
            if not 1 <= size <= 100000 or any(len(tree[key]) != size for key in ("right", "feature", "threshold", "value")):
                raise ValueError()
            for i in range(size):
                left, right, feature = tree["left"][i], tree["right"][i], tree["feature"][i]
                if type(left) is not int or type(right) is not int or type(feature) is not int:
                    raise ValueError()
                if left == -1:
                    if right != -1 or len(tree["value"][i]) != len(model["classes"]):
                        raise ValueError()
                    if any(type(x) not in (float, int) or not math.isfinite(x) or x < 0 for x in tree["value"][i]) or sum(tree["value"][i]) <= 0:
                        raise ValueError()
                elif not i < left < size or not i < right < size or not 0 <= feature < len(model["features"]) or type(tree["threshold"][i]) not in (float, int) or not math.isfinite(tree["threshold"][i]):
                    raise ValueError()
        return model
    except (ValueError, KeyError, TypeError, OverflowError):
        raise ValueError("Saved model is incompatible or damaged. Train a new model.") from None
