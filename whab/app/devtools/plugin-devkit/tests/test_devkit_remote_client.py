"""Remote profile wiring stays public, bounded and honest about uncertainty."""

import copy
import runpy
import sys
from types import SimpleNamespace

import httpx
import pytest
from whab_plugin_devkit import remote_client as remote
from whab_plugin_devkit import remote_launcher as launcher
from whab_plugin_devkit.runtime_harness import NativeRuntime

from whab import plugins as sdk


def descriptor():
    adapter = getattr(sdk, "RemoteLedgerClient", None)
    if adapter is None:
        return None
    return sdk.Plugin("fictional-remote", "Fictional remote", storage=adapter(
        url_setting="FICTIONAL_REMOTE_URL", token_setting="FICTIONAL_REMOTE_TOKEN",
        budget_setting="FICTIONAL_REMOTE_BUDGET_ID"),
        contract=sdk.PluginContract(authority="remote", lifecycle="live"))


def test_remote_declaration_has_a_real_floor_and_revalidates_data_without_configuration(monkeypatch):
    plugin = descriptor()
    assert remote.MINIMUM_REMOTE_HOST == "2.40.0"
    if plugin is None:
        with pytest.raises(ValueError, match="2.39"):
            remote.remote_declaration(SimpleNamespace(storage=None, contract=None))
        return
    assert remote.remote_declaration(plugin) == {
        "protocol_version": 1, "url_setting": "FICTIONAL_REMOTE_URL",
        "token_setting": "FICTIONAL_REMOTE_TOKEN", "budget_setting": "FICTIONAL_REMOTE_BUDGET_ID"}
    for storage, contract in ((None, plugin.contract), (plugin.storage, None),
                              (plugin.storage, SimpleNamespace(authority="local", lifecycle="live")),
                              (plugin.storage, SimpleNamespace(authority="remote", lifecycle="restart"))):
        with pytest.raises(ValueError):
            remote.remote_declaration(SimpleNamespace(storage=storage, contract=contract))
    object.__setattr__(plugin.storage, "protocol_version", True)
    with pytest.raises(ValueError):
        remote.remote_declaration(plugin)
    monkeypatch.delattr(sdk, "RemoteLedgerClient")
    with pytest.raises(ValueError):
        remote.remote_declaration(plugin)


class FakeRemote:
    def __init__(self, fault=None):
        self.fault, self.enabled, self.unknown = fault, True, False
        self.rows, self.calls = [], []
        self.reopened = False
        self.gated = False
        self.transition_timeout = 180
        self.server = SimpleNamespace(expect=self.server_expect)

    def server_expect(self, *_):
        return {"accounts": [] if self.fault == "authority" else copy.deepcopy(self.rows)}

    def wait(self, observe, *, timeout):
        assert timeout == self.transition_timeout
        return observe()

    def stop(self):
        self.calls.append("stop")

    def start(self):
        self.calls.append("start")
        self.reopened = True

    def expect(self, method, path, body=None, **options):
        self.calls.append((method, path, body, options))
        if options.get("status", 200) not in {200, 202}:
            return {}
        if path.endswith("/selection"):
            self.enabled = body["enabled"]
            if self.fault == "selection-unknown":
                self.unknown = True
            return {}
        if path.endswith("/status"):
            return {"enabled": self.enabled and self.fault != "disabled",
                    "unknown_outcome": self.unknown or self.reopened and self.fault == "reopen",
                    "receipt_lookup": self.fault == "receipt", "automatic_reconciliation": self.fault == "automatic",
                    "revision": "fictional-selection", "runtime": {"admission": "gated" if self.gated else "open", "pending": None, "phase": "active"}}
        if method == "POST":
            self.rows = [] if self.fault == "missing" else [{"name": "Fictional remote wallet", "balance": 3211 if self.fault == "balance" else 3210}]
            return {}
        return {"accounts": copy.deepcopy(self.rows)}


def test_remote_behavior_probe_uses_financial_services_and_explicit_same_binding_controls(monkeypatch):
    monkeypatch.setattr(remote, "remote_declaration", lambda _: {})
    runtime = FakeRemote()
    result = remote.assert_remote_client(runtime, SimpleNamespace(id="fictional-remote"))
    assert result["read_write"] == result["pause_enable"] == result["reopen"] == "passed"
    assert result["migration_copies"].startswith("not-applicable")
    assert result["automatic_reconciliation"] == "not-supported"
    choices = [call[2] for call in runtime.calls if isinstance(call, tuple) and call[1].endswith("/selection")]
    assert [body["enabled"] for body in choices] == [False, True]
    assert all(body["expectedRevision"] == "fictional-selection" for body in choices)
    assert runtime.calls.count("start") == runtime.calls.count("stop") == 1


