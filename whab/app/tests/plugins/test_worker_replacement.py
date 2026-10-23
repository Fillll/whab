"""Stopped-host reference controls use native atomic publication, without code loading."""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from whab.plugins import PluginError
from whab.plugins import worker_selection as selection
from whab.plugins.environments import PreparedEnvironment


def prepared(home, number):
    return PreparedEnvironment(str(number) * 64, "fictional-reports", "fictional-reports", f"{number}.0.0",
                               home / "unused-python", home / ("unused-environment-" + str(number)), ())


def setup_reference(tmp_path, monkeypatch):
    old, replacement = prepared(tmp_path, 1), prepared(tmp_path, 2)
    selection.save_reference(tmp_path, old)
    reference = tmp_path / "plugin-workers/active/fictional-reports.json"
    raw = reference.read_bytes()
    revision = selection.worker_reference_revision(tmp_path, "fictional-reports")
    assert revision == hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(selection, "prepare_environment", lambda *_: replacement)
    return replacement, reference, raw, revision


def test_compare_and_swap_preserves_exact_prior_reference_and_refuses_another_decision(tmp_path, monkeypatch):
    replacement, reference, raw, revision = setup_reference(tmp_path, monkeypatch)
    # Preserve noncanonical but valid old bytes, including their formatting.
    reference.write_bytes(json.dumps(json.loads(raw), indent=2).encode() + b"\n")
    raw = reference.read_bytes()
    revision = selection.worker_reference_revision(tmp_path, "fictional-reports")
    assert selection.replace_saved_worker(tmp_path, replacement.plugin_id, (), expected_reference_sha256=revision) == replacement
    history = tmp_path / "plugin-workers/history/fictional-reports" / (revision + ".json")
    assert history.read_bytes() == raw
    assert reference.stat().st_mode & 0o777 == history.stat().st_mode & 0o777 == 0o600
    selected = reference.read_bytes()
    different = prepared(tmp_path, 3)
    monkeypatch.setattr(selection, "prepare_environment", lambda *_: different)
    with pytest.raises(PluginError) as raised:
        selection.replace_saved_worker(tmp_path, different.plugin_id, (), expected_reference_sha256=revision)
    assert raised.value.code == "worker_reference_conflict"
    assert reference.read_bytes() == selected and history.read_bytes() == raw


def test_history_failure_prevents_publication_and_never_overwrites_conflicting_history(tmp_path, monkeypatch):
    replacement, reference, raw, revision = setup_reference(tmp_path, monkeypatch)
    history = tmp_path / "plugin-workers/history/fictional-reports" / (revision + ".json")
    history.parent.mkdir(parents=True)
    history.write_bytes(b"reviewed conflicting historical reference")
    with pytest.raises(PluginError) as raised:
        selection.replace_saved_worker(tmp_path, replacement.plugin_id, (), expected_reference_sha256=revision)
    assert raised.value.code == "worker_replacement_failed"
    assert reference.read_bytes() == raw and history.read_bytes() == b"reviewed conflicting historical reference"
    history.unlink()

    def fail_link(*_):
        raise OSError("synthetic private disk-full path")

    monkeypatch.setattr(selection.os, "link", fail_link)
    with pytest.raises(PluginError) as raised:
        selection.replace_saved_worker(tmp_path, replacement.plugin_id, (), expected_reference_sha256=revision)
    assert raised.value.code == "worker_replacement_failed" and "synthetic" not in str(raised.value)
    assert reference.read_bytes() == raw and not tuple(history.parent.iterdir())


def test_concurrent_replacements_publish_only_one_cas_decision(tmp_path, monkeypatch):
    replacement, reference, raw, revision = setup_reference(tmp_path, monkeypatch)
    barrier = Barrier(2)

    def candidate(_root, _plugin, approvals):
        barrier.wait(timeout=5)
        return approvals[0]

    monkeypatch.setattr(selection, "prepare_environment", candidate)

    def publish(value):
        try:
            return selection.replace_saved_worker(tmp_path, value.plugin_id, (value,), expected_reference_sha256=revision)
        except PluginError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(publish, (replacement, prepared(tmp_path, 3))))
    winner, = (value for value in outcomes if not isinstance(value, str))
    assert outcomes.count("worker_reference_conflict") == 1
    assert json.loads(reference.read_bytes())["environment"] == winner.identifier
    assert (tmp_path / "plugin-workers/history/fictional-reports" / (revision + ".json")).read_bytes() == raw


@pytest.mark.parametrize("mode", ("oversized", "fifo", "file-link", "directory-link"))
def test_revision_refuses_unbounded_or_redirected_reference_without_execution(tmp_path, mode):
    active = tmp_path / "plugin-workers/active"
    active.mkdir(parents=True)
    reference = active / "fictional-reports.json"
    external = tmp_path / "external-reference"
    external.write_bytes(b"synthetic external bytes")
    if mode == "oversized":
        reference.write_bytes(b"x" * 16385)
    elif mode == "fifo":
        os.mkfifo(reference)
    elif mode == "file-link":
        reference.symlink_to(external)
    else:
        active.rmdir()
        target = tmp_path / "external-directory"
        target.mkdir()
        (target / reference.name).write_bytes(b"external decision")
        active.symlink_to(target, target_is_directory=True)
    with pytest.raises(PluginError) as raised:
        selection.worker_reference_revision(tmp_path, "fictional-reports")
    assert raised.value.code == "worker_environment_unavailable" and str(tmp_path) not in str(raised.value)
    assert external.read_bytes() == b"synthetic external bytes"


@pytest.mark.parametrize("plugin_id,revision", (("../outside", "1" * 64), ("fictional-reports", "incomplete")))
def test_invalid_replacement_identity_fails_before_environment_preparation(tmp_path, monkeypatch, plugin_id, revision):
    monkeypatch.setattr(selection, "prepare_environment", lambda *_: pytest.fail("Reject malformed control arguments before preparation"))
    with pytest.raises(PluginError) as raised:
        selection.replace_saved_worker(tmp_path, plugin_id, (), expected_reference_sha256=revision)
    assert raised.value.code == "worker_replacement_failed" and not tuple(tmp_path.iterdir())


def test_cancellation_identity_survives_preparation_without_changing_reference(tmp_path, monkeypatch):
    replacement, reference, raw, revision = setup_reference(tmp_path, monkeypatch)
    cancellation = KeyboardInterrupt("fictional operator cancellation")

    def cancel(*_):
        raise cancellation

    monkeypatch.setattr(selection, "prepare_environment", cancel)
    with pytest.raises(KeyboardInterrupt) as raised:
        selection.replace_saved_worker(tmp_path, replacement.plugin_id, (), expected_reference_sha256=revision)
    assert raised.value is cancellation and reference.read_bytes() == raw
