"""Storage and browser interfaces are explicit capabilities, independent of their implementations."""

from dataclasses import FrozenInstanceError
from inspect import signature
from pathlib import Path

import pytest

from whab.plugins import (
    UNSET,
    BankStore,
    Insight,
    Plugin,
    PluginError,
    PluginRegistry,
    StorageBackend,
    StorageContext,
    StoreError,
    UserInterface,
    load_plugins,
)

from .test_runtime import FakeEntryPoint


def build_context(data, status, items, *, workspace, page, month, selected=None):
    return {
        "data": data,
        "status": status,
        "items": items,
        "workspace": workspace,
        "page": page,
        "month": month,
        "selected": selected,
    }


def test_storage_factory_descriptor_can_be_loaded_and_selected_without_running_factory():
    calls = []
    descriptor = Plugin("backend", "Example storage", storage=lambda context: calls.append(context))
    entry = FakeEntryPoint("backend", descriptor)
    registry = load_plugins(("backend",), (entry,))
    selected = registry.storage_plugin("backend")
    assert selected.id == "backend" and selected.storage is descriptor.storage
    assert registry.describe()[0].has_storage
    assert registry.describe()[0].kinds == ("storage",)
    assert calls == []
    assert entry.loaded == 1


def test_storage_selector_rejects_an_extra_enabled_backend_without_constructing_any():
    calls = []
    registry = PluginRegistry(
        (
            Plugin("one", "First backend", storage=lambda context: calls.append("one")),
            Plugin("two", "Second backend", storage=lambda context: calls.append("two")),
        )
    )
    with pytest.raises(PluginError, match="Multiple storage plugins") as error:
        registry.storage_plugin("one")
    assert error.value.code == "multiple_storage_plugins"
    assert calls == []


def test_storage_selection_errors_are_distinct_and_sanitized():
    report = Plugin("report", "Report", insights=lambda snapshot: (Insight("A", "1"),))
    registry = PluginRegistry((report,))
    for name, code in (
        ("backend", "missing_storage_plugin"),
        ("report", "invalid_storage_plugin"),
        ("../private-fixture-secret", "invalid_storage_configuration"),
        ("", "invalid_storage_configuration"),
    ):
        with pytest.raises(PluginError) as error:
            registry.storage_plugin(name)
        assert error.value.code == code
        assert "private-fixture-secret" not in str(error.value)


def test_storage_context_is_frozen_with_copied_configuration_and_no_bank_credentials(tmp_path):
    config = {"EXAMPLE_DSN": "private-storage-fixture"}
    context = StorageContext(tmp_path, "sandbox", config)
    config["EXAMPLE_DSN"] = "changed"
    assert context.home == tmp_path and context.environment == "sandbox"
    assert context.config["EXAMPLE_DSN"] == "private-storage-fixture"
    assert "private-storage-fixture" not in repr(context)
    with pytest.raises(TypeError):
        context.config["EXAMPLE_DSN"] = "changed"
    with pytest.raises(FrozenInstanceError):
        context.environment = "production"
    for unavailable in ("store", "settings", "encryption_key", "plaid_secret", "access_token"):
        assert not hasattr(context, unavailable)


@pytest.mark.parametrize(
    ("home", "environment", "config"),
    [
        ("/tmp", "sandbox", {}),
        (Path("/tmp"), "live", {}),
        (Path("/tmp"), ["sandbox"], {}),
        (Path("/tmp"), "sandbox", []),
        (Path("/tmp"), "sandbox", {"DSN": object()}),
    ],
)
def test_storage_context_rejects_invalid_shapes(home, environment, config):
    with pytest.raises(ValueError):
        StorageContext(home, environment, config)


def test_storage_backend_protocol_is_database_independent_and_preserves_public_sentinel():
    from whab.domain.errors import StoreError as DomainStoreError
    from whab.domain.transactions import UNSET as DomainUnset

    assert StoreError is DomainStoreError
    assert UNSET is DomainUnset
    assert signature(StorageBackend.edit_transaction).parameters["category_id"].default is UNSET
    assert signature(StorageBackend.edit_transaction).parameters["note"].default is UNSET
    assert signature(StorageBackend.apply_sync).parameters["expected_cursor"].default is UNSET
    assert signature(BankStore.apply_sync).parameters["expected_cursor"].default is UNSET
    expected = {
        "close",
        "accounts",
        "categories",
        "transactions",
        "workspace",
        "add_account",
        "add_category",
        "add_transaction",
        "edit_transaction",
        "assign",
        "items",
        "get_item",
        "save_item",
        "set_item_status",
        "disconnect_item",
        "apply_sync",
    }
    methods = {
        name
        for name, value in vars(StorageBackend).items()
        if not name.startswith("_") and callable(value)
    }
    assert methods == expected
    for unavailable in ("state", "db", "database_path", "_mutate", "migrate"):
        assert not hasattr(StorageBackend, unavailable)
    assert signature(StorageBackend.save_item).parameters["provider_id"].default == "plaid"


