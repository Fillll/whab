"""Installed code binds independent closures to each budget's portable state."""

from dataclasses import replace

import pytest

from whab.budgets.session import BudgetSession
from whab.config import Settings
from whab.plugins import Interface, Plugin, PluginError, PluginRegistry


def test_budget_binding_creates_independent_contexts_and_preserves_disabled_data(tmp_path):
    contexts = []

    def bind(context):
        contexts.append(context)
        context.save_state({"visits": context.state.get("visits", 0) + 1})
        return Plugin("fixture", "Fixture", insights=lambda snapshot: ())

    installed = PluginRegistry((Plugin("fixture", "Fixture", bind=bind),))
    first = BudgetSession.create(tmp_path, "sandbox", "First", ("fixture",))
    second = BudgetSession.create(tmp_path, "sandbox", "Second", ("fixture",))
    one = installed.bind_budget(first.context)
    two = installed.bind_budget(second.context)
    assert one is not two
    assert contexts[0].budget_id != contexts[1].budget_id
    first.save_plugin_data("fixture", state={"visits": 4})
    assert second.plugin_record("fixture")["state"] == {"visits": 1}
    first.save_selection(())
    assert not first.plugin_record("fixture")["enabled"]
    first.save_selection(("fixture",))
    assert first.plugin_record("fixture")["state"] == {"visits": 4}
    with pytest.raises(TypeError):
        contexts[0].settings["new"] = "value"


@pytest.mark.parametrize("factory", [
    lambda context: None,
    lambda context: Plugin("other", "Wrong identity", insights=lambda snapshot: ()),
    lambda context: Plugin("fixture", "Wrong API", api_version=999, insights=lambda snapshot: ()),
    lambda context: Plugin("fixture", "Recursive binding", bind=lambda ctx: None),
])
def test_bad_binders_fail_with_sanitized_plugin_error(tmp_path, factory):
    session = BudgetSession.create(tmp_path, "sandbox", "Fixture", ("fixture",))
    registry = PluginRegistry((Plugin("fixture", "Fixture", bind=factory),))
    with pytest.raises(PluginError) as error:
        registry.bind_budget(session.context)
    assert error.value.code == "budget_plugin_startup_failed"


def test_interface_and_binder_validation_and_unbound_plugins():
    with pytest.raises(ValueError, match="Interface factory"):
        Interface(None)
    base = Plugin("fixture", "Fixture", insights=lambda snapshot: ())
    with pytest.raises(ValueError, match="interface"):
        replace(base, interface=object())
    with pytest.raises(ValueError, match="binder"):
        replace(base, bind=1)
    registry = PluginRegistry((base,))
    assert registry.bind_budget(lambda plugin: None).descriptors() == (base,)


def test_portable_budget_does_not_depend_on_unrelated_legacy_plugin_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("WHAB_HOME", str(tmp_path))
    monkeypatch.setenv("WHAB_BUDGET", str(tmp_path / "Family.whab"))
    monkeypatch.setenv("WHAB_ENV", "sandbox")
    (tmp_path / "plugins.json").write_text("invalid legacy configuration")
    assert Settings.load().budget_path == tmp_path / "Family.whab"
