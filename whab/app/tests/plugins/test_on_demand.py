"""Stateless worker sessions retain exact code and release each invoked child."""

from types import SimpleNamespace

import pytest

from whab.plugins import Plugin, PluginContract, PluginError
from whab.plugins.on_demand import OnDemandProcess, release_stateless


def original():
    return SimpleNamespace(plugin_id="fictional", info={"version": "1"}, code_identity="a" * 64, scope=None, dependencies=())


def test_stateless_hook_acquires_then_releases_exact_child(monkeypatch):
    children = []
    class Child:
        def __init__(self, _):
            self.info, self.code_identity = {"version": "1"}, "a" * 64
            self.closed = self.cleanup_pending = False
            self._process = SimpleNamespace(pid=123)
            self.descriptor = SimpleNamespace(insights=lambda value: value + 1)
            children.append(self)
        def close(self):
            self.closed = True
    monkeypatch.setattr("whab.plugins.process_worker.PluginProcess", Child)
    owner = OnDemandProcess(original())
    assert owner.hook("insights")(1) == 2
    assert children[0].closed and owner._process is None and not owner.children
    owner.close()
    with pytest.raises(PluginError, match="closed"):
        owner.hook("insights")(1)
    assert len(children) == 1


def test_changed_code_is_closed_without_invoking_hook(monkeypatch):
    called = []
    child = SimpleNamespace(info={"version": "different"}, code_identity="a" * 64, cleanup_pending=False,
                            close=lambda: called.append("closed"))
    monkeypatch.setattr("whab.plugins.process_worker.PluginProcess", lambda _: child)
    # Use a hashable worker test double, like the production process owner.
    class Child:
        info, code_identity, cleanup_pending = child.info, child.code_identity, False
        def close(self):
            called.append("closed")
    monkeypatch.setattr("whab.plugins.process_worker.PluginProcess", lambda _: Child())
    with pytest.raises(PluginError, match="code changed"):
        OnDemandProcess(original()).hook("insights")(1)
    assert called == ["closed"]


def test_stateful_and_legacy_descriptors_keep_their_owner():
    process = original()
    legacy = Plugin("fictional", "Fictional", insights=lambda _: ())
    assert release_stateless(process, legacy) is legacy
    bound = Plugin("fictional", "Fictional", bind=lambda _: None, contract=PluginContract(lifecycle="live"))
    assert release_stateless(process, bound) is bound
