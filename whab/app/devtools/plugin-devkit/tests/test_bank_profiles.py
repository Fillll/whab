"""Synthetic HTTP adapter tests; installed native evidence is tested separately."""

import copy
import shutil
from types import SimpleNamespace

import pytest
from whab_plugin_devkit import bank_conformance as bank
from whab_plugin_devkit import runtime_profiles as profiles

from whab import plugins as sdk


class ScriptedBankHTTP:
    """Detached fictional responses, with a captured public request transcript."""

    transition_timeout = 1

    def __init__(self, *, permits_retirement=False):
        self.data = {"accounts": [], "transactions": []}
        self.flows, self.calls = {}, []
        self.cleanups, self.permits_retirement = 0, permits_retirement

    def stop(self):
        self.cleanups += 1

    def start(self):
        pass

    def wait(self, observe, **_):
        result = observe()
        assert result
        return result

    def expect(self, method, path, body=None, **_):
        self.calls.append((method, path, copy.deepcopy(body)))
        result = self._request(method, path, body)
        return copy.deepcopy(result)

    def _request(self, method, path, body):
        if path.startswith("/api/workspace"):
            return {"accounts": [], "transactions": []} if "workspace=demo" in path else self.data
        if path == "/api/plugins":
            return {"plugins": [{"id": "sqlite"}, {"id": "fictional-bank"}]}
        if path == "/api/plugins/management/transition":
            return {"code": "would_admit" if self.permits_retirement else "continuation_busy"}
        if "/continuations/" in path:
            flow = self.flows[path.rsplit("/", 1)[-1]]
            if method == "DELETE":
                flow["stage"] = "cancelled"
            return flow
        if path.endswith("/link-token"):
            kind = "update" if body.get("itemId") else "link"
            for flow in self.flows.values():
                if flow["kind"] == kind and flow["stage"] == "ready":
                    return flow
            identifier = f"{len(self.flows):032x}"
            self.flows[identifier] = {"flow_id": identifier, "kind": kind, "stage": "ready", "link_token": "fictional-link-token"}
            return self.flows[identifier]
        if path.endswith("/exchange"):
            flow = self.flows[body["flowId"]]
            if flow["stage"] != "complete" and flow["kind"] == "link":
                self.data = {"accounts": [{"id": "fictional-account", "source": "fictional-bank", "name": "Fictional checking", "currency": "USD", "balance": 10000}],
                             "transactions": [{"id": "pending", "source": "fictional-bank", "pending": True,
                                               "merchant": "Fictional grocer", "amount": 725}]}
            flow["stage"] = "complete"
            return {"flow_id": flow["flow_id"], "stage": "complete"}
        if method == "PATCH":
            self.data["transactions"][0].update(body)
            return {}
        if path.endswith("/sync"):
            row = self.data["transactions"][0]
            if row["pending"]:
                row.update(id="posted", pending=False)
                return {"ok": True, "added": 1, "modified": 0, "removed": 1}
            return {"ok": True, "added": 0, "modified": 0, "removed": 0}
        assert method == "DELETE" and "/items/" in path
        return {}


@pytest.mark.parametrize("variant", bank.BANK_VARIANTS)
@pytest.mark.parametrize("disconnect", [True, False])
def test_offline_flow_variants_preserve_annotation_and_exact_public_flow_identity(variant, disconnect):
    runtime = ScriptedBankHTTP()
    scenario = bank.fictional_scenario("fictional-bank")
    result = bank.exercise_bank_runtime(runtime, scenario, variant=variant, disconnect=disconnect)
    assert result["variant"] == variant and ("disconnect" in result["steps"]) is disconnect
    posted, = runtime.data["transactions"]
    assert posted["note"] == "Fictional retained annotation" and posted["reviewed"]
    exchanges = [body for _, path, body in runtime.calls if path.endswith("/exchange")]
    assert exchanges[-1].keys() == {"flowId"}  # Update completion cannot exchange a new public token.
    assert len(exchanges) == (3 if variant == "completion-replay" else 2)


def test_bank_fixture_refuses_retirement_success_and_malformed_variant():
    with pytest.raises(AssertionError, match="defer provider retirement"):
        bank.exercise_bank_runtime(ScriptedBankHTTP(permits_retirement=True), bank.fictional_scenario("fictional-bank"), variant="ready-defer")
    for options in ({"variant": "unreviewed"}, {"disconnect": 1}):
        with pytest.raises(ValueError):
            bank.exercise_bank_runtime(None, bank.fictional_scenario("fictional-bank"), **options)


@pytest.fixture
def candidate(monkeypatch):
    # Older toolkit hosts can unit-test this data-only predicate; this fake does
    # not claim their host can run a typed provider or native bank profile.
    class Adapter:
        pass
    monkeypatch.setattr(sdk, "BankAdapter", Adapter, raising=False)
    contract = SimpleNamespace(lifecycle="live", authority="local", state_version=1, state_upgrades=(),
                               provides=(SimpleNamespace(name="ingestion.bank", version=1), SimpleNamespace(name="lifecycle.background", version=1)))
    return SimpleNamespace(id="fictional-bank", bank=Adapter(), contract=contract)


def test_bank_predicate_is_pure_and_rejects_restart_mixed_or_incomplete_declarations(candidate):
    assert bank.bank_declaration(candidate)["fixture"] == "fictional-v1"
    assert profiles._bank_candidate(candidate)
    assert not profiles._bank_candidate(SimpleNamespace(bank=None))
    for change in ({"settings": object()}, {"bank": lambda _: None}, {"contract": None}):
        changed = copy.copy(candidate)
        changed.__dict__.update(change)
        assert not profiles._bank_candidate(changed)
    candidate.contract.provides = ()
    with pytest.raises(ValueError, match="capabilities"):
        bank.bank_declaration(candidate)


