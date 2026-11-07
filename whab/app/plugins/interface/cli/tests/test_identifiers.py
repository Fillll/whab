"""CLI importer identifiers retain the existing host and saved-budget identity."""

import pytest
from whab_plugin_cli import client
from whab_plugin_cli.identifiers import catalog_identifiers, cli_id, host_id, plugin_identifiers

from whab.plugins.documentation import installed_catalog


def test_identifier_mapping_preserves_unknown_and_legacy_identifiers():
    assert host_id("ynab-zip-importer") == "ynab"
    assert host_id("ynab") == "ynab"
    assert host_id("external") == cli_id("external") == "external"
    assert cli_id("ynab") == "ynab-zip-importer"
    source = {"plugins": [{"id": "external", "dependencies": [{"id": "ynab"}]}]}
    result = plugin_identifiers(source)
    assert result["plugins"][0]["dependencies"] == [{"id": "ynab-zip-importer"}]
    assert source["plugins"][0]["dependencies"] == [{"id": "ynab"}]


def test_reserved_cli_identifier_collision_fails_before_selecting_another_plugin():
    with pytest.raises(ValueError, match="conflicts"):
        catalog_identifiers({"plugins": [{"id": "ynab-zip-importer"}]})


@pytest.mark.parametrize("identifier", ["ynab-zip-importer", "ynab"])
def test_offline_help_and_json_describe_expose_explicit_identifier(identifier, capsys):
    import json

    with pytest.raises(SystemExit) as result:
        client.main(["describe", "--plugin", identifier, "--help"])
    assert result.value.code == 0
    output = capsys.readouterr().out
    assert "Plugin ynab-zip-importer " in output
    assert "YNAB ZIP importer" in output
    assert client.main(["describe", "--plugin", identifier, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["plugins"][0]["id"] == data["plugins"][0]["documentation"]["plugin_id"] == "ynab-zip-importer"
    assert any(row["id"] == "ynab" for row in installed_catalog()["plugins"])


def test_conflicting_installation_cannot_submit_plugin_management(monkeypatch, capsys):
    monkeypatch.setattr(client, "installed_catalog", lambda: {"plugins": [{"id": "ynab-zip-importer"}]})
    monkeypatch.setattr(client, "_local", lambda args: pytest.fail("Ambiguous identifiers cannot submit operations"))
    assert client.main(["--home", "/fictional", "plugins", "enable", "ynab-zip-importer"]) == 1
    assert "error" in capsys.readouterr().err


def test_top_level_help_advertises_canonical_importer_identifier(capsys):
    with pytest.raises(SystemExit) as result:
        client.main(["--help"])
    assert result.value.code == 0
    text = capsys.readouterr().out
    assert "ynab-zip-importer: YNAB ZIP importer" in text
    assert "  ynab:" not in text
