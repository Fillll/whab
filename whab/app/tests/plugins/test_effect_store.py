"""Native private-store boundaries shared by namespace and bank journals."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from whab.plugins.effect_store import EffectError
from whab.plugins.namespace_effects import (
    NamespaceJournal,
    NamespaceOperation,
    NamespaceScope,
    NamespaceSnapshot,
)

SCOPE = NamespaceScope("synthetic-budget", "sandbox", "fictional-engine")


def opened(root):
    return NamespaceJournal(root, SCOPE, NamespaceSnapshot({}, {}))


def change(journal):
    return NamespaceOperation("synthetic-operation", "train", journal.generation, journal.snapshot.digest, b"input")


def test_new_ancestor_entries_are_flushed_and_existing_permissions_are_preserved(tmp_path, monkeypatch):
    import whab.plugins.effect_store as module
    original = module.fsync_directory
    flushed = []

    def observed(path):
        flushed.append(Path(path))
        return original(path)

    monkeypatch.setattr(module, "fsync_directory", observed)
    tmp_path.chmod(0o755)
    root = tmp_path / "new-runtime" / "private-effects" / "engine"
    journal = opened(root)
    try:
        assert tmp_path.stat().st_mode & 0o777 == 0o755
        for path in (root, root.parent, root.parent.parent):
            assert path.stat().st_mode & 0o777 == 0o700
            assert path in flushed and path.parent in flushed
        assert (root / "namespace-effects.json.lock").stat().st_mode & 0o777 == 0o600
    finally:
        journal.close()


def test_fd_conversion_failure_closes_temporary_descriptor_and_retains_original_generation(tmp_path, monkeypatch):
    import whab.plugins.effect_store as module
    journal = opened(tmp_path)
    request = change(journal)
    original, count = module.os.fdopen, len(os.listdir("/dev/fd"))

    def fail(descriptor, mode, **options):
        if mode == "wb":
            raise OSError("synthetic private conversion failure")
        return original(descriptor, mode, **options)

    monkeypatch.setattr(module.os, "fdopen", fail)
    with pytest.raises(EffectError) as raised:
        journal.commit(request, NamespaceSnapshot({}, {"model": b"new"}), b"result")
    assert raised.value.code == "effect_commit_failed"
    assert journal.generation == 0 and not tuple(tmp_path.glob(".effect-*"))
    assert len(os.listdir("/dev/fd")) == count
    journal.close()


def test_disk_full_before_temporary_creation_is_sanitized_and_preserves_committed_state(tmp_path, monkeypatch):
    import whab.plugins.effect_store as module
    journal = opened(tmp_path)
    request = change(journal)
    raw = (tmp_path / "namespace-effects.json").read_bytes()

    def fail(*_, **__):
        raise OSError("synthetic private disk-full path")

    monkeypatch.setattr(module.tempfile, "mkstemp", fail)
    with pytest.raises(EffectError) as raised:
        journal.commit(request, NamespaceSnapshot({}, {"model": b"new"}), b"result")
    assert raised.value.code == "effect_commit_failed" and "synthetic" not in str(raised.value)
    assert journal.generation == 0 and (tmp_path / "namespace-effects.json").read_bytes() == raw
    journal.close()


def test_external_change_is_a_handle_fence_even_if_someone_restores_original_bytes(tmp_path):
    journal = opened(tmp_path)
    path = tmp_path / "namespace-effects.json"
    raw = path.read_bytes()
    path.write_bytes(raw + b" ")
    with pytest.raises(EffectError):
        _ = journal.snapshot
    path.write_bytes(raw)
    for action in (lambda: journal.snapshot, journal.store.recover):
        with pytest.raises(EffectError) as raised:
            action()
        assert raised.value.code == "effect_changed"
    journal.close()
    reopened = opened(tmp_path)
    reopened.close()


def test_recovery_observed_external_change_remains_fenced_after_original_bytes_are_restored(tmp_path):
    journal = opened(tmp_path)
    path = tmp_path / "namespace-effects.json"
    raw = path.read_bytes()
    path.write_bytes(raw + b" ")
    with pytest.raises(EffectError):
        journal.store.recover()
    path.write_bytes(raw)
    with pytest.raises(EffectError) as raised:
        _ = journal.snapshot
    assert raised.value.code == "effect_changed"
    journal.close()


def test_missing_lock_against_acknowledged_revision_zero_is_never_recreated(tmp_path):
    journal = opened(tmp_path)
    raw = (tmp_path / "namespace-effects.json").read_bytes()
    lock = tmp_path / "namespace-effects.json.lock"
    lock.unlink()
    with pytest.raises(EffectError):
        opened(tmp_path)
    assert not lock.exists() and (tmp_path / "namespace-effects.json").read_bytes() == raw
    with pytest.raises(EffectError) as raised:
        journal.commit(NamespaceOperation("blocked", "train", 0, NamespaceSnapshot({}, {}).digest, b"input"),
                       NamespaceSnapshot({}, {"model": b"must not publish"}), b"result")
    assert raised.value.code == "effect_changed"
    journal.close()


def test_same_token_new_inode_cannot_open_or_overwrite_a_paused_writer_decision(tmp_path):
    journal = opened(tmp_path)
    request = change(journal)
    lock = tmp_path / "namespace-effects.json.lock"
    before = (tmp_path / "namespace-effects.json").read_bytes()
    prepared, finish = Event(), Event()

    def pause(phase):
        if phase == "before_replace":
            prepared.set()
            assert finish.wait(timeout=5)

    journal.store.fault = pause

    def commit():
        try:
            journal.commit(request, NamespaceSnapshot({}, {"old-writer": b"old"}), b"old")
        except EffectError as error:
            return error.code
        return "acknowledged"

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(commit)
        assert prepared.wait(timeout=5)
        token, old_inode = lock.read_bytes(), lock.stat().st_ino
        replacement = tmp_path / "replacement-lock"
        replacement.write_bytes(token)
        replacement.chmod(0o600)
        os.replace(replacement, lock)
        assert lock.stat().st_ino != old_inode
        try:
            # A fresh cooperative store cannot acquire authority merely because
            # copied lock bytes match. The private record binds native identity.
            with pytest.raises(EffectError) as raised:
                opened(tmp_path)
            assert raised.value.code == "effect_changed"
        finally:
            finish.set()
        assert future.result(timeout=5) == "effect_changed"
    assert (tmp_path / "namespace-effects.json").read_bytes() == before
    journal.close()


@pytest.mark.parametrize("target", ("journal", "lock"))
def test_redirected_native_files_are_refused_without_following_external_content(tmp_path, target):
    external = tmp_path / "synthetic-external"
    external.write_bytes(b"external must remain unchanged")
    root = tmp_path / "journal"
    root.mkdir()
    name = "namespace-effects.json" + (".lock" if target == "lock" else "")
    (root / name).symlink_to(external)
    with pytest.raises(EffectError):
        opened(root)
    assert external.read_bytes() == b"external must remain unchanged"


@pytest.mark.parametrize("value", (b'{"format":1,"format":1}', b'{"private":"\\ud800"}', b'[]', b'NaN', b'{' + b'"x":[' * 50 + b'0' + b']}' * 50))
def test_invalid_bounded_json_never_becomes_empty_state(tmp_path, value):
    journal = opened(tmp_path)
    journal.close()
    path = tmp_path / "namespace-effects.json"
    path.write_bytes(value)
    with pytest.raises(EffectError):
        opened(tmp_path)
    assert path.read_bytes() == value


def test_cancellation_before_replace_preserves_identity_and_after_replace_fences_until_recovered(tmp_path):
    journal = opened(tmp_path)
    request = change(journal)
    cancellation = KeyboardInterrupt("synthetic private cancellation")

    def cancel(boundary):
        if boundary == "after_replace":
            raise cancellation

    journal.store.fault = cancel
    with pytest.raises(KeyboardInterrupt) as raised:
        journal.commit(request, NamespaceSnapshot({}, {"model": b"new"}), b"saved-result")
    assert raised.value is cancellation
    with pytest.raises(EffectError) as fenced:
        journal.lookup(request)
    assert fenced.value.code == "effect_commit_uncertain"
    journal.store.fault = None
    journal.store.recover()
    assert journal.lookup(request).receipt.result == b"saved-result"
    journal.close()
