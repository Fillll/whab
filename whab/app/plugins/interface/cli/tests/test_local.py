"""Local profiles share the public host adapter and retain intent validation."""

import json
from contextlib import contextmanager

import httpx
import pytest
from whab_plugin_cli import client
from whab_plugin_cli.config import connection_defaults


@pytest.fixture
def local_transport(monkeypatch):
    from whab.plugins import local

    calls, result = [], {"budgetId": "default", "revision": "one", "data": []}
    status = [200]

    def respond(request):
        calls.append(request)
        return httpx.Response(status[0], json=result)

    @contextmanager
    def connection(*args, **kwargs):
        with httpx.Client(base_url="https://127.0.0.1:12345", transport=httpx.MockTransport(respond)) as session:
            yield session
    monkeypatch.setattr(local, "local_connection", connection)
    return calls, result, status


@pytest.mark.parametrize("arguments,method,suffix", [
    (["query", "accounts"], "GET", "/queries/accounts"),
    (["command", "add_account", "--parameters", "{}", "--revision", "one", "--request-id", "intent"], "POST", "/commands"),
    (["feature-query", "subscriptions", "report", "--parameters", "{}"], "POST", "/queries"),
    (["plugins", "list"], "GET", "/plugins"), (["plugins", "status"], "GET", "/plugins"),
    (["plugins", "enable", "plaid"], "POST", "/plugins"),
    (["plugins", "disable", "plaid"], "POST", "/plugins"),
    (["sync", "plaid"], "POST", "/sync"),
    (["jobs", "run"], "POST", "/jobs/run"), (["jobs", "status"], "GET", "/jobs"),
])
def test_local_operations_use_explicit_budget_routes(local_transport, capsys, arguments, method, suffix):
    calls, _, _ = local_transport
    assert client.main(["--home", "/fictional/runtime", *arguments]) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == "one"
    assert calls[-1].method == method
    assert calls[-1].url.path == "/budgets/default/api/local/v1" + suffix


def test_local_profiles_resolve_relative_home_and_select_environment(tmp_path):
    path = tmp_path / "local.budget"
    path.write_text(json.dumps({"home": "runtime", "budget": "default", "environment": "production"}))
    assert connection_defaults([str(path)])[1] == {"home": str(tmp_path / "runtime"), "budget": "default", "environment": "production"}


def test_local_history_parameters_reach_the_public_query_body(local_transport, capsys):
    calls, _, _ = local_transport
    assert client.main(['--home', '/fictional/runtime', 'query', 'history', '--parameters', '{"limit":1}']) == 0
    capsys.readouterr()
    assert calls[-1].method == 'POST'
    assert calls[-1].url.path == '/budgets/default/api/local/v1/queries'
    assert json.loads(calls[-1].content)['parameters'] == {'limit': 1}


@pytest.mark.parametrize("values", [{"home": "runtime", "url": "https://localhost"}, {"environment": "typo"}])
def test_invalid_local_profile_is_rejected_before_help(tmp_path, capsys, values):
    path = tmp_path / "local.budget"
    path.write_text(json.dumps(values))
    assert client.main([str(path), "--help"]) == 1
    assert "config" in capsys.readouterr().err


@pytest.mark.parametrize("arguments", [
    ["--url", "https://localhost", "query", "accounts"],
    ["--workspace", "demo", "sync", "plaid"], ["plugins", "enable"],
])
def test_invalid_local_management_cannot_submit(local_transport, capsys, arguments):
    calls, _, _ = local_transport
    assert client.main(["--home", "/fictional", *arguments]) == 1
    assert not calls
    assert "error" in capsys.readouterr().err


@pytest.mark.parametrize("arguments", [["plugins", "list"], ["sync", "plaid"], ["jobs", "status"]])
def test_management_requires_local_profile(capsys, arguments):
    assert client.main(arguments) == 1
    assert "error" in capsys.readouterr().err


def test_local_rejections_and_wrong_budget_are_not_replayed(local_transport, capsys):
    calls, result, status = local_transport
    args = ["--home", "/fictional", "--budget", "another", "query", "accounts"]
    assert client.main(args) == 1
    assert len(calls) == 1 and "error" in capsys.readouterr().err
    result.update(budgetId="another", revision="")
    assert client.main(args) == 1
    result.update(revision="one")
    result.pop("data")
    assert client.main(args) == 1
    status[0] = 409
    assert client.main(args) == 1
    assert len(calls) == 4


def test_local_response_limit_does_not_replay(local_transport, capsys):
    calls, result, _ = local_transport
    result["data"] = "x" * (16 * 1024 * 1024)
    assert client.main(["--home", "/fictional", "query", "accounts"]) == 1
    assert len(calls) == 1 and "error" in capsys.readouterr().err


@pytest.mark.parametrize("action", ["enable", "disable"])
def test_cli_manages_os_schedule_through_sdk(monkeypatch, capsys, action):
    from whab.plugins import scheduling

    calls = []
    def configure(home, environment, *, enabled):
        calls.append((home, environment, enabled))
        return {"enabled": enabled}
    monkeypatch.setattr(scheduling, "configure_schedule", configure)
    assert client.main(["--home", "/fictional", "jobs", action]) == 0
    assert json.loads(capsys.readouterr().out)["enabled"] == (action == "enable")
    assert calls == [("/fictional", "production", action == "enable")]


@pytest.mark.parametrize("action", ["enable", "disable"])
def test_explicit_importer_identifier_targets_existing_budget_plugin(local_transport, capsys, action):
    calls, result, _ = local_transport
    result.update(plugins=[{"id": "ynab", "display_name": "YNAB ZIP importer", "dependencies": []}],
                  configured=["ynab"], active=["ynab"])
    assert client.main(["--home", "/fictional", "plugins", action, "ynab-zip-importer"]) == 0
    assert json.loads(calls[-1].content) == {"action": action, "plugin": "ynab"}
    output = json.loads(capsys.readouterr().out)
    assert output["plugins"][0]["id"] == "ynab-zip-importer"
    assert output["configured"] == output["active"] == ["ynab-zip-importer"]
    assert result["configured"] == ["ynab"]
