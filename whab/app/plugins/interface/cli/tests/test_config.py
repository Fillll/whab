"""Connection files select the target without executing code or storing a token."""

import json
from types import SimpleNamespace

import httpx
import pytest
from whab_plugin_cli import client
from whab_plugin_cli.config import CONFIG_LIMIT, connection_defaults


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_config_help_accepts_continuations_quotes_comments_and_relative_ca(tmp_path, monkeypatch, capsys, newline):
    config = tmp_path / "default-budget.config"
    config.write_bytes(newline.join([
        "# Connection defaults, not a script",
        "--url https://localhost:5173 \\",
        "--budget default \\",
        '--ca "tls folder/ca.crt" \\',
        "# No credentials are needed for help",
    ]).encode("utf-8-sig"))
    monkeypatch.chdir(tmp_path.parent)
    monkeypatch.delenv("WHAB_CLI_TOKEN", raising=False)
    monkeypatch.setattr(client.httpx, "Client", lambda **kwargs: pytest.fail("Help cannot connect"))
    arguments, defaults = connection_defaults([str(config), "--help"])
    assert arguments == ["--help"]
    assert defaults == {"url": "https://localhost:5173", "budget": "default", "ca": str(tmp_path / "tls folder/ca.crt")}
    with pytest.raises(SystemExit) as result:
        client.main([str(config), "--help"])
    assert result.value.code == 0
    assert "[CONFIG]" in capsys.readouterr().out
    with pytest.raises(SystemExit) as result:
        client.main([str(config), "command", "edit_transaction", "--help"])
    assert result.value.code == 0
    assert "categoryId" in capsys.readouterr().out
    assert client.main([str(config), "describe", "--offline", "--plugin", "core", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["core"]["plugin_id"] == "core"


def test_explicit_options_override_config_target_workspace_and_token_variable(tmp_path, monkeypatch, capsys):
    config = tmp_path / "connection.config"
    config.write_text("--url=https://localhost:5173\n--budget=first\n--workspace demo\n--token-env FIRST_TOKEN\n")
    first, second = "fictional-first-" + "x" * 32, "fictional-second-" + "y" * 32
    monkeypatch.setenv("FIRST_TOKEN", first)
    monkeypatch.setenv("SECOND_TOKEN", second)
    monkeypatch.delenv("WHAB_CLI_TOKEN", raising=False)
    calls = []

    def respond(request):
        calls.append(request)
        budget = request.url.path.split("/")[4]
        return httpx.Response(200, json={"budgetId": budget, "revision": "one", "data": []})

    original = httpx.Client
    monkeypatch.setattr(client, "httpx", SimpleNamespace(
        Client=lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)), HTTPError=httpx.HTTPError))
    assert client.main([str(config), "query", "accounts"]) == 0
    assert json.loads(capsys.readouterr().out)["budgetId"] == "first"
    assert calls[-1].url.port == 5173 and calls[-1].url.params["workspace"] == "demo"
    assert calls[-1].headers["authorization"] == "Bearer " + first
    assert client.main([str(config), "--url", "https://localhost:5443", "--budget", "second",
                        "--workspace", "personal", "--token-env", "SECOND_TOKEN", "query", "accounts"]) == 0
    assert json.loads(capsys.readouterr().out)["budgetId"] == "second"
    assert calls[-1].url.port == 5443 and calls[-1].url.params["workspace"] == "personal"
    assert calls[-1].headers["authorization"] == "Bearer " + second


@pytest.mark.parametrize("contents", [
    b"--url", b"--budget=", b"--budget --url https://localhost", b'--budget "unclosed',
    b"--workspace unknown", b"--token fictional-private-secret", b"query accounts",
    b"--parameters fictional-private-secret", b"--help", b"\xff", b"x" * (CONFIG_LIMIT + 1),
])
def test_invalid_configs_fail_before_help_or_network_without_echoing_contents(tmp_path, monkeypatch, capsys, contents):
    config = tmp_path / "invalid.config"
    config.write_bytes(contents)
    monkeypatch.setattr(client.httpx, "Client", lambda **kwargs: pytest.fail("Invalid config cannot connect"))
    assert client.main([str(config), "--help"]) == 1
    output = capsys.readouterr()
    assert not output.out and "fictional-private-secret" not in output.err
    assert "config" in json.loads(output.err)["error"].lower()


def test_missing_config_and_empty_defaults(tmp_path, capsys):
    assert connection_defaults([]) == ([], {})
    assert client.main([str(tmp_path / "missing.config"), "--help"]) == 1
    assert "Cannot read CLI config" in capsys.readouterr().err
    config = tmp_path / "empty.config"
    config.write_text("# No defaults\n")
    assert connection_defaults([str(config), "--help"]) == (["--help"], {})


def test_pasted_config_accepts_a_final_backslash_without_a_final_newline(tmp_path):
    config = tmp_path / "default-budget.config"
    config.write_text("--url https://localhost:5173 \\\n--budget default \\\n--ca /fictional/tls/ca.crt \\")
    assert connection_defaults([str(config), "--help"]) == (["--help"], {
        "url": "https://localhost:5173", "budget": "default", "ca": "/fictional/tls/ca.crt"})


def test_ca_paths_expand_home_and_explicit_cli_ca_overrides_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / "paths.config"
    config.write_text("--ca ~/tls/ca.crt\n")
    argv, defaults = connection_defaults([str(config), "--ca", "override.crt", "query", "accounts"])
    assert defaults["ca"] == str(tmp_path / "tls/ca.crt")
    assert client.parser(argv, defaults=defaults).parse_args(argv).ca == "override.crt"


def test_config_values_are_literal_and_do_not_execute_shell_expressions(tmp_path, capsys):
    marker = tmp_path / "must-not-exist"
    config = tmp_path / "literal.config"
    config.write_text(f"--budget '$(touch {marker})'\n")
    assert connection_defaults([str(config)])[1]["budget"] == f"$(touch {marker})"
    with pytest.raises(SystemExit) as result:
        client.main([str(config), "--help"])
    assert result.value.code == 0 and not marker.exists()
    capsys.readouterr()
