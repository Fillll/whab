"""Discoverable name-addressed commands reuse the authenticated local host."""

import json
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from whab_plugin_cli import console


@pytest.fixture
def transport(monkeypatch, tmp_path):
    calls, settings = [], []
    state = {"catalog": {"budgets": [{"id": "uuid-one", "name": "Empty Budget"}]},
             "status": 200, "wrong_budget": False}

    def respond(request):
        calls.append(request)
        if request.url.path.endswith("/budgets"):
            data = state["catalog"] if request.method == "GET" else {"id": "created", "name": "new", "path": "new.whab"}
        elif request.url.path.endswith("/plugins"):
            data = {"plugins": [{"id": "ynab", "dependencies": []}], "configured": ["ynab"], "active": ["ynab"]}
        else:
            data = {"budgetId": "wrong" if state["wrong_budget"] else "uuid-one", "revision": "revision-one", "data": {"currency": "USD", "summary": {}}}
            if request.url.path.endswith("/transactions"):
                data["data"] = [{"id": "new", "reviewed": False}, {"id": "done", "reviewed": True}, {"id": "deleted", "deleted": True}]
        return httpx.Response(state["status"], json=data)

    @contextmanager
    def connection(home, environment, **kwargs):
        settings.append((home, environment, kwargs))
        with httpx.Client(base_url="https://localhost", transport=httpx.MockTransport(respond)) as client:
            yield client
    monkeypatch.setattr(console, "local_connection", connection)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("WHAB_HOME", raising=False)
    return calls, settings, state


@pytest.mark.parametrize("args", [[], ["--help"], ["budget", "--help"], ["budget", "create", "--help"],
    ["plugin", "--help"], ["Empty Budget", "--help"], ["Empty Budget", "plugin", "--help"],
    ["Empty Budget", "insights", "--help"], ["Empty Budget", "insights", "summary", "--help"],
    ["Empty Budget", "trnx", "non-reviewed", "--help"], ["api", "--help"]])
def test_help_is_offline_and_nested(transport, capsys, args):
    with pytest.raises(SystemExit) as result:
        console.main(args)
    assert result.value.code == (2 if not args else 0)
    assert not transport[0] and not transport[1]
    text = capsys.readouterr()
    assert "usage:" in text.out + text.err


