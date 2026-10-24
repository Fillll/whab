import builtins
import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from whab_plugin_json import CommitUncertain, ExternalChange, Store, durability

from whab.plugins import StoreError


def _account(store):
    return store.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 100})


def test_stable_lock_rejects_competing_writers_and_survives_replacement(json_store):
    before = json_store._writer.identity
    _account(json_store)
    assert json_store._writer.identity == before
    with pytest.raises(StoreError, match="writer"):
        Store(json_store.path)
    child = subprocess.run([
        sys.executable, "-c",
        "import sys; from whab_plugin_json import Store; from whab.plugins import StoreError\n"
        "try: Store(sys.argv[1])\nexcept StoreError: raise SystemExit(0)\nraise SystemExit(1)",
        str(json_store.path),
    ], capture_output=True, timeout=10)
    assert child.returncode == 0
    json_store.close()
    with pytest.raises(StoreError, match="closed"):
        json_store.commit_status()
    reopened = Store(json_store.path)
    reopened.close()
    reopened.close()


@pytest.mark.parametrize("change", ["replace", "rewrite", "delete", "lock-delete", "lock-replace"])
def test_unexpected_external_changes_fence_writer_without_overwrite(json_store, change):
    before = json_store.path.read_bytes()
    if change == "replace":
        replacement = json_store.path.with_suffix(".replacement")
        replacement.write_bytes(before)
        os.replace(replacement, json_store.path)
    elif change == "rewrite":
        json_store.path.write_bytes(before)
    elif change == "delete":
        json_store.path.unlink()
    else:
        json_store._writer.path.unlink()
        if change == "lock-replace":
            json_store._writer.path.touch()
    with pytest.raises(ExternalChange):
        _account(json_store)
    with pytest.raises(ExternalChange):
        json_store.accounts("personal")
    if json_store.path.exists():
        assert json_store.path.read_bytes() == before


def test_external_change_at_handover_barrier_is_not_overwritten(json_store):
    external = json_store.path.read_bytes() + b" "

    def barrier(stage):
        if stage == "before_replace":
            json_store.path.write_bytes(external)

    json_store._hook = barrier
    with pytest.raises(ExternalChange):
        _account(json_store)
    assert json_store.path.read_bytes() == external


def test_disk_full_before_replace_keeps_previous_generation_and_allows_retry(json_store, monkeypatch):
    previous = json_store.commit_status()
    raw = json_store.path.read_bytes()
    real_fsync = os.fsync

    def full_disk(descriptor):
        raise OSError(errno.ENOSPC, "fictional disk full")

    monkeypatch.setattr(os, "fsync", full_disk)
    with pytest.raises(StoreError, match="previous generation"):
        _account(json_store)
    monkeypatch.setattr(os, "fsync", real_fsync)
    assert json_store.path.read_bytes() == raw
    assert json_store.commit_status() == previous
    assert not list(json_store.path.parent.glob(".whab-json-*"))
    _account(json_store)
    assert json_store.commit_status()["generation"] == previous["generation"] + 1


def test_disk_full_when_creating_temporary_file_preserves_committed_state(json_store, monkeypatch):
    previous = json_store.commit_status()

    def no_space(*args, **kwargs):
        raise OSError(errno.ENOSPC, "fictional disk full")

    monkeypatch.setattr("whab_plugin_json.store.tempfile.mkstemp", no_space)
    with pytest.raises(StoreError, match="previous generation"):
        _account(json_store)
    assert json_store.commit_status() == previous


