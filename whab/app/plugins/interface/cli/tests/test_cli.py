"""Real host command dispatch and bounded, credential-safe terminal transport."""

import io
import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from whab_plugin_cli import client
from whab_plugin_cli.plugin import plugin as cli
from whab_plugin_server.plugin import plugin as server
from whab_plugin_subscriptions.plugin import plugin as subscriptions

from whab.app import create_app
from whab.config import Settings
from whab.plugins import PluginRegistry

TOKEN = "fictional-access-token-" + "x" * 32
ARGS = ["--url", "https://localhost:5173", "--budget", "default"]


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv("WHAB_CLI_TOKEN", TOKEN)


def mock_transport(monkeypatch, handler):
    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        assert kwargs["verify"].check_hostname
        return httpx.Client(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(client, "httpx", SimpleNamespace(Client=factory, HTTPError=httpx.HTTPError))


def test_cli_edits_categories_and_subscription_labels_on_real_headless_host(store, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SERVER_TOKEN", TOKEN)
    account = store.add_account("personal", {"name": "Fictional", "type": "depository", "balance": 10000})
    transaction = store.add_transaction("personal", {"accountId": account, "date": "2026-10-15", "merchant": "Fictional plan", "amount": 1200, "categoryId": None})
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    app = create_app(store=store, settings=settings, plugins=PluginRegistry((server, subscriptions, cli)))
    with TestClient(app, base_url="https://localhost:5173") as connection:
        @contextmanager
        def factory(**kwargs):
            connection.headers["Authorization"] = kwargs["headers"]["Authorization"]
            yield connection
        monkeypatch.setattr(client, "httpx", SimpleNamespace(Client=factory, HTTPError=httpx.HTTPError))
        def run(*arguments):
            assert client.main(ARGS + list(arguments)) == 0
            return json.loads(capsys.readouterr().out)
        assert connection.get("/api/plugin-settings/cli").json()["command"] == "whab-cli"
        assert "subscriptions" in run("query", "features")["data"]
        initial = run("query", "transactions", "--month", "2026-10")
        category = run("query", "categories")["data"][0]["id"]
        intent = ["command", "edit_transaction", "--parameters", json.dumps({"transactionId": transaction, "categoryId": category}), "--revision", initial["revision"], "--request-id", "category-intent"]
        changed = run(*intent)
        assert run(*intent) == changed
        assert not store.transactions("personal")[0]["reviewed"]
        report = run("feature-query", "subscriptions", "report", "--parameters", '{"month":"2026-10","currency":"USD"}')
        row = report["data"]["transactions"][0]
        labeled = run("feature-command", "subscriptions", "set_label", "--parameters", json.dumps({"transactionId": transaction, "fingerprint": row["fingerprint"], "isSubscription": True, "cadence": "monthly"}), "--revision", report["revision"], "--request-id", "subscription-intent")
        assert labeled["data"]["saved"]
        assert run("feature-query", "subscriptions", "report", "--parameters", '{"month":"2026-10","currency":"USD"}')["data"]["estimatedMonthly"] == 1200
        # The exact old read cannot authorize a different mutation.
        stale = [*intent[:-1], "another-intent"]
        assert client.main(ARGS + stale) == 1
        assert "error" in json.loads(capsys.readouterr().err)
        # Query JSON parameters must reach real dispatch, rather than silently
        # becoming an unfiltered GET. Preview is also a parameterized read.
        history = run('query', 'history', '--parameters', '{"limit":1}')
        assert history['data']['total'] >= 2 and len(history['data']['entries']) == 1
        assert history['data']['next'] is not None
        older = run('query', 'history', '--parameters', json.dumps({'limit': 1, 'before': history['data']['next']}))
        assert older['data']['entries'][0]['id'] != history['data']['entries'][0]['id']
        preview = run('query', 'history_preview', '--parameters', '{"action":"snapshot"}')
        assert preview['data']['action'] == 'snapshot' and preview['data']['changes'] == []
        assert run('query', 'history', '--parameters', '{"limit":1}') == history


@pytest.mark.parametrize("url", ["http://localhost", "https://user:secret@localhost", "https://localhost/path", "https://localhost?q=1", "https://localhost/#fragment", "https://local host", "https://localhost:invalid"])
def test_invalid_origins_never_send_credentials(url, capsys):
    assert client.main(["--url", url, "--budget", "default", "query", "accounts"]) == 1
    output = capsys.readouterr()
    assert not output.out and TOKEN not in output.err and "secret" not in output.err


@pytest.mark.parametrize("value", ["", "short", "x" * 4097, "x" * 32 + "\n"])
def test_invalid_tokens_fail_before_network(value, monkeypatch, capsys):
    monkeypatch.setenv("WHAB_CLI_TOKEN", value)
    assert client.main(ARGS + ["query", "accounts"]) == 1
    assert "error" in capsys.readouterr().err


@pytest.mark.parametrize("raw", ["invalid", "[]", '{"x": NaN}', " " * (1024 * 1024 + 1)])
def test_invalid_parameters_are_bounded_before_network(raw, capsys):
    assert client.main(ARGS + ["feature-query", "subscriptions", "report", "--parameters", raw]) == 1
    assert "error" in capsys.readouterr().err


@pytest.mark.parametrize("status,payload", [(302, {}), (401, {}), (409, {}), (200, []), (200, {"budgetId": "other", "revision": "one", "data": {}}), (200, {"budgetId": "default", "revision": "", "data": {}}), (200, {"budgetId": "default", "revision": "one"})])
def test_response_status_identity_and_shape_are_validated(status, payload, monkeypatch, capsys):
    mock_transport(monkeypatch, lambda request: httpx.Response(status, json=payload))
    assert client.main(ARGS + ["query", "accounts"]) == 1
    assert TOKEN not in capsys.readouterr().err


def test_stdin_generic_features_and_bounded_responses(monkeypatch, capsys):
    requests = []
    def reply(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"budgetId": "default", "revision": "one", "data": {"ok": True}})
    mock_transport(monkeypatch, reply)
    monkeypatch.setattr(client.sys, "stdin", io.StringIO('{"month":"2026-10","currency":"USD"}'))
    assert client.main(ARGS + ["feature-query", "subscriptions", "report", "--parameters", "-"]) == 0
    assert requests[0]["operation"] == "feature"
    assert json.loads(capsys.readouterr().out)["data"]["ok"]
    mock_transport(monkeypatch, lambda request: httpx.Response(200, content=b"x" * (16 * 1024 * 1024 + 1)))
    assert client.main(ARGS + ["query", "accounts"]) == 1
    assert "error" in capsys.readouterr().err


def test_bad_budget_and_network_failures_are_sanitized(monkeypatch, capsys):
    assert client.main(["--url", "https://localhost", "--budget", "", "query", "accounts"]) == 1
    capsys.readouterr()
    def fail(request):
        raise httpx.ConnectError("private transport details")
    mock_transport(monkeypatch, fail)
    assert client.main(ARGS + ["query", "accounts"]) == 1
    assert "private" not in capsys.readouterr().err