def test_catalog_is_offline_and_uses_cli_importer_identifier(transport, capsys):
    assert console.main(["plugin", "list"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert any(row["id"] == "ynab-zip-importer" for row in result["plugins"])
    assert not transport[0]


def test_create_lists_and_expands_directory_paths_in_production(transport, capsys, tmp_path):
    calls, settings, _ = transport
    assert console.main(["budget", "create", "new", "--path", "~/chosen"]) == 0
    body = json.loads(calls[-1].content)
    assert body == {"name": "new", "path": str(Path("~/chosen/new.whab").expanduser())}
    assert settings[-1][:2] == (str(tmp_path / ".whab"), "production")
    assert json.loads(capsys.readouterr().out)["id"] == "created"
    assert console.main(["--environment", "sandbox", "budget", "list"]) == 0
    assert settings[-1][1] == "sandbox"
    capsys.readouterr()
    assert console.create_path("new", "~/chosen.whab") == str(Path("~/chosen.whab").expanduser())
    assert console.create_path("new", None) is None


@pytest.mark.parametrize("args", [["Empty Budget", "plugin", "enable", "ynab-zip-importer"],
    ["uuid-one", "plugin", "disable", "ynab-zip-importer"], ["empty budget", "plugin", "list"],
    ["Empty Budget", "plugin", "status"]])
def test_names_resolve_once_and_plugins_use_stable_host_identity(transport, capsys, args):
    calls, _, _ = transport
    assert console.main(args) == 0
    assert calls[-1].url.path == "/budgets/uuid-one/api/local/v1/plugins"
    if calls[-1].method == "POST":
        assert json.loads(calls[-1].content)["plugin"] == "ynab"
    assert json.loads(capsys.readouterr().out)["configured"] == ["ynab-zip-importer"]
    assert len(calls) == 2


def test_missing_plugin_cannot_submit_a_change(transport, capsys):
    assert console.main(["Empty Budget", "plugin", "enable"]) == 1
    assert len(transport[0]) == 1
    assert "Choose a plugin" in capsys.readouterr().err


def test_summary_and_aliases_are_scoped_read_only(transport, capsys):
    calls, _, state = transport
    assert console.main(["Empty Budget", "insights", "summary", "--month", "2026-11", "--currency", "EUR"]) == 0
    assert dict(calls[-1].url.params) == {"currency": "EUR", "month": "2026-11"}
    capsys.readouterr()
    assert console.main(["Empty Budget", "insights", "summary"]) == 0
    assert dict(calls[-1].url.params) == {"currency": "USD"}
    capsys.readouterr()
    for group, action in [("trnx", "non-reviewed"), ("transactions", "unreviewed")]:
        assert console.main(["Empty Budget", group, action]) == 0
        assert json.loads(capsys.readouterr().out)["data"] == [{"id": "new", "reviewed": False}]
    assert console.main(["Empty Budget", "trnx", "non-reviewed", "--month", "2026-11"]) == 0
    assert calls[-1].url.params["month"] == "2026-11"
    capsys.readouterr()
    state["wrong_budget"] = True
    assert console.main(["Empty Budget", "insights", "summary"]) == 1
    assert "another budget" in capsys.readouterr().err
    assert all(request.method == "GET" for request in calls)


@pytest.mark.parametrize("rows", [[], [{"id": "a", "name": "Same"}, {"id": "b", "name": "same"}]])
def test_missing_or_ambiguous_name_does_not_fall_back(transport, capsys, rows):
    calls, _, state = transport
    state["catalog"]["budgets"] = rows
    assert console.main(["same", "plugin", "enable", "ynab-zip-importer"]) == 1
    assert len(calls) == 1
    assert "Unknown or ambiguous" in capsys.readouterr().err


def test_profiles_and_overrides_are_explicit(transport, tmp_path, monkeypatch):
    profile = tmp_path / ".config/whab/default.budget"
    profile.parent.mkdir(parents=True)
    profile.write_text(json.dumps({"home": "runtime", "environment": "sandbox", "ca": "ca.crt"}))
    options = console.options_parser().parse_args([])
    assert console.configuration(options) == (str(profile.parent / "runtime"), "sandbox", str(profile.parent / "ca.crt"))
    monkeypatch.setenv("WHAB_HOME", str(tmp_path / "environment-home"))
    assert console.configuration(options)[0] == str(tmp_path / "environment-home")
    args = console.options_parser().parse_args(["--home", str(tmp_path / "override"), "--environment", "production", "--ca", "/chosen/ca"])
    assert console.configuration(args) == (str(tmp_path / "override"), "production", "/chosen/ca")
    profile.write_text(json.dumps({"url": "https://localhost"}))
    with pytest.raises(console.CommandError, match="local home"):
        console.configuration(options)
    options.profile = str(tmp_path / "missing.budget")
    with pytest.raises(console.ConfigError):
        console.configuration(options)


def test_api_delegates_with_production_defaults_and_ca(transport, monkeypatch, capsys):
    forwarded = []
    monkeypatch.setattr(console.client, "main", lambda args: forwarded.append(args) or 0)
    assert console.main(["--ca", "/chosen/ca", "api", "query", "accounts"]) == 0
    assert forwarded[0][-5:] == ["production", "--ca", "/chosen/ca", "query", "accounts"]
    assert console.main(["api", "query", "accounts"]) == 0
    assert forwarded[1][-3:] == ["production", "query", "accounts"]


@pytest.mark.parametrize("failure", [ValueError, RuntimeError])
def test_failures_are_sanitized_and_requests_are_never_replayed(transport, capsys, monkeypatch, failure):
    calls, _, state = transport
    state["status"] = 409
    assert console.main(["budget", "create", "new"]) == 1
    assert len(calls) == 1 and "HTTP 409" in capsys.readouterr().err
    monkeypatch.setattr(console, "configuration", lambda args: (_ for _ in ()).throw(failure("private-secret")))
    assert console.main(["budget", "list"]) == 1
    assert "private-secret" not in capsys.readouterr().err
    monkeypatch.setattr(console, "configuration", lambda args: (_ for _ in ()).throw(console.ConfigError("Invalid JSON in CLI config.")))
    assert console.main(["budget", "list"]) == 1
    assert "Invalid JSON" in capsys.readouterr().err


def test_oversized_response_is_not_replayed():
    calls = []
    def reply(request):
        calls.append(request)
        return httpx.Response(200, content=b"x" * (16 * 1024 * 1024 + 1))
    with httpx.Client(base_url="https://localhost", transport=httpx.MockTransport(reply)) as connection:
        with pytest.raises(console.CommandError, match="16 MiB"):
            console.exchange(connection, "GET", "/budgets")
    assert len(calls) == 1


@pytest.mark.parametrize('content', [b'not-json', b'[]', b'{"detail":{}}', b'{"detail":"Authored refusal"}', b'{"error":"Authored refusal"}', b'{"detail":""}', json.dumps({'detail':'x'*1001}).encode()])
def test_ledger_error_keeps_status_and_only_bounded_authored_text(content):
    with httpx.Client(base_url='https://localhost', transport=httpx.MockTransport(lambda _: httpx.Response(400, content=content))) as connection:
        with pytest.raises(console.CommandError) as error:
            console.exchange(connection, 'POST', '/queries', ledger_errors=True)
    assert error.value.status_code == 400
    assert ('Authored refusal' in str(error.value)) == (b'Authored refusal' in content)


def test_reserved_importer_collision_cannot_target_another_plugin(transport, monkeypatch, capsys):
    monkeypatch.setattr(console, "installed_catalog", lambda: {"plugins": [{"id": "ynab-zip-importer"}]})
    assert console.main(["Empty Budget", "plugin", "enable", "ynab-zip-importer"]) == 1
    assert not transport[0] and not transport[1]
    assert "error" in capsys.readouterr().err
