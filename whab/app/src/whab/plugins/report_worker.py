"""Data-only report worker entry point and bounded parent process adapter.

Only immutable report snapshots cross this boundary. No financial mutation,
router, backend handle, callable or pickle is accepted by this worker protocol.
The caller chooses a Python interpreter from its approved versioned environment.
"""

import os
import selectors
import signal
import subprocess
import sys
from dataclasses import asdict
from importlib import metadata
from threading import Lock
from time import monotonic
from uuid import uuid4

from whab import __version__

from .contracts import contract_for
from .runtime import PluginError, load_plugins
from .sdk import (
    API_VERSION,
    AccountSnapshot,
    BudgetSnapshot,
    CategorySnapshot,
    ExportTable,
    Insight,
    TransactionSnapshot,
    valid_plugin_name,
)
from .wire import HEADER, PROTOCOL_VERSION, Frame, WireError, frame_lengths

_ERRORS = {
    "worker_startup_failed": "The plugin worker could not start or negotiate a compatible protocol.",
    "worker_failed": "The plugin worker failed. The active plugin version must be checked before retrying.",
    "worker_timeout": "The plugin worker timed out. Its report was not completed.",
    "worker_protocol": "The plugin worker returned an invalid response.",
    "worker_hook_failed": "The plugin worker could not generate this report.",
    "worker_closed": "The plugin worker has closed. Start a prepared replacement before using it.",
}


def _error(code, plugin_id):
    return PluginError(_ERRORS[code], code=code, plugin_id=plugin_id)


def _snapshot(value):
    if type(value) is not dict or set(value) != {"workspace", "month", "currency", "accounts", "categories", "transactions"}:
        raise WireError()
    values = dict(value)
    for name, record in (("accounts", AccountSnapshot), ("categories", CategorySnapshot), ("transactions", TransactionSnapshot)):
        if type(values[name]) is not list or any(type(row) is not dict for row in values[name]):
            raise WireError()
        values[name] = tuple(record(**row) for row in values[name])
    return BudgetSnapshot(**values)


def _snapshot_data(snapshot):
    if type(snapshot) is not BudgetSnapshot:
        raise WireError()
    value = {"workspace": snapshot.workspace, "month": snapshot.month, "currency": snapshot.currency}
    for name in ("accounts", "categories", "transactions"):
        value[name] = [asdict(row) for row in getattr(snapshot, name)]
    _snapshot(value)
    return value


def _blocking_read(source, size):
    result = bytearray()
    while len(result) < size:
        part = source.read(size - len(result))
        if not part:
            raise EOFError()
        result.extend(part)
    return bytes(result)


def supports_live_report(plugin):
    """Require an explicit live contract with only stateless report hooks."""
    contract = contract_for(plugin)
    return (
        plugin.contract is not None and contract.lifecycle == "live"
        and (plugin.insights is not None or plugin.export is not None)
        and {capability.name for capability in contract.provides} <= {"reporting.insights", "reporting.export"}
        and contract.state_version == 1 and not contract.state_upgrades
        and all(getattr(plugin, name) is None for name in (
            "bind", "settings", "migrate_state", "budget_connection", "categorization",
            "bank", "bank_ui", "storage", "ui", "bank_logos", "file_import",
            "merchant_logos", "interface",
        ))
    )


