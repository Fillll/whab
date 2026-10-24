"""Mocked bank effects: encrypted captures, stable receipts, barriers and SIGKILL."""

import json
import os
import select
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from whab.plugins.bank_continuations import (
    BankContinuations,
    ContinuationScope,
    NativeApplyEvidence,
)
from whab.plugins.effect_store import EffectError

SCOPE = ContinuationScope("synthetic-budget", "sandbox", "fictional-bank", 7)
NATIVE_FINGERPRINT = "e" * 64


class Clock:
    now = 1000

    def __call__(self):
        return self.now


def cipher():
    # Reviewed synthetic runtime key; it is never included in the journal.
    aes = AESGCM(b"\x11" * 32)

    def seal(raw, aad):
        nonce = os.urandom(12)
        return nonce + aes.encrypt(nonce, raw, aad)

    def open(raw, aad):
        return aes.decrypt(raw[:12], raw[12:], aad)

    return seal, open


def coordinator(root, clock=None):
    seal, open = cipher()
    return BankContinuations(root, SCOPE.budget_id, SCOPE.environment, SCOPE.provider_id,
                             seal=seal, open=open, clock=clock or Clock())


class Native:
    def __init__(self):
        self.receipt = None
        self.calls = 0
        self.lost_reply = False
        self.proof = "not_committed"

    def lookup(self, operation_id, fingerprint):
        return self.receipt or NativeApplyEvidence(operation_id, fingerprint, self.proof)

    def commit(self, operation_id, fingerprint, response):
        assert response == b"fictional provider access secret"
        self.calls += 1
        self.receipt = NativeApplyEvidence(operation_id, fingerprint, "committed", b"fictional saved result")
        if self.lost_reply:
            raise OSError("fictional lost native save response")
        return self.receipt


def captured(journal):
    status = journal.begin(SCOPE, "link", secret=b"fictional link secret")
    journal.exchange(SCOPE, status.identifier, "synthetic-exchange", b"fictional public secret",
                     lambda _: b"fictional provider access secret")
    return status.identifier


def test_intent_precedes_http_encrypted_capture_precedes_native_save_and_retries_never_exchange(tmp_path):
    journal = coordinator(tmp_path)
    flow = journal.begin(SCOPE, "link", secret=b"fictional link secret")
    assert journal.secret(SCOPE, flow.identifier) == b"fictional link secret"
    with pytest.raises(EffectError) as blocked:
        journal.prepare_close()
    assert blocked.value.code == "continuation_busy"
    calls = []

    def send(request):
        assert request == b"fictional public secret"
        row, = json.loads((tmp_path / "bank-continuations.json").read_bytes())["body"]["records"]
        assert row["stage"] == "exchanging" and row["operation"] == "synthetic-exchange"
        calls.append(request)
        return b"fictional provider access secret"

    assert journal.exchange(SCOPE, flow.identifier, "synthetic-exchange", b"fictional public secret", send).stage == "captured"
    assert journal.exchange(SCOPE, flow.identifier, "synthetic-exchange", b"fictional public secret", send).stage == "captured"
    assert len(calls) == 1
    native = Native()
    assert journal.apply(SCOPE, flow.identifier, native_fingerprint=NATIVE_FINGERPRINT, lookup=native.lookup, commit=native.commit) == b"fictional saved result"
    assert journal.apply(SCOPE, flow.identifier, native_fingerprint=NATIVE_FINGERPRINT, lookup=native.lookup, commit=native.commit) == b"fictional saved result"
    assert native.calls == 1 and journal.status(SCOPE, flow.identifier).stage == "complete"
    journal.prepare_close()
    raw = (tmp_path / "bank-continuations.json").read_bytes()
    for secret in (b"fictional link secret", b"fictional public secret", b"fictional provider access secret", b"fictional saved result"):
        assert secret not in raw and secret.decode() not in repr(flow)
    journal.close()
    reopened = coordinator(tmp_path)
    try:
        assert reopened.apply(SCOPE, flow.identifier, native_fingerprint=NATIVE_FINGERPRINT,
                              lookup=lambda *_: pytest.fail("Completed receipt requires no native reapply"), commit=native.commit) == b"fictional saved result"
    finally:
        reopened.close()


