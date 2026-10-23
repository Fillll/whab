import json
from copy import deepcopy
from uuid import uuid4

import pytest

from whab.plugins.effect_store import EffectError
from whab.plugins.ui_selection import DISABLED_GENERATION, UISelectionStore


def decision(identifier="fictional-ui", *, generation="a" * 64, worker=True):
    return {"id": str(uuid4()), "selection": identifier, "generation": generation,
            "worker": {"environment": "b" * 64, "distribution": "whab-plugin-fictional-ui", "version": "1.0.0"} if worker else None}


def test_code_selection_and_resource_identity_are_one_durable_decision_with_exact_receipts(tmp_path):
    with UISelectionStore(tmp_path, "fictional-budget", "sandbox", "web") as store:
        revision = store.revision(store.snapshot())
        chosen = decision()
        assert store.publish(chosen, revision) == chosen
        assert store.publish(chosen, revision) == chosen
        changed = deepcopy(chosen)
        changed["generation"] = "c" * 64
        with pytest.raises(EffectError) as error:
            store.publish(changed, revision)
        assert error.value.code == "effect_conflict"
        assert store.snapshot()["worker"] == chosen["worker"]
        assert store.snapshot()["known"] == ["fictional-ui", "web"]
        with pytest.raises(EffectError):
            store.publish(decision(), revision)
    # Initial WHAB_UI is only the bootstrap choice, never a silent fallback.
    with UISelectionStore(tmp_path, "fictional-budget", "sandbox", "other-ui") as reopened:
        assert reopened.snapshot()["selection"] == "fictional-ui"
        disabled = decision(None, generation=DISABLED_GENERATION, worker=False)
        reopened.publish(disabled, reopened.revision(reopened.snapshot()))
        assert reopened.snapshot()["worker"] is None
        assert reopened.publish(chosen, revision) == chosen
        assert reopened.snapshot()["selection"] is None
    with UISelectionStore(tmp_path, "fictional-budget", "sandbox", "web") as reopened:
        assert reopened.snapshot()["generation"] == DISABLED_GENERATION
    with pytest.raises(EffectError):
        UISelectionStore(tmp_path, "other-budget", "sandbox", "web")


@pytest.mark.parametrize("stage", ["before_replace", "after_replace", "after_directory_fsync"])
def test_lost_publication_reply_requires_recovery_and_preserves_exact_decision(tmp_path, stage):
    with UISelectionStore(tmp_path, "fictional", "sandbox", "web") as store:
        original = store.snapshot()
        chosen = decision()
        def fail(boundary):
            if boundary == stage:
                raise OSError("fictional private path must not escape")
        store.store.fault = fail
        with pytest.raises(EffectError) as failure:
            store.publish(chosen, store.revision(original))
        assert "private path" not in str(failure.value)
        store.store.fault = None
        observed = store.recover()
        assert observed["selection"] == ("web" if stage == "before_replace" else "fictional-ui")
        assert store.publish(chosen, store.revision(original)) == chosen
    with UISelectionStore(tmp_path, "fictional", "sandbox", "web") as reopened:
        assert reopened.snapshot()["decisions"][-1] == chosen


def test_corrupt_unknown_schema_duplicate_fields_and_competing_owners_never_reset(tmp_path):
    with UISelectionStore(tmp_path, "fictional", "sandbox", "web") as store:
        with pytest.raises(EffectError) as failure:
            UISelectionStore(tmp_path, "fictional", "sandbox", "web")
        assert failure.value.code == "effect_busy"
        path = store.store.path
    original = path.read_bytes()
    value = json.loads(original)
    value["body"]["format"] = 2
    for corrupted in (b'{"format":1,"format":1}', json.dumps(value).encode(), original[:-1]):
        path.write_bytes(corrupted)
        with pytest.raises(EffectError):
            UISelectionStore(tmp_path, "fictional", "sandbox", "web")
        assert path.read_bytes() == corrupted
    path.write_bytes(original)
    with UISelectionStore(tmp_path, "fictional", "sandbox", "web") as reopened:
        assert reopened.snapshot()["selection"] == "web"


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(extra=True),
    lambda value: value.update(id="not-an-operation"),
    lambda value: value.update(selection="../private"),
    lambda value: value.update(generation=True),
    lambda value: value.update(selection=None),
    lambda value: value["worker"].update(environment="x" * 64),
    lambda value: value["worker"].update(distribution="private/path"),
    lambda value: value["worker"].update(version="invalid version"),
])
def test_malformed_decisions_fail_before_changing_active_selection(tmp_path, mutate):
    with UISelectionStore(tmp_path, "fictional", "sandbox", "web") as store:
        original = store.snapshot()
        value = decision()
        mutate(value)
        with pytest.raises(EffectError):
            store.publish(value, store.revision(original))
        assert store.snapshot() == original
