"""CLI imports preserve explicit file intent, parser guards and budget scope."""

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from whab_plugin_cli import console


@pytest.fixture
def importer(tmp_path, monkeypatch):
    archive = tmp_path / "export.zip"
    archive.write_bytes(b"synthetic archive")
    fingerprint = hashlib.sha256(archive.read_bytes()).hexdigest()
    calls = []
    state = {"preview": {"fingerprint": fingerprint, "currency": "USD", "parserIdentity": "parser-one",
        "canImport": True, "alreadyImported": False, "accounts": 1, "categories": 2,
        "transactions": 3, "assignments": 4, "warnings": ["Classify imported accounts."]}, "commit_status": 200}

    def respond(request):
        calls.append(request)
        if request.url.path.endswith("/budgets"):
            return httpx.Response(200, json={"budgets": [{"id": "one", "name": "Empty Budget"}]})
        if request.url.path.endswith("/preview"):
            return httpx.Response(200, json=state["preview"])
        return httpx.Response(state["commit_status"], json={"counts": {"transactions": 3}, "alreadyImported": False})

    @contextmanager
    def connection(*args, **kwargs):
        with httpx.Client(base_url="https://localhost", transport=httpx.MockTransport(respond)) as client:
            yield client

    monkeypatch.setattr(console, "local_connection", connection)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("WHAB_HOME", raising=False)
    return archive, fingerprint, calls, state


def run(importer, stage, *extra, plugin="ynab-zip-importer", file=None):
    return console.main(["Empty Budget", "import", plugin, stage, file or str(importer[0]), "--currency", "USD", *extra])


@pytest.mark.parametrize("args", [["Empty Budget", "import", "--help"],
    ["Empty Budget", "import", "ynab-zip-importer", "--help"],
    ["Empty Budget", "import", "ynab-zip-importer", "preview", "--help"],
    ["Empty Budget", "import", "ynab-zip-importer", "commit", "--help"]])
def test_import_help_is_offline(importer, capsys, args):
    with pytest.raises(SystemExit) as result:
        console.main(args)
    assert result.value.code == 0
    assert not importer[2]
    assert "preview" in capsys.readouterr().out


def test_preview_returns_counts_warnings_and_exact_budget_without_commit(importer, capsys):
    assert run(importer, "preview") == 0
    output = json.loads(capsys.readouterr().out)
    assert output["budgetId"] == "one" and output["fingerprint"] == importer[1]
    assert output["transactions"] == 3 and output["warnings"]
    request = importer[2][-1]
    assert request.url.path == "/budgets/one/api/plugins/ynab/import/preview"
    assert request.content == importer[0].read_bytes()
    assert request.headers["content-type"] == "application/zip"
    assert request.headers["x-import-currency"] == "USD"
    assert len(importer[2]) == 2


@pytest.mark.parametrize("plugin", ["ynab-zip-importer", "ynab", "other-importer"])
def test_commit_previews_then_echoes_current_parser_and_reviewed_fingerprint(importer, capsys, plugin):
    assert run(importer, "commit", "--fingerprint", importer[1], plugin=plugin) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"budgetId": "one", "fingerprint": importer[1], "counts": {"transactions": 3}, "alreadyImported": False}
    preview, commit = importer[2][-2:]
    assert preview.url.path.endswith("/import/preview") and commit.url.path.endswith("/import/commit")
    assert ("/other-importer/" if plugin == "other-importer" else "/ynab/") in commit.url.path
    assert preview.content == commit.content == importer[0].read_bytes()
    assert commit.headers["x-import-fingerprint"] == importer[1]
    assert commit.headers["x-import-parser"] == "parser-one"


def test_commit_requires_explicit_reviewed_fingerprint(importer):
    with pytest.raises(SystemExit) as result:
        run(importer, "commit")
    assert result.value.code == 2 and not importer[2]


@pytest.mark.parametrize("fingerprint", ["wrong", "0" * 64])
def test_changed_archive_cannot_reach_host(importer, capsys, fingerprint):
    assert run(importer, "commit", "--fingerprint", fingerprint) == 1
    assert not importer[2] and "reviewed fingerprint" in capsys.readouterr().err


@pytest.mark.parametrize("raw", [b"", b"x" * (10 * 1024 * 1024 + 1)], ids=["empty", "oversized"])
def test_empty_or_oversized_file_is_rejected_before_network(importer, capsys, raw):
    importer[0].write_bytes(raw)
    assert run(importer, "preview") == 1
    assert not importer[2] and "10 MiB" in capsys.readouterr().err