@pytest.mark.parametrize("cancel", (False, True))
def test_response_loss_or_cancellation_is_uncertain_and_never_reexchanges(tmp_path, cancel):
    journal = coordinator(tmp_path)
    flow = journal.begin(SCOPE, "update")
    cancellation = KeyboardInterrupt("fictional cancellation")
    calls = []

    def send(_):
        calls.append(True)
        raise cancellation if cancel else OSError("fictional private provider failure")

    with pytest.raises(KeyboardInterrupt if cancel else EffectError) as raised:
        journal.exchange(SCOPE, flow.identifier, "update-exchange", b"fictional public secret", send)
    if cancel:
        assert raised.value is cancellation
    else:
        assert raised.value.code == "exchange_uncertain" and "fictional" not in str(raised.value)
    assert journal.status(SCOPE, flow.identifier).stage == "exchange_uncertain"
    with pytest.raises(EffectError):
        journal.cancel(SCOPE, flow.identifier)
    with pytest.raises(EffectError):
        journal.prepare_close()
    journal.close()
    reopened = coordinator(tmp_path)
    try:
        with pytest.raises(EffectError) as raised:
            reopened.exchange(SCOPE, flow.identifier, "update-exchange", b"fictional public secret", send)
        assert raised.value.code == "exchange_uncertain" and len(calls) == 1
        # Only explicit independent confirmation can resolve an unknown exchange.
        assert reopened.reconcile_exchange(SCOPE, flow.identifier, confirmed_no_effect=True).stage == "cancelled"
        reopened.prepare_close()
    finally:
        reopened.close()


def test_controlled_deadline_expires_only_preexchange_flows_and_keeps_uncertain_work(tmp_path):
    clock = Clock()
    journal = coordinator(tmp_path, clock)
    ready = journal.begin(SCOPE, "link", ttl=5)
    clock.now += 5
    assert journal.status(SCOPE, ready.identifier).stage == "expired"
    with pytest.raises(EffectError):
        journal.exchange(SCOPE, ready.identifier, "expired", b"public", lambda _: pytest.fail("Expired flow must not make HTTP"))
    journal.prepare_close()
    flow = journal.begin(SCOPE, "link", ttl=5)

    def send(_):
        clock.now += 100
        raise TimeoutError("fictional provider may already have committed")

    with pytest.raises(EffectError):
        journal.exchange(SCOPE, flow.identifier, "unknown", b"public", send)
    assert journal.status(SCOPE, flow.identifier).stage == "exchange_uncertain"
    with pytest.raises(EffectError):
        journal.prepare_close()
    journal.close()


def test_scope_generation_and_operation_identity_cannot_cross_budgets_or_reuse_an_exchange(tmp_path):
    journal = coordinator(tmp_path)
    token = captured(journal)
    for scope in (replace(SCOPE, budget_id="other"), replace(SCOPE, environment="production"),
                  replace(SCOPE, provider_id="other"), replace(SCOPE, generation=8)):
        with pytest.raises(EffectError) as raised:
            journal.status(scope, token)
        assert raised.value.code == "continuation_missing"
    with pytest.raises(EffectError) as raised:
        journal.exchange(SCOPE, token, "synthetic-exchange", b"different input", lambda _: pytest.fail("Changed retry must not exchange"))
    assert raised.value.code == "effect_conflict"
    other = journal.begin(SCOPE, "link")
    with pytest.raises(EffectError):
        journal.exchange(SCOPE, other.identifier, "synthetic-exchange", b"other", lambda _: pytest.fail("Reused native identity must not exchange"))
    journal.close()


def test_lost_native_reply_reconciles_original_receipt_without_repeating_save(tmp_path):
    journal = coordinator(tmp_path)
    token = captured(journal)
    native = Native()
    native.lost_reply = True
    with pytest.raises(EffectError) as raised:
        journal.apply(SCOPE, token, native_fingerprint=NATIVE_FINGERPRINT, lookup=native.lookup, commit=native.commit)
    assert raised.value.code == "apply_uncertain" and native.calls == 1
    journal.close()
    reopened = coordinator(tmp_path)
    try:
        assert reopened.status(SCOPE, token).stage == "apply_uncertain"
        assert reopened.apply(SCOPE, token, native_fingerprint=NATIVE_FINGERPRINT, lookup=native.lookup, commit=native.commit) == b"fictional saved result"
        assert native.calls == 1
    finally:
        reopened.close()


