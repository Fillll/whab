"""Opt-in privileged bank descriptors remain distinct from scoped report hooks."""

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import get_type_hints

import pytest

from whab.plugins import (
    BankContext,
    BankError,
    BankStore,
    BankUI,
    BudgetSnapshot,
    ImportedAccount,
    ImportedTransaction,
    Insight,
    Plugin,
    SyncChanges,
    WebhookResult,
    load_plugins,
)

from .test_runtime import FakeEntryPoint


def test_normalized_import_records_and_scoped_store_are_public_sdk_types():
    annotations = get_type_hints(BankStore.apply_sync)
    assert annotations["changes"] is SyncChanges
    assert "balance" in ImportedAccount.__required_keys__
    assert "mask" in ImportedAccount.__optional_keys__
    assert "pendingTransactionId" in ImportedTransaction.__required_keys__
    for unavailable in ("state", "workspace", "close", "connection", "plaid_token"):
        assert not hasattr(BankStore, unavailable)


def test_bank_only_descriptor_is_discovered_without_constructing_service():
    contexts = []

    def factory(context):
        contexts.append(context)
        return object()

    bank = Plugin("bank", "Example bank", bank=factory)
    report = Plugin("report", "Example report", insights=lambda snapshot: (Insight("A", "1"),))
    registry = load_plugins(
        ("report", "bank"),
        (FakeEntryPoint("bank", bank), FakeEntryPoint("report", report)),
    )
    descriptions = registry.describe()
    assert descriptions[0].has_insights and not descriptions[0].has_bank
    assert descriptions[1].has_bank and not descriptions[1].has_export
    assert len(registry.bank_plugins()) == 1
    assert registry.bank_plugins()[0].id == "bank"
    assert contexts == []  # Host supplies the scoped context and owns startup/cleanup.


def test_disabled_bank_descriptor_never_loads_or_runs_factory():
    entry = FakeEntryPoint("bank", RuntimeError("must not import"))
    registry = load_plugins((), (entry,))
    assert registry.bank_plugins() == ()
    assert entry.loaded == 0


def test_bank_context_copies_config_freezes_fields_and_hides_privileged_values():
    fixture_config = {"EXAMPLE_SECRET": "private-fixture-secret"}
    fixture_store = object()
    context = BankContext(fixture_store, fixture_config, "sandbox", "private-fixture-key")
    fixture_config["EXAMPLE_SECRET"] = "changed"
    assert context.config["EXAMPLE_SECRET"] == "private-fixture-secret"
    with pytest.raises(TypeError):
        context.config["EXAMPLE_SECRET"] = "changed"
    with pytest.raises(FrozenInstanceError):
        context.environment = "production"
    assert "private-fixture" not in repr(context)
    assert "store" not in repr(context)
    assert "config" not in repr(context)


def test_privileged_context_does_not_enter_insight_or_export_snapshot():
    snapshot = BudgetSnapshot("demo", "2026-10", (), (), ())
    received = []

    def hook(data):
        received.append(data)
        assert not isinstance(data, BankContext)
        assert not hasattr(data, "config")
        assert not hasattr(data, "store")
        assert not hasattr(data, "encryption_key")
        return (Insight("Selected month", data.month),)

    registry = load_plugins(
        ("bank",),
        (
            FakeEntryPoint(
                "bank", Plugin("bank", "Example bank", insights=hook, bank=lambda context: None)
            ),
        ),
    )
    assert registry.insights(snapshot)[0].error is None
    assert received[0] == snapshot and received[0] is not snapshot


@pytest.mark.parametrize(
    ("config", "environment", "key"),
    [
        ([], "sandbox", ""),
        ({"A": object()}, "sandbox", ""),
        ({}, "live", ""),
        ({}, ["sandbox"], ""),
        ({}, "sandbox", object()),
    ],
)
def test_bank_context_rejects_invalid_primitive_configuration(config, environment, key):
    with pytest.raises(ValueError):
        BankContext(object(), config, environment, key)


def test_bank_ui_requires_path_resources_and_bank_capability(tmp_path):
    ui = BankUI(tmp_path / "templates", tmp_path / "static")
    bank = Plugin("bank", "Example bank", bank=lambda context: None, bank_ui=ui)
    assert bank.bank_ui == ui
    with pytest.raises(FrozenInstanceError):
        ui.static = Path("/other")
    with pytest.raises(ValueError, match="requires a bank factory"):
        Plugin("report", "Example report", insights=lambda snapshot: (), bank_ui=ui)
    with pytest.raises(ValueError):
        BankUI("templates", "static")
    with pytest.raises(ValueError):
        Plugin("bank", "Example bank", bank="not callable")


def test_webhook_result_is_a_strict_bounded_response_contract():
    assert WebhookResult(True) == WebhookResult(ok=True, status_code=200, error=None)
    assert WebhookResult(False, 401, "Invalid webhook").status_code == 401
    assert issubclass(BankError, ValueError)


@pytest.mark.parametrize(
    ("ok", "status_code", "error"),
    [
        (1, 200, None),
        (True, 400, None),
        (False, 200, None),
        (False, True, None),
        (False, 700, None),
        (True, 200, "unexpected error"),
        (False, 400, {}),
        (False, 400, "x" * 201),
    ],
)
def test_invalid_webhook_outputs_are_rejected(ok, status_code, error):
    with pytest.raises(ValueError):
        WebhookResult(ok, status_code, error)
