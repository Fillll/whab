"""API declarations stay data-only; the installed generated gate runs real HTTPS."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from whab_plugin_devkit import api_declaration, assert_api_interface
from whab_plugin_devkit import api_interface as api
from whab_plugin_devkit import runtime_harness as harness

from whab import plugins as sdk


def descriptor(**changes):
    if not hasattr(sdk, "RemoteLedgerInterface"):
        return None
    return sdk.Plugin("fictional-api", "Fictional API", interface=sdk.Interface(adapter=sdk.RemoteLedgerInterface(token_setting="FICTIONAL_API_TOKEN", **changes)), contract=sdk.PluginContract(lifecycle="live"))


def test_declaration_revalidates_exact_public_data_and_never_calls_a_factory(monkeypatch):
    plugin = descriptor()
    if plugin is None:
        with pytest.raises(ValueError, match="2.34"):
            api_declaration(SimpleNamespace(interface=None))
        return
    assert api_declaration(plugin) == {"protocol_version": 1, "token_setting": "FICTIONAL_API_TOKEN", "queries": True, "commands": True}
    with pytest.raises(ValueError):
        api_declaration(SimpleNamespace(interface=SimpleNamespace(adapter=plugin.interface.adapter, factory=lambda: pytest.fail("Never execute a router factory"))))
    with pytest.raises(ValueError):
        api_declaration(SimpleNamespace(interface=SimpleNamespace(adapter=object(), factory=None)))
    alien = descriptor()
    object.__setattr__(alien.interface.adapter, "token_setting", "OTHER_TOKEN")
    with pytest.raises(ValueError, match="configuration prefix"):
        api_declaration(alien)
    object.__setattr__(plugin.interface.adapter, "queries", 1)
    with pytest.raises(ValueError):
        api_declaration(plugin)
    monkeypatch.delattr(sdk, "RemoteLedgerInterface")
    with pytest.raises(ValueError):
        api_declaration(plugin)


class FakeAPI:
    def __init__(self, fault=None):
        self.calls = []
        self.writes = 0
        self.fault = fault

    def expect(self, method, path, body=None, **options):
        self.calls.append((method, path, body, options))
        if options.get("status", 200) != 200:
            return {}
        if path == "/api/budget":
            return {"id": "fictional-budget"}
        if method == "POST":
            self.writes += 1
            return {"revision": "different" if self.fault == "retry" and self.writes == 2 else "after", "data": "created"}
        return {"revision": "before", "data": [] if self.writes == 0 or self.fault == "missing" else [{"id": "created", "balance": 125 if self.fault != "balance" else 126}]}


@pytest.mark.parametrize("changes", [{}, {"commands": False}, {"queries": False}])
def test_api_probe_exercises_declared_host_protocol_and_never_emits_token_values(changes):
    plugin = descriptor(**changes)
    if plugin is None:
        assert api.MINIMUM_API_HOST == "2.34.0"
        return
    runtime = FakeAPI()
    result = assert_api_interface(runtime, plugin)
    assert result["guards"] == "passed"
    assert all("Authorization" not in str(body) for _, _, body, _ in runtime.calls)
    assert any(options.get("status") == 401 for _, _, _, options in runtime.calls)
    if not changes:
        assert result["command"] == result["retry"] == "passed"
        assert runtime.writes == 2
    else:
        assert runtime.writes == 0


@pytest.mark.parametrize("fault", ["retry", "missing", "balance"])
def test_api_probe_rejects_changed_acknowledgements_and_invalid_financial_results(fault):
    plugin = descriptor()
    if plugin is None:
        assert api.MINIMUM_API_HOST == "2.34.0"
        return
    with pytest.raises(AssertionError):
        assert_api_interface(FakeAPI(fault), plugin)


@pytest.mark.parametrize("setting", [True, "", "x", "X" * 101, "WHAB_HOME", "PORT", "PYTHONPATH", "LC_MESSAGES", "TOKEN_ENCRYPTION_KEY"])
def test_native_harness_refuses_token_settings_that_can_replace_runtime_selectors(setting):
    with pytest.raises(ValueError, match="dedicated host API"):
        harness.NativeRuntime("sqlite", (), api_token_setting=setting)


def test_native_harness_generates_its_own_credential_and_confines_authenticated_requests(monkeypatch):
    monkeypatch.setenv("FICTIONAL_API_TOKEN", "caller-value-must-not-enter-runtime")
    runtime = harness.NativeRuntime("sqlite", (), api_token_setting="FICTIONAL_API_TOKEN")
    requests = []
    class Connection:
        def __init__(self, *_, **__):
            pass
        def request(self, *args, **options):
            requests.append((args, options))
        def getresponse(self):
            return SimpleNamespace(status=200, read=lambda _: b'{"ok":true}')
        def close(self):
            pass
    monkeypatch.setattr(harness.http.client, "HTTPSConnection", Connection)
    try:
        token = runtime.environment()["FICTIONAL_API_TOKEN"]
        assert len(token) >= 32 and token != "caller-value-must-not-enter-runtime"
        with pytest.raises(ValueError, match="remote protocol"):
            runtime.request("GET", "/api/budget", authenticated=True)
        assert runtime.expect("POST", "/remote/v1/example", {}, authenticated=True, headers={"Host": "fictional.example"}) == {"ok": True}
        assert requests[0][1]["headers"] == {"Authorization": "Bearer " + token, "Host": "fictional.example", "Content-Type": "application/json"}
    finally:
        runtime.__exit__(None, None, None)
    plain = harness.NativeRuntime("sqlite", ())
    try:
        with pytest.raises(ValueError, match="remote protocol"):
            plain.request("GET", "/remote/v1/example", authenticated=True)
    finally:
        plain.__exit__(None, None, None)


def worker_metadata(monkeypatch, *, requires=None):
    package = SimpleNamespace(metadata={"Name": "fictional_api"})
    monkeypatch.setattr(api.metadata, "entry_points", lambda **_: [SimpleNamespace(name="fictional-api", dist=package)])
    versions = {"fictional-api": "1", "whab": "2.34.0", "runtime": "1"}
    requirements = {"fictional-api": ["whab>=2.34", "unused; python_version < '3'", "unused; extra == 'test'"], "whab": ["runtime>=1"], "runtime": ["whab>=2.34"]}
    if requires is not None:
        requirements["fictional-api"] = requires
    monkeypatch.setattr(api.metadata, "distribution", lambda name: SimpleNamespace(version=versions[name], requires=requirements[name]))
    records = [{"distribution": name, "version": version, "filename": name + ".whl", "sha256": "a" * 64} for name, version in versions.items()]
    return records


def test_api_worker_uses_only_exact_transitive_runtime_artifacts_not_backend_toolkit_or_peers(monkeypatch, tmp_path):
    records = worker_metadata(monkeypatch)
    records += [{"distribution": name, "version": "1", "filename": name + ".whl", "sha256": "b" * 64} for name in ("whab-plugin-sqlite", "whab-plugin-devkit", "whab-devkit-runtime-peers")]
    closure = api.api_worker_wheels(SimpleNamespace(id="fictional-api"), records, tmp_path)
    assert {Path(row["path"]).name for row in closure} == {"fictional-api.whl", "whab.whl", "runtime.whl"}
    assert all(row["sha256"] == "a" * 64 for row in closure)
    monkeypatch.setattr(api.metadata, "entry_points", lambda **_: [])
    with pytest.raises(ValueError, match="one installed"):
        api.api_worker_wheels(SimpleNamespace(id="fictional-api"), records, tmp_path)


@pytest.mark.parametrize("failure", ["wheelhouse", "missing", "version", "url", "extra", "dependency", "specifier", "no-requires", "bound"])
def test_api_worker_refuses_unapproved_or_incompatible_dependency_closures(monkeypatch, tmp_path, failure):
    records = worker_metadata(monkeypatch, requires={
        "url": ["runtime @ https://invalid.example/never.whl"], "extra": ["runtime[optional]"],
        "dependency": ["missing"], "specifier": ["runtime>=2"], "no-requires": [],
    }.get(failure))
    if failure == "missing":
        records = [row for row in records if row["distribution"] != "runtime"]
    if failure == "version":
        records[0]["version"] = "2"
    if failure == "bound":
        records = [{"distribution": str(index), "version": "1", "filename": str(index) + ".whl", "sha256": "a" * 64} for index in range(129)]
        records += [{"distribution": "fictional-api", "version": "1", "filename": "candidate.whl", "sha256": "a" * 64}, {"distribution": "whab", "version": "2.34.0", "filename": "whab.whl", "sha256": "a" * 64}]
        monkeypatch.setattr(api.metadata, "distribution", lambda name: SimpleNamespace(version="2.34.0" if name == "whab" else "1", requires=[str(index) for index in range(129)] if name == "fictional-api" else None))
    if failure == "no-requires":
        assert api.api_worker_wheels(SimpleNamespace(id="fictional-api"), records, tmp_path)
    else:
        with pytest.raises(ValueError):
            api.api_worker_wheels(SimpleNamespace(id="fictional-api"), records, None if failure == "wheelhouse" else tmp_path)