def test_missing_native_receipt_is_unknown_not_permission_to_repeat(tmp_path):
    journal = coordinator(tmp_path)
    token = captured(journal)
    native = Native()
    native.proof = "unknown"
    with pytest.raises(EffectError) as raised:
        journal.apply(SCOPE, token, native_fingerprint=NATIVE_FINGERPRINT, lookup=native.lookup, commit=native.commit)
    assert raised.value.code == "apply_uncertain" and native.calls == 0
    assert journal.status(SCOPE, token).stage == "captured"
    native.proof = "not_committed"
    journal.apply(SCOPE, token, native_fingerprint=NATIVE_FINGERPRINT, lookup=native.lookup, commit=native.commit)
    with pytest.raises(EffectError):
        journal.apply(SCOPE, token, native_fingerprint="f" * 64, lookup=native.lookup, commit=native.commit)
    assert native.calls == 1
    journal.close()


def test_barrier_keeps_external_work_owned_and_blocks_close_until_capture(tmp_path):
    journal = coordinator(tmp_path)
    flow = journal.begin(SCOPE, "link")
    started, finish = Event(), Event()

    def send(_):
        started.set()
        assert finish.wait(timeout=5)
        return b"fictional provider access secret"

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(journal.exchange, SCOPE, flow.identifier, "barrier-exchange", b"public", send)
        assert started.wait(timeout=5)
        try:
            for action in (journal.prepare_close, journal.recover, journal.close):
                with pytest.raises(EffectError) as raised:
                    action()
                assert raised.value.code == "continuation_busy"
            assert journal.status(SCOPE, flow.identifier).stage == "exchanging"
        finally:
            finish.set()
        assert future.result(timeout=5).stage == "captured"
    journal.close()


