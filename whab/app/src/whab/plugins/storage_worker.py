"""A separately installed local storage backend behind fixed, private data RPC.

Financial timeouts retain the child and fence the proxy. Explicit recovery must
first quiesce that child, then validate native receipts using the approved code.
An absent response is never interpreted as rollback or automatically repeated.
Installed plugin code remains trusted code, not a security sandbox.
"""

import os
import selectors
import subprocess
import sys
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from threading import RLock
from time import monotonic
from uuid import uuid4

from whab import __version__
from whab.domain.errors import StoreError

from .contracts import Capability, contract_for
from .process_environment import process_environment
from .report_worker import ReportWorker, _blocking_read
from .runtime import PluginError, load_plugins
from .sdk import API_VERSION, BudgetContext, StorageContext, valid_plugin_name
from .storage import close_storage, open_storage
from .storage_transfer import CommitEvidence
from .storage_worker_codec import (
    CONTROL_WRITES,
    HEADER,
    METHODS,
    STORAGE_PROTOCOL,
    bind_call,
    decode,
    encode,
    lengths,
)
from .wire import WireError
from .worker_adapters import contract_info, read_contract

_CAPABILITIES = frozenset({
    "ledger.read", "ledger.mutate", "provider.state", "provider.sync", "receipts",
    "transfer.neutral", "transfer.prepare", "transfer.local", "storage.authority",
    "storage.commands", "storage.replica", "storage.worker",
})


def supports_storage_worker(plugin):
    contract = contract_for(plugin)
    capabilities = {cap.name for cap in contract.provides}
    return (plugin.contract is not None and contract.lifecycle == "live" and contract.authority == "local"
            and {"storage.authority", "storage.commands", "storage.worker"} <= capabilities <= _CAPABILITIES
            and all(cap.version == 1 for cap in contract.provides)
            and plugin.storage is not None and contract.state_version == 1 and not contract.state_upgrades
            and all(getattr(plugin, name) is None for name in (
                "bind", "migrate_state", "settings", "budget_connection", "categorization", "bank", "bank_ui",
                "ui", "interface", "insights", "export", "branding_factory", "bank_logos", "merchant_logos", "file_import",
            )))


def storage_error(plugin_id, code="storage_worker_failed"):
    messages = {
        "storage_worker_failed": "The storage worker is unavailable. Preserve the runtime and check its approved environment.",
        "storage_worker_protocol": "The storage worker returned an invalid or incompatible response.",
        "storage_write_unknown": "The storage write outcome is unknown. This budget is fenced until explicit native-evidence recovery.",
        "storage_worker_rejected": "The storage operation was rejected without a native change. Refresh its validated inputs before retrying.",
        "storage_worker_closed": "The storage worker has closed.",
        "storage_cleanup_pending": "The native storage worker still has pending cleanup. Preserve its runtime and retry cleanup before reopening it.",
    }
    return PluginError(messages[code], code=code, plugin_id=plugin_id)


def _deny(*_):
    raise StoreError("Storage workers do not own host plugin namespaces.")


def _context(value):
    if type(value) is not dict or value.keys() != {"home", "environment", "config", "budget_id", "initial_role", "read_only", "expected_instance_id"}:
        raise WireError()
    if type(value["home"]) is not str or not Path(value["home"]).is_absolute():
        raise WireError()
    from .storage_transfer import storage_identifier
    storage_identifier(value["budget_id"])
    budget = BudgetContext(value["budget_id"], {}, {}, _deny)
    return StorageContext(Path(value["home"]), value["environment"], value["config"], budget,
                          value["initial_role"], value["read_only"], value["expected_instance_id"])


def _read_packet(source):
    header = _blocking_read(source, HEADER.size)
    metadata_size, body_size = lengths(header)
    return decode(header + _blocking_read(source, metadata_size + body_size))


