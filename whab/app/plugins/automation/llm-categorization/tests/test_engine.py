"""Reviewed-only examples, bounded requests, abstention and strict untrusted output."""

import json
from copy import deepcopy

import pytest
from whab_plugin_llm_categorization import providers
from whab_plugin_llm_categorization.engine import Engine, settings, text, transaction
from whab_plugin_llm_categorization.private import Vault, document, identifier

from conftest import save_profile
from whab.plugins import StoreError


def test_profile_lifecycle_and_no_data_sent_on_save(engine, config, row, monkeypatch):
    monkeypatch.setattr(providers, "complete", lambda *args: pytest.fail("Saving must not call inference"))
    rows = [{**row, "reviewed": True, "categoryId": "food"}, row,
            {**row, "reviewed": True, "categoryId": "food", "pending": True},
            {**row, "reviewed": True, "categoryId": "food", "isTransfer": True},
            {**row, "reviewed": True, "categoryId": "food", "splits": [{}]},
            {**row, "reviewed": True, "categoryId": "unknown"}]
    assert engine.index() == {"active": None, "models": []}
    first = save_profile(engine, config, rows)
    assert "examples" not in first
    saved = json.loads(engine.context.read_file("profiles.json"))
    example = saved["models"][0]["examples"][0]
    assert set(example) == {"transactionId", "merchant", "amount", "currency", "categoryId"}
    assert len(saved["models"][0]["examples"]) == 1
    assert "private-" not in json.dumps(example)
    assert "fictional-key" not in json.dumps(saved)
    engine.rename(first["id"], "Renamed")
    assert engine.index()["models"][0]["name"] == "Renamed"
    second = save_profile(engine, config)
    engine.delete(first["id"])
    assert engine.index()["active"] == second["id"]
    engine.delete(second["id"])
    assert engine.index() == {"active": None, "models": []}
    for action in (engine.select, engine.delete, lambda key: engine.rename(key, "x")):
        with pytest.raises(StoreError, match="saved LLM profile"):
            action(first["id"])


def test_manual_and_automatic_predictions_minimize_data_and_preserve_inputs(engine, config, row, monkeypatch):
    assert engine.predict([row]) == []
    profile = save_profile(engine, config)
    assert engine.predict([row]) == []
    assert engine.preview([]) == []
    sent = []
    def respond(vault, provider, model, payload):
        sent.append(deepcopy(payload))
        return json.dumps({"suggestions": [{"transactionId": item["transactionId"], "categoryId": "food", "score": .95}
                                           for item in payload["transactions"]]})
    monkeypatch.setattr(providers, "complete", respond)
    original = deepcopy(row)
    assert engine.preview([row]) == [{"transactionId": row["id"], "categoryId": "food", "score": .95, "modelId": profile["id"]}]
    assert row == original
    assert set(sent[0]["transactions"][0]) == {"transactionId", "merchant", "amount", "currency"}
    assert "private-" not in json.dumps(sent)
    assert "note" not in json.dumps(sent)
    save_profile(engine, {**config, "automatic": True})
    predictions = engine.predict([{**row, "id": str(index)} for index in range(42)])
    assert len(predictions) == 42
    assert [len(value["transactions"]) for value in sent] == [1, 20, 20, 2]
    assert engine.predict([{**row, "reviewed": True}, {**row, "categoryId": "food"},
                           {**row, "pending": True}, {**row, "isTransfer": True}, {**row, "splits": [1]}]) == []


@pytest.mark.parametrize("output", [
    "not json", "[]", "{}", '{"suggestions":{}}',
    {"suggestions": [{"transactionId": "0", "categoryId": "food", "score": .9}] * 2},
    {"suggestions": [None]}, {"suggestions": [{"transactionId": "0"}]},
    {"suggestions": [{"transactionId": "private-transaction-id", "categoryId": "food", "score": .9}]},
    {"suggestions": [{"transactionId": "0", "categoryId": "unknown", "score": .9}]},
    {"suggestions": [{"transactionId": "0", "categoryId": "euro", "score": .9}]},
    *[{"suggestions": [{"transactionId": "0", "categoryId": "food", "score": value}]} for value in [True, "0.9", -1, 2, float("nan"), float("inf")]],
    {"suggestions": [{"transactionId": [], "categoryId": "food", "score": .9}]},
    {"suggestions": [{"transactionId": "0", "categoryId": [], "score": .9}]},
])
def test_invalid_response_never_produces_partial_suggestions(engine, config, row, monkeypatch, output):
    save_profile(engine, config)
    monkeypatch.setattr(providers, "complete", lambda *args: output if type(output) is str else json.dumps(output))
    with pytest.raises(StoreError, match="invalid category suggestions"):
        engine.preview([row])


