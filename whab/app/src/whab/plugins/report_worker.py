"""Data-only optional plugin workers with bounded typed parent adapters.

Immutable snapshots, public artwork, bounded imports and UI resource bytes cross
this boundary. No financial mutation, router, backend handle, callable or pickle
is accepted by this worker protocol.
The caller chooses a Python interpreter from its approved versioned environment.
"""

import os
import selectors
import signal
import subprocess
import sys
from dataclasses import asdict, replace
from importlib import metadata
from pathlib import Path
from threading import Lock
from time import monotonic
from uuid import uuid4

from whab import __version__

from .bank_adapter import BankAdapter, ProviderFailure
from .categorization_worker import CategorizationDispatcher
from .contracts import contract_for
from .effect_store import decode, encode
from .process_environment import process_environment
from .provider_worker import MAX_PROVIDER_BYTES, ProviderDispatcher, read_provider_result
from .remote_client_adapter import supports_remote_client
from .runtime import PluginError, load_plugins
from .sdk import (
    API_VERSION,
    AccountSnapshot,
    BudgetSnapshot,
    CategorySnapshot,
    ExportTable,
    Insight,
    LogoImage,
    TransactionSnapshot,
    valid_plugin_name,
)
from .ui_worker import read_html, snapshot_ui
from .wire import HEADER, PROTOCOL_VERSION, Frame, WireError, frame_lengths
from .worker_adapters import (
    BINARY_OPERATIONS,
    LIVE_CAPABILITIES,
    OPERATIONS,
    adapter_info,
    contract_info,
    dispatch,
    read_contract,
    read_import,
    read_settings,
    supports_live_plugin,
    validate_adapter_info,
)
from .worker_namespace import MAX_WORKER_NAMESPACE, namespace_bundle, read_namespace
from .worker_resources import capture_bank_resources, read_resource_bundle, resource_bundle

_ERRORS = {
    "worker_startup_failed": "The plugin worker could not start or negotiate a compatible protocol.",
    "worker_failed": "The plugin worker failed. The active plugin version must be checked before retrying.",
    "worker_timeout": "The plugin worker timed out. Its operation was not completed.",
    "worker_protocol": "The plugin worker returned an invalid response.",
    "worker_hook_failed": "The plugin worker could not complete this operation.",
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


def _read_request(source):
    header = _blocking_read(source, HEADER.size)
    metadata_size, payload_size = frame_lengths(header)
    magic, version, _, _ = HEADER.unpack(header)
    metadata = Frame.decode(HEADER.pack(magic, version, metadata_size, 0) + _blocking_read(source, metadata_size)).metadata
    maximum = {"file.parse": 10 * 1024 * 1024, "category.call": MAX_WORKER_NAMESPACE, "provider.call": MAX_PROVIDER_BYTES}.get(metadata.get("operation"), 0)
    if payload_size > maximum:
        raise WireError()
    return Frame(metadata, _blocking_read(source, payload_size))


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
            "branding_factory",
        ))
    )


def serve(plugin_id, source, target):
    """Worker loop; diagnostics and plugin exception details never enter frames."""
    registry = load_plugins((plugin_id,)).bind_branding()
    descriptor = registry.descriptors()[0]
    if descriptor.bind is not None or not any((descriptor.insights, descriptor.export, descriptor.bank_logos, descriptor.merchant_logos, descriptor.file_import, descriptor.categorization, type(descriptor.bank) is BankAdapter, descriptor.interface and descriptor.interface.adapter, descriptor.ui, supports_remote_client(descriptor))):
        raise WireError()
    if descriptor.ui is not None:
        if not supports_live_plugin(descriptor) or descriptor.ui.render is not None:
            raise WireError()
        descriptor = replace(descriptor, ui=snapshot_ui(descriptor.ui))
    entries = tuple(entry for entry in metadata.entry_points(group="whab.plugins") if entry.name == plugin_id)
    distribution = entries[0].dist
    package = distribution.metadata["Name"]
    version = distribution.version

    def send(value, payload=b""):
        target.write(Frame(value, payload).encode())
        target.flush()

    send({"kind": "ready", "worker": PROTOCOL_VERSION, "sdk": API_VERSION,
          "plugin": plugin_id, "distribution": package, "version": version,
          "host": __version__, "live": supports_live_plugin(descriptor),
          "capabilities": [{"name": capability.name, "version": capability.version}
                           for capability in contract_for(descriptor).provides
                           if capability.name in LIVE_CAPABILITIES
                           and (capability.name not in {'ingestion.bank', 'lifecycle.background'} or type(descriptor.bank) is BankAdapter)],
          "insights": descriptor.insights is not None, "export": descriptor.export is not None,
          "adapters": adapter_info(descriptor), "contract": contract_info(descriptor)})
    categorization = CategorizationDispatcher(descriptor) if descriptor.categorization is not None else None
    provider = ProviderDispatcher(descriptor) if type(descriptor.bank) is BankAdapter else None
    try:
        _serve_requests(registry, descriptor, source, send, categorization, provider)
    finally:
        try:
            if categorization is not None:
                categorization.close()
        finally:
            if provider is not None:
                provider.close()