def serve(plugin_id, source, target):
    """Worker loop; diagnostics and plugin exception details never enter frames."""
    registry = load_plugins((plugin_id,))
    descriptor = registry.descriptors()[0]
    if descriptor.bind is not None or not (descriptor.insights or descriptor.export):
        raise WireError()
    entries = tuple(entry for entry in metadata.entry_points(group="whab.plugins") if entry.name == plugin_id)
    distribution = entries[0].dist
    package = distribution.metadata["Name"]
    version = distribution.version

    def send(value):
        target.write(Frame(value).encode())
        target.flush()

    send({"kind": "ready", "worker": PROTOCOL_VERSION, "sdk": API_VERSION,
          "plugin": plugin_id, "distribution": package, "version": version,
          "host": __version__, "live": supports_live_report(descriptor),
          "capabilities": [{"name": capability.name, "version": capability.version}
                           for capability in contract_for(descriptor).provides
                           if capability.name in {"reporting.insights", "reporting.export"}],
          "insights": descriptor.insights is not None, "export": descriptor.export is not None})
    while True:
        try:
            header = _blocking_read(source, HEADER.size)
            metadata_size, payload_size = frame_lengths(header)
            frame = Frame.decode(header + _blocking_read(source, metadata_size + payload_size))
        except EOFError:
            return
        request = frame.metadata
        if frame.payload or set(request) != {"id", "operation", "snapshot"} or type(request["id"]) is not str or not 1 <= len(request["id"]) <= 64:
            raise WireError()
        identifier = request["id"]
        if request["operation"] == "shutdown" and request["snapshot"] is None:
            send({"id": identifier, "kind": "closed"})
            return
        if request["operation"] not in {"insights", "export"}:
            raise WireError()
        try:
            snapshot = _snapshot(request["snapshot"])
            if request["operation"] == "insights":
                results = registry.insights(snapshot)
                if descriptor.insights is None or len(results) != 1 or results[0].error:
                    raise WireError()
                output = [asdict(row) for row in results[0].insights]
            else:
                table = registry.export(plugin_id, snapshot)
                output = {"columns": list(table.columns), "rows": [list(row) for row in table.rows]}
            send({"id": identifier, "kind": "result", "data": output})
        except Exception:
            send({"id": identifier, "kind": "error", "code": "hook_failed"})


def _stop_process_group(process):
    """Stop only the session owned by a ``start_new_session`` child.

    A worker or installer can create descendants which outlive its leader.
    Always escalate the owned group, even when the leader exits on TERM.
    The caller retains the process if the bounded reap cannot finish.
    """
    for requested in (signal.SIGTERM, signal.SIGKILL):
        try:
            if os.name == "posix":
                os.killpg(process.pid, requested)
            elif requested == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()
        except ProcessLookupError:
            pass
        if requested == signal.SIGTERM:
            try:
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
    process.wait(timeout=1)


