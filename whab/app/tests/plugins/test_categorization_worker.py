"""Detached categorizer transport keeps models and continuation objects local."""

import json

import pytest

from whab.plugins import Categorization, Plugin, PluginContract
from whab.plugins.categorization_worker import CategorizationDispatcher, TrainedModelToken
from whab.plugins.namespace_effects import NamespaceSnapshot
from whab.plugins.wire import WireError
from whab.plugins.worker_namespace import namespace_bundle, read_namespace


class Engine:
    defaults = {"trees": 2, "threshold": 0.8}
    instances = []

    def __init__(self, context):
        self.context, self.closed = context, 0
        self.instances.append(self)

    def index(self):
        raw = self.context.read_file("models.json")
        return json.loads(raw) if raw else {"models": [], "active": None}

    def train(self, rows, parameters):
        yield {"stage": "Reviewed synthetic input", "progress": 50}
        return {"privateVocabulary": "fictional private training material", "id": "synthetic-model"}

    def save(self, model, name):
        self.context.write_file("model.bin", model["privateVocabulary"].encode())
        self.context.write_file("models.json", json.dumps({"models": [{"id": model["id"], "name": name}], "active": None}).encode())
        return {"id": model["id"], "name": name}

    def select(self, identifier):
        self.context.write_file("models.json", json.dumps({"models": [], "active": identifier}).encode())

    def rename(self, identifier, name):
        self.context.write_file("renamed", name.encode())

    def delete(self, identifier):
        self.context.delete_file("model.bin")

    def predict(self, rows):
        return []

    def preview(self, rows):
        return []

    def close(self):
        self.closed += 1


def descriptor(factory=Engine):
    return Plugin("fictional-categorizer", "Fictional", categorization=Categorization(factory), contract=PluginContract(lifecycle="live"))


def call(dispatcher, snapshot, command, values=None, *, token=None, workspace="personal"):
    metadata, payload = namespace_bundle(snapshot)
    output, raw = dispatcher.call({"engine": token, "command": command, "values": values or {}, "namespace": metadata,
                                   "context": {"budget": "fictional-budget", "workspace": workspace, "settings": {}} if command == "open" else None}, payload)
    return output, read_namespace(output["namespace"], raw)


def test_opaque_training_model_and_complete_save_effect_preserve_unknown_files():
    dispatcher = CategorizationDispatcher(descriptor())
    original = NamespaceSnapshot({}, {"unknown.bin": b"retain exact unrelated namespace bytes"})
    opened, snapshot = call(dispatcher, original, "open")
    token = opened["engine"]
    assert opened["result"] == {"defaults": Engine.defaults, "preview": True}
    assert snapshot.digest == original.digest
    call(dispatcher, snapshot, "train", {"rows": [], "parameters": {}}, token=token)
    progress, snapshot = call(dispatcher, snapshot, "step", token=token)
    assert progress["result"]["complete"] is False and progress["result"]["progress"]["progress"] == 50
    completed, snapshot = call(dispatcher, snapshot, "step", token=token)
    model = TrainedModelToken(completed["result"]["model"])
    assert "fictional private" not in repr(model) and "fictional private" not in repr(completed["result"])
    saved, changed = call(dispatcher, snapshot, "save", {"model": model.identifier, "name": "Fictional reviewed model"}, token=token)
    assert saved["result"] == {"id": "synthetic-model", "name": "Fictional reviewed model"}
    assert changed.files["unknown.bin"] == original.files["unknown.bin"]
    assert set(changed.files) == {"unknown.bin", "model.bin", "models.json"}
    assert snapshot.digest == original.digest
    call(dispatcher, changed, "release", {"model": model.identifier}, token=token)
    with pytest.raises(WireError):
        call(dispatcher, changed, "save", {"model": model.identifier, "name": "reused"}, token=token)
    call(dispatcher, changed, "close", token=token)
    assert not dispatcher.engines


def test_two_workspace_engines_read_latest_namespace_and_keep_file_scopes_separate():
    dispatcher = CategorizationDispatcher(descriptor())
    initial = NamespaceSnapshot({}, {"models.json": b'{"models":[],"active":"personal"}',
                                     "demo/models.json": b'{"models":[],"active":"demo"}'})
    personal, _ = call(dispatcher, initial, "open")
    demo, _ = call(dispatcher, initial, "open", workspace="demo")
    assert call(dispatcher, initial, "index", token=personal["engine"])[0]["result"]["active"] == "personal"
    assert call(dispatcher, initial, "index", token=demo["engine"])[0]["result"]["active"] == "demo"
    _, changed = call(dispatcher, initial, "select", {"id": "new-demo"}, token=demo["engine"])
    assert changed.files["models.json"] == initial.files["models.json"]
    assert call(dispatcher, changed, "index", token=demo["engine"])[0]["result"]["active"] == "new-demo"
    with pytest.raises(WireError):
        call(dispatcher, initial, "open")
    dispatcher.close()


