"""Bounded report workers use fictional snapshots and reviewed subprocess fixtures."""

import io
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, replace
from importlib import metadata
from types import SimpleNamespace

import pytest

from whab.plugins import (
    AccountSnapshot,
    BudgetSnapshot,
    Capability,
    CategorySnapshot,
    Plugin,
    PluginContract,
    PluginError,
    PluginRegistry,
    PluginSettings,
    TransactionSnapshot,
    report_worker,
)
from whab.plugins.report_worker import ReportWorker, serve
from whab.plugins.wire import (
    HEADER,
    MAX_METADATA_BYTES,
    MAX_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
    Frame,
    WireError,
    frame_lengths,
)

_POPEN = subprocess.Popen


@pytest.fixture
def worker_snapshot():
    return BudgetSnapshot("personal", "2026-10", (
        AccountSnapshot("wallet", "Fictional wallet", "depository", "checking", 10000),
    ), (CategorySnapshot("food", "Food", "Everyday", 1000, 125, 875),), (
        TransactionSnapshot("purchase", "2026-10-01", "Fictional merchant", 125, "wallet", "food"),
    ))


def raw_frame(metadata, *, payload=b"", version=PROTOCOL_VERSION, magic=b"WHAB"):
    return HEADER.pack(magic, version, len(metadata), len(payload)) + metadata + payload


def decode_sequence(raw):
    frames = []
    while raw:
        metadata_size, payload_size = frame_lengths(raw[:HEADER.size])
        length = HEADER.size + metadata_size + payload_size
        frames.append(Frame.decode(raw[:length]))
        raw = raw[length:]
    return frames


def test_strict_data_frames_roundtrip_binary_without_disclosing_payloads():
    frame = Frame({"id": "fixture", "flags": [True, False, None], "signed": -(2**53 - 1), "score": 0.5}, b"\x00\xfffictional-private-payload")
    assert Frame.decode(frame.encode()) == frame
    assert "fictional-private-payload" not in repr(frame)
    assert frame_lengths(frame.encode()[:HEADER.size]) == (len(frame.encode()) - HEADER.size - len(frame.payload), len(frame.payload))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), 2**53, -(2**53), 2**53 * 1.0,
                                  b"binary-in-json", ("tuple",), {"set"}, object(), "nul\x00text", "\ud800", "a" * 100001])
def test_encoder_rejects_unsupported_nonfinite_oversized_and_nonunicode_values(value):
    with pytest.raises(WireError):
        Frame({"value": value}).encode()


@pytest.mark.parametrize("metadata", [[], None, {"": 1}, {1: "value"}, {"k" * 201: 1}, {"nested": {"value": [object()]}}])
def test_encoder_rejects_invalid_metadata_shapes(metadata):
    with pytest.raises(WireError):
        Frame(metadata).encode()


def test_encoder_checks_depth_collection_size_and_exact_binary_types(monkeypatch):
    nested = None
    for _ in range(18):
        nested = [nested]
    for value in (nested, [0] * 100001, {str(index): 0 for index in range(100001)}):
        with pytest.raises(WireError):
            Frame({"value": value}).encode()
    with pytest.raises(WireError):
        Frame({}, bytearray(b"binary")).encode()
    monkeypatch.setattr("whab.plugins.wire.MAX_METADATA_BYTES", 8)
    with pytest.raises(WireError):
        Frame({"value": "too large"}).encode()
    monkeypatch.setattr("whab.plugins.wire.MAX_PAYLOAD_BYTES", 2)
    with pytest.raises(WireError):
        Frame({}, b"123").encode()