@pytest.mark.parametrize("phase", ("intent", "external", "capture", "native", "complete"))
def test_sigkill_exchange_capture_and_native_receipt_boundaries_never_repeat_unknown_work(tmp_path, phase):
    program = '''import json,os,signal,sys
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from whab.plugins.bank_continuations import BankContinuations,ContinuationScope,NativeApplyEvidence
root=Path(sys.argv[1]); phase=sys.argv[2]; pipe=int(sys.argv[3]); aes=AESGCM(bytes([17])*32)
def seal(raw,aad):
 nonce=os.urandom(12)
 return nonce+aes.encrypt(nonce,raw,aad)
def open(raw,aad): return aes.decrypt(raw[:12],raw[12:],aad)
scope=ContinuationScope('synthetic-budget','sandbox','fictional-bank',7)
journal=BankContinuations(root,scope.budget_id,scope.environment,scope.provider_id,seal=seal,open=open,clock=lambda:1000)
flow=journal.begin(scope,'link',secret=b'fictional link secret')
(root/'flow-id').write_text(flow.identifier)
def stop():
 os.write(pipe,b'B'); os.kill(os.getpid(),signal.SIGSTOP)
def fault(boundary):
 if boundary=='after_directory_fsync':
  stage=json.loads((root/'bank-continuations.json').read_bytes())['body']['records'][0]['stage']
  if (phase=='intent' and stage=='exchanging') or (phase=='capture' and stage=='captured') or (phase=='complete' and stage=='complete'): stop()
journal.store.fault=fault
def send(_):
 (root/'external-count').write_text('one')
 if phase=='external': stop()
 return b'fictional provider access secret'
journal.exchange(scope,flow.identifier,'kill-exchange',b'fictional public secret',send)
def lookup(operation,fingerprint): return NativeApplyEvidence(operation,fingerprint,'not_committed')
def commit(operation,fingerprint,response):
 with (root/'native-receipt').open('wb') as saved:
  saved.write(b'durable synthetic native receipt'); saved.flush(); os.fsync(saved.fileno())
 directory=os.open(root,os.O_RDONLY); os.fsync(directory); os.close(directory)
 if phase=='native': stop()
 return NativeApplyEvidence(operation,fingerprint,'committed',b'fictional saved result')
journal.apply(scope,flow.identifier,native_fingerprint='e'*64,lookup=lookup,commit=commit)
'''
    read, write = os.pipe()
    child = subprocess.Popen([sys.executable, "-c", program, str(tmp_path), phase, str(write)], pass_fds=(write,),
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    os.close(write)
    try:
        assert select.select([read], [], [], 10)[0], child.poll()
        assert os.read(read, 1) == b"B"
        child.kill()
        assert child.wait(timeout=5) == -signal.SIGKILL
    finally:
        os.close(read)
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stderr.close()
    journal = coordinator(tmp_path)
    token = (tmp_path / "flow-id").read_text()
    try:
        status = journal.status(SCOPE, token)
        if phase in {"intent", "external"}:
            assert status.stage == "exchange_uncertain"
            assert (tmp_path / "external-count").exists() == (phase == "external")
            with pytest.raises(EffectError):
                journal.exchange(SCOPE, token, "kill-exchange", b"fictional public secret", lambda _: pytest.fail("Unknown external effect must not repeat"))
        else:
            native = Native()
            if phase in {"native", "complete"}:
                assert (tmp_path / "native-receipt").read_bytes() == b"durable synthetic native receipt"
                native.receipt = NativeApplyEvidence("kill-exchange", NATIVE_FINGERPRINT, "committed", b"fictional saved result")
            assert journal.apply(SCOPE, token, native_fingerprint=NATIVE_FINGERPRINT, lookup=native.lookup, commit=native.commit) == b"fictional saved result"
            assert native.calls == (1 if phase == "capture" else 0)
    finally:
        journal.close()


@pytest.mark.parametrize("boundary", ("before_replace", "after_replace", "after_directory_fsync"))
def test_capture_commit_fault_never_repeats_provider_http(tmp_path, boundary):
    journal = coordinator(tmp_path)
    flow = journal.begin(SCOPE, "link")
    calls = []

    def send(_):
        calls.append(True)
        def fail(phase):
            if phase == boundary:
                raise OSError("fictional capture disk failure")
        journal.store.fault = fail
        return b"fictional provider access secret"

    with pytest.raises(EffectError):
        journal.exchange(SCOPE, flow.identifier, "capture-fault", b"public", send)
    journal.store.fault = None
    journal.recover()
    status = journal.status(SCOPE, flow.identifier)
    assert status.stage == ("exchange_uncertain" if boundary == "before_replace" else "captured")
    try:
        journal.exchange(SCOPE, flow.identifier, "capture-fault", b"public", lambda _: pytest.fail("Capture failure must not repeat HTTP"))
    except EffectError as error:
        assert error.code == "exchange_uncertain"
    assert len(calls) == 1
    journal.close()


def test_authenticated_capture_rejects_wrong_key_and_scope_without_leaking_ciphertext(tmp_path):
    journal = coordinator(tmp_path)
    flow = journal.begin(SCOPE, "link", secret=b"fictional secret")
    journal.close()
    seal, _ = cipher()
    wrong = AESGCM(b"\x22" * 32)
    before = (tmp_path / "bank-continuations.json").read_bytes()
    with pytest.raises(EffectError) as raised:
        BankContinuations(tmp_path, SCOPE.budget_id, SCOPE.environment, SCOPE.provider_id, seal=seal,
                          open=lambda raw, aad: wrong.decrypt(raw[:12], raw[12:], aad), clock=Clock())
    assert raised.value.code == "effect_unavailable" and "fictional secret" not in str(raised.value)
    assert (tmp_path / "bank-continuations.json").read_bytes() == before and len(flow.identifier) == 32


@pytest.mark.parametrize("field,value", (("stage", "unknown"), ("response", "not-base64"), ("generation", True), ("id", "private-record-name")))
def test_corrupt_continuations_are_preserved_without_silent_new_flow(tmp_path, field, value):
    journal = coordinator(tmp_path)
    journal.begin(SCOPE, "link")
    journal.close()
    path = tmp_path / "bank-continuations.json"
    record = json.loads(path.read_bytes())
    record["body"]["records"][0][field] = value
    raw = json.dumps(record).encode()
    path.write_bytes(raw)
    with pytest.raises(EffectError):
        coordinator(tmp_path)
    assert path.read_bytes() == raw


def test_only_terminal_flows_can_be_pruned_and_missing_ids_never_restart_exchange(tmp_path, monkeypatch):
    import whab.plugins.bank_continuations as module
    monkeypatch.setattr(module, "MAX_CONTINUATIONS", 2)
    journal = coordinator(tmp_path)
    old = journal.begin(SCOPE, "link")
    journal.begin(SCOPE, "update")
    with pytest.raises(EffectError) as raised:
        journal.begin(SCOPE, "link")
    assert raised.value.code == "continuation_busy"
    journal.cancel(SCOPE, old.identifier)
    replacement = journal.begin(SCOPE, "link")
    assert replacement.identifier != old.identifier
    with pytest.raises(EffectError) as raised:
        journal.exchange(SCOPE, old.identifier, "unknown-pruned", b"public", lambda _: pytest.fail("Pruned flow must not restart"))
    assert raised.value.code == "continuation_missing"
    journal.close()


def test_secret_sealing_failure_is_sanitized_before_any_durable_intent(tmp_path):
    seal, open = cipher()
    journal = BankContinuations(tmp_path, SCOPE.budget_id, SCOPE.environment, SCOPE.provider_id,
                                seal=seal, open=open, clock=Clock())
    raw = (tmp_path / "bank-continuations.json").read_bytes()

    def fail(*_):
        raise ValueError("fictional provider private secret from crypto callback")

    journal.seal = fail
    with pytest.raises(EffectError) as raised:
        journal.begin(SCOPE, "link", secret=b"fictional secret")
    assert raised.value.code == "effect_unavailable" and "fictional" not in str(raised.value)
    assert (tmp_path / "bank-continuations.json").read_bytes() == raw
    journal.close()


def test_ciphertext_cannot_be_moved_between_scoped_fields_or_continuations(tmp_path):
    journal = coordinator(tmp_path)
    journal.begin(SCOPE, "link", secret=b"first fictional secret")
    journal.begin(SCOPE, "link", secret=b"second fictional secret")
    journal.close()
    path = tmp_path / "bank-continuations.json"
    record = json.loads(path.read_bytes())
    first, second = record["body"]["records"]
    second["input"] = first["input"]
    raw = json.dumps(record).encode()
    path.write_bytes(raw)
    with pytest.raises(EffectError) as raised:
        coordinator(tmp_path)
    assert raised.value.code == "effect_unavailable" and path.read_bytes() == raw


@pytest.mark.parametrize("boundary", ("before_replace", "after_replace", "after_directory_fsync"))
def test_intent_commit_fault_does_not_call_http_and_unknown_intent_never_retries_it(tmp_path, boundary):
    journal = coordinator(tmp_path)
    flow = journal.begin(SCOPE, "link")

    def fail(phase):
        if phase == boundary:
            raise OSError("synthetic uncertain intent publication")

    journal.store.fault = fail
    with pytest.raises(EffectError):
        journal.exchange(SCOPE, flow.identifier, "intent-fault", b"public", lambda _: pytest.fail("Unacknowledged intent cannot call HTTP"))
    journal.store.fault = None
    journal.recover()
    expected = "ready" if boundary == "before_replace" else "exchange_uncertain"
    assert journal.status(SCOPE, flow.identifier).stage == expected
    if expected == "exchange_uncertain":
        with pytest.raises(EffectError):
            journal.exchange(SCOPE, flow.identifier, "intent-fault", b"public", lambda _: pytest.fail("Unknown intent must not repeat HTTP"))
    journal.close()


@pytest.mark.parametrize("boundary", ("before_replace", "after_replace", "after_directory_fsync"))
def test_native_result_publication_fault_replays_receipt_without_another_native_write(tmp_path, boundary):
    journal = coordinator(tmp_path)
    token = captured(journal)
    native = Native()

    def commit(operation_id, fingerprint, response):
        result = native.commit(operation_id, fingerprint, response)
        def fail(phase):
            if phase == boundary:
                raise OSError("synthetic lost native result publication")
        journal.store.fault = fail
        return result

    with pytest.raises(EffectError) as raised:
        journal.apply(SCOPE, token, native_fingerprint=NATIVE_FINGERPRINT, lookup=native.lookup, commit=commit)
    assert raised.value.code == "apply_uncertain" and native.calls == 1
    journal.store.fault = None
    journal.recover()
    assert journal.apply(SCOPE, token, native_fingerprint=NATIVE_FINGERPRINT, lookup=native.lookup, commit=native.commit) == b"fictional saved result"
    assert native.calls == 1
    journal.close()
