"""CLI help is complete offline and discovery never invokes documented actions."""

import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from whab_plugin_cli import client
from whab_plugin_cli import help as help_module

from whab.plugins.documentation import installed_catalog


def sample_catalog():
    value = installed_catalog()
    value["plugins"] = [row for row in value["plugins"] if row["id"] in {"subscriptions", "cli"}]
    value["plugins"][0]["enabled"] = True
    value["plugins"][1]["enabled"] = False
    value["plugins"].append(
        {
            "id": "legacy",
            "version": "0",
            "enabled": None,
            "documented": False,
            "documentation": None,
        }
    )
    return value


def test_cli_is_documented_as_an_installed_management_tool():
    catalog = installed_catalog(enabled=set())
    row = next(row for row in catalog["plugins"] if row["id"] == "cli")
    assert row["enabled"] is None and row["selection_control"] == "management"
    assert "installed; management tool" in help_module.render_catalog(catalog)


@pytest.mark.parametrize(
    "arguments,expected",
    [
        (["--help"], "Installed plugin documentation"),
        (["command", "edit_transaction", "--help"], "categoryId"),
        (["query", "transactions", "--help"], "reviewed"),
        (["feature-command", "subscriptions", "set_label", "--help"], "fingerprint"),
        (["feature-query", "subscriptions", "report", "--help"], "estimatedMonthly"),
        (
            [
                "describe",
                "--plugin",
                "ai-credentials",
                "--operation",
                "save-key",
                "--kind",
                "local",
                "--help",
            ],
            "api_key",
        ),
    ],
)
def test_help_needs_no_connection_or_credentials(arguments, expected, monkeypatch, capsys):
    monkeypatch.delenv("WHAB_CLI_TOKEN", raising=False)
    monkeypatch.setattr(
        client.httpx, "Client", lambda **kwargs: pytest.fail("Help cannot open a connection")
    )
    with pytest.raises(SystemExit) as result:
        client.main(arguments)
    assert result.value.code == 0
    text = capsys.readouterr().out
    assert expected in text
    if "edit_transaction" in arguments:
        assert "Retry:" in text and "Side effects:" in text and "Examples:" in text


def test_offline_describe_filters_and_machine_schema(monkeypatch, capsys):
    monkeypatch.delenv("WHAB_CLI_TOKEN", raising=False)
    assert (
        client.main(
            [
                "describe",
                "--plugin",
                "subscriptions",
                "--kind",
                "feature-command",
                "--operation",
                "set_label",
                "--json",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["core"] is None and len(data["plugins"]) == 1
    row = data["plugins"][0]["documentation"]["operations"][0]
    assert row["input"]["required"] == ["transactionId", "fingerprint", "isSubscription", "cadence"]
    assert (
        client.main(
            [
                "--url",
                "http://never-contact.invalid",
                "describe",
                "--offline",
                "--plugin",
                "core",
                "--operation",
                "edit_transaction",
            ]
        )
        == 0
    )
    assert "categoryId" in capsys.readouterr().out
    assert client.main(["describe", "--plugin", "missing"]) == 1
    assert "error" in capsys.readouterr().err
    assert client.main(["query", "accounts"]) == 1
    capsys.readouterr()


def test_renderers_include_access_defaults_choices_and_explicit_unknown_metadata():
    catalog = sample_catalog()
    original = copy.deepcopy(catalog)
    assert help_module.describe(catalog, plugin="core")["plugins"] == []
    assert (
        help_module.describe(catalog, plugin="subscriptions", kind="local", name="status")["core"]
        is None
    )
    assert catalog == original
    with pytest.raises(ValueError):
        help_module.describe(catalog, plugin="absent")
    text = help_module.render_catalog(catalog)
    assert "enabled" in text and "disabled" in text and "Legacy plugin" in text
    assert "choices:" in text and "default:" in text and "No operation-specific fields" in text
    assert "legacy; undocumented" in help_module.overview(catalog)
    assert "Query operations" in help_module.contextual_help([])
    assert "Query operations" in help_module.contextual_help(["command", "unknown"], catalog)
    assert "No installed documentation" in help_module.contextual_help(
        ["feature-command", "absent", "unknown"], catalog
    )
    assert "No installed documentation" in help_module.contextual_help(
        ["feature-command", "subscriptions", "unknown"], catalog
    )
    assert "subscriptions" in help_module.contextual_help(
        ["describe", "--plugin", "subscriptions"], catalog
    )
    assert "Query operations" in help_module.contextual_help(["describe", "--plugin"], catalog)
    assert "Input fields" in help_module.contextual_help(
        ["feature-query", "subscriptions", "report"], catalog
    )
    assert help_module.describe(catalog, name="not-an-operation")["core"]["operations"] == []
    row = copy.deepcopy(catalog["core"]["operations"][0])
    row["output"] = {"type": "array", "items": True, "description": "Any JSON items."}
    assert "Any JSON items" in help_module.render_operation(row)
    nested = {"properties": {"options": {"type": "object", "description": "Controls.", "properties": {"threshold": {"type": "number", "description": "Cutoff.", "default": 0.8}}}}}
    assert "options.threshold" in "\n".join(help_module._fields(nested))


def test_online_describe_and_generic_feature_queries_use_the_same_guarded_transport(
    monkeypatch, capsys
):
    monkeypatch.setenv("WHAB_CLI_TOKEN", "fictional-access-" + "x" * 32)
    catalog = sample_catalog()
    catalog["source"] = "authority"
    catalog["http"] = {
        "access": "Local routes remain local.",
        "openapi": {
            "paths": {
                "/api/example": {
                    "get": {"summary": "Read example"},
                    "parameters": [],
                    "post": {"operationId": "write_example"},
                }
            }
        },
    }
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path, request.content))
        return httpx.Response(200, json={"budgetId": "default", "revision": "one", "data": catalog})

    original = httpx.Client
    monkeypatch.setattr(
        client,
        "httpx",
        SimpleNamespace(
            Client=lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)),
            HTTPError=httpx.HTTPError,
        ),
    )
    args = ["--url", "https://localhost", "--budget", "default"]
    assert client.main(args + ["describe", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["source"] == "authority"
    assert calls[0][1].endswith("/queries/describe")
    assert client.main(args + ["describe"]) == 0
    text = capsys.readouterr().out
    assert (
        "GET /api/example" in text
        and "POST /api/example" in text
        and "Local routes remain local" in text
    )
    assert (
        client.main(
            args
            + [
                "query",
                "feature",
                "--parameters",
                '{"pluginId":"subscriptions","action":"report","parameters":{"month":"2026-11","currency":"USD"}}',
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert calls[-1][0] == "POST" and calls[-1][1].endswith("/queries")
    assert client.main(args + ["query", "feature"]) == 1
    capsys.readouterr()


@pytest.mark.parametrize("budget", ["x" * 201, "invalid\nidentifier"])
def test_invalid_nonempty_budget_ids_fail_before_network(monkeypatch, budget, capsys):
    monkeypatch.setenv("WHAB_CLI_TOKEN", "fictional-access-" + "x" * 32)
    assert client.main(["--url", "https://localhost", "--budget", budget, "query", "accounts"]) == 1
    assert "error" in capsys.readouterr().err