@pytest.mark.parametrize("raw", [
    b"", b"WHAB", bytearray(b"WHAB"), raw_frame(b"{}", magic=b"NOPE"),
    raw_frame(b"{}", version=PROTOCOL_VERSION + 1), raw_frame(b"{}") + b"trailing",
    raw_frame(b"{}", payload=b"binary")[:-1],
    HEADER.pack(b"WHAB", PROTOCOL_VERSION, MAX_METADATA_BYTES + 1, 0),
    HEADER.pack(b"WHAB", PROTOCOL_VERSION, 2, MAX_PAYLOAD_BYTES + 1),
    HEADER.pack(b"WHAB", PROTOCOL_VERSION, 1, 0),
    raw_frame(b'[]'), raw_frame(b'{"key":1,"key":2}'), raw_frame(b'{"key":{"x":1,"x":2}}'),
    raw_frame(b'{"value":NaN}'), raw_frame(b'{"value":Infinity}'), raw_frame(b'{"value":9007199254740992}'),
    raw_frame(b'{"value":"\xff"}'), raw_frame(b'{"value":'),
    raw_frame(b'{"value":' + b'[' * 1000 + b'0' + b']' * 1000 + b'}'),
])
def test_decoder_rejects_malformed_headers_lengths_json_and_duplicate_fields(raw):
    with pytest.raises(WireError) as raised:
        Frame.decode(raw)
    assert "invalid or incompatible frame" in str(raised.value)


def test_snapshot_boundary_rejects_floats_booleans_unknown_fields_and_raw_objects(worker_snapshot):
    valid = asdict(worker_snapshot)
    for name in ("accounts", "categories", "transactions"):
        valid[name] = [dict(row) for row in valid[name]]
    for value in (1.25, True, "125"):
        malformed = json.loads(json.dumps(valid))
        malformed["transactions"][0]["amount_cents"] = value
        with pytest.raises(ValueError):
            report_worker._snapshot(malformed)
    with pytest.raises(WireError):
        report_worker._snapshot({**valid, "backend": "forbidden"})
    with pytest.raises(WireError):
        report_worker._snapshot({**valid, "accounts": [object()]})
    assert report_worker._snapshot(valid) == worker_snapshot


_FIXTURE_WORKER = """
import json
import os
import sys
from whab.plugins.wire import Frame, HEADER, PROTOCOL_VERSION, frame_lengths
from whab import __version__

mode = MODE
if mode.startswith(('child-close:', 'child-timeout:')):
    import subprocess
    child_program = 'import os,pathlib,signal,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)'
    subprocess.Popen([sys.executable, '-I', '-c', child_program, mode.split(':',1)[1]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
def send(metadata, payload=b''):
    sys.stdout.buffer.write(Frame(metadata, payload).encode())
    sys.stdout.buffer.flush()
if mode == 'startup-crash':
    sys.stderr.write('synthetic-private-diagnostic')
    raise SystemExit(7)
if mode == 'startup-timeout':
    sys.stdin.buffer.read()
    raise SystemExit(0)
hello = {'kind':'ready','worker':PROTOCOL_VERSION,'sdk':1,'plugin':'fixture','distribution':'fixture-package','version':'1.0.0','host':__version__,'live':True,'capabilities':[{'name':'reporting.insights','version':1},{'name':'reporting.export','version':1}],'insights':True,'export':True,
         'adapters':{'bank_logos':None,'merchant_logos':False,'file_import':None,'settings':None,'interface':None,'ui':None,'categorization':False,'remote_client':None},
         'contract':{'requires':[],'conflicts':[],'authority':'local','state_version':1,'state_upgrades':[],'lifecycle':'live','legacy':False}}
if mode.startswith('hello:'):
    hello.update(json.loads(mode.removeprefix('hello:')))
send(hello)
if mode == 'no-read':
    import signal
    signal.pause()
while True:
    header = sys.stdin.buffer.read(HEADER.size)
    if not header:
        break
    metadata_size, payload_size = frame_lengths(header)
    request = Frame.decode(header + sys.stdin.buffer.read(metadata_size + payload_size)).metadata
    identifier = request['id']
    if mode == 'oversized-logo':
        from whab.plugins.wire import PROTOCOL_VERSION
        sys.stdout.buffer.write(HEADER.pack(b'WHAB',PROTOCOL_VERSION,2,128*1024+1)+b'{}')
        sys.stdout.buffer.flush()
        sys.stdin.buffer.read()
        break
    if request['operation'] == 'shutdown':
        if mode == 'shutdown-timeout':
            sys.stdin.buffer.read()
            break
        if mode == 'shutdown-result':
            send({'id':identifier,'kind':'result','data':[]})
            continue
        send({'id':identifier,'kind':'closed'})
        break
    if mode == 'crash':
        os._exit(7)
    if mode == 'timeout' or mode.startswith('child-timeout:'):
        sys.stdin.buffer.read()
        break
    if mode == 'bad-frame':
        sys.stdout.buffer.write(b'INVALID-FRAME!')
        sys.stdout.buffer.flush()
        break
    if mode == 'hook-error':
        sys.stderr.write('synthetic-private-diagnostic')
        send({'id':identifier,'kind':'error','code':'hook_failed'})
        continue
    response = {'id':identifier,'kind':'result','data':[{'title':'Fictional','value':'1','detail':''}]}
    payload = b''
    if mode == 'wrong-id':
        response['id'] = 'different-request'
    elif mode == 'unknown-field':
        response['unknown'] = 'unexpected'
    elif mode == 'payload':
        payload = b'synthetic-private-payload'
    elif mode == 'insights-type':
        response['data'] = {'title':'bad shape'}
    elif mode == 'insights-fields':
        response['data'][0]['private-field'] = 'synthetic-private-payload'
    elif mode == 'export-float':
        response['data'] = {'columns':['amount'],'rows':[[1.25]]}
    elif mode == 'export-width':
        response['data'] = {'columns':['amount'],'rows':[[1,2]]}
    elif mode == 'export-fields':
        response['data'] = {'columns':['amount'],'rows':[[1]],'unknown':True}
    send(response,payload)
"""


