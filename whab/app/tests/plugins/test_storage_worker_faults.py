"""Bounded malformed/timeout channels retain financial uncertainty until reaping."""

import base64
import subprocess
import sys
from uuid import uuid4

import pytest

from whab.plugins import PluginError, report_worker, storage_worker
from whab.plugins.storage_transfer import StorageOperation, canonical_storage_json
from whab.plugins.storage_worker import StorageWorker
from whab.plugins.storage_worker_codec import HEADER, MAX_METADATA, encode


def fake_channel(monkeypatch, *, malformed):
    reference = StorageWorker("json")
    hello = encode(reference.info)
    reference.close()
    packet = HEADER.pack(b"WHST", 1, MAX_METADATA + 1, 0)
    source = (
        "import sys,base64\n"
        f"sys.stdout.buffer.write(base64.b64decode({base64.b64encode(hello)!r}));sys.stdout.buffer.flush()\n"
        "sys.stdin.buffer.read(14)\n"
        + (f"sys.stdout.buffer.write({packet!r});sys.stdout.buffer.flush()\n" if malformed else "")
        + "sys.stdin.buffer.read()\n"
    )
    popen = subprocess.Popen
    monkeypatch.setattr(storage_worker.subprocess, "Popen", lambda _args, **kwargs: popen([sys.executable, "-I", "-u", "-c", source], **kwargs))
    worker = StorageWorker("json", timeout=5)
    worker.timeout = 0.05
    return worker


@pytest.mark.parametrize("mutating", [False, True])
@pytest.mark.parametrize("malformed", [False, True])
def test_bad_or_timed_out_financial_response_does_not_terminate_or_retry_work(monkeypatch, mutating, malformed):
    worker = fake_channel(monkeypatch, malformed=malformed)
    try:
        with pytest.raises(PluginError) as error:
            if mutating:
                worker.call("execute_operation", StorageOperation("fictional", str(uuid4()), 0, "personal", "add_account",
                    canonical_storage_json({"name": "Fictional", "type": "depository", "balance": 1})))
            else:
                worker.call("accounts", "personal")
        assert error.value.code == ("storage_write_unknown" if mutating else "storage_worker_protocol")
        assert worker.uncertain is mutating
        assert worker.healthy is mutating
        if mutating:
            with pytest.raises(PluginError) as rejected:
                worker.call("accounts", "personal")
            assert rejected.value.code == "storage_write_unknown"
            worker.quiesce()
            assert not worker.healthy and not worker.cleanup_pending
    finally:
        worker.close()


def test_explicit_quiescence_retries_retained_cleanup_once_then_fences_persistent_failure(monkeypatch):
    worker = StorageWorker("json")
    stop = report_worker._stop_process_group
    attempts = 0

    def transient(process):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("fictional transient reap failure")
        return stop(process)

    monkeypatch.setattr(report_worker, "_stop_process_group", transient)
    worker.quiesce()
    assert attempts == 2 and not worker.cleanup_pending
    worker = StorageWorker("json")
    monkeypatch.setattr(report_worker, "_stop_process_group", lambda _: (_ for _ in ()).throw(OSError("fictional-private-path")))
    try:
        with pytest.raises(PluginError) as failure:
            worker.quiesce()
        assert failure.value.code == "storage_write_unknown" and "fictional-private" not in str(failure.value)
        assert worker.cleanup_pending
    finally:
        monkeypatch.setattr(report_worker, "_stop_process_group", stop)
        worker.quiesce()


