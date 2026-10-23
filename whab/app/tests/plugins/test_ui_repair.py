"""Stopped visual repair owns one exact CAS decision and never opens old code."""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from whab.plugins import Plugin, PluginContract, PluginError, PluginResources, UserInterface
from whab.plugins import ui_repair as repair
from whab.plugins.effect_store import EffectError
from whab.plugins.environments import ApprovedWheel
from whab.plugins.ui_selection import DISABLED_GENERATION, UISelectionStore

ID = "fictional-ui"
OLD = {"environment": "a" * 64, "distribution": "fictional-ui", "version": "1.0.0"}
NEW = {"environment": "b" * 64, "distribution": "fictional-ui", "version": "2.0.0"}


def approvals(home):
    return (ApprovedWheel(home / "fictional_ui-2.0.0-py3-none-any.whl", "c" * 64),)


@pytest.fixture
def saved(tmp_path, monkeypatch):
    with UISelectionStore(tmp_path, "fictional", "sandbox", ID) as selected:
        selected.publish({"id": str(uuid4()), "selection": ID, "generation": "1" * 64, "worker": OLD},
                         selected.revision(selected.snapshot()))
        revision = selected.revision(selected.snapshot())
    path = tmp_path / "visual-ui/visual-selection.json"
    raw = path.read_bytes()
    calls = []

    def candidate(home, plugin_id, approvals):
        assert home == tmp_path and plugin_id == ID and len(approvals) == 1
        calls.append(True)
        return SimpleNamespace(generation="2" * 64), dict(NEW)

    monkeypatch.setattr(repair, "_candidate", candidate)
    return tmp_path, path, raw, revision, calls


def replace(saved, **values):
    home, _, _, revision, _ = saved
    return repair.replace_saved_visual_worker(home, "fictional", "sandbox", ID, approvals(home),
                                             expected_revision=values.get("revision", revision))


def test_current_approved_repair_preserves_exact_prior_bytes_and_retained_choice(saved):
    home, path, raw, revision, calls = saved
    assert repair.visual_worker_revision(home, "fictional", "sandbox") == revision
    decision = replace(saved)
    assert calls == [True] and decision["worker"] == NEW and path.read_bytes() != raw
    assert (home / "visual-ui/history" / (revision + ".json")).read_bytes() == raw
    with UISelectionStore(home, "fictional", "sandbox", None) as selected:
        record = selected.snapshot()
        assert record["worker"] == record["workers"][ID] == NEW
        assert record["known"] == [ID] and len(record["decisions"]) == 2


def test_completed_retry_needs_no_wheel_files_or_candidate_even_after_later_disable(saved, monkeypatch):
    home, path, _, _, calls = saved
    decision = replace(saved)
    monkeypatch.setattr(repair, "_candidate", lambda *_: pytest.fail("A durable retry cannot start code"))
    assert not approvals(home)[0].path.exists()
    assert replace(saved) == decision and calls == [True]
    revision = repair.visual_worker_revision(home, "fictional", "sandbox")
    repair.disable_saved_visual(home, "fictional", "sandbox", expected_revision=revision)
    disabled = path.read_bytes()
    assert replace(saved) == decision and path.read_bytes() == disabled
    changed = (ApprovedWheel(approvals(home)[0].path, "d" * 64),)
    with pytest.raises(EffectError) as caught:
        repair.replace_saved_visual_worker(home, "fictional", "sandbox", ID, changed,
                                            expected_revision=saved[3])
    assert caught.value.code == "effect_conflict" and path.read_bytes() == disabled


def test_history_stream_conversion_failure_closes_raw_descriptor_and_preserves_decision(saved, monkeypatch):
    home, path, original, revision, _ = saved
    descriptors = []
    def failed_open(descriptor, *_, **__):
        descriptors.append(descriptor)
        raise OSError("fictional stream conversion failure")
    with UISelectionStore(home, "fictional", "sandbox", None) as selected:
        with monkeypatch.context() as failure:
            failure.setattr(repair.os, "fdopen", failed_open)
            with pytest.raises(OSError):
                repair._preserve(selected, revision)
        assert selected.snapshot()["selection"] == ID
    assert path.read_bytes() == original and len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert not list((home / "visual-ui/history").iterdir())