@pytest.fixture
def fixture_process(monkeypatch):
    original = subprocess.Popen
    processes = []
    observed = []

    def select(mode):
        program = _FIXTURE_WORKER.replace("mode = MODE", "mode = " + repr(mode))

        def spawn(command, **kwargs):
            observed.append((command, kwargs))
            process = original([sys.executable, "-I", "-c", program], **kwargs)
            processes.append(process)
            return process

        monkeypatch.setattr(report_worker.subprocess, "Popen", spawn)
        return processes, observed

    yield select
    for process in processes:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        for stream in (process.stdin, process.stdout):
            stream.close()


def assert_retired(worker, process):
    assert worker._process is None
    assert process.poll() is not None
    assert process.stdin.closed and process.stdout.closed


def test_real_example_worker_roundtrip_matches_public_results_and_closes(worker_snapshot, capfd):
    distribution = metadata.distribution("whab_plugin_example")
    entry, = (entry for entry in distribution.entry_points if entry.group == "whab.plugins")
    descriptor = entry.load()
    worker = ReportWorker("example", timeout=5, expected_distribution=distribution.metadata["Name"], expected_version=distribution.version)
    process = worker._process
    try:
        assert worker.info["insights"] and worker.info["export"]
        assert worker.insights(worker_snapshot) == descriptor.insights(worker_snapshot)
        assert worker.export(worker_snapshot) == descriptor.export(worker_snapshot)
    finally:
        worker.close()
    assert_retired(worker, process)
    worker.close()
    with pytest.raises(PluginError) as raised:
        worker.insights(worker_snapshot)
    assert raised.value.code == "worker_closed" and raised.value.plugin_id == "example"
    output = capfd.readouterr()
    assert "Fictional merchant" not in output.out + output.err