def _writer_owner(context, plugin_id):
    from .effect_store import EffectStore

    def validate(record):
        if type(record) is not dict or record != {"format": 1} or type(record["format"]) is not int:
            raise ValueError()

    # Native SQLite transactions serialize individual commits, but cannot prove
    # that an old child has finished its pending dispatch after its parent died.
    # This retained native-home owner prevents a new writable worker from making
    # an absent-receipt/unchanged-G claim while that child still owns any work.
    return EffectStore(context.home / "plugin-workers" / "native-storage" / plugin_id / context.environment,
                       "storage-writer.json", {"budget_id": context.budget.budget_id if context.budget else "default",
                                                "environment": context.environment, "plugin_id": plugin_id},
                       {"format": 1}, validate, limit=4096)


def serve(plugin_id, source, target):
    registry = load_plugins((plugin_id,))
    descriptor = registry.storage_plugin(plugin_id)
    if descriptor.id != plugin_id or not supports_storage_worker(descriptor):
        raise WireError()
    entries = tuple(entry for entry in metadata.entry_points(group="whab.plugins") if entry.name == plugin_id)
    if len(entries) != 1:
        raise WireError()
    distribution = entries[0].dist
    backend = owner = None

    def send(value):
        target.write(encode(value))
        target.flush()

    send({"kind": "ready", "storage_protocol": STORAGE_PROTOCOL, "sdk": API_VERSION,
          "host": __version__, "plugin": plugin_id, "distribution": distribution.metadata["Name"],
          "version": distribution.version, "contract": contract_info(descriptor),
          "capabilities": [{"name": row.name, "version": row.version, "composition": row.composition} for row in contract_for(descriptor).provides],
          "payload_paths": descriptor.storage_payload_paths})
    try:
        while True:
            try:
                request = _read_packet(source)
            except EOFError:
                return
            if (type(request) is not dict or request.keys() != {"id", "method", "args", "kwargs"}
                    or type(request["id"]) is not str or not 1 <= len(request["id"]) <= 64):
                raise WireError()
            identifier, method = request["id"], request["method"]
            mutating = method == "execute_operation" or method in CONTROL_WRITES
            before = None
            try:
                if method == "open":
                    if backend is not None or owner is not None or request["args"] or type(request["kwargs"]) is not dict:
                        raise WireError()
                    context = _context(request["kwargs"])
                    if not context.read_only:
                        owner = _writer_owner(context, plugin_id)
                    if context.expected_instance_id is not None and not context.read_only:
                        from dataclasses import replace
                        reader = open_storage(descriptor, replace(context, read_only=True))
                        try:
                            if reader.commit_evidence().instance_id != context.expected_instance_id:
                                raise StoreError("The native instance does not match its explicit host selection.")
                        finally:
                            reader.close()
                    backend = open_storage(descriptor, context)
                    if context.expected_instance_id is not None and backend.commit_evidence().instance_id != context.expected_instance_id:
                        raise StoreError("The native instance does not match its explicit host selection.")
                    result = {"evidence": backend.commit_evidence(),
                              "methods": tuple(name for name in METHODS if callable(getattr(backend, name, None)))}
                elif method == "close":
                    if request["args"] or request["kwargs"]:
                        raise WireError()
                    if backend is not None:
                        backend.close()
                        backend = None
                    if owner is not None:
                        owner.close()
                        owner = None
                    send({"id": identifier, "kind": "result", "data": None})
                    return
                else:
                    if backend is None:
                        raise WireError()
                    if owner is not None:
                        owner.snapshot()
                    arguments = bind_call(method, request["args"], request["kwargs"])
                    if mutating:
                        before = backend.commit_evidence()
                    implementation = getattr(backend, method, None)
                    if not callable(implementation):
                        raise StoreError("Unsupported storage method.")
                    result = implementation(**arguments)
                    if owner is not None:
                        owner.snapshot()
                send({"id": identifier, "kind": "result", "data": result})
            except Exception:
                code = "rejected"
                if mutating:
                    # This is evidence after the completed synchronous call on
                    # the sole child, not an inference from a missing receipt.
                    try:
                        if before is None or backend.commit_evidence() != before:
                            code = "unknown"
                    except Exception:
                        code = "unknown"
                send({"id": identifier, "kind": "error", "code": code})
    finally:
        try:
            if backend is not None:
                close_storage(backend)
        finally:
            if owner is not None:
                owner.close()


