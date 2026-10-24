"""Remote outcome metadata is scoped, bounded, immutable to callers and fail closed."""

import json
from copy import deepcopy
from uuid import uuid4

import pytest

from whab.plugins import RemoteLedgerClient
from whab.plugins.effect_store import EffectError
from whab.plugins.remote_client_adapter import client_record
from whab.plugins.remote_selection import RemoteSelectionStore, command_fingerprint


def initial():
    return {"format": 1, "binding": "a" * 64, "origin_plugin": "client", "original_source": None,
            "choice": {"plugin": "client", "adapter": client_record(RemoteLedgerClient()), "enabled": True, "worker": None},
            "decisions": [], "pending": None, "acknowledged": None}


def command():
    return {"operation": "add_account", "workspace": "personal", "payload": {"name": "Fictional only", "balance": 1},
            "request_id": str(uuid4()), "expected_revision": "fictional-original-revision"}


def test_outcome_metadata_reopens_without_private_command_and_preserves_selection_cas(tmp_path):
    body = command()
    with RemoteSelectionStore(tmp_path, "fictional-budget", "sandbox", initial()) as owner:
        revision = owner.revision(owner.snapshot())
        owner.begin(body)
        assert owner.revision(owner.snapshot()) == revision
        detached = owner.snapshot()
        detached["pending"]["binding"] = "b" * 64
        assert owner.snapshot()["pending"]["binding"] == "a" * 64
        with pytest.raises(EffectError, match="unknown"):
            owner.begin(command())
        with pytest.raises(EffectError):
            owner.acknowledge({**body, "payload": {"name": "different"}}, "next", {})
    raw = (tmp_path / "remote-client/remote-client.json").read_bytes()
    assert b"Fictional only" not in raw
    with RemoteSelectionStore(tmp_path, "fictional-budget", "sandbox") as owner:
        assert owner.snapshot()["pending"]["fingerprint"] == command_fingerprint(body)
        owner.acknowledge(body, "fictional-next-revision", {"name": "Fictional only"})
        assert owner.snapshot()["pending"] is None
        assert owner.revision(owner.snapshot()) == revision
    assert b"Fictional only" not in (tmp_path / "remote-client/remote-client.json").read_bytes()


@pytest.mark.parametrize("mutate", [
    lambda body: body.update(format=True),
    lambda body: body.update(format=2),
    lambda body: body.update(private="unrecognized"),
    lambda body: body.update(binding="x" * 64),
    lambda body: body.update(origin_plugin="../escape"),
    lambda body: body.update(original_source="wrong"),
    lambda body: body["choice"].update(enabled=1),
    lambda body: body["choice"].update(plugin="foreign"),
    lambda body: body["choice"]["adapter"].update(token="must-never-be-read"),
    lambda body: body["choice"].update(worker={"environment": "b" * 64, "distribution": "valid", "version": "../bad"}),
    lambda body: body.update(pending={"request_id": str(uuid4()), "fingerprint": "c" * 64, "expected_revision": "old", "binding": "b" * 64}),
    lambda body: body.update(pending={"request_id": str(uuid4()), "fingerprint": "c" * 64, "expected_revision": "x" * 201, "binding": "a" * 64}),
    lambda body: body.update(acknowledged={"response_revision": "next"}),
    lambda body: body.update(decisions=[{}] * 33),
])
def test_malformed_schema_refused_without_replacing_original_bytes(tmp_path, mutate):
    with RemoteSelectionStore(tmp_path, "fictional-budget", "sandbox", initial()):
        pass
    path = tmp_path / "remote-client/remote-client.json"
    envelope = json.loads(path.read_bytes())
    mutate(envelope["body"])
    raw = json.dumps(envelope).encode()
    path.write_bytes(raw)
    with pytest.raises(EffectError):
        RemoteSelectionStore(tmp_path, "fictional-budget", "sandbox")
    assert path.read_bytes() == raw


def test_wrong_budget_environment_competing_writer_and_symlink_never_adopt_journal(tmp_path):
    with RemoteSelectionStore(tmp_path, "fictional-budget", "sandbox", initial()):
        with pytest.raises(EffectError, match="Another writer"):
            RemoteSelectionStore(tmp_path, "fictional-budget", "sandbox")
    raw = (tmp_path / "remote-client/remote-client.json").read_bytes()
    for budget, environment in (("other-budget", "sandbox"), ("fictional-budget", "production")):
        with pytest.raises(EffectError):
            RemoteSelectionStore(tmp_path, budget, environment)
    assert (tmp_path / "remote-client/remote-client.json").read_bytes() == raw
    alias = tmp_path / "alias"
    alias.mkdir()
    (alias / "remote-client").symlink_to(tmp_path / "remote-client", target_is_directory=True)
    with pytest.raises(EffectError):
        RemoteSelectionStore(alias, "fictional-budget", "sandbox")


def test_decision_exact_acknowledgment_does_not_restore_it_after_later_pause(tmp_path):
    with RemoteSelectionStore(tmp_path, "fictional-budget", "sandbox", initial()) as owner:
        first = {"id": str(uuid4()), "expected_revision": owner.revision(owner.snapshot()), "choice": deepcopy(initial()["choice"])}
        owner.publish(first, first["expected_revision"])
        second = {"id": str(uuid4()), "expected_revision": owner.revision(owner.snapshot()), "choice": {**first["choice"], "enabled": False}}
        owner.publish(second, second["expected_revision"])
        assert owner.publish(first, first["expected_revision"]) == first
        assert not owner.snapshot()["choice"]["enabled"]
        with pytest.raises(EffectError):
            owner.publish({**first, "choice": second["choice"]}, first["expected_revision"])