def test_readiness_and_read_only_commands_cannot_persist_even_one_file():
    class BadStartup(Engine):
        def __init__(self, context):
            super().__init__(context)
            context.write_file("bad", b"must not publish")

    dispatcher = CategorizationDispatcher(descriptor(BadStartup))
    original = NamespaceSnapshot({}, {"unknown": b"preserved"})
    with pytest.raises(ValueError):
        call(dispatcher, original, "open")
    assert not dispatcher.engines

    class BadIndex(Engine):
        def index(self):
            self.context.write_file("bad", b"readiness cannot persist")

    dispatcher = CategorizationDispatcher(descriptor(BadIndex))
    opened, _ = call(dispatcher, original, "open")
    with pytest.raises(ValueError):
        call(dispatcher, original, "index", token=opened["engine"])
    assert original.files == {"unknown": b"preserved"}
    dispatcher.close()


def test_partial_child_save_failure_returns_no_effect_and_closes_delayed_callbacks():
    class FailedSave(Engine):
        def rename(self, identifier, name):
            self.context.write_file("first", b"staged only")
            raise ValueError("fictional private implementation detail")

    dispatcher = CategorizationDispatcher(descriptor(FailedSave))
    original = NamespaceSnapshot({}, {"unknown": b"preserved"})
    opened, _ = call(dispatcher, original, "open")
    with pytest.raises(ValueError):
        call(dispatcher, original, "rename", {"id": "model", "name": "name"}, token=opened["engine"])
    engine = Engine.instances[-1]
    with pytest.raises(WireError):
        engine.context.write_file("late", b"cannot escape an RPC")
    assert original.files == {"unknown": b"preserved"}
    dispatcher.close()


@pytest.mark.parametrize("mutation", ("hash", "size", "duplicate", "path", "extra", "state"))
def test_namespace_frame_rejects_invalid_bounds_hashes_paths_and_state(mutation):
    snapshot = NamespaceSnapshot({}, {"model": b"synthetic model"})
    metadata, payload = namespace_bundle(snapshot)
    if mutation == "hash":
        metadata["files"][0]["sha256"] = "0" * 64
    elif mutation == "size":
        metadata["files"][0]["size"] += 1
    elif mutation == "duplicate":
        metadata["files"] *= 2
    elif mutation == "path":
        metadata["files"][0]["name"] = "../outside"
    elif mutation == "state":
        metadata["state"] = {"accessToken": "forbidden"}
    else:
        payload += b"unclaimed"
    with pytest.raises(WireError):
        read_namespace(metadata, payload)


def test_engine_token_cannot_cross_child_instances_and_oversized_training_is_rejected():
    original = NamespaceSnapshot({}, {})
    dispatcher = CategorizationDispatcher(descriptor())
    opened, _ = call(dispatcher, original, "open")
    other = CategorizationDispatcher(descriptor())
    with pytest.raises(WireError):
        call(other, original, "index", token=opened["engine"])
    with pytest.raises(WireError):
        call(dispatcher, original, "train", {"rows": [{}] * 20001, "parameters": {}}, token=opened["engine"])
    dispatcher.close()


def test_malformed_engine_cleanup_runs_and_invalid_model_tokens_refuse():
    class Malformed(Engine):
        predict = None

    dispatcher = CategorizationDispatcher(descriptor(Malformed))
    with pytest.raises(WireError):
        call(dispatcher, NamespaceSnapshot({}, {}), "open")
    assert Engine.instances[-1].closed == 1
    with pytest.raises(WireError):
        TrainedModelToken("unsafe-model")


@pytest.mark.parametrize('progress', [
    {'stage': 'Model', 'progress': True}, {'stage': 'Model', 'progress': 101},
    {'stage': '', 'progress': 50}, {'stage': 'x' * 201, 'progress': 50},
    {'stage': 'Model', 'progress': 50, 'privateModel': 'must not enter public progress'},
])
def test_progress_has_exact_bounded_public_fields(progress):
    class BadProgress(Engine):
        def train(self, rows, parameters):
            yield progress
    dispatcher = CategorizationDispatcher(descriptor(BadProgress))
    before = NamespaceSnapshot({}, {})
    opened, _ = call(dispatcher, before, 'open')
    call(dispatcher, before, 'train', {'rows': [], 'parameters': {}}, token=opened['engine'])
    with pytest.raises(WireError):
        call(dispatcher, before, 'step', token=opened['engine'])
    call(dispatcher, before, 'cancel', token=opened['engine'])
    dispatcher.close()
