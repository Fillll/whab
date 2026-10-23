"""Stopped visual repair owns one exact CAS decision and never opens old code."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from whab.plugins import Plugin, PluginContract, PluginError, PluginResources, UserInterface
from whab.plugins import ui_repair as repair
from whab.plugins.effect_store import EffectError
from whab.plugins.ui_selection import DISABLED_GENERATION, UISelectionStore

ID = "fictional-ui"
OLD = {"environment": "a" * 64, "distribution": "fictional-ui", "version": "1.0.0"}
NEW = {"environment": "b" * 64, "distribution": "fictional-ui", "version": "2.0.0"}


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
        assert home == tmp_path and plugin_id == ID and approvals == ("reviewed",)
        calls.append(True)
        return SimpleNamespace(generation="2" * 64), dict(NEW)

    monkeypatch.setattr(repair, "_candidate", candidate)
    return tmp_path, path, raw, revision, calls


def replace(saved, **values):
    home, _, _, revision, _ = saved
    return repair.replace_saved_visual_worker(home, "fictional", "sandbox", ID, ("reviewed",),
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
        repair.replace_saved_visual_worker(home, "other-budget", "sandbox", ID, ("reviewed",), expected_revision=revision)
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
    with pytest.raises(PluginError):
        repair.disable_saved_visual(home, "fictional", "sandbox", expected_revision=revision)
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

    def prepare(*args):
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

    def start(template, environment, home):
        assert environment is prepared and home == tmp_path and template.id == ID
        child = Child()
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
