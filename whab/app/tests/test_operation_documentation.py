"""Packaged discovery is complete, read-only and uses the dispatched schemas."""

import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from whab_plugin_server.plugin import plugin as server
from whab_plugin_subscriptions.plugin import plugin as subscriptions

from whab.app import create_app
from whab.config import Settings
from whab.plugins import FeatureOperations, PluginRegistry, StoreError
from whab.plugins import documentation as docs
from whab.plugins.operation_schema import validate_document, validate_value
from whab.services.ledger import LedgerService


def test_schema_reference_validation_never_attempts_network_lookup(monkeypatch):
    def no_lookup(*args, **kwargs):
        pytest.fail("Operation schemas must validate without network access.")

    monkeypatch.setattr("urllib.request.urlopen", no_lookup)
    validate_value({"$defs": {"count": {"type": "integer"}}, "$ref": "#/$defs/count"}, 1)
    with pytest.raises(ValueError):
        validate_value({"$ref": "#/extension", "extension": {"$ref": "https://example.invalid/no-network"}}, {})


def test_all_installed_plugins_have_complete_nonexecuting_documents(monkeypatch):
    calls = []
    entries = list(docs.metadata.entry_points(group="whab.plugins"))
    for entry in entries:
        monkeypatch.setattr(type(entry), "load", lambda self: calls.append(self.name))
    catalog = docs.installed_catalog()
    assert not calls and len(catalog["plugins"]) == 23
    assert all(row["documented"] and row["enabled"] is None for row in catalog["plugins"])
    assert len([row for row in catalog["core"]["operations"] if row["kind"] in {"query", "command"}]) == 26
    assert {row["name"] for row in catalog["core"]["operations"] if row["kind"] == "local"} == {
        "bank.connections", "bank.preview", "bank.configure", "bank.sync", "bank.schedule", "bank.background", "bank.investments",
    }
    for row in catalog["plugins"]:
        validate_document(row["documentation"], row["id"])
    catalog["core"]["operations"].clear()
    assert docs.core_document()["operations"]


def test_legacy_missing_invalid_and_ambiguous_resources_are_explicit(tmp_path, monkeypatch):
    entry = SimpleNamespace(value="fictional:plugin", name="fictional")
    monkeypatch.setattr(docs.util, "find_spec", lambda _: None)
    assert docs.installed_document(entry) is None

    def invalid(_):
        raise ValueError("no spec")

    monkeypatch.setattr(docs.util, "find_spec", invalid)
    assert docs.installed_document(entry) is None
    monkeypatch.setattr(
        docs.util,
        "find_spec",
        lambda _: SimpleNamespace(submodule_search_locations=[str(tmp_path)]),
    )
    assert docs.installed_document(entry) is None
    resource = tmp_path / "operations.json"
    resource.write_text("x" * (512 * 1024 + 1))
    with pytest.raises(ValueError):
        docs.installed_document(entry)
    resource.unlink()
    target = tmp_path / "target.json"
    target.write_text("{}")
    resource.symlink_to(target)
    with pytest.raises(ValueError):
        docs.installed_document(entry)
    monkeypatch.setattr(docs.metadata, "entry_points", lambda **_: [])
    assert docs.plugin_document("fictional") is None


def test_documented_feature_rejects_missing_contract_actions_and_invalid_input(store):
    template = docs.plugin_document("subscriptions")
    template["plugin_id"] = "fictional"
    template["operations"] = [
        row for row in template["operations"] if row["kind"].startswith("feature-")
    ]
    calls = []
    service = LedgerService(store)
    with pytest.raises(ValueError):
        service.register_feature(
            "fictional",
            FeatureOperations(("other",), (), lambda *args: None, documentation=template),
        )
    service.register_feature(
        "fictional",
        FeatureOperations(
            ("report",),
            ("set_label",),
            lambda *args: calls.append(args) or {},
            lambda *args: {},
            documentation=template,
        ),
    )
    template["operations"][0]["input"] = {}  # Registration owns a detached contract.
    with pytest.raises(StoreError, match="documented"):
        service.query(
            "feature",
            "personal",
            parameters={"pluginId": "fictional", "action": "report", "parameters": {}},
        )
    assert not calls
    service.query(
        "feature",
        "personal",
        parameters={
            "pluginId": "fictional",
            "action": "report",
            "parameters": {"month": "2026-11", "currency": "USD"},
        },
    )
    assert len(calls) == 1
    described = service.query("describe", "personal")["data"]
    record = next(row for row in described["plugins"] if row["id"] == "fictional")
    assert record["documented"] and record["enabled"]
    assert record["documentation"]["operations"][0]["input"]
    with pytest.raises(ValueError):
        FeatureOperations(("read",), (), lambda *args: None, documentation={})


