"""Stopped repair owns all child handles and acknowledges only exact scoped decisions."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from whab.budgets import remote_runtime as module
from whab.plugins import PluginError
from whab.plugins.environments import ApprovedWheel
from whab.plugins.remote_selection import RemoteSelectionStore

from .test_remote_client_adapter import client
from .test_remote_selection import initial

CONFIG = {"CLIENT_URL": "https://fictional.example", "CLIENT_TOKEN": "fictional-" + "a" * 32,
          "CLIENT_BUDGET_ID": "fictional-budget"}


class Child:
    plugin_id = "client"

    def __init__(self):
        self.cleanup_pending = True
        self.refuse = False
        self.calls = 0

    def close(self):
        self.calls += 1
        if not self.refuse:
            self.cleanup_pending = False


def test_stopped_repair_exact_retry_reads_no_artifact_or_code_even_after_later_pause(tmp_path, monkeypatch):
    plugin = client()
    body = initial()
    body["binding"] = module.binding_identity(plugin.storage, CONFIG, "fictional-key", "local-budget", "sandbox")
    with RemoteSelectionStore(tmp_path, "local-budget", "sandbox", body):
        pass
    revision = module.remote_client_revision(tmp_path, "local-budget", "sandbox")
    approval = ApprovedWheel(tmp_path / "reviewed-2.0.0-py3-none-any.whl", "b" * 64)
    child = Child()
    environment = SimpleNamespace(identifier="c" * 64, distribution="reviewed", version="2.0.0")
    calls = []
    def prepare(*args, **kwargs):
        calls.append((args, kwargs))
        return plugin, SimpleNamespace(worker=child, environment=environment)
    monkeypatch.setattr(module, "_prepare_worker", prepare)
    receipt = module.replace_saved_remote_worker(tmp_path, "local-budget", "sandbox", "client", (approval,),
        expected_revision=revision, config=CONFIG, binding_key="fictional-key")
    assert not child.cleanup_pending and len(calls) == 1
    with RemoteSelectionStore(tmp_path, "local-budget", "sandbox") as owner:
        current = owner.snapshot()
        paused = {"id": str(uuid4()), "expected_revision": owner.revision(current),
                  "choice": {**current["choice"], "enabled": False}}
        owner.publish(paused, paused["expected_revision"])
    monkeypatch.setattr(module, "_prepare_worker", lambda *args, **kwargs: pytest.fail("Acknowledgment executed candidate code"))
    assert module.replace_saved_remote_worker(tmp_path, "local-budget", "sandbox", "client", (approval,),
        expected_revision=revision, config={}, binding_key="unused-for-ack") == receipt
    with RemoteSelectionStore(tmp_path, "local-budget", "sandbox") as owner:
        assert not owner.snapshot()["choice"]["enabled"]
    with pytest.raises(PluginError):
        module.replace_saved_remote_worker(tmp_path, "local-budget", "sandbox", "client", (approval,),
            expected_revision="d" * 64, config=CONFIG, binding_key="fictional-key")
    with pytest.raises(Exception):
        module.replace_saved_remote_worker(tmp_path, "other-budget", "sandbox", "client", (approval,),
            expected_revision=revision, config=CONFIG, binding_key="fictional-key")


def test_failed_partial_probe_is_retained_and_blocks_another_prepare_until_cleanup(tmp_path, monkeypatch):
    child, calls = Child(), []
    child.refuse = True
    def failed_prepare(*args, worker_owner=None, **kwargs):
        calls.append(True)
        worker_owner(child)
        raise PluginError("Candidate readiness failed", code="worker_failed", plugin_id="client")
    monkeypatch.setattr(module, "prepare_environment", failed_prepare)
    with pytest.raises(PluginError):
        module._prepare_worker(tmp_path, client(), approvals=())
    assert child.cleanup_pending and child.calls
    with pytest.raises(PluginError) as error:
        module._prepare_worker(tmp_path, client(), approvals=())
    assert error.value.code == "remote_cleanup_pending" and len(calls) == 1
    child.refuse = False
    module._reap(tmp_path)
    assert not child.cleanup_pending
    assert str(tmp_path.resolve()) not in module._CHILDREN
