"""Native rejection proof is separate from failures after a returned commit.

The transport is controlled and synchronous; the bundled native backends and
durable host intent journals are real. This is not installed IPC evidence.
"""

from dataclasses import replace

import pytest

from whab.plugins import PluginError, StorageContext, StorageOperation, load_plugins
from whab.plugins.storage import open_storage
from whab.plugins.storage_intents import StorageIntents
from whab.plugins.storage_proxy import StorageWorkerProxy
from whab.plugins.storage_transfer import canonical_storage_json
from whab.plugins.storage_worker import storage_error


class FaultWorker:
    uncertain = False
    cleanup_pending = False
    methods = ("commit_evidence", "operation_receipt")

    def __init__(self, plugin, mode):
        self.plugin, self.plugin_id, self.mode = plugin, plugin.id, mode
        self.dispatched, self.healthy = False, False

    def open(self, context):
        self.backend = open_storage(self.plugin, context)
        self.healthy = True
        return self.backend.commit_evidence()

    def call(self, method, *args, **kwargs):
        if method == "commit_evidence" and self.dispatched and self.mode in {
            "execute-rejected-evidence-unavailable", "result-returned-evidence-rejected",
        }:
            # The real child uses rejected for an unsuccessful nonmutating read.
            raise storage_error(self.plugin_id, "storage_worker_rejected")
        if method == "execute_operation":
            self.dispatched = True
            if self.mode in {"execute-rejected-evidence-unavailable", "execute-rejected-unchanged"}:
                raise storage_error(self.plugin_id, "storage_worker_rejected")
            result = self.backend.execute_operation(*args, **kwargs)
            if self.mode == "execute-rejected-evidence-changed":
                raise storage_error(self.plugin_id, "storage_worker_rejected")
            if self.mode == "result-returned-invalid-receipt":
                return replace(result, receipt=replace(result.receipt, fingerprint="0" * 64))
            return result
        return getattr(self.backend, method)(*args, **kwargs)

    def close(self):
        self.backend.close()
        self.healthy = False


@pytest.mark.parametrize("kind", ["sqlite", "json"])
@pytest.mark.parametrize("mode", [
    "execute-rejected-evidence-unavailable", "execute-rejected-evidence-changed",
    "result-returned-evidence-rejected", "result-returned-finish-rejected",
    "result-returned-invalid-receipt", "intent-published-rejected",
])
def test_unproved_outcome_retains_durable_intent_and_reports_unknown(tmp_path, monkeypatch, kind, mode):
    worker = FaultWorker(load_plugins((kind,)).storage_plugin(kind), mode)
    root = tmp_path / "intents"
    proxy = StorageWorkerProxy(worker, StorageContext(tmp_path / "native", "sandbox", {}), root)
    before = proxy.commit_evidence()
    operation = StorageOperation("fictional-native-request", before.authority_epoch, before.generation,
                                 "personal", "add_account", canonical_storage_json({
                                     "name": "Fictional wallet", "type": "depository", "balance": 125,
                                 }))
    if mode == "result-returned-finish-rejected":
        def rejected(*_):
            raise storage_error(kind, "storage_worker_rejected")
        monkeypatch.setattr(proxy.intents, "finish", rejected)
    if mode == "intent-published-rejected":
        begin = proxy.intents.begin

        def rejected_begin(*args):
            begin(*args)
            raise storage_error(kind, "storage_worker_rejected")
        monkeypatch.setattr(proxy.intents, "begin", rejected_begin)
    try:
        with pytest.raises(PluginError) as raised:
            proxy.execute_operation(operation)
        assert raised.value.code == "storage_write_unknown"
        assert proxy.uncertain is True
        pending = proxy.intents.snapshot()["pending"]
        assert pending["operation_id"] == operation.operation_id
        assert pending["fingerprint"] == operation.fingerprint
        committed = mode not in {"execute-rejected-evidence-unavailable", "intent-published-rejected"}
        assert worker.dispatched is (mode != "intent-published-rejected")
        assert (worker.backend.operation_receipt(operation.operation_id) is not None) is committed
        assert worker.backend.commit_evidence().generation == before.generation + committed
    finally:
        proxy.close()
    retained = StorageIntents(root / before.instance_id, kind, before)
    try:
        assert retained.snapshot()["pending"] == pending
    finally:
        retained.close()


@pytest.mark.parametrize("kind", ["sqlite", "json"])
def test_execute_rejection_with_exact_noncommit_proof_clears_intent_without_fencing(tmp_path, kind):
    worker = FaultWorker(load_plugins((kind,)).storage_plugin(kind), "execute-rejected-unchanged")
    proxy = StorageWorkerProxy(worker, StorageContext(tmp_path / "native", "sandbox", {}), tmp_path / "intents")
    try:
        before = proxy.commit_evidence()
        operation = StorageOperation("fictional-rejected-request", before.authority_epoch, before.generation,
                                     "personal", "add_account", canonical_storage_json({
                                         "name": "Fictional rejected wallet", "type": "depository", "balance": 125,
                                     }))
        with pytest.raises(PluginError) as raised:
            proxy.execute_operation(operation)
        assert raised.value.code == "storage_worker_rejected"
        assert proxy.uncertain is False
        assert proxy.intents.snapshot()["pending"] is None
        assert worker.backend.commit_evidence() == before
        assert worker.backend.operation_receipt(operation.operation_id) is None
    finally:
        proxy.close()
