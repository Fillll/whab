"""Stable synchronous public storage projection over an approved local worker."""

import base64
from contextlib import contextmanager
from dataclasses import asdict, replace
from threading import RLock
from uuid import uuid4

from whab.domain.errors import StoreError
from whab.domain.file_imports import WorkspaceImport
from whab.domain.transactions import UNSET

from .runtime import PluginError
from .storage_intents import StorageIntents, validate_receipt
from .storage_transfer import (
    OperationResult,
    StorageOperation,
    canonical_storage_json,
    check_storage_operation,
    evidence_record,
    read_evidence,
    read_storage_json,
)
from .storage_worker import StorageWorker, storage_error
from .storage_worker_codec import CONTROL_WRITES, METHODS

_MUTATIONS = {
    "add_account": (("workspace", "account"), {}),
    "update_account": (("workspace", "account_id", "changes"), {}),
    "add_category": (("workspace", "category"), {}),
    "add_transaction": (("workspace", "transaction"), {"source": "manual"}),
    "edit_transaction": (("workspace", "transaction_id"), {"category_id": UNSET, "reviewed": None, "note": UNSET}),
    "review_transactions": (("workspace", "transaction_ids", "reviewed"), {}),
    "assign": (("workspace", "category_id", "month", "assigned"), {}),
    "assign_next_month": (("workspace", "month", "currency", "assigned"), {}),
    "set_rollover_mode": (("workspace", "month", "currency", "mode"), {}),
    "save_item": (("item_id", "encrypted_token", "institution_name"), {"provider_id": "plaid"}),
    "set_item_status": (("item_id", "status", "error"), {}),
    "disconnect_item": (("item_id",), {}),
    "apply_sync": (("item_id", "changes", "cursor"), {"accounts": None, "historical_complete": True, "expected_cursor": UNSET}),
    "import_workspace": (("workspace", "plugin_id", "fingerprint", "data"), {}),
    "restore_budget": (("workspace", "data"), {}),
    "restore_budget_files": (("workspace", "files"), {}),
    "prepare_budget_files": (("workspace", "files"), {"existing": False}),
    "save_receipt": (("workspace", "transaction_id", "raw"), {}),
    "delete_receipt": (("workspace", "transaction_id"), {}),
}


def _plain(value):
    if type(value) is tuple:
        return [_plain(row) for row in value]
    if type(value) is list:
        return [_plain(row) for row in value]
    if type(value) is dict:
        return {key: _plain(row) for key, row in value.items()}
    return value


def operation_payload(name, args, kwargs):
    required, optional = _MUTATIONS[name]
    names = (*required, *optional)
    if len(args) > len(names) or any(key not in names or key in names[:len(args)] for key in kwargs):
        raise StoreError("Invalid storage operation arguments.")
    values = dict(optional) | dict(zip(names, args)) | kwargs
    if any(key not in values for key in required):
        raise StoreError("Invalid storage operation arguments.")
    workspace = values.pop("workspace", "personal")
    if name in {"add_account", "add_category", "add_transaction"}:
        payload = dict(values.pop({"add_account": "account", "add_category": "category", "add_transaction": "transaction"}[name]))
        if name == "add_transaction":
            payload["source"] = values["source"]
    elif name == "update_account":
        payload = {**values["changes"], "accountId": values["account_id"]}
    elif name == "edit_transaction":
        payload = {"transactionId": values["transaction_id"]}
        for source, target in (("category_id", "categoryId"), ("reviewed", "reviewed"), ("note", "note")):
            if values[source] is not UNSET and (source != "reviewed" or values[source] is not None):
                payload[target] = values[source]
    elif name == "import_workspace":
        if type(values["data"]) is not WorkspaceImport:
            raise StoreError("Workspace imports require the public import record.")
        payload = {"pluginId": values["plugin_id"], "fingerprint": values["fingerprint"],
                   "data": _plain(asdict(values["data"].validated_copy()))}
    elif name == "restore_budget":
        payload = {"snapshot": values["data"]}
    elif name in {"restore_budget_files", "prepare_budget_files"}:
        if type(values["files"]) is not dict or any(type(raw) is not bytes for raw in values["files"].values()):
            raise StoreError("Native budget preparation requires bounded file bytes.")
        payload = {"files": {key: base64.b64encode(raw).decode("ascii") for key, raw in values["files"].items()}}
        if name == "prepare_budget_files":
            payload["existing"] = values["existing"]
    elif name == "save_receipt":
        if type(values["raw"]) is not bytes:
            raise StoreError("Receipt storage requires private image bytes.")
        payload = {"transactionId": values["transaction_id"], "image": base64.b64encode(values["raw"]).decode("ascii")}
    else:
        keys = {"account_id": "accountId", "category_id": "categoryId", "transaction_id": "transactionId",
                "transaction_ids": "transactionIds", "item_id": "itemId", "encrypted_token": "encryptedToken",
                "institution_name": "institutionName", "provider_id": "providerId", "historical_complete": "historicalComplete",
                "expected_cursor": "expectedCursor"}
        payload = {keys.get(key, key): _plain(value) for key, value in values.items() if value is not UNSET}
    return workspace, canonical_storage_json(payload)


