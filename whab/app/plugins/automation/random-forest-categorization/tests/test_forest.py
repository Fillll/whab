"""Model quality boundaries, persistence parity, and hostile data-only artifacts."""

import json
from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction import DictVectorizer
from whab_plugin_random_forest_categorization import model
from whab_plugin_random_forest_categorization.plugin import Engine, plugin

from whab.plugins import Categorization, StoreError


@pytest.fixture
def rows():
    return [{"id": str(i), "accountId": "account", "accountType": "checking", "accountSubtype": "",
             "currency": "USD", "merchant": "Grocery market" if i % 2 else "Cafe dining",
             "amount": 2300 if i % 2 else 999, "date": str(date(2026, 1, 1) + timedelta(days=i // 2)),
             "categoryId": "groceries" if i % 2 else "dining", "reviewed": True}
            for i in range(60)]


@pytest.fixture
def engine():
    files = {}
    return Engine(SimpleNamespace(read_file=files.get, write_file=files.__setitem__,
                                  delete_file=lambda key: files.pop(key, None)))


def finish(iterator):
    while True:
        try:
            next(iterator)
        except StopIteration as result:
            return result.value


@pytest.fixture
def trained(engine, rows):
    return finish(engine.train(rows, {"trees": 20, "leaf": 1, "vocabulary": 50, "depth": 4, "threshold": .5}))


def test_descriptor_and_feature_inputs(rows):
    assert isinstance(plugin.categorization, Categorization)
    with pytest.raises(ValueError):
        Categorization(None)
    assert "word:7" in model.tokens("7 ELEVEN 123456")
    assert "word:123456" not in model.tokens("7 ELEVEN 123456")
    features = model.features(rows[0], model.tokens(rows[0]["merchant"]))
    assert features["day"] == 1 and features["monthEnd"] == 30
    assert features["amount"] == 999 and features["direction"] == 1
    assert model.features({**rows[0], "amount": -999}, set())["direction"] == -1
    assert model.features({**rows[0], "amount": 0}, set())["direction"] == 0
    assert not model.tokens("").difference({"payee:"})


@pytest.mark.parametrize("values", [[], {"unknown": 1}, {"trees": True}, {"trees": 9}, {"trees": 501},
                                    {"depth": 1}, {"depth": 25}, {"leaf": 0}, {"vocabulary": 5001},
                                    {"balanced": 1}, {"threshold": "x"}, {"threshold": -1}, {"threshold": 2},
                                    {"start": 1}, {"start": "bad"}, {"end": "2026-99-01"},
                                    {"start": "2026-02-01", "end": "2026-01-01"}])
def test_bad_parameters(values):
    with pytest.raises(ValueError):
        model.parameters(values)


def test_training_splits_and_unreviewed_exclusion(engine, rows):
    train, validation, test = model.split(rows)
    assert max(row["date"] for row in train) < min(row["date"] for row in validation)
    assert max(row["date"] for row in validation) < min(row["date"] for row in test)
    extra = {**rows[0], "merchant": "SECRET unreviewedpayee", "reviewed": False}
    unlabeled = {**rows[0], "merchant": "SECRET uncategorized", "categoryId": None}
    heldout = {**rows[-1], "merchant": "heldoutonly"}
    trained = finish(engine.train([*rows[:-1], heldout, extra, unlabeled], {"trees": 10, "balanced": True,
                              "start": "2026-01-01", "end": "2026-12-31"}))
    assert trained["partitions"]["train"]["count"] == 36
    assert trained["test"]["count"] == 12
    assert all("secret" not in word and "heldoutonly" not in word for word in trained["vocabulary"])
    with pytest.raises(ValueError, match="three distinct dates"):
        finish(engine.train(rows[:2], {}))
    with pytest.raises(ValueError, match="two categories"):
        finish(engine.train([row for row in rows if row["categoryId"] == "dining"], {}))
    assert not engine.index()["models"]


def test_data_only_predictions_match_sklearn_and_saved_models(engine, trained, rows):
    train, _, test = model.split(rows)
    vectorizer = DictVectorizer()
    matrix = vectorizer.fit_transform([model.features(row, set(trained["vocabulary"])) for row in train])
    forest = RandomForestClassifier(n_estimators=20, random_state=42, max_depth=4, min_samples_leaf=1, n_jobs=1)
    forest.fit(matrix, [row["categoryId"] for row in train])
    expected = forest.predict_proba(vectorizer.transform([model.features(row, set(trained["vocabulary"])) for row in test]))
    np.testing.assert_allclose(model.score(trained, test), expected)
    record = engine.save(trained, "First model")
    assert record["name"] == "First model" and "forest" not in record
    assert engine.predict(rows) == []
    engine.select(record["id"])
    predictions = engine.predict(rows)
    assert len(predictions) == len(rows)
    restored = engine.load(record["id"])
    np.testing.assert_array_equal(model.score(restored, test), expected)
    # Previously saved dense models remain readable and produce the same scores.
    legacy = deepcopy(trained)
    legacy['format'] = 1
    for tree in legacy['forest']:
        tree['value'] = [[dict(leaf).get(index, 0.0) for index in range(len(legacy['classes']))]
                         for leaf in tree['value']]
    restored_legacy = model.decode(model.encode(legacy))
    np.testing.assert_array_equal(model.score(restored_legacy, test), expected)
    assert engine.predict([]) == []
    assert trained["test"]["accuracy"] == 1
    assert trained["validation"]["count"] == 12
    engine.delete(record["id"])
    assert engine.index() == {"active": None, "models": []}
    with pytest.raises(ValueError, match="saved model"):
        engine.select("missing")
    with pytest.raises(ValueError, match="saved model"):
        engine.load("../missing")


def test_metrics_abstention_and_lifecycle_limits(engine, trained, rows, monkeypatch):
    trained["parameters"]["threshold"] = 1
    # Force uncertainty to check abstention metrics independently of perfect fixture labels.
    monkeypatch.setattr(model, "score", lambda *_: np.array([[.5, .5]] * len(rows)))
    result = model.metrics(trained, rows)
    assert result["suggestionPrecision"] is None and result["coverage"] == 0
    engine.save(trained, "One")
    engine.select(trained["id"])
    trained2 = deepcopy(trained)
    trained2["id"] = str(uuid4())
    engine.save(trained2, "Two")
    engine.delete(trained2["id"])
    assert engine.index()["active"] == trained["id"]
    monkeypatch.setattr(engine, "index", lambda: {"models": [{}] * 20})
    with pytest.raises(ValueError, match="maximum 20"):
        engine.save(trained, "overflow")
    monkeypatch.setattr(model, "LIMIT", 2)
    with pytest.raises(ValueError, match="too large"):
        model.encode(trained)
    monkeypatch.setattr(engine, "index", lambda: {"models": []})
    with pytest.raises(StoreError, match="too large"):
        engine.save(trained, "Too large")


def test_default_forest_saves_large_multicategory_history_with_sklearn_parity(engine):
    # A realistic history size exceeded 16 MiB with dense scores at every node.
    rows = [{'id': str(i), 'date': str(date(2020, 1, 1) + timedelta(days=i // 6)),
             'merchant': f'Merchant {i % 127} branch {i % 31}', 'amount': ((i * 7919) % 200000) - 100000,
             'accountId': f'account-{i % 5}', 'accountType': 'depository', 'accountSubtype': 'checking',
             'currency': 'USD', 'reviewed': True, 'categoryId': f'category-{i % 60}'} for i in range(11022)]
    trained = finish(engine.train(rows, {}))
    dense_weights_lower_bound = sum(len(tree['left']) for tree in trained['forest']) * len(trained['classes']) * 4
    assert dense_weights_lower_bound > model.LIMIT
    assert len(model.encode(trained)) < model.LIMIT
    record = engine.save(trained, 'Large history')
    restored = engine.load(record['id'])
    train, _, test = model.split(rows)
    vectorizer = DictVectorizer()
    matrix = vectorizer.fit_transform([model.features(row, set(trained['vocabulary'])) for row in train])
    forest = RandomForestClassifier(n_estimators=300, max_depth=20, min_samples_leaf=2, random_state=42, n_jobs=1)
    forest.fit(matrix, [row['categoryId'] for row in train])
    expected = forest.predict_proba(vectorizer.transform([model.features(row, set(trained['vocabulary'])) for row in test[:50]]))
    np.testing.assert_allclose(model.score(restored, test[:50]), expected, rtol=1e-14, atol=1e-15)


@pytest.mark.parametrize("change", [
    lambda m: m.update(format=99), lambda m: m.update(features=["x"] * 20001),
    lambda m: m.update(classes=[]), lambda m: m.update(parameters={"trees": -1}),
    lambda m: m.update(features=[True]), lambda m: m.update(forest=[]),
    lambda m: m["forest"][0].update(left=[]), lambda m: m["forest"][0].update(right=[]),
    lambda m: m["forest"][0]["left"].__setitem__(0, "x"),
    lambda m: m["forest"][0]["right"].__setitem__(0, "x"),
    lambda m: m["forest"][0]["feature"].__setitem__(0, "x"),
    lambda m: m["forest"][0]["left"].__setitem__(0, -1),
    lambda m: m["forest"][0]["feature"].__setitem__(0, 999999),
    lambda m: m["forest"][0]["left"].__setitem__(0, 0),
    lambda m: m["forest"][0]["right"].__setitem__(0, 0),
    lambda m: m["forest"][0]["threshold"].__setitem__(0, "x"),
    lambda m: m["forest"][0]["threshold"].__setitem__(0, float("inf")),
])
def test_reject_malformed_tree(trained, change):
    change(trained)
    with pytest.raises(ValueError, match="incompatible or damaged"):
        model.decode(json.dumps(trained).encode())


@pytest.mark.parametrize("raw", [None, b"", b"{}", b"null", b"false", b"invalid"])
def test_reject_bad_container(raw):
    with pytest.raises(ValueError):
        model.decode(raw)


@pytest.mark.parametrize('format', [1, 2])
def test_leaf_validation(trained, monkeypatch, format):
    if format == 1:
        trained['format'] = 1
        for tree in trained['forest']:
            tree['value'] = [[dict(leaf).get(index, 0.0) for index in range(len(trained['classes']))]
                             for leaf in tree['value']]
    original = deepcopy(trained)
    leaf = trained["forest"][0]["left"].index(-1)
    invalid = ([], [-1, 2], [0, 0], [True, 1], [float("inf"), 1], ["x", 1]) if format == 1 else (
        None, [], [None], [[0]], [[True, 1]], [[-1, 1]], [[2, 1]], [[0, 1], [0, 2]],
        [[0, True]], [[0, float('inf')]], [[0, 0]], [[0, -1]], [[0, 'x']])
    for value in invalid:
        changed = deepcopy(original)
        changed["forest"][0]["value"][leaf] = value
        with pytest.raises(ValueError):
            model.decode(json.dumps(changed).encode())
    changed = deepcopy(original)
    changed["forest"][0]["right"][leaf] = 1
    with pytest.raises(ValueError):
        model.decode(json.dumps(changed).encode())
    monkeypatch.setattr(model, "LIMIT", 1)
    with pytest.raises(ValueError):
        model.decode(b"{}")


def test_rename_and_invalid_model_index(engine, trained):
    engine.save(trained, 'Original')
    engine.rename(trained['id'], 'Renamed')
    assert engine.index()['models'][0]['name'] == 'Renamed'
    for operation in (engine.rename, engine.delete):
        with pytest.raises(ValueError, match='saved model'):
            operation('missing', 'Name') if operation == engine.rename else operation('missing')
    original = engine.context.read_file('models.json')
    for raw in (b'invalid', b'{}', b'[]', b'{"models":false}', b'{"models":[],"active":"missing"}',
                b'{"models":[{"id":"../escape"}],"active":null}',
                b'{"models":[{"id":false}],"active":null}'):
        engine.context.write_file('models.json', raw)
        with pytest.raises(ValueError, match='model list is damaged'):
            engine.index()
    engine.context.write_file('models.json', original)


def test_train_limits_and_abstaining_prediction(engine, trained, rows, monkeypatch):
    with pytest.raises(ValueError):
        finish(engine.train(rows, {'trees': -1}))
    with pytest.raises(ValueError, match='20,000'):
        finish(engine.train(rows * 334, {}))
    engine.save(trained, 'Threshold')
    engine.select(trained['id'])
    from whab_plugin_random_forest_categorization import plugin as module
    monkeypatch.setattr(module, 'score', lambda *_: np.array([[.4, .6]]))
    assert engine.predict(rows[:1])
    trained['parameters']['threshold'] = .9
    engine.context.write_file(f"models/{trained['id']}.json", model.encode(trained))
    assert engine.predict(rows[:1]) == []
    preview = engine.preview(rows[:1])
    assert len(preview) == 1 and preview[0]['belowCutoff'] is True
    assert preview[0]['score'] == .6
    assert engine.load(trained['id'])['parameters']['threshold'] == .9
    assert engine.preview([]) == []
    engine.delete(trained['id'])
    with pytest.raises(StoreError, match='Use model'):
        engine.preview(rows[:1])