class ReportWorker:
    """Serialized bounded requests to one preinstalled trusted plugin version.

    There is no automatic restart or mutation retry. Startup failure never
    replaces an active worker. Environment provisioning belongs to the host.
    """

    def __init__(self, plugin_id, *, python=sys.executable, timeout=10, expected_distribution=None, expected_version=None):
        if not valid_plugin_name(plugin_id) or type(timeout) not in {int, float} or not 0 < timeout <= 60:
            raise ValueError("Choose a valid report worker and a timeout within 60 seconds.")
        self.plugin_id, self.timeout = plugin_id, timeout
        self._lock = Lock()
        self._process = None
        self._retiring = None
        try:
            password_variable = os.environ.get("WHAB_BUDGET_PASSWORD_ENV")
            env = {key: value for key, value in os.environ.items() if not key.startswith(("WHAB_", "PLAID_", "CLIENT_", "SERVER_")) and key not in {"PYTHONPATH", "TOKEN_ENCRYPTION_KEY", "ALLOW_LIVE_PLAID"} and key != password_variable}
            self._process = subprocess.Popen(
                [str(python), "-I", "-B", "-m", "whab.plugins.report_worker", plugin_id],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=env, bufsize=0, start_new_session=True,
            )
            hello = self._read(timeout)
            if hello.payload or set(hello.metadata) != {"kind", "worker", "sdk", "plugin", "distribution", "version", "host", "live", "capabilities", "insights", "export"}:
                raise WireError()
            value = hello.metadata
            if value["kind"] != "ready" or type(value["worker"]) is not int or value["worker"] != PROTOCOL_VERSION or type(value["sdk"]) is not int or value["sdk"] != API_VERSION or value["plugin"] != plugin_id or type(value["insights"]) is not bool or type(value["export"]) is not bool or not (value["insights"] or value["export"]):
                raise WireError()
            if type(value["distribution"]) is not str or not value["distribution"] or type(value["version"]) is not str or not value["version"]:
                raise WireError()
            expected_capabilities = sorted(
                [{"name": name, "version": 1} for name, enabled in (("reporting.insights", value["insights"]), ("reporting.export", value["export"])) if enabled],
                key=lambda item: item["name"],
            )
            if type(value["host"]) is not str or not value["host"] or type(value["live"]) is not bool or type(value["capabilities"]) is not list or any(type(item) is not dict or set(item) != {"name", "version"} or type(item["version"]) is not int for item in value["capabilities"]):
                raise WireError()
            if sorted(value["capabilities"], key=lambda item: item["name"]) != expected_capabilities:
                raise WireError()
            if expected_distribution is not None and value["distribution"] != expected_distribution or expected_version is not None and value["version"] != expected_version:
                raise WireError()
            self.info = value
        except BaseException as error:
            self._stop()
            if not isinstance(error, Exception):
                raise
            raise _error("worker_startup_failed", plugin_id) from None

    def _write(self, raw, timeout):
        deadline = monotonic() + timeout
        descriptor = self._process.stdin.fileno()
        os.set_blocking(descriptor, False)
        with selectors.DefaultSelector() as selector:
            selector.register(descriptor, selectors.EVENT_WRITE)
            sent = 0
            while sent < len(raw):
                remaining = deadline - monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError()
                try:
                    sent += os.write(descriptor, raw[sent:sent + 64 * 1024])
                except BlockingIOError:
                    continue
        return max(0, deadline - monotonic())

    def _read(self, timeout):
        deadline = monotonic() + timeout
        descriptor = self._process.stdout.fileno()
        with selectors.DefaultSelector() as selector:
            selector.register(descriptor, selectors.EVENT_READ)

            def read(size):
                result = bytearray()
                while len(result) < size:
                    remaining = deadline - monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise TimeoutError()
                    part = os.read(descriptor, min(size - len(result), 64 * 1024))
                    if not part:
                        raise EOFError()
                    result.extend(part)
                return bytes(result)

            header = read(HEADER.size)
            metadata_size, payload_size = frame_lengths(header)
            return Frame.decode(header + read(metadata_size + payload_size))

    def _stop(self):
        process = self._process or self._retiring
        self._process, self._retiring = None, None
        if process is None:
            return
        try:
            _stop_process_group(process)
        except (OSError, subprocess.TimeoutExpired):
            # Retain uncertain cleanup for another close attempt. Never replace
            # an authored startup/RPC failure with process exception details.
            self._retiring = process
        finally:
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except OSError:
                    pass

    @property
    def cleanup_pending(self):
        return self._retiring is not None

    @property
    def healthy(self):
        return self._process is not None and self._process.poll() is None

    def _request(self, operation, snapshot):
        with self._lock:
            if self._process is None:
                raise _error("worker_closed", self.plugin_id)
            identifier = uuid4().hex
            try:
                raw = Frame({"id": identifier, "operation": operation, "snapshot": _snapshot_data(snapshot) if snapshot is not None else None}).encode()
                frame = self._read(self._write(raw, self.timeout))
                value = frame.metadata
                if frame.payload or value.get("id") != identifier:
                    raise WireError()
                if value == {"id": identifier, "kind": "error", "code": "hook_failed"}:
                    raise _error("worker_hook_failed", self.plugin_id)
                if operation == "shutdown":
                    if value != {"id": identifier, "kind": "closed"}:
                        raise WireError()
                    return None
                if set(value) != {"id", "kind", "data"} or value["kind"] != "result":
                    raise WireError()
                return value["data"]
            except PluginError:
                raise
            except TimeoutError:
                self._stop()
                raise _error("worker_timeout", self.plugin_id) from None
            except Exception:
                self._stop()
                raise _error("worker_protocol", self.plugin_id) from None
            except BaseException:
                self._stop()
                raise

    def insights(self, snapshot):
        value = self._request("insights", snapshot)
        try:
            if type(value) is not list or len(value) > 50 or any(type(row) is not dict for row in value):
                raise WireError()
            return tuple(Insight(**row) for row in value)
        except Exception:
            self._stop()
            raise _error("worker_protocol", self.plugin_id) from None

    def export(self, snapshot):
        value = self._request("export", snapshot)
        try:
            if type(value) is not dict or set(value) != {"columns", "rows"} or type(value["columns"]) is not list or type(value["rows"]) is not list or any(type(row) is not list for row in value["rows"]):
                raise WireError()
            return ExportTable(tuple(value["columns"]), tuple(tuple(row) for row in value["rows"]))
        except Exception:
            self._stop()
            raise _error("worker_protocol", self.plugin_id) from None

    def close(self):
        try:
            if self._process is not None:
                self._request("shutdown", None)
        finally:
            self._stop()


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2 or not valid_plugin_name(sys.argv[1]):
            raise WireError()
        serve(sys.argv[1], sys.stdin.buffer, sys.stdout.buffer)
    except Exception:
        sys.exit(1)
