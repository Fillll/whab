"""Startup ownership begins before a worker can fail its hello or adapters."""

import asyncio
from types import SimpleNamespace

import pytest

from whab.budgets.runtime import RuntimeGeneration, RuntimePreparation
from whab.plugins import Plugin, PluginContract, PluginError
from whab.plugins import worker_selection as selection


@pytest.mark.parametrize("failure", ["hello", "adapter"])
def test_failed_start_keeps_exact_worker_resource_for_cleanup_without_exception_owner(monkeypatch, failure):
    allocated, resources = [], []
    class Worker:
        cleanup_pending = True
        healthy = False
        def __init__(self, *_, on_create, **__):
            self.closed = 0
            allocated.append(self)
            on_create(self)
            if failure == "hello":
                raise RuntimeError("fictional rejected hello")
            self.info = {"live": True, "adapters": {"bank_logos": None, "file_import": None, "ui": {}}}
        def visual_resources(self):
            raise RuntimeError("fictional invalid resource reply")
        def close(self):
            self.closed += 1
    monkeypatch.setattr(selection, "ReportWorker", Worker)
    template = Plugin("fictional", "Fictional", insights=lambda _: (), contract=PluginContract(lifecycle="live"))
    prepared = SimpleNamespace(python="reviewed-python", distribution="fictional", version="1.0.0")
    with pytest.raises(RuntimeError):
        selection._start(template, prepared, resource_owner=resources.append)
    assert len(allocated) == len(resources) == 1
    with pytest.raises(PluginError) as caught:
        asyncio.run(resources[0].close())
    assert caught.value.code == "worker_cleanup_pending"
    allocated[0].cleanup_pending = False
    asyncio.run(resources[0].close())
    assert allocated[0].closed >= 2


def test_successfully_reaped_probe_releases_only_its_preparation_resource():
    preparation = RuntimePreparation(RuntimeGeneration(1, {}, ()))
    owners = selection.PreparationWorkers(preparation, "fictional")
    first = SimpleNamespace(cleanup_pending=True, healthy=False, close=lambda: None)
    second = SimpleNamespace(cleanup_pending=False, healthy=False, close=lambda: None)
    owners.own(first)
    owners.own(second)
    retained = tuple(preparation._resources)
    with pytest.raises(PluginError):
        owners.retired(first)
    assert tuple(preparation._resources) == retained
    owners.retired(second)
    assert preparation._resources == [retained[0]]
    first.cleanup_pending = False
    owners.retired(first)
    assert preparation._resources == []