class StorageWorkerProxy:
    """No backend handle escapes the child; all user writes have native receipts."""

    def __init__(self, worker, context, intent_root, *, intents=None, on_unknown=None, on_create=None):
        self.worker, self.context = worker, context
        self._workers = [worker]
        self.lock = RLock()
        self._scope = None
        self._attempted = None
        self._control_unknown = None
        self._fenced = False
        self._on_unknown = on_unknown
        self.intents = intents
        self._closed = False
        if on_create is not None:
            on_create(self)
        try:
            evidence = worker.open(context)
        except BaseException:
            worker.close()
            raise
        if not context.read_only:
            try:
                self.intents = intents or StorageIntents(intent_root / evidence.instance_id, worker.plugin_id, evidence)
                self.intents.validate_current(evidence)
                self._fenced = self.intents.snapshot()["pending"] is not None
            except BaseException:
                worker.close()
                if self.intents is not None:
                    self.intents.close()
                raise
        self._before = evidence

    @property
    def uncertain(self):
        return self._fenced or self.worker.uncertain

    def _unknown(self):
        self._fenced = True
        if self._on_unknown is not None:
            self._on_unknown(self.worker.plugin_id)

    def _call(self, method, *args, **kwargs):
        try:
            return self.worker.call(method, *args, **kwargs)
        except PluginError as error:
            if error.code != "storage_worker_rejected":
                if method in CONTROL_WRITES:
                    # Migration/copy/promotion own these decisions and their
                    # admission gates. Native transport uncertainty must not
                    # replace their recovery owner with a financial-code fault.
                    self._fenced = True
                    self._control_unknown = method
                else:
                    self._unknown()
            raise

    def _check_write(self):
        if self.context.read_only:
            raise StoreError("Read-only storage cannot accept mutations.")
        if self.uncertain:
            raise storage_error(self.worker.plugin_id, "storage_write_unknown")

    def execute_operation(self, operation):
        with self.lock:
            self._check_write()
            if type(operation) is not StorageOperation:
                raise StoreError("Durable storage requires a validated operation.")
            current = self._call("commit_evidence")
            previous = self._call("operation_receipt", operation.operation_id)
            replay = check_storage_operation(current, operation, previous)
            if replay is not None:
                return replay
            try:
                self._attempted = {"operation_id": operation.operation_id, "fingerprint": operation.fingerprint,
                                   "before": evidence_record(current),
                                   "request": self._scope}
                self.intents.begin(operation, current, self._scope)
                result = self._call("execute_operation", operation)
                if type(result) is not OperationResult:
                    raise StoreError("Invalid native storage operation result.")
                current = self._call("commit_evidence")
                self.intents.finish(result.receipt, current)
                self._attempted = None
                return result
            except PluginError as error:
                if error.code == "storage_worker_rejected":
                    try:
                        self.intents.not_committed(self._call("commit_evidence"))
                        self._attempted = None
                    except Exception:
                        self._unknown()
                    raise
                self._unknown()
                raise
            except BaseException:
                self._unknown()
                raise storage_error(self.worker.plugin_id, "storage_write_unknown") from None

    def _mutate(self, name, args, kwargs):
        # Host command validation remains above this boundary. Native domain
        # validation repeats inside the atomic backend transaction.
        workspace, payload = operation_payload(name, args, kwargs)
        with self.lock:
            self._check_write()
            current = self._call("commit_evidence")
            operation = StorageOperation(str(uuid4()), current.authority_epoch, current.generation, workspace, name, payload)
            result = self.execute_operation(operation)
            return read_storage_json(result.receipt.result)

    def __getattr__(self, name):
        if name in _MUTATIONS:
            return lambda *args, **kwargs: self._mutate(name, args, kwargs)
        if name in METHODS and name in self.worker.methods:
            def invoke(*args, **kwargs):
                with self.lock:
                    if self.worker.uncertain:
                        raise storage_error(self.worker.plugin_id, "storage_write_unknown")
                    return self._call(name, *args, **kwargs)
            return invoke
        raise AttributeError(name)

    def worker_health(self):
        return self.worker.healthy and not self.uncertain

    def worker_cleanup_pending(self):
        return any(worker.cleanup_pending for worker in self._workers)

    def worker_owned(self):
        """A native actor still owns work or process cleanup for this handle."""
        return any(getattr(worker, "_starting", False) or worker.healthy or worker.cleanup_pending
                   for worker in self._workers)

    def worker_recovery_required(self):
        return self.uncertain

    def worker_environment(self):
        prepared = self.prepared_environment
        return {"identifier": prepared.identifier, "distribution": prepared.distribution, "version": prepared.version}

    def durable_command_replay(self, request_id, fingerprint):
        with self.lock:
            if self.intents is None:
                return None
            row = self.intents.request(request_id, fingerprint)
            if row is None:
                return None
            receipt = self._call("operation_receipt", row["operation_id"])
            validate_receipt(row, receipt, self._call("commit_evidence"))
            return {"revision": row["request"]["response_revision"], "data": read_storage_json(receipt.result)}

    @contextmanager
    def durable_command_scope(self, request_id, fingerprint, response_revision):
        with self.lock:
            self._check_write()
            if self._scope is not None:
                raise StoreError("A durable storage command is already active.")
            self.intents.request(request_id, fingerprint)
            self._scope = {"id": request_id, "fingerprint": fingerprint, "response_revision": response_revision}
            try:
                yield
            finally:
                self._scope = None

    def recover_control(self, expected):
        """Reopen a drained authority source; its coordinator decides the role.

        This never reconciles a financial request or resumes admission. The
        authority journal supplies the exact predecision content and identity.
        Only the native role may differ after a lost role-change response.
        """
        with self.lock:
            if (self._control_unknown != "set_role" or self.intents is None
                    or self.intents.snapshot()["pending"] is not None or self._attempted is not None):
                raise storage_error(self.worker.plugin_id, "storage_write_unknown")
            self._fenced = True
            self.worker.quiesce()
            replacement = self._replacement()
            try:
                current = replacement.open(replace(self.context, expected_instance_id=expected.instance_id))
                self.intents.validate_current(current)
                if replace(current, role=expected.role) != expected:
                    raise StoreError("Native authority source changed its retained content.")
                self.worker, self._before = replacement, current
                self._fenced = False
                self._control_unknown = None
                return current
            except BaseException:
                replacement.close()
                raise storage_error(self.worker.plugin_id, "storage_write_unknown") from None

    def recover(self):
        """Explicitly quiesce, reopen exact code, and reconcile without reissuing."""
        with self.lock:
            if self._control_unknown is not None:
                raise storage_error(self.worker.plugin_id, "storage_write_unknown")
            self._fenced = True
            self.worker.quiesce()
            if self.intents is None:
                raise storage_error(self.worker.plugin_id, "storage_write_unknown")
            self.intents.store.recover()
            replacement = self._replacement()
            try:
                current = replacement.open(replace(self.context, expected_instance_id=self._before.instance_id))
                self.intents.validate_current(current)
                pending = self.intents.snapshot()["pending"]
                outcome = "unchanged"
                if pending is not None:
                    receipt = replacement.call("operation_receipt", pending["operation_id"])
                    if receipt is not None:
                        self.intents.finish(receipt, current)
                        outcome = "committed"
                    else:
                        self.intents.not_committed(current)
                        outcome = "not_committed"
                elif self._attempted is not None:
                    receipt = replacement.call("operation_receipt", self._attempted["operation_id"])
                    if receipt is not None:
                        validate_receipt(self._attempted, receipt, current)
                        outcome = "committed"
                    elif current == read_evidence(self._attempted["before"]):
                        outcome = "not_committed"
                    else:
                        raise StoreError("Native storage cannot prove this operation outcome.")
                elif current.instance_id != self._before.instance_id or current.authority_epoch != self._before.authority_epoch:
                    raise StoreError("Native storage changed its approved authority.")
                self.worker = replacement
                self._fenced = False
                self._attempted = None
                return {"outcome": outcome}
            except BaseException:
                replacement.close()
                raise storage_error(self.worker.plugin_id, "storage_write_unknown") from None

    def _replacement(self):
        self._workers = [worker for worker in self._workers if worker.healthy or worker.cleanup_pending]
        return StorageWorker(self.worker.plugin_id, python=self.worker.python, timeout=self.worker.timeout,
                             expected_distribution=self.worker.info["distribution"], expected_version=self.worker.info["version"],
                             on_create=self._workers.append)

    def close(self):
        with self.lock:
            failed = False
            for worker in {id(worker): worker for worker in (*self._workers, self.worker)}.values():
                try:
                    worker.close()
                except Exception:
                    failed = True
                failed = failed or worker.cleanup_pending or worker.healthy
            if failed:
                raise storage_error(self.worker.plugin_id, "storage_cleanup_pending")
            if self.intents is not None:
                self.intents.close()
            self._closed = True
