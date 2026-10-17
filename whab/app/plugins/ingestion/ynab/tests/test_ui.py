"""Enabled file imports supply their own UI while host scope and mounting stay generic."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from whab_plugin_web.plugin import plugin as web_plugin
from whab_plugin_ynab.plugin import plugin as ynab_plugin

from whab.app import create_app
from whab.config import Settings
from whab.plugins import (
    FileImport,
    Plugin,
    PluginError,
    PluginRegistry,
    UserInterface,
    WorkspaceImport,
)


def settings(home, ui="web"):
    return Settings(home, 5173, "sandbox", "", "", "", False, "", "", (), ui=ui)


def client_for(store, home, *plugins, ui="web"):
    return TestClient(
        create_app(store=store, settings=settings(home, ui), plugins=PluginRegistry(plugins)),
        base_url="https://127.0.0.1:5173",
    )


def test_selected_import_renders_only_on_personal_accounts_and_serves_its_assets(store, tmp_path):
    with client_for(store, tmp_path, web_plugin, ynab_plugin) as client:
        response = client.get("/budgets/home/accounts/current-month/usd")
        assert response.status_code == 200
        assert "Import from YNAB ZIP file" in response.text
        assert 'data-file-import="ynab"' in response.text
        assert "I confirm this export uses US dollars (USD)." in response.text
        assert "Import into empty budget" in response.text
        assert "/imports/ynab/static/import.js" in response.text
        asset = client.get("/imports/ynab/static/import.js")
        assert asset.status_code == 200
        assert "X-Import-Fingerprint" in asset.text
        for workspace, page in (("demo", "accounts"), ("personal", "budget"), ("personal", "transactions")):
            response = client.get(f"/budgets/{workspace.replace('personal', 'home')}/{page}/current-month/usd")
            assert response.status_code == 200
            assert "data-file-import" not in response.text
            assert "/imports/ynab/static/import.js" not in response.text


def test_inactive_import_has_no_form_or_public_assets(store, tmp_path):
    with client_for(store, tmp_path, web_plugin) as client:
        response = client.get("/budgets/home/accounts/current-month/usd")
        assert response.status_code == 200
        assert "data-file-import" not in response.text
        assert client.get("/imports/ynab/static/import.js").status_code == 404


def test_alternate_ui_can_embed_import_without_bundled_interface_helpers(store, tmp_path):
    templates, static = tmp_path / "templates", tmp_path / "static"
    templates.mkdir()
    static.mkdir()
    (templates / "base.html").write_text(
        "{% for importer in import_panels %}{% include importer.template %}{% endfor %}"
        '{% for path in plugin_scripts %}<script type="module" src="{{ path }}"></script>{% endfor %}'
    )
    alternate = Plugin("alternate", "Alternate UI", ui=UserInterface(templates, static, lambda *a, **k: {}))
    with client_for(store, tmp_path, alternate, ynab_plugin, ui="alternate") as client:
        response = client.get("/budgets/home/accounts/current-month/usd")
        assert response.status_code == 200
        assert "data-file-import" in response.text
        assert "/imports/ynab/static/import.js" in response.text
        assert client.get("/imports/ynab/static/import.js").status_code == 200
        assert client.get("/static/client.js").status_code == 404


def test_import_without_ui_resources_keeps_pages_usable(store, tmp_path):
    api_only = replace(ynab_plugin, file_import=FileImport(ynab_plugin.file_import.parse))
    with client_for(store, tmp_path, web_plugin, api_only) as client:
        response = client.get("/budgets/home/accounts/current-month/usd")
        assert response.status_code == 200
        assert "data-file-import" not in response.text
        assert client.get("/imports/ynab/static/import.js").status_code == 404


@pytest.mark.parametrize("missing", ("template", "script"))
def test_missing_selected_import_resources_fail_before_startup(store, tmp_path, missing):
    templates, static = tmp_path / "templates", tmp_path / "static"
    templates.mkdir()
    static.mkdir()
    if missing != "template":
        (templates / "accounts.html").write_text("Import")
    if missing != "script":
        (static / "import.js").write_text("// Import")
    broken = replace(ynab_plugin, file_import=FileImport(ynab_plugin.file_import.parse, templates, static))
    with pytest.raises(PluginError) as error:
        client_for(store, tmp_path, web_plugin, broken)
    assert error.value.code == "missing_resources"
    assert error.value.plugin_id == "ynab"


def test_headless_import_never_mounts_visuals_or_requires_ui_resources(store, tmp_path):
    absent = tmp_path / "absent"
    headless = replace(ynab_plugin, file_import=FileImport(ynab_plugin.file_import.parse, absent, absent))
    with client_for(store, tmp_path, headless, ui="") as client:
        assert client.get("/").status_code == 404
        assert client.get("/imports/ynab/static/import.js").status_code == 404
        assert client.get("/api/workspace?workspace=personal").status_code == 200


def test_imported_accounts_are_not_labelled_as_sandbox_bank_accounts(store, tmp_path):
    store.import_workspace("personal", "ynab", "a" * 64, WorkspaceImport(
        accounts=({
            "id": "imported", "name": "Imported account", "institution": "YNAB export",
            "type": "other", "balance": 0, "source": "ynab", "stale": True,
            "balanceUnknown": True,
        },),
        categories=(), transactions=(), assignments=(), budget_start_month="2026-10",
    ))
    with client_for(store, tmp_path, web_plugin, ynab_plugin) as client:
        response = client.get("/budgets/home/accounts/current-month/usd")
        assert response.status_code == 200
        assert "Test account" not in response.text