@pytest.mark.parametrize("mode", ["startup-crash", "startup-timeout", 'hello:{"worker":999}', 'hello:{"worker":true}',
                                  'hello:{"sdk":2}', 'hello:{"sdk":true}', 'hello:{"plugin":"different"}',
                                  'hello:{"distribution":""}', 'hello:{"version":""}',
                                  'hello:{"insights":false,"export":false}', 'hello:{"insights":"true"}',
                                  'hello:{"unknown":true}', 'hello:{"host":""}',
                                  'hello:{"live":1}', 'hello:{"live":"true"}',
                                  'hello:{"capabilities":[]}', 'hello:{"capabilities":[{"name":"reporting.insights","version":2}]}',
                                  'hello:{"capabilities":[{"name":"reporting.insights","version":true}]}',
                                  'hello:{"capabilities":[{"name":"reporting.insights","version":1,"extra":true}]}'])
def test_startup_rejects_crashes_timeouts_and_incompatible_hello_and_cleans_process(mode, fixture_process):
    processes, _ = fixture_process(mode)
    with pytest.raises(PluginError) as raised:
        ReportWorker("fixture", timeout=0.1 if mode == "startup-timeout" else 5)
    assert raised.value.code == "worker_startup_failed" and raised.value.plugin_id == "fixture"
    process, = processes
    assert process.poll() is not None and process.stdin.closed and process.stdout.closed


@pytest.mark.parametrize("expected", [{"expected_version": "2.0.0"}, {"expected_distribution": "different-package"}])
def test_exact_approved_package_identity_is_required_before_use(expected, fixture_process):
    processes, _ = fixture_process("valid")
    with pytest.raises(PluginError) as raised:
        ReportWorker("fixture", timeout=5, **expected)
    assert raised.value.code == "worker_startup_failed"
    process, = processes
    assert process.poll() is not None and process.stdin.closed and process.stdout.closed


def test_legacy_worker_is_readable_but_does_not_claim_live_upgrade_support(fixture_process, worker_snapshot):
    fixture_process('hello:{"live":false}')
    worker = ReportWorker("fixture", timeout=5)
    try:
        assert worker.info["live"] is False
        assert worker.insights(worker_snapshot)[0].title == "Fictional"
    finally:
        worker.close()


def test_live_report_support_requires_explicit_stateless_report_contract_without_executing_hooks():
    def unused(*_):
        pytest.fail("Lifecycle inspection cannot execute authored hooks")

    supported = Plugin("fixture", "Fictional", insights=unused, contract=PluginContract(lifecycle="live"))
    assert report_worker.supports_live_report(supported)
    assert report_worker.supports_live_report(replace(supported, insights=None, export=unused))
    for overrides in (
        {"contract": None}, {"contract": PluginContract()},
        {"contract": PluginContract(lifecycle="live", state_version=2)},
        {"contract": PluginContract(lifecycle="live", state_version=2, state_upgrades=((1, 2),))},
        {"contract": PluginContract(lifecycle="live", provides=(Capability("fictional.other"),))},
        {"bind": unused}, {"settings": PluginSettings(unused)}, {"migrate_state": unused},
        {"budget_connection": unused}, {"storage": unused}, {"bank": unused},
    ):
        assert not report_worker.supports_live_report(replace(supported, **overrides))


@pytest.mark.parametrize("mode", ["crash", "bad-frame", "wrong-id", "unknown-field", "payload", "insights-type", "insights-fields"])
def test_failed_or_malformed_worker_reports_are_attributed_and_retired(mode, fixture_process, worker_snapshot):
    processes, _ = fixture_process(mode)
    worker = ReportWorker("fixture", timeout=5)
    with pytest.raises(PluginError) as raised:
        worker.insights(worker_snapshot)
    assert raised.value.code in {"worker_protocol", "worker_failed"} and raised.value.plugin_id == "fixture"
    assert "synthetic-private-payload" not in str(raised.value)
    assert_retired(worker, processes[0])


@pytest.mark.parametrize("mode", ["export-float", "export-width", "export-fields"])
def test_unvalidated_export_cells_and_shapes_never_escape_parent(mode, fixture_process, worker_snapshot):
    processes, _ = fixture_process(mode)
    worker = ReportWorker("fixture", timeout=5)
    with pytest.raises(PluginError) as raised:
        worker.export(worker_snapshot)
    assert raised.value.code == "worker_protocol"
    assert_retired(worker, processes[0])