@pytest.mark.parametrize("stage", ["probe", "startup", "resources"])
def test_partial_start_retains_cleanup_and_blocks_another_child(tmp_path, monkeypatch, stage):
    from whab.plugins import worker_selection

    class Partial:
        cleanup_pending = True
        def close(self):
            pass

    partial = Partial()
    prepared = SimpleNamespace(identifier=NEW["environment"], distribution=NEW["distribution"],
                               version=NEW["version"], python="reviewed-current-python")
    started = []

    def prepare(*_, worker_owner):
        if stage == "probe":
            started.append(stage)
            worker_owner(partial)
            raise RuntimeError("fictional failed preparation probe")
        return prepared

    def start(*_, on_create, **__):
        started.append(stage)
        on_create(partial)
        if stage == "startup":
            raise RuntimeError("fictional failed hello")
        partial.info = {"live": True, "adapters": {"bank_logos": None, "file_import": None, "ui": {}}}
        partial.visual_resources = lambda: (_ for _ in ()).throw(RuntimeError("fictional malformed resources"))
        return partial

    monkeypatch.setattr(repair, "prepare_environment", prepare)
    monkeypatch.setattr(worker_selection, "ReportWorker", start)
    with pytest.raises(RuntimeError):
        repair._candidate(tmp_path, ID, approvals(tmp_path))
    assert repair._CLEANUP[str(tmp_path.resolve())] == [partial]
    with pytest.raises(PluginError):
        repair._candidate(tmp_path, ID, approvals(tmp_path))
    assert started == [stage]
    partial.cleanup_pending = False
    repair._reap(tmp_path)
    assert str(tmp_path.resolve()) not in repair._CLEANUP


@pytest.mark.parametrize("phase", ["before_replace", "after_replace", "after_directory_fsync"])
def test_lost_publication_retry_keeps_one_repair_decision_and_does_not_select_after_later_disable(saved, monkeypatch, phase):
    home, path, raw, revision, _ = saved
    owner = repair._owner
    faults = []

    def open_owner(*args):
        selected = owner(*args)
        def fail(boundary):
            if boundary == phase and not faults:
                faults.append(True)
                raise OSError("fictional lost visual repair response")
        selected.store.fault = fail
        return selected

    monkeypatch.setattr(repair, "_owner", open_owner)
    with pytest.raises(EffectError) as caught:
        replace(saved)
    assert caught.value.code == ("effect_commit_failed" if phase == "before_replace" else "effect_commit_uncertain")
    assert (path.read_bytes() == raw) is (phase == "before_replace")
    decision = replace(saved)
    assert decision == replace(saved)
    after = repair.visual_worker_revision(home, "fictional", "sandbox")
    disabled = repair.disable_saved_visual(home, "fictional", "sandbox", expected_revision=after)
    assert disabled["selection"] is None and disabled["generation"] == DISABLED_GENERATION
    latest = path.read_bytes()
    assert replace(saved) == decision and path.read_bytes() == latest
    with UISelectionStore(home, "fictional", "sandbox", None) as selected:
        assert selected.snapshot()["selection"] is None
        assert selected.snapshot()["workers"][ID] == NEW


def test_running_owner_wrong_scope_bad_revision_and_missing_decision_never_start_candidate(saved):
    home, path, raw, revision, calls = saved
    with UISelectionStore(home, "fictional", "sandbox", None):
        with pytest.raises(EffectError, match="Another writer"):
            replace(saved)
        with pytest.raises(PluginError):
            repair.visual_worker_revision(home, "fictional", "sandbox")
    for value in ("../private", "c" * 64, None):
        with pytest.raises(PluginError):
            replace(saved, revision=value)
    with pytest.raises(EffectError):
        repair.replace_saved_visual_worker(home, "other-budget", "sandbox", ID, approvals(home), expected_revision=revision)
    assert not calls and path.read_bytes() == raw
    path.unlink()
    with pytest.raises(PluginError):
        replace(saved)
    assert not path.exists() and not calls


