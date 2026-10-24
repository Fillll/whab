import asyncio
from dataclasses import replace
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest

from whab.budgets.runtime import BudgetRuntime
from whab.config import Settings
from whab.plugins import (
    Plugin,
    PluginContract,
    PluginError,
    PluginRegistry,
    PluginResources,
    UserInterface,
)
from whab.plugins.live import LivePluginSelection, RegistryGeneration
from whab.plugins.ui_resources import UIResourceArchive
from whab.plugins.ui_runtime import VisualController, apply_visual_choice, read_visual_choice
from whab.plugins.ui_selection import UISelectionStore
from whab.web.visual_interface import VisualGeneration


def descriptor(identifier, *, broken=False):
    def context(data, status, items, **values):
        if broken:
            raise RuntimeError("private fictional helper detail")
        return {"data": data}
    return Plugin(identifier, identifier, contract=PluginContract(lifecycle="live"), ui=UserInterface(
        None, None, context, view_protocol=1, resources=PluginResources({
            "templates/base.html": f"<p>{identifier}</p>".encode(), "static/app.js": identifier.encode(),
        })))


def setup(home, *plugins, selected=None, browser_timeout=0.1):
    registry = RegistryGeneration(PluginRegistry(tuple(plugin for plugin in plugins if plugin.id == selected)))
    archive = UIResourceArchive(home)
    active = VisualGeneration.prepare(next((row for row in plugins if row.id == selected), None), archive)
    ledger = object()
    runtime = BudgetRuntime("fictional", {"plugins": registry, "visual": active, "ledger": ledger})
    selection = UISelectionStore(home, "fictional", "sandbox", selected)
    settings = Settings(home, 5173, "sandbox", "", "", "", False, "", "", (), ui=selected or "")
    manager = SimpleNamespace(settings=settings, required=("sqlite", selected) if selected else ("sqlite",), _lock=Lock())
    entries = tuple(SimpleNamespace(name=plugin.id, group="whab.plugins", load=lambda plugin=plugin: plugin) for plugin in plugins)
    live = LivePluginSelection(runtime, manager.required, lambda: entries, lambda *_: None, home=home)
    controller = VisualController(runtime, selection, home, lambda: entries, manager, live, browser_timeout=browser_timeout)
    return controller, runtime, ledger


async def arm(controller, identifier):
    response = await controller.preview(identifier)
    plan = controller.previews.pop(response["preview_id"])
    return asyncio.create_task(controller.activate(plan, str(uuid4())))


def test_enable_replace_disable_and_reopen_choice_keep_services_and_original_resources(tmp_path):
    async def run():
        first, second = descriptor("first-ui"), descriptor("second-ui")
        controller, runtime, ledger = setup(tmp_path, first, second)
        assert await (await arm(controller, first.id)) == 2
        old = runtime._active.services["visual"]
        assert runtime._active.services["ledger"] is ledger
        assert controller.manager.required == ("sqlite", first.id)
        assert await (await arm(controller, second.id)) == 3
        assert controller.manager.required == ("sqlite", second.id)
        assert UIResourceArchive(tmp_path).load(old.generation).resources.files["static/app.js"] == b"first-ui"
        assert await (await arm(controller, None)) == 4
        assert controller.status()["active"]["plugin_id"] is None
        assert runtime._active.services["ledger"] is ledger
        assert controller.live.required == frozenset({"sqlite"})
        await controller.close()
        await runtime.close()
        choice = read_visual_choice(tmp_path, "fictional", "sandbox", "first-ui")
        assert choice["selection"] is None and choice["known"] == ["first-ui", "second-ui"]
        settings = replace(controller.manager.settings, ui="first-ui", plugins=("first-ui", "second-ui", "example"))
        restored = apply_visual_choice(settings, choice)
        assert restored.ui == "" and restored.plugins == ("example",)
    asyncio.run(run())


def test_unsaved_browser_defers_before_gate_and_preparation_failure_preserves_selection(tmp_path):
    async def run():
        first, second, broken = descriptor("first-ui"), descriptor("second-ui"), descriptor("broken-ui", broken=True)
        controller, runtime, _ = setup(tmp_path, first, second, broken, selected=first.id, browser_timeout=1)
        generation = controller.views.generation
        view = controller.views.register(generation, dirty=True)["view"]
        change = await arm(controller, second.id)
        while controller.views.pending is None and not change.done():
            await asyncio.sleep(0)
        assert not change.done() and runtime.status()["admission"] == "open"
        # Saving remains admitted before the browser acknowledges a pause.
        with runtime.lease():
            assert runtime._active.services["visual"].plugin_id == first.id
        proposal = controller.views.pending["id"]
        controller.views.update(view, generation, dirty=False, prepared=proposal)
        assert await change == 2 and controller.views.status(view)["reload"]
        controller.views.unregister(view)
        before = controller.selection.snapshot()
        with pytest.raises(PluginError):
            await (await arm(controller, broken.id))
        assert controller.selection.snapshot() == before
        assert runtime._active.services["visual"].plugin_id == second.id
        assert runtime.status()["admission"] == "open" and controller.views.pending is None
        await controller.close()
        await runtime.close()
    asyncio.run(run())


@pytest.mark.parametrize("boundary,authority", [("before_replace", "active"), ("after_replace", "candidate"), ("after_directory_fsync", "candidate")])
def test_lost_decision_reply_recovers_only_its_durable_visual_authority(tmp_path, boundary, authority):
    async def run():
        first, second = descriptor("first-ui"), descriptor("second-ui")
        controller, runtime, _ = setup(tmp_path, first, second, selected=first.id)
        def fail(stage):
            if stage == boundary:
                raise OSError("fictional private failure")
        controller.selection.store.fault = fail
        with pytest.raises(PluginError):
            await (await arm(controller, second.id))
        assert runtime.status()["health"] == "recovery_required" and controller.views.pending is not None
        controller.selection.store.fault = None
        # The optional-plugin endpoint must not validate an unrelated UI choice.
        with pytest.raises(PluginError):
            await controller.live.recover(authority)
        wrong = "candidate" if authority == "active" else "active"
        with pytest.raises(PluginError):
            await controller.recover(wrong)
        assert runtime.status()["admission"] == "gated"
        await controller.recover(authority)
        assert runtime.status()["admission"] == "open" and controller.views.pending is None
        expected = first.id if authority == "active" else second.id
        assert runtime._active.services["visual"].plugin_id == expected
        await controller.close()
        await runtime.close()
        assert read_visual_choice(tmp_path, "fictional", "sandbox", None)["selection"] == expected
    asyncio.run(run())


def test_stale_preview_and_legacy_browser_protocol_reject_without_changing_active_ui(tmp_path):
    async def run():
        first, second = descriptor("first-ui"), descriptor("second-ui")
        controller, runtime, _ = setup(tmp_path, first, second, selected=first.id)
        stale = await controller.preview(second.id)
        assert await (await arm(controller, None)) == 2
        plan = controller.previews.pop(stale["preview_id"])
        with pytest.raises(PluginError) as failure:
            await controller.activate(plan, str(uuid4()))
        assert failure.value.code == "ui_selection_conflict"
        assert runtime._active.services["visual"].plugin_id is None
        await controller.close()
        await runtime.close()
        legacy = replace(first, ui=replace(first.ui, view_protocol=0))
        controller, runtime, _ = setup(tmp_path / "legacy", legacy, second, selected=legacy.id)
        with pytest.raises(PluginError) as failure:
            await controller.preview(second.id)
        assert failure.value.code == "ui_transition_required"
        await controller.close()
        await runtime.close()
    asyncio.run(run())