def test_authority_description_includes_current_enablement_and_http_schemas_without_values(
    store, tmp_path, monkeypatch
):
    secret = "fictional-private-server-token-" + "x" * 32
    monkeypatch.setenv("SERVER_TOKEN", secret)
    account = store.add_account(
        "personal",
        {
            "name": "Private fictional account must not appear",
            "type": "depository",
            "balance": 12345,
        },
    )
    store.add_transaction(
        "personal",
        {
            "accountId": account,
            "date": "2026-11-01",
            "merchant": "Private fictional payee must not appear",
            "amount": 123,
            "categoryId": None,
        },
    )
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    app = create_app(
        store=store, settings=settings, plugins=PluginRegistry((server, subscriptions))
    )
    with TestClient(app, base_url="https://localhost:5173") as connection:
        path = "/remote/v1/budgets/default/queries/describe?workspace=personal"
        assert connection.get(path).status_code == 401
        result = connection.get(path, headers={"Authorization": "Bearer " + secret})
        assert result.status_code == 200
        data = result.json()["data"]
        assert data["source"] == "authority"
        rows = {row["id"]: row for row in data["plugins"]}
        assert rows["subscriptions"]["enabled"] and not rows["ai-credentials"]["enabled"]
        assert "/api/plugin-settings/subscriptions" in data["http"]["openapi"]["paths"]
        assert secret not in result.text and "Private fictional" not in result.text
        assert "isSubscription" in result.text and "api_key" in result.text
        # Describe does not advance the financial revision or trigger any model.
        assert (
            connection.get(path, headers={"Authorization": "Bearer " + secret}).json()["revision"]
            == result.json()["revision"]
        )


def test_discovery_can_forward_to_an_authority_without_reinterpreting_its_metadata():
    value = {"source": "authority", "core": {}, "plugins": []}
    service = LedgerService(
        SimpleNamespace(describe_operations=lambda workspace: copy.deepcopy(value))
    )
    assert service.query("describe", "personal")["data"] == value


def test_source_check_rejects_omitted_and_unshipped_resources(tmp_path):
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts/check_operations.py"
    spec = importlib.util.spec_from_file_location("operation_check", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths, errors = module.check()
    assert len(paths) == 24 and not errors
    assert module.main([]) == 0

    import shutil

    root = tmp_path / "fictional-source"
    for relative in (
        "src/whab/operations.json",
        "src/whab/services/ledger.py",
        "src/whab/plugins/operation_schema.py",
        "devtools/plugin-devkit/src/whab_plugin_devkit/operation_schema.py",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(script.parent.parent / relative, target)
    project = root / "plugins/interface/fictional"
    package = project / "src/fictional"
    package.mkdir(parents=True)
    metadata = project / "pyproject.toml"
    text = '[project.entry-points."whab.plugins"]\nfictional = "fictional:plugin"\n[tool.setuptools.package-data]\nfictional = ["operations.json"]\n'
    metadata.write_text(text)
    assert module.check(root)[1]
    resource = package / "operations.json"
    document = docs.plugin_document("subscriptions")
    document["plugin_id"] = "fictional"
    document["operations"] = [
        row for row in document["operations"] if row["kind"] == "local" and row["name"] == "status"
    ]
    resource.write_text(json.dumps(document))
    metadata.write_text(text.replace('"operations.json"', '"plugin.json"'))
    assert "Package data" in " ".join(module.check(root)[1])
    metadata.write_text(text)
    (project / "README.md").write_text(
        "## Operation documentation\n\n[Contract](src/fictional/operations.json)\n"
    )
    (package / "plugin.py").write_text(
        'from typing import Literal\nclass Request:\n action: Literal["status", "missing"]\n'
    )
    assert "missing" in " ".join(module.check(root)[1])
    (package / "plugin.py").write_text(
        'FeatureOperations(queries=("undocumented",), query=lambda: None)\n'
    )
    assert "Registered feature" in " ".join(module.check(root)[1])
    (package / "plugin.py").write_text('"""No additional actions."""\n')
    assert not module.check(root)[1]
