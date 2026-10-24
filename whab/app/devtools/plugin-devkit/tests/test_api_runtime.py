"""The API runtime recipe is versioned separately and never counts old-host N/A."""

import copy
import shutil
from importlib import metadata

import pytest
from whab_plugin_devkit import runtime_profiles as profiles

from .test_api_interface import descriptor


def inputs():
    entry, = metadata.distribution("whab_plugin_sqlite").entry_points
    return descriptor(), entry.load(), [{"distribution": "whab", "version": metadata.version("whab"), "filename": "approved-host.whl", "sha256": "a" * 64}]


def test_new_api_profile_has_its_actual_floor_and_preserves_the_old_reader():
    candidate, reference, artifacts = inputs()
    if candidate is None:
        assert profiles.MINIMUM_API_HOST == "2.34.0"
        return
    manifest = profiles.sample_runtime_configurations(candidate, reference, artifacts, combinations_count=1)[0]
    assert manifest["runtime"]["profile"] == "native-api-v1"
    assert manifest["runtime"]["minimum_host"] == "2.34.0"
    assert "api-worker-readiness" in manifest["runtime"]["steps"]
    assert profiles.validate_runtime_manifest(manifest) == manifest
    old = copy.deepcopy(artifacts)
    old[0]["version"] = "2.22.0"  # A schema/applicability unit fixture, not an artifact test.
    older = profiles.sample_runtime_configurations(candidate, reference, old, combinations_count=1)[0]
    result = profiles.replay_runtime_configuration(older, candidate, reference, native=lambda *_: pytest.fail("No unsupported host starts"))
    assert result["runtime"]["status"] == "not-applicable"
    wrong = copy.deepcopy(manifest)
    wrong["runtime"].update(profile="native-local-v1", minimum_host="2.32.0", steps=profiles._STEPS.copy())
    with pytest.raises(ValueError, match="applicability declaration"):
        profiles.replay_runtime_configuration(wrong, candidate, reference)


def test_legacy_or_partial_api_declarations_do_not_claim_the_complete_runtime_profile():
    candidate, reference, artifacts = inputs()
    if candidate is None:
        assert profiles.MINIMUM_API_HOST == "2.34.0"
        return
    from whab.plugins import Interface, Plugin
    legacy = Plugin("legacy-api", "Legacy", interface=Interface(lambda _: pytest.fail("Never execute a legacy router factory")))
    assert profiles._api_candidate(legacy) is None
    limited = descriptor(commands=False)
    manifest = profiles.sample_runtime_configurations(limited, reference, artifacts, combinations_count=1)[0]
    assert manifest["runtime"]["profile"] == "native-local-v1"
    assert manifest["runtime"]["candidate_applicability"].startswith("not-applicable")
    result = profiles.replay_runtime_configuration(manifest, limited, reference, native=lambda *_: pytest.fail("No limited API financial profile starts"))
    assert result["runtime"]["status"] == "not-applicable"


def test_api_profile_exercises_candidate_selection_worker_and_reopen_through_public_controls(tmp_path, monkeypatch):
    candidate, reference, artifacts = inputs()
    if candidate is None:
        assert profiles.MINIMUM_API_HOST == "2.34.0"
        return
    manifest = profiles.sample_runtime_configurations(candidate, reference, artifacts, combinations_count=1)[0]
    observations = []
    monkeypatch.setattr(profiles, "assert_api_interface", lambda runtime, plugin: {"guards": "passed", "command": "passed"})
    monkeypatch.setattr(profiles, "api_worker_wheels", lambda *_: [{"path": "/approved/fictional-api.whl", "sha256": "a" * 64}])
    worker = {"plugin": candidate.id, "version": "1", "environment": "a" * 64, "health": "ready"}
    class Native:
        def __init__(self, storage, selection, **options):
            assert options == {"api_token_setting": "FICTIONAL_API_TOKEN"}
            self.storage, self.selection = storage, selection
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
            observations.append(("operation", args))
        def wait(self, operation):
            return operation()
        def expect(self, method, path, body=None, **options):
            observations.append((method, path, body, options))
            if path == "/api/health":
                return {"version": artifacts[0]["version"]}
            if path == "/api/budget":
                return {"id": "fictional-budget"}
            if path == "/api/plugins/runtime":
                return {"workers": [worker]}
            if path == "/api/plugins":
                return {"plugins": [{"id": name} for name in [self.storage, *self.selection]]}
            if path == "/api/plugins/management/transition":
                if options.get("status") != 409:
                    self.selection = body["plugins"]
                return {"id": "selection"}
            if path == "/api/plugins/management/upgrade":
                assert body["pluginId"] == candidate.id and len(body["wheels"]) == 1
                return {"id": "worker"}
            if path.endswith("/preview"):
                self.destination = body["destination"]
                return {"preview_id": "preview"}
            if path.endswith("/promote") or path == "/api/storage/transition":
                self.storage = self.destination
                return {"id": "migration"}
            if path == "/api/storage/copies/transition":
                return {"id": "copy"}
            if path == "/api/storage/copies":
                return {"copies": [{"id": "copy", "state": "saved"}]}
            if path == "/api/storage/status":
                return {"storage": self.storage}
            if path == "/api/accounts":
                self.accounts.append({"id": "runtime-wallet", "balance": 125})
                return {"accountId": "runtime-wallet"}
            if path.startswith("/remote/v1/"):
                return {"data": self.accounts}
            assert path.startswith("/api/workspace?")
            return {"accounts": self.accounts}
    result = profiles.replay_runtime_configuration(manifest, candidate, reference, native=Native, wheelhouse=tmp_path)
    assert result["runtime"]["status"] == "passed" and result["runtime"]["api"]["worker"] == worker
    assert result["runtime"]["cleanup_complete"] is True
    assert any(call[0] == "GET" and call[-1].get("status") == 403 for call in observations if len(call) == 4)
