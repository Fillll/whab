"""Curated catalogs are detached data, never an executable package-discovery path."""

import copy
import json
from importlib import metadata
from pathlib import Path

import pytest

from whab.plugins import catalog


def corrupt_linked_artifact(value):
    row = next(row for row in value["plugins"] if row["verifications"])
    evidence = next(record for record in value["verifications"] if record["id"] == row["verifications"][0])
    evidence["artifacts"][row["distribution"]] = "9.9.9"


def test_catalog_reads_packaged_data_without_discovery_and_returns_independent_payloads(monkeypatch):
    def forbidden(*_, **__):
        pytest.fail("Catalog reads must not discover or import plugin entry points")

    monkeypatch.setattr(metadata, "entry_points", forbidden)
    first = catalog.catalog_snapshot()
    assert first["publisher"]["maintainer"] == "Fillll"
    assert first["publisher"]["security"] == "https://github.com/Fillll/whab/security/advisories/new"
    entries = {row["id"]: row for row in first["plugins"]}
    assert entries["example"]["live_support"] == "stateless-report-worker"
    assert entries["bank-logos"]["live_support"] == "stateless-optional-worker"
    assert entries["ynab"]["live_support"] == "stateless-optional-worker"
    assert entries["random-forest-categorization"]["live_support"] == "categorization-host"
    assert entries["sqlite"]["legacy"] is False
    assert entries["client"]["authority"] == "remote"
    assert entries["json"]["legacy"] is False
    assert entries["plaid"]["lifecycle"] == "restart"
    first["plugins"].clear()
    assert catalog.catalog_snapshot()["plugins"]


@pytest.mark.parametrize("change", [
    lambda value: value.update(format_version=True),
    lambda value: value.update(extra="forbidden"),
    lambda value: value["publisher"].update(repository="https://github.com/Fillll/whab?token=private"),
    lambda value: value["publisher"].update(security="http://example.invalid"),
    lambda value: value["publisher"].update(security_verified_date="not-a-date"),
    lambda value: value["plugins"].append(copy.deepcopy(value["plugins"][0])),
    lambda value: value["plugins"][0].update(api_version=True),
    lambda value: value["plugins"][0].update(state_upgrades=[[2, 1]]),
    lambda value: value["plugins"][0].update(lifecycle="live", live_support="all-families"),
    lambda value: value["plugins"][0].update(source="whab/app/plugins/../../budgets/private"),
    lambda value: value["plugins"][0]["entry_point"].update(value="../arbitrary:call"),
    lambda value: value["plugins"][0]["capabilities"].append(copy.deepcopy(value["plugins"][0]["capabilities"][0])),
    lambda value: value["plugins"][0].update(verifications=["invented-evidence"]),
    lambda value: value["plugins"][0].update(display_name="\ud800"),
    lambda value: value["plugins"][0].update(requires=[{"name": "ledger.read", "min_version": 3, "max_version": 2}]),
    lambda value: value["plugins"][0].update(conflicts=[value["plugins"][0]["capabilities"][0]["name"]]),
    lambda value: value["verifications"][0]["artifacts"].update(whab="9.9.9"),
    lambda value: value["verifications"][0].update(source_commit="not-a-reviewed-commit"),
    lambda value: value["verifications"][0].update(selection=["unreviewed-provider"]),
    lambda value: value["verifications"][0].update(date="2026-02-30"),
    lambda value: value["verifications"][0].update(pull_requests=["https://github.com/Fillll/whab/issues/79"]),
    corrupt_linked_artifact,
])
def test_invalid_catalog_data_is_rejected_with_authored_private_data_free_diagnostic(change):
    value = catalog.catalog_snapshot()
    change(value)
    with pytest.raises(catalog.CatalogError) as raised:
        catalog.validate_catalog(value)
    assert str(raised.value) == "The curated plugin catalog is unavailable or invalid. Check this WHAB installation."


@pytest.mark.parametrize("raw", [b"", b"not json", b'{"format_version":1,"format_version":1}', b'"\xff"'])
def test_missing_corrupt_or_duplicate_catalog_resource_never_falls_back_to_discovery(monkeypatch, raw):
    class Resource:
        def joinpath(self, _):
            return self

        def read_bytes(self):
            return raw

    monkeypatch.setattr(catalog, "files", lambda _: Resource())
    with pytest.raises(catalog.CatalogError):
        catalog.catalog_snapshot()


def test_source_packaged_catalog_contains_no_runtime_records_or_install_controls():
    raw = Path(catalog.__file__).with_name("catalog.json").read_bytes()
    value = json.loads(raw)
    assert len(raw) <= catalog.MAX_CATALOG_BYTES
    assert all("install" not in row and "commands" not in row and "config" not in row for row in value["plugins"])


def test_verification_retains_exact_historical_selection_without_claiming_all_installed_plugins():
    value = catalog.catalog_snapshot()
    evidence = {row["id"]: row for row in value["verifications"]}
    sqlite = evidence["pr82-installed-sqlite-web"]
    assert sqlite["selection"] == ["bank-logos", "example", "plaid", "sqlite", "web", "ynab"]
    assert "whab_plugin_random_forest_categorization" in sqlite["artifacts"]
    assert "random-forest-categorization" not in sqlite["selection"]
    assert evidence["pr82-installed-json-web"]["selection"] == ["json", "web"]
    assert evidence["pr79-json-native-https"]["host_version"] == "2.23.0"
    assert evidence["pr79-json-native-https"]["selection"] == ["json"]


def test_capability_requirement_ranges_are_detached_data_and_remain_independent_of_package_ranges():
    value = catalog.catalog_snapshot()
    row = value["plugins"][0]
    row["requires"] = [{"name": "custom.source", "min_version": 2},
                       {"name": "ledger.read", "min_version": 1, "max_version": 3}]
    row["conflicts"] = ["custom.writer"]
    validated = catalog.validate_catalog(value)
    assert validated["plugins"][0]["requires"] == row["requires"]
    assert validated["plugins"][0]["requires_host"] == row["requires_host"]
    row["requires"].clear()
    assert validated["plugins"][0]["requires"]


def test_declarative_api_label_requires_the_exact_interface_capability():
    value = catalog.catalog_snapshot()
    server = next(row for row in value["plugins"] if row["id"] == "server")
    assert server["live_support"] == "declarative-api-worker"
    assert server["capabilities"] == [{"name": "interface.api", "version": 1, "composition": "additive"}]
    for change in (
        {"live_support": "stateless-optional-worker"},
        {"capabilities": [{"name": "reporting.insights", "version": 1, "composition": "additive"}]},
        {"capabilities": [{"name": "interface.api", "version": 2, "composition": "additive"}]},
    ):
        malformed = copy.deepcopy(value)
        next(row for row in malformed["plugins"] if row["id"] == "server").update(change)
        with pytest.raises(catalog.CatalogError):
            catalog.validate_catalog(malformed)