def test_request_timeout_retires_worker_without_retry(fixture_process, worker_snapshot):
    processes, observed = fixture_process("timeout")
    worker = ReportWorker("fixture", timeout=5)
    worker.timeout = 0.05
    with pytest.raises(PluginError) as raised:
        worker.insights(worker_snapshot)
    assert raised.value.code == "worker_timeout"
    assert len(observed) == 1
    assert_retired(worker, processes[0])


def test_logo_response_size_is_rejected_from_header_before_waiting_for_binary_body(fixture_process):
    processes, _ = fixture_process("oversized-logo")
    worker = ReportWorker("fixture", timeout=5)
    started = time.monotonic()
    with pytest.raises(PluginError) as raised:
        worker.fetch("fixture")
    assert raised.value.code == "worker_protocol" and time.monotonic() - started < 2
    assert_retired(worker, processes[0])


def test_unread_large_request_has_a_bounded_write_timeout_and_closes_pipes(fixture_process, worker_snapshot):
    processes, observed = fixture_process("no-read")
    worker = ReportWorker("fixture", timeout=5)
    worker.timeout = 0.05
    large = replace(worker_snapshot, transactions=tuple(
        replace(worker_snapshot.transactions[0], id=f"row-{index}", payee="fictional" * 200)
        for index in range(150)
    ))
    with pytest.raises(PluginError) as raised:
        worker.insights(large)
    assert raised.value.code == "worker_timeout"
    assert len(observed) == 1
    assert_retired(worker, processes[0])


@pytest.mark.parametrize(("mode", "code"), [("shutdown-result", "worker_protocol"), ("shutdown-timeout", "worker_timeout")])
def test_shutdown_requires_exact_closed_ack_and_remains_bounded(mode, code, fixture_process):
    processes, _ = fixture_process(mode)
    worker = ReportWorker("fixture", timeout=5)
    worker.timeout = 0.05
    with pytest.raises(PluginError) as raised:
        worker.close()
    assert raised.value.code == code and raised.value.plugin_id == "fixture"
    assert_retired(worker, processes[0])
    worker.close()


def test_startup_reap_failure_still_closes_pipes_and_exposes_only_authored_error(monkeypatch):
    calls = []
    workers = []

    def wait(*, timeout):
        calls.append("wait")
        if len(calls) == 1:
            raise subprocess.TimeoutExpired("synthetic-private-command", timeout)
        if len(calls) == 2:
            raise OSError("synthetic-private-reap-failure")
        return 0

    process = SimpleNamespace(pid=321234, stdin=io.BytesIO(), stdout=io.BytesIO(), poll=lambda: None,
                              terminate=lambda: None, kill=lambda: None, wait=wait)
    signals = []
    monkeypatch.setattr(report_worker.os, "killpg", lambda group, requested: signals.append((group, requested)))
    monkeypatch.setattr(report_worker.subprocess, "Popen", lambda *_, **__: process)

    def reject_hello(self, timeout):
        assert workers == [self]
        raise WireError()

    monkeypatch.setattr(ReportWorker, "_read", reject_hello)
    with pytest.raises(PluginError) as raised:
        ReportWorker("fixture", timeout=5, on_create=workers.append)
    assert raised.value.code == "worker_startup_failed"
    assert "synthetic-private" not in str(raised.value)
    assert process.stdin.closed and process.stdout.closed
    worker, = workers
    assert worker.cleanup_pending
    worker.close()
    assert not worker.cleanup_pending and calls == ["wait", "wait", "wait", "wait"]
    assert signals == [(process.pid, requested) for requested in (signal.SIGTERM, signal.SIGKILL, signal.SIGTERM, signal.SIGKILL)]