def test_remote_selection_waits_for_the_observed_decision():
    runtime = FakeRemote()
    def wait(observe, *, timeout):
        assert timeout == runtime.transition_timeout
        runtime.enabled = True
        assert observe() is None
        runtime.enabled = False
        runtime.gated = True
        assert observe() is None
        runtime.gated = False
        return observe()
    runtime.wait = wait
    assert remote.remote_selection(runtime, SimpleNamespace(id="fictional-remote"), False)["enabled"] is False


@pytest.mark.parametrize("fault", ["disabled", "receipt", "automatic", "missing", "balance", "authority", "selection-unknown", "reopen"])
def test_remote_probe_rejects_broken_authority_and_never_counts_unknown_as_success(monkeypatch, fault):
    monkeypatch.setattr(remote, "remote_declaration", lambda _: {})
    with pytest.raises(AssertionError):
        remote.assert_remote_client(FakeRemote(fault), SimpleNamespace(id="fictional-remote"))


def test_remote_runtime_owns_two_hosts_and_only_supplies_its_fictional_settings(monkeypatch):
    plugin = descriptor()
    if plugin is None:
        with pytest.raises(ValueError):
            remote.RemoteClientRuntime(SimpleNamespace(storage=None, contract=None))
        return
    with pytest.raises(ValueError, match="separate"):
        remote.RemoteClientRuntime(plugin, (plugin.id,))
    starts = []

    def start(runtime):
        starts.append(runtime)
        runtime.process = None
        runtime.port = 443
    monkeypatch.setattr(NativeRuntime, "start", start)
    runtime = remote.RemoteClientRuntime(plugin)
    monkeypatch.setattr(runtime.server, "expect", lambda *_: {"id": "fictional-budget"})
    monkeypatch.setenv("FICTIONAL_REMOTE_TOKEN", "caller-must-not-enter-fictional-host")
    try:
        runtime.start()
        runtime.server.process = object()
        runtime.start()
        runtime.server.process = None
        environment = runtime.environment()
        assert environment["FICTIONAL_REMOTE_URL"] == "https://127.0.0.1:443"
        assert environment["FICTIONAL_REMOTE_TOKEN"] == runtime.server._api_token
        assert environment["FICTIONAL_REMOTE_TOKEN"] != "caller-must-not-enter-fictional-host"
        assert environment["FICTIONAL_REMOTE_BUDGET_ID"] == "fictional-budget"
        assert environment["WHAB_DEVKIT_REMOTE_CA"] == str(runtime.server.home / "tls/ca.crt")
        assert "SSL_CERT_FILE" not in environment
        assert starts == [runtime.server, runtime, runtime]
        assert runtime.launch_command() == [sys.executable, "-I", "-B", "-m", "whab_plugin_devkit.remote_launcher", "--port", "443"]
        runtime.lose_next_public_result()
        assert (runtime.home / "devkit-remote-drop-result").read_bytes() == launcher.DROP_RESULT
    finally:
        runtime.__exit__(None, None, None)
    assert not runtime.home.exists() and not runtime.server.home.exists()


@pytest.mark.parametrize("cli_failure", [False, True])
def test_reviewed_launcher_only_adapts_public_transport_then_runs_official_cli(monkeypatch, cli_failure):
    calls = []
    class PublicRemote:
        def __init__(self, url, token, budget_id, *, transport=None):
            calls.append((url, token, budget_id, transport))
    original = PublicRemote.__init__
    monkeypatch.setitem(sys.modules, "whab.plugins.remote_transport", SimpleNamespace(RemoteStore=PublicRemote))
    monkeypatch.setenv("WHAB_DEVKIT_REMOTE_CA", "fictional-ca.crt")
    monkeypatch.setenv("WHAB_HOME", "/fictional-home")
    monkeypatch.setattr(launcher.ssl, "create_default_context", lambda **options: options)
    monkeypatch.setattr(launcher.httpx, "HTTPTransport", lambda **options: options)
    def official(name, *, run_name):
        assert name == "whab" and run_name == "__main__"
        PublicRemote("https://fictional.example", "fictional-token", "fictional-budget")
        with pytest.raises(ValueError, match="owns"):
            PublicRemote("https://fictional.example", "fictional-token", "fictional-budget", transport=object())
        if cli_failure:
            raise RuntimeError("fictional CLI failure")
    monkeypatch.setattr(launcher.runpy, "run_module", official)
    monkeypatch.setattr(sys, "argv", ["fictional-launcher", "--port", "443"])
    if cli_failure:
        with pytest.raises(RuntimeError, match="fictional CLI"):
            launcher.main()
    else:
        launcher.main()
    assert PublicRemote.__init__ is original and sys.argv == ["whab", "--port", "443"]
    url, token, budget, transport = calls[0]
    assert (url, token, budget) == ("https://fictional.example", "fictional-token", "fictional-budget")
    assert transport.transport == {"verify": {"cafile": "fictional-ca.crt"}, "trust_env": False}
    assert transport.marker.as_posix() == "/fictional-home/devkit-remote-drop-result"