def test_failed_file_object_conversion_closes_raw_temporary_descriptor(json_store, monkeypatch):
    descriptors = set(os.listdir("/dev/fd"))
    previous = json_store.commit_status()
    original = os.fdopen

    def no_file_object(descriptor, mode, *args, **kwargs):
        if mode == "wb":
            raise OSError("fictional file-object allocation failure")
        return original(descriptor, mode, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", no_file_object)
    with pytest.raises(StoreError, match="previous generation"):
        _account(json_store)
    assert set(os.listdir("/dev/fd")) == descriptors
    assert json_store.commit_status() == previous
    assert not list(json_store.path.parent.glob(".whab-json-*"))


def test_cancellation_during_lock_startup_releases_descriptor(tmp_path, monkeypatch):
    import fcntl

    def interruption(*args):
        raise KeyboardInterrupt()

    path = tmp_path / "writer.lock"
    original = fcntl.flock
    monkeypatch.setattr(fcntl, "flock", interruption)
    with pytest.raises(KeyboardInterrupt):
        durability.WriterLock(path)
    monkeypatch.setattr(fcntl, "flock", original)
    lock = durability.WriterLock(path)
    lock.close()


@pytest.mark.parametrize("stage", ["replaced", "directory_flushed", "published"])
def test_post_replace_failure_is_uncertain_and_recovered_by_identity(json_store, stage):
    previous = json_store.commit_status()

    def failure(boundary):
        if boundary == stage:
            raise OSError("fictional interrupted handover")

    json_store._hook = failure
    with pytest.raises(CommitUncertain) as error:
        _account(json_store)
    attempted = json_store.commit_status()
    assert attempted["status"] == "uncertain"
    assert attempted["operationId"] == error.value.operation_id
    assert attempted["generation"] == previous["generation"] + 1
    with pytest.raises(CommitUncertain):
        json_store.accounts("personal")
    with pytest.raises(CommitUncertain):
        _account(json_store)
    json_store.close()
    reopened = Store(json_store.path)
    try:
        recovered = reopened.commit_status()
        assert recovered["operationId"] == attempted["operationId"]
        assert recovered["generation"] == attempted["generation"]
        assert recovered["status"] == "committed"
        assert len(reopened.accounts("personal")) == 1
        _account(reopened)
    finally:
        reopened.close()


def test_replace_call_can_succeed_before_lost_response(json_store, monkeypatch):
    replace = os.replace

    def lost_response(source, destination):
        replace(source, destination)
        raise OSError("fictional lost replacement response")

    monkeypatch.setattr(os, "replace", lost_response)
    with pytest.raises(CommitUncertain) as error:
        _account(json_store)
    assert json.loads(json_store.path.read_bytes())["operationId"] == error.value.operation_id


def test_directory_flush_failure_fences_uncertain_write(json_store, monkeypatch):
    def failure(directory):
        raise OSError("fictional directory fsync failure")

    monkeypatch.setattr("whab_plugin_json.store.flush_directory", failure)
    with pytest.raises(CommitUncertain):
        _account(json_store)


@pytest.mark.parametrize("stage,committed", [
    ("temporary_flushed", False), ("before_replace", False),
    ("replaced", True), ("directory_flushed", True), ("published", True),
])
def test_process_termination_at_every_durable_commit_boundary(json_store, stage, committed):
    previous = json_store.commit_status()
    path = json_store.path
    json_store.close()
    script = (
        "import os, sys\nfrom whab_plugin_json import Store\n"
        "store = Store(sys.argv[1])\n"
        "def barrier(stage):\n"
        "    if stage == sys.argv[2]: os._exit(73)\n"
        "store._hook = barrier\n"
        "store.add_account('personal', {'name': 'Fictional', 'type': 'depository', 'balance': 100})\n"
        "raise SystemExit(1)\n"
    )
    process = subprocess.run([sys.executable, "-c", script, str(path), stage], capture_output=True, timeout=10)
    assert process.returncode == 73
    reopened = Store(path)
    try:
        assert len(reopened.accounts("personal")) == int(committed)
        assert reopened.commit_status()["generation"] == previous["generation"] + int(committed)
        if committed:
            assert reopened.commit_status()["operationId"] != previous["operationId"]
        _account(reopened)
    finally:
        reopened.close()


def test_startup_failure_releases_writer_lock(json_store):
    path = json_store.path
    original = path.read_bytes()
    json_store.close()
    path.write_bytes(b"truncated {")
    with pytest.raises(StoreError):
        Store(path)
    path.write_bytes(original)
    reopened = Store(path)
    reopened.close()


def test_platform_without_posix_locking_fails_explicitly(tmp_path, monkeypatch):
    original = builtins.__import__

    def no_fcntl(name, *args, **kwargs):
        if name == "fcntl":
            raise ImportError("synthetic unsupported platform")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_fcntl)
    with pytest.raises(StoreError, match="POSIX"):
        durability.WriterLock(tmp_path / "writer.lock")


@pytest.mark.parametrize("kind", ["directory", "hard-link", "fifo"])
def test_runtime_data_must_be_one_regular_file(tmp_path, kind):
    path = tmp_path / "data.json"
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.write_bytes(b"{}")
        os.link(path, tmp_path / "second-name")
    with pytest.raises(StoreError, match="regular"):
        durability.read_file(path)


def test_runtime_symlink_is_not_followed(tmp_path):
    original = tmp_path / "original.json"
    original.write_bytes(b"fictional")
    path = tmp_path / "linked.json"
    path.symlink_to(original)
    with pytest.raises(OSError):
        durability.read_file(path)
    assert original.read_bytes() == b"fictional"


def test_directory_flush_closes_descriptor_on_failure(tmp_path, monkeypatch):
    before = set(os.listdir("/dev/fd"))
    monkeypatch.setattr(os, "fsync", lambda descriptor: (_ for _ in ()).throw(OSError("fictional")))
    with pytest.raises(OSError):
        durability.flush_directory(tmp_path)
    assert set(os.listdir("/dev/fd")) == before


def test_file_growth_during_read_is_still_bounded(tmp_path, monkeypatch):
    path = tmp_path / "growing.json"
    path.write_bytes(b"x")
    original = os.fstat

    def grow(descriptor):
        metadata = original(descriptor)
        path.write_bytes(b"x" * 20)
        return metadata

    monkeypatch.setattr(durability, "MAX_FILE_BYTES", 10)
    monkeypatch.setattr(os, "fstat", grow)
    with pytest.raises(StoreError, match="size"):
        durability.read_file(path)