def test_user_interface_descriptor_is_frozen_and_matches_view_builder_signature(tmp_path):
    interface = UserInterface(tmp_path / "templates", tmp_path / "static", build_context)
    assert interface.budget_months == 1
    descriptor = Plugin("frontend", "Example UI", ui=interface)
    registry = load_plugins(("frontend",), (FakeEntryPoint("frontend", descriptor),))
    selected = registry.ui_plugin("frontend")
    context = selected.ui.build_context(
        {"month": "2026-10"},
        {},
        [],
        workspace="demo",
        page="budget",
        month="2026-10",
        selected="groceries",
    )
    assert context["workspace"] == "demo" and context["selected"] == "groceries"
    assert registry.describe()[0].has_ui
    assert registry.describe()[0].kinds == ("user_interface",)
    with pytest.raises(FrozenInstanceError):
        interface.static = tmp_path / "other"


def test_interface_selector_rejects_an_extra_enabled_interface(tmp_path):
    interface = UserInterface(tmp_path, tmp_path, build_context)
    registry = PluginRegistry(
        (Plugin("one", "One UI", ui=interface), Plugin("two", "Two UI", ui=interface))
    )
    with pytest.raises(PluginError, match="Multiple user interface plugins") as error:
        registry.ui_plugin("one")
    assert error.value.code == "multiple_ui_plugins"


def test_interface_selection_errors_are_distinct_and_sanitized():
    registry = PluginRegistry((Plugin("report", "Report", insights=lambda snapshot: ()),))
    for name, code in (
        ("frontend", "missing_ui_plugin"),
        ("report", "invalid_ui_plugin"),
        ("../private-fixture-secret", "invalid_ui_configuration"),
        ("", "invalid_ui_configuration"),
    ):
        with pytest.raises(PluginError) as error:
            registry.ui_plugin(name)
        assert error.value.code == code
        assert "private-fixture-secret" not in str(error.value)


@pytest.mark.parametrize(
    "templates,static,builder",
    [
        ("templates", Path("/tmp"), build_context),
        (Path("/tmp"), "static", build_context),
        (Path("/tmp"), Path("/tmp"), None),
    ],
)
def test_user_interface_rejects_invalid_resource_or_builder_types(templates, static, builder):
    with pytest.raises(ValueError):
        UserInterface(templates, static, builder)


@pytest.mark.parametrize("months", [True, False, None, "3", 3.0, 0, -1, 13])
def test_user_interface_rejects_invalid_budget_month_count(tmp_path, months):
    with pytest.raises(ValueError, match="budget months"):
        UserInterface(tmp_path, tmp_path, build_context, budget_months=months)


@pytest.mark.parametrize("months", [1, 3, 12])
def test_user_interface_accepts_bounded_budget_month_count(tmp_path, months):
    assert UserInterface(tmp_path, tmp_path, build_context, budget_months=months).budget_months == months


def test_capability_kinds_are_ordered_and_reporting_is_one_kind_for_both_hooks(tmp_path):
    from whab.plugins import ExportTable

    descriptor = Plugin(
        "combined",
        "Combined example",
        insights=lambda snapshot: (),
        export=lambda snapshot: ExportTable(("A",), ()),
        bank=lambda context: None,
        storage=lambda context: None,
        ui=UserInterface(tmp_path, tmp_path, build_context),
    )
    (description,) = PluginRegistry((descriptor,)).describe()
    assert description.kinds == ("user_interface", "storage", "data_ingestion", "reporting")
    assert description.has_ui and description.has_storage and description.has_bank
    assert description.has_insights and description.has_export


def test_plugin_rejects_invalid_storage_or_ui_descriptors():
    with pytest.raises(ValueError):
        Plugin("backend", "Backend", storage="not callable")
    with pytest.raises(ValueError):
        Plugin("frontend", "UI", ui={"templates": "not typed"})


def test_empty_registry_remains_metadata_free_until_host_composes_selections(monkeypatch):
    def unexpected_discovery(**kwargs):
        pytest.fail("Pure empty registry should not discover any installed metadata")

    monkeypatch.setattr("whab.plugins.runtime.metadata.entry_points", unexpected_discovery)
    registry = load_plugins(())
    assert registry.describe() == ()
    with pytest.raises(PluginError) as error:
        registry.storage_plugin("backend")
    assert error.value.code == "missing_storage_plugin"