def test_ten_bank_profiles_require_explicit_offline_approval_and_both_native_backends(candidate, monkeypatch):
    from whab_plugin_devkit import configurations

    # Resolver sampling is tested with real public Plugins elsewhere. Preserve
    # its reviewed manifests while isolating bank applicability on old hosts.
    report = sdk.Plugin("fictional-bank", "Report fixture", insights=lambda _: ())
    reference = sdk.Plugin("sqlite", "Reference", storage=lambda _: None)
    artifacts = [{"distribution": "whab", "version": "2.38.0", "filename": "whab.whl", "sha256": "0" * 64}]
    originals = configurations.sample_configurations(report, reference, artifacts)
    monkeypatch.setattr(profiles, "sample_configurations", lambda *_args, **_kwargs: copy.deepcopy(originals))
    monkeypatch.setattr(profiles, "resolve_plugins", lambda selected: selected)
    candidate.contract.requires = ()
    manifests = profiles.sample_runtime_configurations(candidate, reference, artifacts, bank_fixture="fictional-v1")
    assert len({(row["runtime"]["primary"], row["runtime"]["bank"]["variant"]) for row in manifests}) == 10
    assert len({tuple(row["peers"]) for row in manifests}) == 10
    for manifest in manifests:
        assert profiles.validate_runtime_manifest(manifest) == manifest
    missing = profiles.sample_runtime_configurations(candidate, reference, artifacts)[0]
    assert missing["runtime"]["candidate_applicability"] == "not-applicable: unapproved bank fixture"
    with pytest.raises(ValueError, match="Opt in"):
        profiles.sample_runtime_configurations(candidate, reference, artifacts, bank_fixture="real-provider")
    with pytest.raises(ValueError, match="Opt in"):
        profiles.sample_runtime_configurations(report, reference, artifacts, bank_fixture="fictional-v1")
    changed = copy.deepcopy(manifests[0])
    changed["runtime"]["bank"]["fixture"] = "real-provider"
    with pytest.raises(ValueError, match="reviewed"):
        profiles.validate_runtime_manifest(changed)


def test_bank_profile_uses_public_worker_controls_and_keeps_connection_through_authority_steps(tmp_path, monkeypatch):
    if not hasattr(sdk, "BankAdapter"):
        assert profiles.MINIMUM_BANK_HOST == "2.38.0"
        return  # No old-host native bank claim; installed owner tests require >=2.38.
    candidate = sdk.Plugin("fictional-bank", "Offline", bank=sdk.BankAdapter(lambda _: pytest.fail("Unit RPC fake never constructs providers")),
        contract=sdk.PluginContract(lifecycle="live", authority="local", provides=(sdk.Capability("ingestion.bank"), sdk.Capability("lifecycle.background"))))
    reference = sdk.Plugin("sqlite", "Reference", storage=lambda _: None)
    artifacts = [{"distribution": "whab", "version": "2.38.0", "filename": "host.whl", "sha256": "0" * 64}]
    manifest = profiles.sample_runtime_configurations(candidate, reference, artifacts, combinations_count=1, bank_fixture="fictional-v1")[0]
    monkeypatch.setattr(profiles, "replay_configuration", lambda *_: {})
    monkeypatch.setattr(profiles, "api_worker_wheels", lambda *_: [{"path": "/approved/fictional.whl", "sha256": "a" * 64}])
    observed = []

    class Native(ScriptedBankHTTP):
        def __init__(self, storage, selection):
            super().__init__()
            self.storage, self.selection = storage, selection
            self.home, self.process = tmp_path / "native", object()
            self.home.mkdir()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.stop()
            shutil.rmtree(self.home)

        def start(self):
            self.process = object()

        def stop(self, **_):
            self.cleanups += int(self.process is not None)
            self.process = None

        def operation(self, *args):
            observed.append(args)

        def expect(self, method, path, body=None, **options):
            if options.get("status") == 409:
                return {}
            return super().expect(method, path, body, **options)

        def _request(self, method, path, body):
            if path == "/api/health":
                return {"version": "2.38.0"}
            if path == "/api/plugins":
                return {"plugins": [{"id": name} for name in [self.storage, *self.selection]]}
            if path == "/api/plugins/runtime":
                return {"workers": [{"plugin": candidate.id, "health": "ready"}]}
            if path == "/api/plugins/management/upgrade":
                assert body["pluginId"] == candidate.id and len(body["wheels"]) == 1
                return {"id": "worker"}
            if path == "/api/plugins/management/transition":
                self.selection = body["plugins"]
                return {"id": "selection"}
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
                self.data["accounts"].append({"id": "manual", "balance": 125})
                return {"accountId": "manual"}
            return super()._request(method, path, body)

    monkeypatch.setattr(profiles, "BankRuntime", Native)
    result = profiles.replay_runtime_configuration(manifest, candidate, reference, wheelhouse=tmp_path)
    assert result["runtime"]["bank"]["variant"] == "standard"
    assert result["runtime"]["cleanup_complete"] is True
    assert ("/api/plugins/runtime", "worker") in observed
    missing = profiles.sample_runtime_configurations(candidate, reference, artifacts, combinations_count=1)[0]
    assert profiles.replay_runtime_configuration(missing, candidate, reference, native=lambda *_: pytest.fail("No unapproved fixture starts"))["runtime"]["status"] == "not-applicable"