class StorageWorker:
    """Serialized transport retaining uncertain native work until explicit recovery."""

    _write = ReportWorker._write
    _stop = ReportWorker._stop
    healthy = ReportWorker.healthy
    cleanup_pending = ReportWorker.cleanup_pending

    def __init__(self, plugin_id, *, python=sys.executable, timeout=30,
                 expected_distribution=None, expected_version=None, on_create=None):
        if not valid_plugin_name(plugin_id) or type(timeout) not in {int, float} or not 0 < timeout <= 60:
            raise ValueError("Choose a storage worker and a timeout within 60 seconds.")
        self.plugin_id, self.timeout, self.python = plugin_id, timeout, python
        self._lock = RLock()
        self._process = self._retiring = None
        self._unknown = False
        self._received = bytearray()
        self._starting = True
        if on_create is not None:
            on_create(self)
        try:
            self._process = subprocess.Popen(
                [str(python), "-I", "-B", "-m", "whab.plugins.storage_worker", plugin_id],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=process_environment(), bufsize=0, start_new_session=True,
            )
            hello = self._read(timeout)
            if type(hello) is not dict or hello.keys() != {
                "kind", "storage_protocol", "sdk", "host", "plugin", "distribution", "version", "contract", "capabilities", "payload_paths",
            }:
                raise WireError()
            if (hello["kind"] != "ready" or type(hello["storage_protocol"]) is not int
                    or hello["storage_protocol"] != STORAGE_PROTOCOL or hello["sdk"] != API_VERSION
                    or type(hello["sdk"]) is not int or hello["host"] != __version__ or hello["plugin"] != plugin_id
                    or type(hello["distribution"]) is not str or not hello["distribution"]
                    or type(hello["version"]) is not str or not hello["version"]):
                raise WireError()
            capabilities = hello["capabilities"]
            if (type(capabilities) is not list or not 1 <= len(capabilities) <= 100
                    or any(type(row) is not dict or row.keys() != {"name", "version", "composition"}
                           for row in capabilities)):
                raise WireError()
            # Native storage carries the declared composition explicitly. The
            # optional-worker protocol retains its original two-field records.
            contract = read_contract(hello["contract"], [])
            self.contract = replace(contract, provides=tuple(Capability(**row) for row in capabilities))
            if (self.contract.lifecycle != "live" or self.contract.authority != "local"
                    or hello["contract"]["legacy"] or self.contract.state_version != 1 or self.contract.state_upgrades
                    or not {"storage.worker", "storage.authority", "storage.commands"} <= {row.name for row in self.contract.provides}
                    or not {row.name for row in self.contract.provides} <= _CAPABILITIES
                    or any(row.version != 1 for row in self.contract.provides)):
                raise WireError()
            if (expected_distribution is not None and hello["distribution"] != expected_distribution
                    or expected_version is not None and hello["version"] != expected_version):
                raise WireError()
            self.info = hello
        except BaseException as error:
            self._stop()
            if not isinstance(error, Exception):
                raise
            raise storage_error(plugin_id) from None
        finally:
            self._starting = False

    def _read(self, timeout):
        deadline = monotonic() + timeout
        descriptor = self._process.stdout.fileno()
        with selectors.DefaultSelector() as selector:
            selector.register(descriptor, selectors.EVENT_READ)

            def receive(size):
                while len(self._received) < size:
                    remaining = deadline - monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise TimeoutError()
                    part = os.read(descriptor, min(size - len(self._received), 64 * 1024))
                    if not part:
                        raise EOFError()
                    self._received.extend(part)

            receive(HEADER.size)
            metadata_size, body_size = lengths(bytes(self._received[:HEADER.size]))
            size = HEADER.size + metadata_size + body_size
            receive(size)
            raw = bytes(self._received[:size])
            del self._received[:size]
            return decode(raw)

    @property
    def uncertain(self):
        return self._unknown

    def call(self, method, *args, **kwargs):
        mutating = method == "execute_operation" or method in CONTROL_WRITES
        if method not in {"open", "close"}:
            bind_call(method, args, kwargs)
        # Serialize/validate BEFORE entering the possibly-written interval.
        identifier = uuid4().hex
        raw = encode({"id": identifier, "method": method, "args": args, "kwargs": kwargs})
        with self._lock:
            if self._unknown:
                raise storage_error(self.plugin_id, "storage_write_unknown")
            if self._process is None:
                raise storage_error(self.plugin_id, "storage_worker_closed")
            try:
                result = self._read(self._write(raw, self.timeout))
                if type(result) is not dict or result.get("id") != identifier:
                    raise WireError()
                if result == {"id": identifier, "kind": "error", "code": "unknown"}:
                    self._unknown = True
                    raise storage_error(self.plugin_id, "storage_write_unknown")
                if result == {"id": identifier, "kind": "error", "code": "rejected"}:
                    raise storage_error(self.plugin_id, "storage_worker_rejected")
                if result.keys() != {"id", "kind", "data"} or result["kind"] != "result":
                    raise WireError()
                return result["data"]
            except PluginError:
                raise
            except BaseException as error:
                if mutating:
                    self._unknown = True
                else:
                    self._stop()
                if not isinstance(error, Exception):
                    raise
                raise storage_error(self.plugin_id, "storage_write_unknown" if mutating else "storage_worker_protocol") from None

    def open(self, context):
        if type(context) is not StorageContext:
            raise ValueError("Storage workers require a validated storage context.")
        value = self.call("open", home=str(context.home.absolute()), environment=context.environment,
                          config=dict(context.config), budget_id=context.budget.budget_id if context.budget else "default",
                          initial_role=context.initial_role, read_only=context.read_only,
                          expected_instance_id=context.expected_instance_id)
        if (type(value) is not dict or value.keys() != {"evidence", "methods"}
                or type(value["evidence"]) is not CommitEvidence or type(value["methods"]) is not tuple
                or not {"commit_evidence", "operation_receipt", "execute_operation"} <= set(value["methods"])
                or any(name not in METHODS for name in value["methods"])
                or len(set(value["methods"])) != len(value["methods"])):
            self._stop()
            raise storage_error(self.plugin_id, "storage_worker_protocol")
        evidence = value["evidence"]
        if evidence.budget_id != (context.budget.budget_id if context.budget else "default") or evidence.environment != context.environment:
            self._stop()
            raise storage_error(self.plugin_id, "storage_worker_protocol")
        self.methods = frozenset(value["methods"])
        return evidence

    def quiesce(self):
        """Explicit recovery/shutdown: reap the entire child before native reopen.

        Termination establishes no financial outcome. The caller must still
        resolve the durable operation against the approved native backend.
        """
        with self._lock:
            # Reap a leader which has already exited before escalating its
            # owned group. A retained cleanup attempt is retried once, bounded;
            # no native backend may reopen while that ownership is unresolved.
            process = self._process or self._retiring
            if process is not None:
                process.poll()
            self._stop()
            if self.cleanup_pending:
                self._stop()
            if self.cleanup_pending:
                raise storage_error(self.plugin_id, "storage_write_unknown")

    def close(self):
        with self._lock:
            if self._unknown:
                self.quiesce()
                return
            try:
                if self._process is not None:
                    self.call("close")
            finally:
                self._stop()
            if self.cleanup_pending:
                raise storage_error(self.plugin_id, "storage_cleanup_pending")


if __name__ == "__main__":
    try:
        serve(sys.argv[1], sys.stdin.buffer, sys.stdout.buffer)
    except Exception:
        raise SystemExit(1) from None
