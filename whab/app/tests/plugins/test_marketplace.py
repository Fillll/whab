"""Curated installation reviews preserve existing packages and never activate code."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from whab.plugins import PluginError
from whab.plugins.manifests import installed_manifest, validate_dependencies, validate_manifest
from whab.plugins.marketplace import Marketplace


def manifest(identifier, dependencies=()):
    return {"schema_version": 1, "id": identifier, "category": "extension", "dependencies": list(dependencies)}


def row(identifier, dependencies=()):
    return {**manifest(identifier, dependencies), "distribution": "whab_plugin_" + identifier.replace("-", "_"),
            "version": "1.0.0", "display_name": identifier, "requires_host": "whab>=2,<3", "requires_python": ">=3.14",
            "source": "whab/app/plugins/extension/" + identifier}


def test_manifests_are_data_only_and_reject_ambiguous_dependencies(tmp_path, monkeypatch):
    package = tmp_path / "fictional_package"
    package.mkdir()
    (package / "__init__.py").write_text("raise AssertionError('Inactive code executed')")
    (package / "plugin.json").write_text(json.dumps(manifest("fictional")))
    monkeypatch.syspath_prepend(str(tmp_path))
    entry = SimpleNamespace(name="fictional", value="fictional_package:plugin")
    assert installed_manifest(entry) == manifest("fictional")
    (package / "plugin.json").write_text('{"invalid":true}')
    with pytest.raises(PluginError, match="metadata is invalid"):
        installed_manifest(entry)
    for invalid in ({}, manifest("bad", [{"id": "bad", "version": ""}]),
                    manifest("good", [{"id": "other", "version": "not-a-version"}])):
        with pytest.raises(ValueError):
            validate_manifest(invalid)


def test_named_dependencies_require_enabled_compatible_providers_and_reject_cycles(monkeypatch):
    records = {"consumer": manifest("consumer", [{"id": "provider", "version": ">=1,<2"}]), "provider": manifest("provider")}
    entries = [SimpleNamespace(name=name, dist=SimpleNamespace(version="1.0.0")) for name in records]
    monkeypatch.setattr("whab.plugins.manifests.installed_manifest", lambda entry: records[entry.name])
    validate_dependencies(records, entries)
    with pytest.raises(PluginError, match="requires enabled plugin"):
        validate_dependencies(["consumer"], entries)
    entries[1].dist.version = "2.0.0"
    with pytest.raises(PluginError, match="requires enabled plugin"):
        validate_dependencies(records, entries)
    entries[1].dist.version = "1.0.0"
    records["provider"]["dependencies"] = [{"id": "consumer", "version": ""}]
    with pytest.raises(PluginError, match="cycle"):
        validate_dependencies(records, entries)


def test_marketplace_closure_orders_dependencies_and_refuses_missing_incompatible_cycles():
    marketplace = Marketplace()
    rows = [row("consumer", [{"id": "provider", "version": ">=1,<2"}]), row("provider")]
    assert [item["id"] for item in marketplace._closure("consumer", rows)] == ["provider", "consumer"]
    with pytest.raises(PluginError, match="unavailable"):
        marketplace._closure("missing", rows)
    rows[1]["version"] = "2.0.0"
    with pytest.raises(PluginError, match="compatible"):
        marketplace._closure("consumer", rows)
    rows[1]["version"] = "1.0.0"
    rows[1]["dependencies"] = [{"id": "consumer", "version": ""}]
    with pytest.raises(PluginError, match="cycle"):
        marketplace._closure("consumer", rows)


@pytest.fixture
def prepared_marketplace(monkeypatch):
    marketplace = Marketplace()
    monkeypatch.setattr(marketplace, "_data", lambda: ([row("fictional")], "a" * 40))
    environment = {"whab": "2.42.0"}
    monkeypatch.setattr("whab.plugins.marketplace._freeze", lambda: dict(environment))
    calls = []

    def run(arguments):
        calls.append(arguments)
        if arguments[0] == "wheel":
            path = Path(arguments[arguments.index("--wheel-dir") + 1]) / "whab_plugin_fictional-1.0.0-py3-none-any.whl"
            path.write_bytes(b"reviewed fictional wheel")
        elif "--dry-run" in arguments:
            Path(arguments[arguments.index("--report") + 1]).write_text(json.dumps({"install": [
                {"metadata": {"name": "whab_plugin_fictional", "version": "1.0.0"}, "download_info": {"url": "file:///fictional.whl"}},
            ]}))
        elif arguments[0] == "install":
            environment["whab-plugin-fictional"] = "1.0.0"

    monkeypatch.setattr("whab.plugins.marketplace._run", run)
    yield marketplace, environment, calls
    for plan in marketplace._plans.values():
        plan["directory"].cleanup()


def test_review_then_install_uses_hashed_offline_wheels_once_and_preserves_host(prepared_marketplace):
    marketplace, environment, calls = prepared_marketplace
    review = marketplace.review("fictional")
    assert environment == {"whab": "2.42.0"}
    assert review["packages"] == [{"name": "whab_plugin_fictional", "version": "1.0.0"}]
    assert marketplace.install(review["token"])["installed"] == ["fictional"]
    assert calls[-1][:3] == ["install", "--no-index", "--no-deps"]
    assert environment["whab"] == "2.42.0"
    with pytest.raises(PluginError, match="Review"):
        marketplace.install(review["token"])


@pytest.mark.parametrize("failure", ["changed-environment", "changed-wheel", "expired"])
def test_stale_or_changed_installation_never_runs_pip(prepared_marketplace, failure):
    marketplace, environment, calls = prepared_marketplace
    review = marketplace.review("fictional")
    plan = marketplace._plans[review["token"]]
    directory = Path(plan["directory"].name)
    if failure == "changed-environment":
        environment["new-package"] = "1"
    elif failure == "changed-wheel":
        Path(next(iter(plan["artifacts"]))).write_bytes(b"changed")
    else:
        plan["created"] -= 901
    count = len(calls)
    with pytest.raises(PluginError):
        marketplace.install(review["token"])
    assert len(calls) == count and not directory.exists()


def test_installed_plugins_are_not_silently_replaced(prepared_marketplace):
    marketplace, environment, calls = prepared_marketplace
    environment["whab-plugin-fictional"] = "1.0.0"
    with pytest.raises(PluginError, match="already installed"):
        marketplace.review("fictional")
    environment["whab-plugin-fictional"] = "2.0.0"
    with pytest.raises(PluginError, match="different version"):
        marketplace.review("fictional")
    assert not calls


def test_live_catalog_marketplace_has_all_six_primary_categories_and_no_teaching_packages():
    rows = Marketplace().projection()["plugins"]
    assert rows and all(row["category"] != "example" for row in rows)
    assert {row["id"] for row in rows} >= {"web", "sqlite", "plaid", "random-forest-categorization"}
