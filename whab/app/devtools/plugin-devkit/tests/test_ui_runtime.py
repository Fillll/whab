"""The visual recipe separates singleton UI choices from optional peers."""

import copy
import shutil
from dataclasses import replace
from importlib import metadata
from pathlib import Path

import pytest
from whab_plugin_devkit import runtime_profiles as profiles

from .test_visual_interface import descriptor


def test_visual_runtime_has_a_genuine_floor_and_preserves_old_manifest_profiles():
    candidate = descriptor()
    if candidate is None:
        assert profiles.MINIMUM_UI_HOST == "2.36.0"
        return
    entry, = metadata.distribution("whab_plugin_sqlite").entry_points
    reference = entry.load()
    artifacts = [{"distribution": "whab", "version": metadata.version("whab"), "filename": "host.whl", "sha256": "a" * 64}]
    manifests = profiles.sample_runtime_configurations(candidate, reference, artifacts)
    assert len({tuple(row["peers"]) for row in manifests}) == 10
    assert {row["runtime"]["primary"] for row in manifests} == {"sqlite", "json"}
    for manifest in manifests:
        assert profiles.validate_runtime_manifest(manifest)["runtime"]["profile"] == "native-ui-v1"
        assert manifest["runtime"]["minimum_host"] == "2.36.0"
        assert manifest["runtime"]["candidate_selection"] == "disable-enable"
    older = copy.deepcopy(artifacts)
    older[0]["version"] = "2.22.0"  # Schema fixture; genuine artifact tests run separately.
    manifest = profiles.sample_runtime_configurations(candidate, reference, older, combinations_count=1)[0]
    assert profiles.replay_runtime_configuration(manifest, candidate, reference, native=lambda *_: pytest.fail("No unsupported host"))["runtime"]["status"] == "not-applicable"
    assert not profiles._ui_candidate(replace(candidate, contract=None))


@pytest.mark.parametrize("resource_kind", ["memory", "paths", "no-static"])
def test_visual_runtime_worker_selection_and_saved_reopen_use_public_controls(tmp_path, monkeypatch, resource_kind):
    candidate = descriptor()
    if candidate is None:
        assert profiles.MINIMUM_UI_HOST == "2.36.0"
        return
    if resource_kind == "paths":
        candidate = replace(candidate, ui=replace(candidate.ui, resources=None, templates=Path("templates"), static=Path("static")))
    if resource_kind == "no-static":
        from whab.plugins import PluginResources
        candidate = replace(candidate, ui=replace(candidate.ui, resources=PluginResources({"templates/base.html": b"Fictional"})))
    entry, = metadata.distribution("whab_plugin_sqlite").entry_points
    reference = entry.load()
    artifacts = [{"distribution": "whab", "version": metadata.version("whab"), "filename": "host.whl", "sha256": "a" * 64}]
    manifest = profiles.sample_runtime_configurations(candidate, reference, artifacts, combinations_count=1)[0]
    worker = {"plugin": candidate.id, "version": "1", "environment": "b" * 64}
    changes = []
    def choose(runtime, identifier, **options):
        changes.append((identifier, options))
        runtime.visual = identifier
        return {"worker": worker}
    monkeypatch.setattr(profiles, "visual_change", choose)
    monkeypatch.setattr(profiles, "assert_visual_interface", lambda *args, **options: {"generation": "a" * 64, "asset": options["asset_name"]})
    monkeypatch.setattr(profiles, "api_worker_wheels", lambda *_: [{"path": "/approved/candidate.whl", "sha256": "c" * 64}])
    class Native:
        def __init__(self, storage, selection, **options):
            assert options == {"ui": candidate.id} and candidate.id not in selection
            self.storage, self.selection, self.visual = storage, selection, candidate.id
            self.home = tmp_path / "fictional-runtime"
            self.home.mkdir()
            self.process, self.cleanups, self.accounts = object(), 0, []
        def __enter__(self):
            return self
        def __exit__(self, *_):
            self.stop()
            shutil.rmtree(self.home)
        def stop(self, **_):
            self.cleanups += int(self.process is not None)
            self.process = None
        def start(self):
            self.process = object()
        def operation(self, *args):
            pass
        def wait(self, observe):
            return observe()
        def expect(self, method, path, body=None, **options):
            if options.get("status", 200) not in {200, 202}:
                return {}
            if path == "/api/health":
                return {"version": artifacts[0]["version"]}
            if path == "/api/ui/status":
                return {"active": {"plugin_id": self.visual, "worker": worker}}
            if path == "/api/plugins":
                return {"plugins": [{"id": name} for name in [self.storage, *self.selection, *([self.visual] if self.visual else [])]]}
            if path == "/api/plugins/management/transition":
                self.selection = body["plugins"]
                return {"id": "selection"}
            if path == "/api/storage/copies/preview" or path == "/api/storage/preview":
                return {"preview_id": "preview"}
            if path.endswith("/promote") or path == "/api/storage/transition":
                self.storage = manifest["runtime"]["destination"] if path.endswith("/promote") else manifest["runtime"]["primary"]
                return {"id": "storage"}
            if path == "/api/storage/copies/transition":
                return {"id": "copy"}
            if path == "/api/storage/copies":
                return {"copies": [{"id": "copy", "state": "saved"}]}
            if path == "/api/storage/status":
                return {"storage": self.storage}
            if path == "/api/accounts":
                self.accounts.append({"id": "account", "balance": 125})
                return {"accountId": "account"}
            assert path.startswith("/api/workspace?")
            return {"accounts": list(self.accounts)}
    result = profiles.replay_runtime_configuration(manifest, candidate, reference, native=Native, wheelhouse=tmp_path)
    assert result["runtime"]["status"] == "passed" and result["runtime"]["visual"]["worker"] == worker
    assert changes[0][1]["dirty"] is True and changes[0][1]["wheels"]
    assert any(identifier is None for identifier, _ in changes) and changes[-1][0] == candidate.id
