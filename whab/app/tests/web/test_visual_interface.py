import asyncio
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI, Request

from whab.budgets.host import BudgetRuntimeRequests
from whab.budgets.runtime import BudgetRuntime, RuntimeCandidate
from whab.plugins import Plugin, PluginContract, PluginRegistry, PluginResources, UserInterface
from whab.plugins.live import LeasedPluginRegistry, RegistryGeneration
from whab.plugins.ui_render import render_page
from whab.plugins.ui_resources import UIResourceArchive
from whab.plugins.ui_worker import proxy_context
from whab.web.visual_interface import (
    LeasedVisualUI,
    VisualGeneration,
    VisualTemplates,
    create_resource_router,
    native_code_identity,
)


def descriptor(version):
    return Plugin("fictional-ui", "Fictional UI", contract=PluginContract(lifecycle="live"), ui=UserInterface(
        None, None, lambda data, status, items, **values: {"data": data, "money": lambda number: f"fictional:{number}"},
        resources=PluginResources({"templates/base.html": f"<p>{version}:{{{{ money(data.amount) }}}}</p>".encode(),
                                   "static/app.js": f"/* {version} */".encode()})))


def test_routes_resolve_current_projection_and_keep_exact_retained_generations_after_restart(tmp_path):
    async def run():
        archive = UIResourceArchive(tmp_path)
        first, second = descriptor("one"), descriptor("two")
        old = VisualGeneration.prepare(first, archive, code_identity="1" * 64)
        new = VisualGeneration.prepare(second, archive, code_identity="2" * 64)
        runtime = BudgetRuntime("fictional", {"visual": old, "plugins": RegistryGeneration(PluginRegistry((first,)))})
        ui = LeasedVisualUI(runtime)
        templates = VisualTemplates(runtime, LeasedPluginRegistry(runtime))
        app = FastAPI()
        app.add_middleware(BudgetRuntimeRequests, runtime=runtime)
        app.include_router(create_resource_router(tmp_path))

        @app.get("/")
        def page(request: Request):
            context = ui.build_context({"amount": 123}, {}, [], workspace="personal", page="budget", month="2000-01", selected=None)
            return templates.TemplateResponse(request=request, name="base.html", context=context)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://localhost") as client:
            assert ui.available and ui.budget_months == 1
            assert (await client.get("/")).text == "<p>one:fictional:123</p>"
            await runtime.transition(lambda preparation: RuntimeCandidate({
                "visual": new, "plugins": RegistryGeneration(PluginRegistry((second,))),
            }))
            assert (await client.get("/")).text == "<p>two:fictional:123</p>"
            assert (await client.get(f"/ui/{old.generation}/static/app.js")).content == b"/* one */"
            assert (await client.get(f"/ui/{new.generation}/static/app.js")).content == b"/* two */"
            assert (await client.get("/ui/" + "0" * 64 + "/static/app.js")).status_code == 404
            assert (await client.get(f"/ui/{old.generation}/static/missing.js")).status_code == 404
            await runtime.transition(lambda preparation: RuntimeCandidate({
                "visual": VisualGeneration(None), "plugins": RegistryGeneration(PluginRegistry(())),
            }))
            assert not ui.available
            assert (await client.get("/")).status_code == 404
            assert (await client.get(f"/ui/{old.generation}/static/app.js")).content == b"/* one */"
        await runtime.close()
        reopened = FastAPI()
        reopened.include_router(create_resource_router(tmp_path))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=reopened), base_url="https://localhost") as client:
            response = await client.get(f"/ui/{old.generation}/static/app.js")
            assert response.content == b"/* one */" and "immutable" in response.headers["cache-control"]
            assert response.headers["x-content-type-options"] == "nosniff"
    asyncio.run(run())


def test_worker_parent_context_is_data_only_and_private_request_headers_never_cross(tmp_path):
    original = descriptor("worker")
    seen = []
    def child(arguments):
        seen.append(arguments)
        return render_page(original.ui, arguments)
    proxy = replace(original, ui=replace(original.ui, build_context=proxy_context, render=child))
    visual = VisualGeneration.prepare(proxy, UIResourceArchive(tmp_path), code_identity="3" * 64)
    runtime = BudgetRuntime("fictional", {"visual": visual, "plugins": RegistryGeneration(PluginRegistry((proxy,)))})
    ui = LeasedVisualUI(runtime)
    scope = {"type": "http", "scheme": "https", "method": "GET", "path": "/fictional", "query_string": b"q=fictional",
             "headers": [(b"authorization", b"Bearer PRIVATE"), (b"cookie", b"PRIVATE"), (b"x-whab-page-fragment", b"1")],
             "server": ("localhost", 443)}
    context = ui.build_context({"amount": 456}, {}, [], workspace="personal", page="budget", month="2000-01", selected=None)
    context.update(url=lambda: pytest.fail("A Python URL helper must not cross IPC"), _ui_routing={
        "key": "fictional", "personal_key": "fictional", "workspace": "personal", "page": "budget",
        "month_token": "2000-01", "currency": "USD", "account_slugs": {},
    })
    response = VisualTemplates(runtime, LeasedPluginRegistry(runtime)).TemplateResponse(request=Request(scope), name="base.html", context=context)
    assert response.body == b"<p>worker:fictional:456</p>"
    assert "PRIVATE" not in repr(seen) and seen[0]["request"]["headers"] == {"x-whab-page-fragment": "1"}


def test_native_code_identity_includes_source_changes_and_bounds_links(tmp_path, monkeypatch):
    import whab.web.visual_interface as module

    source = tmp_path / "builder.py"
    source.write_text("fictional = 1", encoding="utf-8")
    monkeypatch.setattr("inspect.getsourcefile", lambda _: str(source))
    monkeypatch.setattr(module.metadata, "entry_points", lambda **_: ())
    plugin = descriptor("native")
    before = native_code_identity(plugin)
    source.write_text("fictional = 2", encoding="utf-8")
    assert native_code_identity(plugin) != before
    (tmp_path / "linked.py").symlink_to(source)
    with pytest.raises(Exception) as failure:
        native_code_identity(plugin)
    assert failure.value.code == "ui_code_unavailable"