def _serve_requests(registry, descriptor, source, send, categorization, provider):
    while True:
        try:
            frame = _read_request(source)
        except EOFError:
            return
        request = frame.metadata
        if not {"id", "operation"} <= request.keys() or type(request["id"]) is not str or not 1 <= len(request["id"]) <= 64:
            raise WireError()
        identifier = request["id"]
        operation = request["operation"]
        if operation in {"insights", "export", "shutdown"}:
            if frame.payload or set(request) != {"id", "operation", "snapshot"}:
                raise WireError()
        elif operation not in OPERATIONS or set(request) != {"id", "operation", "arguments"}:
            raise WireError()
        if operation == "shutdown" and request["snapshot"] is None:
            send({"id": identifier, "kind": "closed"})
            return
        if operation == "shutdown":
            raise WireError()
        try:
            if operation == "category.call":
                if categorization is None:
                    raise WireError()
                output, payload = categorization.call(request["arguments"], frame.payload)
                send({"id": identifier, "kind": "result", "data": output}, payload)
                continue
            if operation == 'provider.call':
                if provider is None:
                    raise WireError()
                output, payload = provider.call(request['arguments'], frame.payload)
                send({'id': identifier, 'kind': 'result', 'data': output}, payload)
                continue
            if operation == 'provider.resources':
                if provider is None or request['arguments']:
                    raise WireError()
                resources = capture_bank_resources(descriptor.bank_ui)
                output, payload = resource_bundle(resources)
                send({'id': identifier, 'kind': 'result', 'data': output}, payload)
                continue
            if operation in OPERATIONS:
                output, payload = dispatch(registry, descriptor, operation, request["arguments"], frame.payload)
                send({"id": identifier, "kind": "result", "data": output}, payload)
                continue
            snapshot = _snapshot(request["snapshot"])
            if operation == "insights":
                results = registry.insights(snapshot)
                if descriptor.insights is None or len(results) != 1 or results[0].error:
                    raise WireError()
                output = [asdict(row) for row in results[0].insights]
            else:
                table = registry.export(descriptor.id, snapshot)
                output = {"columns": list(table.columns), "rows": [list(row) for row in table.rows]}
            send({"id": identifier, "kind": "result", "data": output})
        except ProviderFailure as error:
            if operation != 'provider.call':
                raise WireError() from None
            send({'id': identifier, 'kind': 'provider_error', 'code': error.code})
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
        except PermissionError:
            # Some POSIX kernels reject signalling an unreaped exited leader.
            # Reap it, then require proof that its complete group is absent;
            # a live or unobservable group still retains cleanup ownership.
            if process.poll() is None:
                raise
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise
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

    def __init__(self, plugin_id, *, python=sys.executable, timeout=10, expected_distribution=None, expected_version=None,
                 on_create=None):
        if not valid_plugin_name(plugin_id) or type(timeout) not in {int, float} or not 0 < timeout <= 60:
            raise ValueError("Choose a valid report worker and a timeout within 60 seconds.")
        self.plugin_id, self.timeout = plugin_id, timeout
        self._lock = Lock()
        self._process = None
        self._retiring = None
        if on_create is not None:
            on_create(self)
        try:
            self._process = subprocess.Popen(
                [str(python), "-I", "-B", "-m", "whab.plugins.report_worker", plugin_id],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=process_environment(), bufsize=0, start_new_session=True,
            )
            hello = self._read(timeout)
            if hello.payload or set(hello.metadata) != {"kind", "worker", "sdk", "plugin", "distribution", "version", "host", "live", "capabilities", "insights", "export", "adapters", "contract"}:
                raise WireError()
            value = hello.metadata
            if value["kind"] != "ready" or type(value["worker"]) is not int or value["worker"] != PROTOCOL_VERSION or type(value["sdk"]) is not int or value["sdk"] != API_VERSION or value["plugin"] != plugin_id or type(value["insights"]) is not bool or type(value["export"]) is not bool:
                raise WireError()
            adapters = validate_adapter_info(value["adapters"])
            if type(value["distribution"]) is not str or not value["distribution"] or type(value["version"]) is not str or not value["version"]:
                raise WireError()
            expected_capabilities = sorted(
                [{"name": name, "version": 1} for name, enabled in (
                    ("reporting.insights", value["insights"]), ("reporting.export", value["export"]),
                    ("branding.bank", adapters["bank_logos"] is not None),
                    ("branding.merchant", adapters["merchant_logos"]),
                    ("ingestion.file", adapters["file_import"] is not None),
                    ("interface.api", adapters["interface"] is not None),
                    ("categorization", adapters["categorization"]),
                    ("interface.ui", adapters["ui"] is not None),
                    ("ledger.read", adapters["remote_client"] is not None),
                    ("ledger.mutate", adapters["remote_client"] is not None),
                    ('ingestion.bank', adapters['bank'] is not None),
                    ('lifecycle.background', adapters['bank'] is not None),
                ) if enabled],
                key=lambda item: item["name"],
            )
            if type(value["host"]) is not str or not value["host"] or type(value["live"]) is not bool or type(value["capabilities"]) is not list or any(type(item) is not dict or set(item) != {"name", "version"} or type(item["version"]) is not int for item in value["capabilities"]):
                raise WireError()
            if not expected_capabilities or sorted(value["capabilities"], key=lambda item: item["name"]) != expected_capabilities:
                raise WireError()
            if (adapters["interface"] is not None or adapters["ui"] is not None or adapters["categorization"] or adapters["remote_client"] is not None) and value["host"] != __version__:
                raise WireError()
            if adapters["ui"] is not None and (value["insights"] or value["export"]):
                raise WireError()
            self.contract = read_contract(value["contract"], value["capabilities"])
            if adapters["remote_client"] is not None:
                from .remote_client_adapter import read_client_record
                from .sdk import Plugin

                declaration = Plugin(plugin_id, "Remote client declaration",
                                     storage=read_client_record(adapters["remote_client"]), contract=self.contract)
                if not value["live"] or value["insights"] or value["export"] or not supports_remote_client(declaration):
                    raise WireError()
            if value["live"] and (value["contract"]["legacy"] or self.contract.lifecycle != "live" or self.contract.state_version != 1 or self.contract.state_upgrades):
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

    def _read(self, timeout, max_payload=0):
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
            if payload_size > max_payload:
                raise WireError()
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

    def _request(self, operation, snapshot=None, *, arguments=None, payload=b""):
        with self._lock:
            if self._process is None:
                raise _error("worker_closed", self.plugin_id)
            identifier = uuid4().hex
            try:
                request = {"id": identifier, "operation": operation}
                if operation in OPERATIONS:
                    request["arguments"] = {} if arguments is None else arguments
                else:
                    request["snapshot"] = _snapshot_data(snapshot) if snapshot is not None else None
                raw = Frame(request, payload).encode()
                payload_limit = {"bank.fetch": 128 * 1024, "merchant.fetch": 128 * 1024,
                                 "file.resources": 4 * 1024 * 1024, "file.parse": 50 * 1024 * 1024,
                                 "category.call": MAX_WORKER_NAMESPACE, "provider.call": MAX_PROVIDER_BYTES,
                                 "provider.resources": 4 * 1024 * 1024,
                                 "ui.resources": 4 * 1024 * 1024, "ui.render": 16 * 1024 * 1024}.get(operation, 0)
                frame = self._read(self._write(raw, self.timeout), payload_limit)
                value = frame.metadata
                if (frame.payload and operation not in BINARY_OPERATIONS) or value.get("id") != identifier:
                    raise WireError()
                if value == {"id": identifier, "kind": "error", "code": "hook_failed"}:
                    if frame.payload:
                        raise WireError()
                    raise _error("worker_hook_failed", self.plugin_id)
                if operation == 'provider.call' and value.keys() == {'id', 'kind', 'code'} and value['kind'] == 'provider_error':
                    if frame.payload:
                        raise WireError()
                    raise ProviderFailure(value['code'])
                if operation == "shutdown":
                    if value != {"id": identifier, "kind": "closed"}:
                        raise WireError()
                    return None
                if set(value) != {"id", "kind", "data"} or value["kind"] != "result":
                    raise WireError()
                return frame if operation in BINARY_OPERATIONS else value["data"]
            except (PluginError, ProviderFailure):
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
        except BaseException:
            self._stop()
            raise

    def export(self, snapshot):
        value = self._request("export", snapshot)
        try:
            if type(value) is not dict or set(value) != {"columns", "rows"} or type(value["columns"]) is not list or type(value["rows"]) is not list or any(type(row) is not list for row in value["rows"]):
                raise WireError()
            return ExportTable(tuple(value["columns"]), tuple(tuple(row) for row in value["rows"]))
        except Exception:
            self._stop()
            raise _error("worker_protocol", self.plugin_id) from None

    def _typed(self, operation, arguments, decode, payload=b""):
        value = self._request(operation, arguments=arguments, payload=payload)
        try:
            return decode(value)
        except Exception:
            self._stop()
            raise _error("worker_protocol", self.plugin_id) from None
        except BaseException:
            self._stop()
            raise

    def provider(self, command, values):
        try:
            frame = self._request('provider.call', arguments={'command': command}, payload=encode(values, MAX_PROVIDER_BYTES))
            if frame.metadata['data'] != {'provider': 1}:
                raise WireError()
            return read_provider_result(command, decode(frame.payload, MAX_PROVIDER_BYTES), provider_id=self.plugin_id,
                                        environment=values.get('environment'), item_id=values.get('item_id'))
        except ProviderFailure:
            if command in {'exchange_token', 'remove_token'}:
                self._stop()
            raise
        except BaseException:
            # A provider heap may cache an unacknowledged effect or external
            # response. Only a fresh approved child can reconstruct its state.
            self._stop()
            raise

    def provider_resources(self):
        return self._typed('provider.resources', {}, lambda frame: read_resource_bundle(frame.metadata['data'], frame.payload))

    def resolve(self, text, *, domain=False):
        def decode(value):
            if value is not None and not valid_plugin_name(value):
                raise WireError()
            return value
        return self._typed("bank.domain" if domain else "bank.resolve", {"text": text}, decode)

    def fetch(self, key, *, merchant=False):
        def decode(frame):
            value = frame.metadata["data"]
            if type(value) is not dict or set(value) != {"media_type"}:
                raise WireError()
            if value["media_type"] is None:
                if frame.payload:
                    raise WireError()
                return None
            return LogoImage(frame.payload, value["media_type"])
        return self._typed("merchant.fetch" if merchant else "bank.fetch", {"key": key}, decode)

    def configure_cache(self, directory):
        def decode(value):
            if value is not None:
                raise WireError()
        return self._typed("cache.configure", {"directory": str(Path(directory))}, decode)

    def settings(self):
        def decode(value):
            result = read_settings(value)
            if [asdict(row) for row in result["actions"]] != self.info["adapters"]["settings"]["actions"]:
                raise WireError()
            return result
        return self._typed("settings.read", {}, decode)

    def settings_action(self, action):
        def decode(value):
            if type(value) is not str or not 0 < len(value) <= 2000:
                raise WireError()
            return value
        return self._typed("settings.act", {"action": action}, decode)

    def parse(self, raw):
        if type(raw) is not bytes or not 0 < len(raw) <= 10 * 1024 * 1024:
            raise ValueError("Choose an import file no larger than 10 MiB.")

        def decode(frame):
            if frame.metadata["data"] != {"format": "workspace-import-v1"}:
                raise WireError()
            return read_import(frame.payload)
        return self._typed("file.parse", {}, decode, raw)

    def resources(self):
        return self._typed("file.resources", {}, lambda frame: read_resource_bundle(frame.metadata["data"], frame.payload))

    def visual_resources(self):
        return self._typed("ui.resources", {}, lambda frame: read_resource_bundle(frame.metadata["data"], frame.payload))

    def render_ui(self, arguments):
        from .ui_render import read_arguments

        return self._typed("ui.render", read_arguments(arguments), lambda frame: read_html(frame.metadata["data"], frame.payload))

    def interface(self):
        from .interface_adapter import interface_record, read_interface_record

        def decode(value):
            result = read_interface_record(value)
            if interface_record(result) != self.info["adapters"]["interface"]:
                raise WireError()
            return result
        return self._typed("interface.describe", {}, decode)

    def category(self, arguments, snapshot):
        namespace, payload = namespace_bundle(snapshot)

        def decode(frame):
            value = frame.metadata["data"]
            if type(value) is not dict or value.keys() != {"engine", "result", "namespace"}:
                raise WireError()
            token = value["engine"]
            if type(token) is not str or len(token) != 32 or any(character not in "0123456789abcdef" for character in token):
                raise WireError()
            if arguments["command"] != "open" and token != arguments["engine"]:
                raise WireError()
            return token, value["result"], read_namespace(value["namespace"], frame.payload)

        return self._typed("category.call", {**arguments, "namespace": namespace}, decode, payload)

    def remote_client(self):
        from .remote_client_adapter import client_record, read_client_record

        def decode(value):
            result = read_client_record(value)
            if client_record(result) != self.info["adapters"]["remote_client"]:
                raise WireError()
            return result
        return self._typed("client.describe", {}, decode)

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