def test_launcher_module_entrypoint_runs_the_same_reviewed_overlay(monkeypatch):
    original = runpy.run_module
    called = []
    monkeypatch.setitem(sys.modules, "whab.plugins.remote_transport", SimpleNamespace(RemoteStore=type("PublicRemote", (), {"__init__": lambda *_: None})))
    monkeypatch.setenv("WHAB_DEVKIT_REMOTE_CA", "fictional-ca.crt")
    monkeypatch.setattr(launcher.ssl, "create_default_context", lambda **_: object())
    monkeypatch.setattr(runpy, "run_module", lambda *args, **kwargs: called.append((args, kwargs)))
    monkeypatch.delitem(sys.modules, "whab_plugin_devkit.remote_launcher", raising=False)
    original("whab_plugin_devkit.remote_launcher", run_name="__main__")
    assert called == [(("whab",), {"run_name": "__main__"})]


@pytest.mark.parametrize("case", ["drop", "missing", "wrong-marker", "large-marker", "symlink", "get", "path", "status", "operation"])
def test_result_loss_fixture_drops_only_one_armed_actual_public_acknowledgment(tmp_path, case):
    class Inner(httpx.BaseTransport):
        closed = False
        def handle_request(self, request):
            self.response = httpx.Response(500 if case == "status" else 200, json={"acknowledged": True})
            return self.response
        def close(self):
            self.closed = True
    inner = Inner()
    transport = launcher.ResultLossTransport(inner, tmp_path)
    marker = transport.marker
    if case != "missing":
        marker.write_bytes(b"wrong\n" if case == "wrong-marker" else launcher.DROP_RESULT + b"extra" if case == "large-marker" else launcher.DROP_RESULT)
    if case == "symlink":
        target = tmp_path / "fictional-marker-target"
        marker.rename(target)
        marker.symlink_to(target)
    request = httpx.Request("GET" if case == "get" else "POST",
                            "https://fictional.example/not-commands" if case == "path" else "https://fictional.example/remote/v1/budgets/fictional/commands",
                            json={"operation": "rename_account" if case == "operation" else "add_account"})
    if case == "drop":
        with pytest.raises(httpx.ReadError, match="Fictional controlled"):
            transport.handle_request(request)
        assert inner.response.is_closed and not marker.exists()
        assert transport.handle_request(request).status_code == 200
    else:
        assert transport.handle_request(request) is inner.response
    transport.close()
    assert inner.closed


class LostRemote:
    def __init__(self, fault=None):
        self.fault, self.unknown, self.reopened, self.finishes = fault, False, False, 0
        self.rows = []
        self.server = SimpleNamespace(expect=lambda *_: {"accounts": copy.deepcopy(self.rows)})
    def lose_next_public_result(self):
        pass
    def stop(self, *, kill):
        assert kill is True
    def start(self):
        self.reopened = True
    def expect(self, method, path, body=None, **options):
        if path == "/api/storage/remote/status":
            return {"unknown_outcome": self.unknown and not (self.fault == "restart-clears" and self.reopened)}
        if path.endswith("/finish"):
            self.finishes += 1
            self.unknown = self.fault == "finish-unknown"
            if self.fault == "duplicate":
                self.rows *= 2
            return {}
        assert path == "/api/accounts" and options["status"] == 400
        if body["name"] != "Must remain blocked":
            self.unknown = self.fault != "cleared"
            self.rows = [] if self.fault == "not-acknowledged" else [{"name": body["name"], "balance": body["balance"]}]
        return {}


def test_lost_result_probe_proves_unknown_restart_and_exact_explicit_completion():
    runtime = LostRemote()
    result = remote.assert_lost_remote_result(runtime)
    assert result["lost_public_result"] == result["unknown_after_client_restart"] == result["explicit_original_finish"] == "passed"
    assert result["server_restart_retry"] == "not-supported" and runtime.finishes == 2


@pytest.mark.parametrize("fault", ["cleared", "not-acknowledged", "restart-clears", "finish-unknown", "duplicate"])
def test_lost_result_probe_never_accepts_automatic_clearing_missing_commit_or_duplicate(fault):
    with pytest.raises(AssertionError):
        remote.assert_lost_remote_result(LostRemote(fault))