def test_parent_death_cannot_open_second_sqlite_writer_before_inflight_native_dispatch_finishes(tmp_path):
    import os
    import selectors
    import signal
    import sqlite3
    from pathlib import Path

    from whab.plugins import StorageContext
    from whab.plugins.storage_proxy import StorageWorkerProxy

    script = r'''
import os,sys
from pathlib import Path
from whab.plugins import StorageContext
from whab.plugins.storage_proxy import StorageWorkerProxy
from whab.plugins.storage_transfer import StorageOperation,canonical_storage_json
from whab.plugins.storage_worker import StorageWorker
from whab.plugins.storage_worker_codec import encode
home=Path(sys.argv[1]); backend=StorageWorkerProxy(StorageWorker('sqlite'),StorageContext(home,'sandbox',{}),home/'intents')
before=backend.commit_evidence()
print(backend.worker._process.pid,flush=True)
sys.stdin.readline()
operation=StorageOperation('orphan-dispatch',before.authority_epoch,before.generation,'personal','add_account',canonical_storage_json({'name':'Fictional orphan dispatch','type':'depository','balance':17}))
backend.intents.begin(operation,before)
backend.worker._write(encode({'id':'orphan-rpc','method':'execute_operation','args':(operation,),'kwargs':{}}),5)
print('dispatched',flush=True)
os._exit(0)
'''
    parent = subprocess.Popen([sys.executable, "-I", "-u", "-c", script, str(tmp_path)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    child_pid = None
    database = None
    replacement = None

    def line():
        with selectors.DefaultSelector() as selector:
            selector.register(parent.stdout, selectors.EVENT_READ)
            assert selector.select(10), "The fictional parent did not reach its barrier"
        return parent.stdout.readline().strip()

    try:
        child_pid = int(line())
        database = sqlite3.connect(Path(tmp_path) / "data/whab-sandbox.sqlite", isolation_level=None)
        database.execute("BEGIN IMMEDIATE")
        parent.stdin.write("dispatch\n")
        parent.stdin.flush()
        assert line() == "dispatched"
        assert parent.wait(timeout=10) == 0
        competing = StorageWorker("sqlite")
        try:
            with pytest.raises(PluginError):
                competing.open(StorageContext(tmp_path, "sandbox", {}))
        finally:
            competing.close()
        # The original child owns an actual dispatched command blocked at the
        # native transaction boundary. Parent death and absent response did not
        # release its lease or authorize a competing writer/absence conclusion.
        database.execute("ROLLBACK")
        database.close()
        database = None
        # Readiness retries do no financial work. The old child commits, sees
        # the closed parent pipe, and releases its native owner in finally.
        for _ in range(10):
            try:
                replacement = StorageWorkerProxy(StorageWorker("sqlite"), StorageContext(tmp_path, "sandbox", {}), tmp_path / "intents")
                break
            except PluginError:
                continue
        assert replacement is not None, "The completed orphan did not release its writer owner"
        assert replacement.worker_recovery_required()
        assert replacement.recover() == {"outcome": "committed"}
        assert [row["balance"] for row in replacement.accounts("personal")] == [17]
    finally:
        if database is not None:
            database.close()
        if replacement is not None:
            replacement.close()
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=10)
        for stream in (parent.stdin, parent.stdout):
            stream.close()
        if child_pid is not None:
            try:
                os.killpg(child_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def test_native_child_receives_scoped_config_without_custom_provider_or_api_environment_credentials(tmp_path, monkeypatch):
    script = r'''
import os,sys
from dataclasses import replace
from whab.plugins import PluginRegistry
from whab.plugins import storage_worker
for key in ('FICTIONAL_PROVIDER_TOKEN','FICTIONAL_SERVICE_SECRET','FICTIONAL_API_KEY','FICTIONAL_AUTHORIZATION','fictional_password','JSON_CUSTOM_VALUE','ODD_PASSWORD_ALIAS','FICTIONAL_SAFE_VALUE'):
 assert key not in os.environ
assert os.environ['LANG']=='C'
original=storage_worker.load_plugins(('json',)).storage_plugin('json')
def factory(context):
 assert dict(context.config)=={'EXPLICIT_STORAGE_SETTING':'scoped'}
 return original.storage(context)
storage_worker.load_plugins=lambda _:PluginRegistry((replace(original,storage=factory),))
storage_worker.serve('json',sys.stdin.buffer,sys.stdout.buffer)
'''
    for key in ('FICTIONAL_PROVIDER_TOKEN', 'FICTIONAL_SERVICE_SECRET', 'FICTIONAL_API_KEY', 'FICTIONAL_AUTHORIZATION',
                'fictional_password', 'JSON_CUSTOM_VALUE', 'ODD_PASSWORD_ALIAS'):
        monkeypatch.setenv(key, 'fictional-never-forward')
    monkeypatch.setenv('WHAB_BUDGET_PASSWORD_ENV', 'ODD_PASSWORD_ALIAS')
    monkeypatch.setenv('FICTIONAL_SAFE_VALUE', 'arbitrary-name-is-not-approved')
    monkeypatch.setenv('LANG', 'C')
    popen = subprocess.Popen
    monkeypatch.setattr(storage_worker.subprocess, 'Popen', lambda _args, **kwargs: popen([sys.executable, '-I', '-B', '-c', script], **kwargs))
    from whab.plugins import StorageContext
    worker = StorageWorker('json')
    try:
        evidence = worker.open(StorageContext(tmp_path, 'sandbox', {'EXPLICIT_STORAGE_SETTING': 'scoped'}))
        assert evidence.budget_id == 'default' and worker.call('accounts', 'personal') == []
    finally:
        worker.close()