def test_failed_candidate_and_conflicting_history_preserve_saved_choice(saved, monkeypatch):
    home, path, raw, revision, _ = saved
    def broken(*_):
        raise RuntimeError("fictional private candidate detail")
    monkeypatch.setattr(repair, "_candidate", broken)
    with pytest.raises(PluginError) as caught:
        replace(saved)
    assert "private candidate" not in str(caught.value) and path.read_bytes() == raw
    history = home / "visual-ui/history"
    history.mkdir()
    (history / (revision + ".json")).write_bytes(b"conflicting historical decision")
    (history / (revision + ".json")).chmod(0o600)
    with pytest.raises(EffectError) as caught:
        repair.disable_saved_visual(home, "fictional", "sandbox", expected_revision=revision)
    assert caught.value.code == "invalid_effect"
    assert path.read_bytes() == raw


def test_history_symlink_and_unrecognized_newer_schema_are_preserved(saved):
    home, path, raw, revision, calls = saved
    outside = home / "preserved-outside"
    outside.mkdir()
    (home / "visual-ui/history").symlink_to(outside, target_is_directory=True)
    with pytest.raises(EffectError):
        replace(saved)
    assert path.read_bytes() == raw and not list(outside.iterdir())
    (home / "visual-ui/history").unlink()
    path.write_bytes(raw.replace(b'"format":1', b'"format":99', 1))
    corrupted = path.read_bytes()
    with pytest.raises(EffectError):
        replace(saved)
    assert path.read_bytes() == corrupted


@pytest.mark.parametrize("failure", [None, "render", "cleanup"])
def test_candidate_runs_only_fresh_approved_visual_code_and_retains_failed_cleanup(tmp_path, monkeypatch, failure):
    prepared = SimpleNamespace(identifier=NEW["environment"], distribution=NEW["distribution"], version=NEW["version"])
    preparations, workers = [], []

    def prepare(*args, **kwargs):
        preparations.append(args)
        return prepared

    class Child:
        def __init__(self):
            self.closed = 0
            self.cleanup_pending = failure == "cleanup"
            self.pages = []

        def render_ui(self, arguments):
            self.pages.append(arguments["input"]["page"])
            if failure == "render":
                raise RuntimeError("fictional private readiness failure")
            return b"<p>fictional</p>"

        def close(self):
            self.closed += 1

    def start(template, environment, home, *, worker_owner):
        assert environment is prepared and home == tmp_path and template.id == ID
        child = Child()
        worker_owner(child)
        workers.append(child)
        plugin = Plugin(ID, "Reviewed fictional UI", contract=PluginContract(lifecycle="live"), ui=UserInterface(
            None, None, lambda *_args, **_kwargs: {}, resources=PluginResources({"templates/base.html": b"<p>fictional</p>"}),
            view_protocol=1))
        return plugin, SimpleNamespace(worker=child)

    monkeypatch.setattr(repair, "prepare_environment", prepare)
    monkeypatch.setattr(repair, "_start", start)
    if failure:
        with pytest.raises(RuntimeError if failure == "render" else PluginError):
            repair._candidate(tmp_path, ID, ("reviewed",))
        assert workers[0].closed and len(preparations) == 1
        if failure == "cleanup":
            with pytest.raises(PluginError):
                repair._candidate(tmp_path, ID, ("reviewed",))
            assert len(preparations) == 1 and len(workers) == 1
            workers[0].cleanup_pending = False
            repair._reap(tmp_path)
            assert str(tmp_path.resolve()) not in repair._CLEANUP
    else:
        visual, reference = repair._candidate(tmp_path, ID, ("reviewed",))
        assert reference == NEW and visual.plugin_id == ID
        assert workers[0].closed == 1 and workers[0].pages == ["budget", "transactions", "insights", "accounts"]
        assert repair._CLEANUP.get(str(tmp_path.resolve())) is None