@pytest.mark.parametrize("group", ["missing", "live", "unobservable"])
def test_exited_leader_permission_error_requires_proof_the_whole_group_is_absent(monkeypatch, group):
    signals = []
    def signal_group(pid, requested):
        signals.append(requested)
        if requested == 0:
            if group == "missing":
                raise ProcessLookupError()
            if group == "unobservable":
                raise PermissionError()
            return
        raise PermissionError()
    monkeypatch.setattr(report_worker.os, "killpg", signal_group)
    process = SimpleNamespace(pid=321234, poll=lambda: 1, wait=lambda **_: 1)
    if group == "missing":
        report_worker._stop_process_group(process)
        assert signals == [signal.SIGTERM, 0, signal.SIGKILL, 0]
    else:
        with pytest.raises(PermissionError):
            report_worker._stop_process_group(process)
        assert signals == [signal.SIGTERM, 0]


@pytest.mark.parametrize("during_startup", [True, False])
def test_cancellation_preserves_identity_and_retires_owned_worker(during_startup, fixture_process, worker_snapshot, monkeypatch):
    processes, observed = fixture_process("valid")
    cancellation = KeyboardInterrupt("fictional cancellation")

    def cancel(*_):
        raise cancellation

    if during_startup:
        monkeypatch.setattr(ReportWorker, "_read", cancel)
        with pytest.raises(KeyboardInterrupt) as raised:
            ReportWorker("fixture", timeout=5)
    else:
        worker = ReportWorker("fixture", timeout=5)
        monkeypatch.setattr(worker, "_read", cancel)
        with pytest.raises(KeyboardInterrupt) as raised:
            worker.insights(worker_snapshot)
        assert worker._process is None
    assert raised.value is cancellation
    process, = processes
    assert process.poll() is not None and process.stdin.closed and process.stdout.closed
    assert observed[0][1]["start_new_session"] is True