def test_duplicate_rows_and_low_scores_are_not_applied(engine, config, row, monkeypatch):
    save_profile(engine, config)
    for result in ([], [{"transactionId": "0", "categoryId": "food", "score": .79}]):
        monkeypatch.setattr(providers, "complete", lambda *args: json.dumps({"suggestions": result}))
        assert engine.preview([row]) == []
    duplicate = [{"transactionId": "0", "categoryId": "food", "score": .9}] * 2
    monkeypatch.setattr(providers, "complete", lambda *args: json.dumps({"suggestions": duplicate}))
    with pytest.raises(StoreError):
        engine.preview([row, {**row, "id": "second"}])


@pytest.mark.parametrize("change", [
    {"extra": 1}, {"consent": False}, {"provider": "unknown"}, {"model": ""}, {"model": "x\n"},
    {"threshold": .7}, {"threshold": True}, {"examples": 51}, {"examples": False}, {"automatic": "true"},
    {"categories": []}, {"categories": [None]}, {"categories": [{"id": "x"}]},
    *[{"categories": [{"id": "x", "name": "Food", "currency": value}]} for value in ["US", "USDD", None]],
    {"categories": [{"id": "", "name": "Food", "currency": "USD"}]},
    {"categories": [{"id": "x", "name": "", "currency": "USD"}]},
    {"categories": [{"id": "x", "name": "Food", "currency": "USD"}] * 2},
])
def test_settings_are_bounded_and_require_explicit_consent(config, change):
    with pytest.raises(StoreError):
        settings({**config, **change})


def test_profile_limits_names_and_corruption(engine, config, row):
    first = save_profile(engine, config)
    with pytest.raises(StoreError):
        engine.rename(first["id"], " ")
    with pytest.raises(StoreError):
        engine.save({}, "x" * 101)
    raw = engine.context.read_file("profiles.json")
    saved = json.loads(raw)
    for value in ({}, {"active": "unknown", "models": []}, {"active": None, "models": [None]},
                  {"active": None, "models": [saved["models"][0]] * 2},
                  {"active": None, "models": [{**saved["models"][0], "id": "unknown"}]}):
        engine.context.write_file("profiles.json", json.dumps(value).encode())
        with pytest.raises(StoreError):
            engine.index()
    engine.context.write_file("profiles.json", raw)
    for _ in range(19):
        save_profile(engine, config)
    with pytest.raises(StoreError, match="20 saved"):
        engine.save({}, "Too many")
    assert text(None, 10) is False and text("x" * 11, 10) is False
    assert transaction({**row, "merchant": "x" * 300}, "0")["merchant"] == "x" * 200


def test_vault_is_machine_local_atomic_and_namespaced(context, tmp_path, monkeypatch):
    vault = Vault(context)
    assert vault.read("openai") == {}
    vault.clear("openai")
    vault.write("openai", {"api_key": "fictional-secret"})
    refs = context.read_file("connections.json")
    path = vault.path(json.loads(refs)["openai"])
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert b"fictional-secret" not in refs
    assert vault.read("openai")["api_key"] == "fictional-secret"
    vault.write("openai", {"api_key": "replacement"})
    vault.clear("openai")
    assert vault.read("openai") == {}
    for value in ("../secret", "bad-uuid", None):
        assert not identifier(value)
        with pytest.raises(StoreError):
            vault.path(value)
    for raw in (b"[1]", b"bad", b"x" * (1024 * 1024 + 1), None):
        with pytest.raises(StoreError):
            document(raw)
    with pytest.raises(StoreError):
        vault.write("other", {})
    for value in ({"other": json.loads(refs)["openai"]}, {"openai": "invalid"}):
        context.write_file("connections.json", json.dumps(value).encode())
        with pytest.raises(StoreError):
            vault.references()
    context.write_file("connections.json", refs)
    path.symlink_to(tmp_path / "other")
    with pytest.raises(StoreError):
        vault.path(path.stem)
    path.unlink()
    vault.root.rmdir()
    vault.root.symlink_to(tmp_path)
    with pytest.raises(StoreError):
        vault.path(json.loads(refs)["openai"])
    monkeypatch.delenv("LLM_CATEGORIZATION_CREDENTIALS_DIR")
    assert Vault(context).root.name == "llm-categorization"


def test_missing_machine_credentials_and_separate_namespace(context, config):
    other = Engine(context)
    with pytest.raises(StoreError, match="Connect this provider"):
        next(other.train([], config))