def test_invalid_writer_lock_does_not_leak_descriptor(tmp_path):
    path = tmp_path / "writer.lock"
    path.touch()
    os.link(path, tmp_path / "duplicate.lock")
    with pytest.raises(StoreError, match="writer"):
        durability.WriterLock(path)
    path.unlink()
    lock = durability.WriterLock(path)
    lock.close()
    lock.close()


@pytest.mark.parametrize("boundary", ["temporary_flushed", "replaced"])
def test_control_interrupt_does_not_claim_a_rollback(json_store, boundary):
    def interruption(stage):
        if stage == boundary:
            raise KeyboardInterrupt()

    json_store._hook = interruption
    with pytest.raises(KeyboardInterrupt):
        _account(json_store)
    if boundary == "replaced":
        assert json_store.commit_status()["status"] == "uncertain"
    else:
        assert json_store.commit_status()["status"] == "committed"


def test_temporary_cleanup_failure_does_not_turn_committed_save_into_failure(json_store, monkeypatch):
    original = Path.unlink

    def cleanup(path, *args, **kwargs):
        if path.name.startswith(".whab-json-"):
            raise OSError("fictional cleanup failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", cleanup)
    _account(json_store)
    assert json_store.commit_status()["status"] == "committed"


def test_new_directory_entries_are_flushed_before_file_publication(tmp_path, monkeypatch):
    events = []
    original_mkdir = Path.mkdir
    original_flush = durability.flush_directory

    def mkdir(path, *args, **kwargs):
        original_mkdir(path, *args, **kwargs)
        events.append(("mkdir", path))

    def flush(path):
        original_flush(path)
        events.append(("flush", Path(path)))

    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(durability, "flush_directory", flush)
    first, second = tmp_path / "runtime", tmp_path / "runtime" / "data"

    def commit(stage):
        if stage == "before_replace":
            assert events == [
                ("flush", tmp_path.parent), ("flush", tmp_path),
                ("mkdir", first), ("flush", tmp_path), ("flush", first),
                ("mkdir", second), ("flush", first), ("flush", second),
            ]

    backend = Store(second / "budget.json", month="2026-10", commit_hook=commit)
    backend.close()


def test_directory_flush_failure_then_retry_does_not_adopt_undurable_anchor(tmp_path, monkeypatch):
    parent = tmp_path / "runtime"
    data = parent / "data"
    flush = durability.flush_directory
    seen = []

    def interrupted(path):
        seen.append(Path(path))
        if Path(path) == tmp_path and parent.exists():
            raise OSError("fictional directory-entry durability failure")
        flush(path)

    monkeypatch.setattr(durability, "flush_directory", interrupted)
    with pytest.raises(OSError):
        Store(data / "budget.json")
    assert parent.is_dir() and not data.exists()
    assert not (data / "budget.json").exists()
    seen.clear()

    def resumed(path):
        seen.append(Path(path))
        flush(path)

    monkeypatch.setattr(durability, "flush_directory", resumed)
    backend = Store(data / "budget.json", month="2026-10")
    try:
        assert seen[:2] == [tmp_path, parent]
        _account(backend)
        assert backend.commit_status()["generation"] == 1
    finally:
        backend.close()


def test_directory_creation_preserves_existing_modes_and_runtime_root_symlink(tmp_path):
    import stat

    physical = tmp_path / "physical"
    physical.mkdir(mode=0o755)
    before = stat.S_IMODE(physical.stat().st_mode)
    linked = tmp_path / "runtime-link"
    linked.symlink_to(physical, target_is_directory=True)
    backend = Store(linked / "data" / "budget.json", month="2026-10")
    backend.close()
    assert linked.is_symlink()
    assert stat.S_IMODE(physical.stat().st_mode) == before
    assert stat.S_IMODE((physical / "data").stat().st_mode) == 0o700
    assert (physical / "data" / "budget.json").exists()


def test_directory_collision_fails_without_changing_existing_file(tmp_path):
    existing = tmp_path / "file"
    existing.write_bytes(b"fictional existing data")
    with pytest.raises(StoreError, match="directory"):
        durability.ensure_durable_directory(existing / "nested")
    assert existing.read_bytes() == b"fictional existing data"


@pytest.mark.parametrize("file_instead", [False, True])
def test_racing_directory_creation_is_checked_and_flushed(tmp_path, monkeypatch, file_instead):
    directory = tmp_path / "created-elsewhere"
    original = Path.mkdir

    def race(path, *args, **kwargs):
        if path == directory:
            if file_instead:
                path.write_bytes(b"fictional conflicting file")
            else:
                original(path, *args, **kwargs)
            raise FileExistsError()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", race)
    if file_instead:
        with pytest.raises(StoreError, match="conflicted"):
            durability.ensure_durable_directory(directory)
    else:
        durability.ensure_durable_directory(directory)
        assert directory.is_dir()
