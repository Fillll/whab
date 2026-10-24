"""Strict reusable fixture checks and actual installed public HTTPS bank flows."""

from dataclasses import replace
from importlib import metadata

import pytest
from whab_plugin_devkit.bank_conformance import (
    BankRuntime,
    BankScenario,
    bank_applicability,
    exercise_bank_runtime,
)


def scenario():
    return BankScenario("example-bank", "fictional-item", "Fictional checking", "Fictional grocer", 725,
                        "fictional-public-token", "fictional-link-token")


@pytest.mark.parametrize("field,value", [
    ("plugin_id", "../private"), ("plugin_id", None), ("item_id", ""), ("item_id", "a" * 201),
    ("item_id", "nul\x00identifier"), ("merchant", "\ud800"), ("amount", True), ("amount", 0),
    ("amount", 2**53), ("currency", "usd"), ("currency", []), ("month", "2026-13"), ("month", True),
])
def test_scenario_rejects_unbounded_or_ambiguous_fixture_fields(field, value):
    with pytest.raises(ValueError, match="bounded fictional"):
        replace(scenario(), **{field: value})


def test_scenario_repr_and_runtime_environment_do_not_expose_or_inherit_credentials(monkeypatch):
    # This unit test does not start a host; the real profile below checks its
    # installed host version and explicitly reports older versions as N/A.
    monkeypatch.setattr("whab_plugin_devkit.bank_conformance.metadata.version", lambda _: "2.38.0")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "unrelated-fictional-private-key")
    monkeypatch.setenv("PLAID_SECRET", "unrelated-fictional-provider-secret")
    runtime = BankRuntime("sqlite", ("example-bank",))
    try:
        environment = runtime.environment()
        assert "PLAID_SECRET" not in environment
        assert environment["TOKEN_ENCRYPTION_KEY"] != "unrelated-fictional-private-key"
        assert len(environment["TOKEN_ENCRYPTION_KEY"]) == 64
        assert runtime.environment()["TOKEN_ENCRYPTION_KEY"] == environment["TOKEN_ENCRYPTION_KEY"]
        assert scenario().public_token not in repr(scenario()) and scenario().link_token not in repr(scenario())
    finally:
        runtime.__exit__(None, None, None)
    assert not runtime.home.exists()


def test_wrong_scenario_type_and_broken_advertised_runtime_fail_strictly():
    with pytest.raises(ValueError, match="explicit reviewed"):
        exercise_bank_runtime(None, {})

    class Broken:
        def expect(self, *_):
            return {"accounts": [{"source": "example-bank"}]}

    with pytest.raises(AssertionError, match="advertised public runtime"):
        exercise_bank_runtime(Broken(), scenario())


def test_older_host_is_explicitly_inapplicable_and_never_allocates_a_runtime(monkeypatch):
    assert bank_applicability("2.22.0") == "not-applicable: host predates typed bank runtime"
    assert bank_applicability("2.38.0") == "supported"
    monkeypatch.setattr("whab_plugin_devkit.bank_conformance.metadata.version", lambda _: "2.22.0")
    with pytest.raises(ValueError, match="requires WHAB 2.38"):
        BankRuntime("sqlite", ())


@pytest.mark.parametrize("storage", ["sqlite", "json"])
def test_actual_installed_offline_bank_over_native_https(storage):
    # The additive helper has its own explicit fixture prerequisite. It never
    # downloads, selects or executes an arbitrary community provider.
    if bank_applicability(metadata.version("whab")) != "supported":
        return  # Explicit profile N/A; no provider or host process was started.
    entries = {entry.name for entry in metadata.entry_points(group="whab.plugins")}
    if "example-bank" not in entries:
        pytest.fail("Install the reviewed plugins/example/example-bank fixture before running this new bank suite.")
    with BankRuntime(storage, ("example-bank",)) as runtime:
        result = exercise_bank_runtime(runtime, scenario())
        assert result["status"] == "passed" and result["profile"] == "offline-bank-v1"
        assert result["steps"][-1] == "demo-isolation"
    assert not runtime.home.exists() and runtime.process is None and runtime.cleanups == 2