def _child_is_running(pid):
    with _POPEN(["ps", "-o", "stat=", "-p", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        state = process.communicate(timeout=2)[0].strip()
    return bool(state) and not state.startswith(b"Z")


@pytest.mark.parametrize("operation", ["close", "timeout"])
def test_worker_retirement_stops_owned_descendants_even_when_the_leader_exits(operation, tmp_path, fixture_process, worker_snapshot):
    marker = tmp_path / "fictional-child.pid"
    processes, _ = fixture_process(f"child-{operation}:{marker}")
    worker = ReportWorker("fixture", timeout=5)
    pid = None
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        pid = int(marker.read_text())
        assert _child_is_running(pid)
        assert os.getpgid(pid) == processes[0].pid
        assert os.getpgid(pid) != os.getpgrp()
        if operation == "timeout":
            worker.timeout = 0.05
            with pytest.raises(PluginError) as raised:
                worker.insights(worker_snapshot)
            assert raised.value.code == "worker_timeout"
        else:
            worker.close()
        deadline = time.monotonic() + 2
        while _child_is_running(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _child_is_running(pid)
        assert_retired(worker, processes[0])
    finally:
        worker.close()
        if pid is not None and _child_is_running(pid):
            os.kill(pid, signal.SIGKILL)


def test_hook_failure_has_sanitized_diagnostics_and_does_not_restart_worker(fixture_process, worker_snapshot, capfd):
    processes, observed = fixture_process("hook-error")
    worker = ReportWorker("fixture", timeout=5)
    try:
        for _ in range(2):
            with pytest.raises(PluginError) as raised:
                worker.insights(worker_snapshot)
            assert raised.value.code == "worker_hook_failed" and raised.value.plugin_id == "fixture"
            assert "synthetic-private" not in str(raised.value)
        assert len(observed) == 1 and processes[0].poll() is None
    finally:
        worker.close()
    output = capfd.readouterr()
    assert "synthetic-private" not in output.out + output.err


def test_worker_never_inherits_configured_runtime_secrets_or_password_alias(fixture_process, monkeypatch):
    processes, observed = fixture_process("valid")
    hostile = {"WHAB_HOME": "/fictional/private", "WHAB_BUDGET_PASSWORD_ENV": "HOUSEHOLD_PASSWORD",
               "HOUSEHOLD_PASSWORD": "synthetic-private-password", "PLAID_SECRET": "synthetic-private-secret",
               "CLIENT_TOKEN": "synthetic-private-token", "TOKEN_ENCRYPTION_KEY": "synthetic-private-key",
               "PYTHONPATH": "/fictional/source"}
    for name, value in hostile.items():
        monkeypatch.setenv(name, value)
    worker = ReportWorker("fixture", timeout=5)
    try:
        assert all(name not in observed[0][1]["env"] for name in hostile)
        assert observed[0][0][1] == "-I"
        assert observed[0][1]["stderr"] == subprocess.DEVNULL
    finally:
        worker.close()
    assert_retired(worker, processes[0])


def test_worker_loop_accepts_only_report_operations_and_sanitizes_hook_errors(monkeypatch, worker_snapshot):
    def failing(_):
        raise RuntimeError("synthetic-private-hook-detail")

    registry = PluginRegistry((Plugin("fixture", "Fixture", insights=failing),))
    monkeypatch.setattr(report_worker, "load_plugins", lambda _: registry)
    monkeypatch.setattr(report_worker.metadata, "entry_points", lambda **_: (
        SimpleNamespace(name="fixture", dist=SimpleNamespace(metadata={"Name": "fixture-package"}, version="1.0.0")),
    ))
    value = asdict(worker_snapshot)
    for name in ("accounts", "categories", "transactions"):
        value[name] = list(value[name])
    request = Frame({"id": "request", "operation": "insights", "snapshot": value}).encode()
    target = io.BytesIO()
    serve("fixture", io.BytesIO(request), target)
    hello, error = decode_sequence(target.getvalue())
    assert hello.metadata["kind"] == "ready"
    assert hello.metadata["live"] is False
    assert error.metadata == {"id": "request", "kind": "error", "code": "hook_failed"}
    assert b"synthetic-private" not in target.getvalue()
    for operation in ("add_transaction", "apply_sync", "router", "pickle"):
        request = Frame({"id": "request", "operation": operation, "snapshot": value}).encode()
        target = io.BytesIO()
        with pytest.raises(WireError):
            serve("fixture", io.BytesIO(request), target)
        assert len(decode_sequence(target.getvalue())) == 1


@pytest.mark.parametrize("host", ["different-host", "2.22.0"])
def test_api_worker_requires_current_host_before_any_dispatch(host, fixture_process):
    adapter = {"protocol_version": 1, "token_setting": "FIXTURE_TOKEN", "queries": True, "commands": True}
    processes, _ = fixture_process("hello:" + json.dumps({
        "host": host, "insights": False, "export": False,
        "capabilities": [{"name": "interface.api", "version": 1}],
        "adapters": {"bank_logos": None, "merchant_logos": False, "file_import": None,
                     "settings": None, "interface": adapter, "ui": None, "categorization": False, "remote_client": None},
    }))
    with pytest.raises(PluginError) as rejected:
        ReportWorker("fixture", timeout=5)
    assert rejected.value.code == "worker_startup_failed"
    assert processes[0].poll() is not None


def test_api_parent_rejects_worker_spec_drift_and_retires_process(fixture_process):
    adapter = {"protocol_version": 1, "token_setting": "FIXTURE_TOKEN", "queries": True, "commands": True}
    processes, _ = fixture_process("hello:" + json.dumps({
        "insights": False, "export": False, "capabilities": [{"name": "interface.api", "version": 1}],
        "adapters": {"bank_logos": None, "merchant_logos": False, "file_import": None,
                     "settings": None, "interface": adapter, "ui": None, "categorization": False, "remote_client": None},
    }))
    worker = ReportWorker("fixture", timeout=5)
    # This reviewed fixture deliberately returns report-shaped data to a spec
    # request. It cannot become a router, service or executable return value.
    with pytest.raises(PluginError) as rejected:
        worker.interface()
    assert rejected.value.code == "worker_protocol"
    assert processes[0].poll() is not None