def test_missing_file_and_currency_do_not_disclose_private_values(importer, capsys):
    assert run(importer, "preview", file="~/private-missing.zip") == 1
    assert "private-missing" not in capsys.readouterr().err and not importer[2]
    with pytest.raises(SystemExit) as result:
        console.main(["Empty Budget", "import", "ynab", "preview", "~/export.zip"])
    assert result.value.code == 2
    assert console.main(["Empty Budget", "import", "ynab", "preview", str(importer[0]), "--currency", "usd"]) == 1
    assert "uppercase" in capsys.readouterr().err and not importer[2]


@pytest.mark.parametrize("changes", [{"fingerprint": "wrong"}, {"currency": "EUR"},
    {"parserIdentity": None}, {"parserIdentity": ""}, {"parserIdentity": "bad\r\nvalue"}])
def test_invalid_preview_cannot_commit(importer, capsys, changes):
    importer[3]["preview"].update(changes)
    assert run(importer, "commit", "--fingerprint", importer[1]) == 1
    assert len(importer[2]) == 2 and "invalid import preview" in capsys.readouterr().err


def test_non_object_preview_is_sanitized(importer, capsys):
    importer[3]["preview"] = []
    assert run(importer, "commit", "--fingerprint", importer[1]) == 1
    assert len(importer[2]) == 2 and "invalid import preview" in capsys.readouterr().err


def test_nonempty_budget_is_reported_by_preview_and_cannot_commit(importer, capsys):
    importer[3]["preview"].update(canImport=False, reason="File imports require an empty personal workspace.")
    assert run(importer, "preview") == 0
    assert not json.loads(capsys.readouterr().out)["canImport"]
    assert run(importer, "commit", "--fingerprint", importer[1]) == 1
    assert "cannot import" in capsys.readouterr().err
    assert all(not call.url.path.endswith("/commit") for call in importer[2])


def test_parser_replacement_between_preview_and_commit_is_not_retried(importer, capsys):
    importer[3]["commit_status"] = 409
    assert run(importer, "commit", "--fingerprint", importer[1]) == 1
    assert "HTTP 409" in capsys.readouterr().err and len(importer[2]) == 3


def test_transport_failure_after_commit_does_not_retry(importer, monkeypatch, capsys):
    original = console.exchange
    def fail_commit(connection, method, path, **kwargs):
        if path.endswith("/commit"):
            original(connection, method, path, **kwargs)
            raise httpx.ReadTimeout("private export")
        return original(connection, method, path, **kwargs)
    monkeypatch.setattr(console, "exchange", fail_commit)
    assert run(importer, "commit", "--fingerprint", importer[1]) == 1
    assert len(importer[2]) == 3 and "private export" not in capsys.readouterr().err


@pytest.mark.parametrize("status,body,expected", [
    (400, {"error": "Invalid table encoding."}, "Invalid table encoding."),
    (409, {"detail": "The import parser changed."}, "The import parser changed."),
    (413, {"error": "Request too large"}, "Request too large"),
    (400, [], None), (400, {"detail": 42}, None), (400, {"detail": ""}, None),
    (400, {"detail": "x" * 1001}, None), (500, {"error": "private crash"}, None),
], ids=["authored-error", "conflict-detail", "body-limit", "non-object", "non-string", "empty", "oversized", "server-error"])
def test_import_errors_show_only_bounded_authored_validation_messages(status, body, expected):
    def reply(request):
        return httpx.Response(status, json=body)
    with httpx.Client(base_url="https://localhost", transport=httpx.MockTransport(reply)) as connection:
        with pytest.raises(console.CommandError) as result:
            console.exchange(connection, "POST", "/preview", import_errors=True)
    assert f"HTTP {status}" in str(result.value)
    if expected:
        assert expected in str(result.value)
    else:
        assert "Inspect whab budget list" in str(result.value) and "private crash" not in str(result.value)


def test_invalid_error_json_retains_sanitized_status():
    def reply(request):
        return httpx.Response(400, content=b"private non-JSON response")
    with httpx.Client(base_url="https://localhost", transport=httpx.MockTransport(reply)) as connection:
        with pytest.raises(console.CommandError, match="HTTP 400") as result:
            console.exchange(connection, "POST", "/preview", import_errors=True)
    assert "private non-JSON" not in str(result.value)
