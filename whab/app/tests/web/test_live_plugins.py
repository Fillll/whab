"""Live report selection preserves budget identity, security and committed state."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from threading import Event

import httpx
import pytest
from whab_plugin_sqlite.plugin import plugin as sqlite_plugin

from whab.app import create_app
from whab.budgets.session import BudgetSession
from whab.config import Settings
from whab.plugins import ExportTable, Insight, Plugin, PluginContract, PluginRegistry, Requirement
from whab.plugins.configuration import read_selection

BASE = "https://127.0.0.1:5173"


@dataclass
class Entry:
    name: str
    descriptor: Plugin
    group: str = "whab.plugins"

    def load(self):
        return self.descriptor


def report(identifier, value, hook=None):
    return Plugin(identifier, identifier.title(),
                  insights=lambda _: (Insight("Result", value),),
                  export=hook or (lambda _: ExportTable(("result",), ((value,),))),
                  contract=PluginContract(lifecycle="live"))


def settings(home, plugins=("first",)):
    return Settings(home, 5173, "sandbox", "", "", "", False, "", "", plugins, ui="")


def install(monkeypatch, *plugins):
    entries = tuple(Entry(plugin.id, plugin) for plugin in (sqlite_plugin, *plugins))
    monkeypatch.setattr("whab.plugins.management.metadata.entry_points", lambda **_: entries)
    return entries


@asynccontextmanager
async def running(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
            yield client


async def settled(app):
    if app.state.live_plugins._tasks:
        await asyncio.gather(*tuple(app.state.live_plugins._tasks), return_exceptions=True)


def export_url(identifier, prefix=""):
    return f"{prefix}/api/plugins/{identifier}/export?workspace=personal&month=2026-10"


def test_held_parser_finishes_before_swap_and_stale_commit_never_calls_replacement(tmp_path, store, monkeypatch):
    from whab.budgets.runtime import RuntimeCandidate
    from whab.plugins import FileImport, WorkspaceImport
    from whab.plugins.live import RegistryGeneration

    async def scenario():
        entered, finish = Event(), Event()
        calls = []

        def old_parser(raw):
            entered.set()
            assert finish.wait(5)
            return WorkspaceImport((), (), (), (), "2026-10", warnings=("Original parser",))

        def replacement(raw):
            calls.append(raw)
            return WorkspaceImport((), (), (), (), "2026-10", warnings=("Replacement parser",))

        first = Plugin("parser", "Original", file_import=FileImport(old_parser), contract=PluginContract(lifecycle="live"))
        second = replace(first, file_import=FileImport(replacement))
        install(monkeypatch, first)
        app = create_app(store=store, settings=settings(tmp_path, ("parser",)), plugins=PluginRegistry((sqlite_plugin, first)))
        headers = {"Content-Type": "application/zip", "X-Import-Currency": "USD"}
        path = "/api/plugins/parser/import/"
        async with running(app) as client:
            pending = asyncio.create_task(client.post(path + "preview", content=b"fictional archive", headers=headers))
            assert await asyncio.to_thread(entered.wait, 5)

            async def prepare(preparation):
                active = preparation.active.services["plugins"]
                registry = PluginRegistry((sqlite_plugin, second))
                return RuntimeCandidate({**preparation.active.services, "plugins": RegistryGeneration(registry, active.stable, previous=active)}, preparation.active.resources)

            change = asyncio.create_task(app.state.budget_runtime.transition(prepare))
            await app.state.budget_runtime.wait_for_phase("draining")
            finish.set()
            preview = (await pending).json()
            assert preview["warnings"] == ["Original parser"]
            assert await change == 2
            for token in (preview["parserIdentity"], None):
                commit_headers = {**headers, "X-Import-Fingerprint": preview["fingerprint"]}
                if token:
                    commit_headers["X-Import-Parser"] = token
                response = await client.post(path + "commit", content=b"fictional archive", headers=commit_headers)
                assert response.status_code == 409
            assert calls == [] and store.accounts("personal") == []
            fresh = (await client.post(path + "preview", content=b"fictional archive", headers=headers)).json()
            assert fresh["warnings"] == ["Replacement parser"]
            assert fresh["fingerprint"] == preview["fingerprint"] and fresh["parserIdentity"] != preview["parserIdentity"]

    asyncio.run(scenario())


def test_unrelated_live_change_keeps_parser_preview_identity(tmp_path, store, monkeypatch):
    from whab.plugins import FileImport, WorkspaceImport

    async def scenario():
        parser = Plugin("parser", "Parser", file_import=FileImport(lambda _: WorkspaceImport((), (), (), (), "2026-10")), contract=PluginContract(lifecycle="live"))
        extra = report("second", "New report")
        install(monkeypatch, parser, extra)
        app = create_app(store=store, settings=settings(tmp_path, ("parser",)), plugins=PluginRegistry((sqlite_plugin, parser)))
        headers = {"Content-Type": "application/zip", "X-Import-Currency": "USD"}
        path = "/api/plugins/parser/import/"
        async with running(app) as client:
            before = (await client.post(path + "preview", content=b"fictional archive", headers=headers)).json()
            assert await app.state.live_plugins.activate(("parser", "second")) == 2
            after = (await client.post(path + "preview", content=b"fictional archive", headers=headers)).json()
            assert before["parserIdentity"] == after["parserIdentity"]
            response = await client.post(path + "commit", content=b"fictional archive", headers={**headers,
                "X-Import-Fingerprint": before["fingerprint"], "X-Import-Parser": before["parserIdentity"]})
            assert response.status_code == 200

    asyncio.run(scenario())


def test_bundled_branding_and_import_enable_disable_with_captured_assets_and_upload_guards(tmp_path, store, monkeypatch):
    import importlib
    import re

    from whab_plugin_bank_logos.plugin import plugin as logos_plugin
    from whab_plugin_web.plugin import plugin as web_plugin
    from whab_plugin_ynab.plugin import plugin as ynab_plugin

    from whab.plugins import LogoImage

    async def scenario():
        install(monkeypatch, logos_plugin, ynab_plugin, web_plugin)
        calls = []
        image = LogoImage(bytes.fromhex("89504e470d0a1a0a") + b"fictional", "image/png")
        monkeypatch.setattr(importlib.import_module("whab_plugin_bank_logos.plugin"), "fetch_logo",
                            lambda key: calls.append(key) or image)
        app = create_app(store=store, settings=replace(settings(tmp_path, ()), ui="web"), plugins=PluginRegistry((sqlite_plugin, web_plugin)))
        async with running(app) as client:
            assert await app.state.live_plugins.activate(("bank-logos", "ynab")) == 2
            page = await client.get("/budgets/home/accounts/current-month/usd")
            asset = re.search(r"/budgets/home/imports/ynab/[0-9a-f]{32}/static/import.js", page.text)[0]
            assert (await client.get(asset)).status_code == 200
            assert "X-Import-Parser" in (await client.get(asset)).text
            # This parser was absent at app construction. ZIP recognition and
            # its 10 MiB body policy still apply without rebuilding middleware.
            upload = await client.post("/api/plugins/ynab/import/preview", content=b"x" * 40000,
                                       headers={"Content-Type": "application/zip", "X-Import-Currency": "USD"})
            assert upload.status_code == 400
            logo = (await client.post("/api/plugins/bank-logo-preview", json={"website": "bank.example.org"})).json()
            assert logo["available"] and (await client.get(logo["logoUrl"])).content == image.data
            assert len(calls) == 1
            assert await app.state.live_plugins.activate(()) == 3
            page = await client.get("/budgets/home/accounts/current-month/usd")
            assert 'data-file-import="ynab"' not in page.text
            assert (await client.get(logo["logoUrl"])).status_code == 404
            assert (await client.get(asset)).status_code == 200  # Retired HTML keeps matching immutable bytes.
            assert await app.state.live_plugins.activate(("bank-logos", "ynab")) == 4
            page = await client.get("/budgets/home/accounts/current-month/usd")
            new_asset = re.search(r"/budgets/home/imports/ynab/[0-9a-f]{32}/static/import.js", page.text)[0]
            assert new_asset != asset
            again = (await client.post("/api/plugins/bank-logo-preview", json={"website": "bank.example.org"})).json()
            assert again["available"] and len(calls) == 1  # Only this budget's saved artwork was reopened.

    asyncio.run(scenario())


def test_held_export_keeps_old_generation_and_new_requests_use_new_report(tmp_path, store, monkeypatch):
    async def scenario():
        entered, finish = Event(), Event()

        def held(_):
            entered.set()
            assert finish.wait(5)
            return ExportTable(("result",), (("old",),))

        first, second = report("first", "old", held), report("second", "new")
        install(monkeypatch, first, second)
        app = create_app(store=store, settings=settings(tmp_path), plugins=PluginRegistry((sqlite_plugin, first)))
        ledger, banks, tracked = app.state.ledger_service, app.state.banks, app.state.tracked_store
        initial_revision = ledger.revision
        async with running(app) as client:
            old = asyncio.create_task(client.get(export_url("first")))
            assert await asyncio.to_thread(entered.wait, 5)
            scheduled = await client.post("/api/plugins/management/transition", json={"plugins": ["second"]})
            assert scheduled.status_code == 202 and scheduled.json()["accepted"]
            await app.state.budget_runtime.wait_for_phase("draining")
            status = await client.get("/api/plugins/runtime")
            assert status.status_code == 200 and status.json()["runtime"]["generation"] == 1
            assert status.json()["runtime"]["admission"] == "gated"
            new = asyncio.create_task(client.get(export_url("second")))
            finish.set()
            assert "old" in (await old).text
            response = await new
            assert response.status_code == 200 and "new" in response.text
            await settled(app)
            assert (await client.get(export_url("first"))).status_code == 404
            assert read_selection(tmp_path) == ("second",)
            assert app.state.budget_runtime.status()["generation"] == 2
            assert app.state.ledger_service is ledger and ledger.revision == initial_revision
            assert app.state.banks is banks and app.state.tracked_store is tracked
            projection = (await client.get("/api/plugins/management")).json()
            assert projection["active"] == projection["configured"] == ["second"]
            assert not projection["restart_required"]
            assert projection["live"]["families"] == ["branding.bank", "branding.merchant", "categorization", "ingestion.file", "reporting.export", "reporting.insights"]
            assert projection["live"]["code_upgrades"] is True

    asyncio.run(scenario())


def test_live_change_preserves_public_revision_and_command_replay(tmp_path, store, monkeypatch):
    async def scenario():
        first, second = report("first", "old"), report("second", "new")
        install(monkeypatch, first, second)
        app = create_app(store=store, settings=settings(tmp_path), plugins=PluginRegistry((sqlite_plugin, first)))
        ledger = app.state.ledger_service
        initial = ledger.revision
        arguments = ("add_account", "personal", {"name": "Fictional cash", "type": "depository", "balance": 0})
        response = ledger.command(*arguments, request_id="replay", expected_revision=initial)
        revision, lock = ledger.revision, ledger._lock
        async with running(app):
            assert await app.state.live_plugins.activate(("second",)) == 2
            assert ledger.command(*arguments, request_id="replay", expected_revision=initial) == response
            assert ledger.revision == revision and ledger._lock is lock
            assert len(ledger.accounts("personal")) == 1

    asyncio.run(scenario())


def test_default_and_secondary_cookie_addressed_requests_have_independent_generations(tmp_path, monkeypatch):
    async def scenario():
        from whab.budgets.host import BudgetHost

        first, second = report("first", "old"), report("second", "new")
        install(monkeypatch, first, second)
        configuration = settings(tmp_path)
        budget_host = BudgetHost(configuration)
        app = create_app(settings=configuration, budget_host=budget_host)
        async with running(app) as client:
            created = await client.post("/api/budget/new", json={"name": "Fictional second budget"})
            assert created.status_code == 200
            identifier = created.json()["id"]
            host = app.state.plugin_manager.settings  # Configuration remains the original stable object.
            assert host.home == tmp_path
            # The cookie selects the child. Its coordinator is reached through
            # the host's shared application registry, without recreating services.
            child_id = identifier
            response = await client.post("/api/plugins/management/transition", json={"plugins": ["second"]})
            assert response.status_code == 202
            await settled(budget_host.applications[child_id][0])
            # The canonical export pins the selected child's generation. It
            # waits for any accepted handover before reading the new registry.
            new = await client.get(export_url("second", f"/budgets/{child_id}"))
            assert new.status_code == 200 and "new" in new.text
            assert (await client.get(export_url("first", "/budgets/home"))).status_code == 200
            assert app.state.budget_runtime.status()["generation"] == 1
            child_status = (await client.get(f"/budgets/{child_id}/api/plugins/runtime")).json()
            assert child_status["runtime"]["generation"] == 2
            assert child_status["runtime"]["budget_id"] == child_id
            assert (await client.get(export_url("second"))).status_code == 200
            client.cookies.clear()
            assert (await client.get(export_url("second"))).status_code == 404
            assert (await client.get(export_url("first"))).status_code == 200

    asyncio.run(scenario())


def test_direct_child_app_has_generation_leases_without_budget_dispatch(tmp_path, store, monkeypatch):
    async def scenario():
        observed = []
        app = None

        def hook(_):
            observed.append(app.state.budget_runtime.status()["active_leases"])
            return ExportTable(("result",), (("fictional",),))

        first = report("first", "old", hook)
        install(monkeypatch, first)
        app = create_app(store=store, settings=settings(tmp_path), plugins=PluginRegistry((sqlite_plugin, first)), _child=True)
        async with running(app) as client:
            assert (await client.get(export_url("first"))).status_code == 200
        assert observed == [1]

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["legacy", "binder", "settings", "nonreport", "state"])
def test_unsupported_live_selection_is_actionable_and_preserves_active_and_saved_state(kind, tmp_path, store, monkeypatch):
    async def scenario():
        from whab.plugins import PluginSettings

        first = report("first", "old")
        second = report("second", "new")
        if kind == "legacy":
            second = replace(second, contract=None)
        elif kind == "binder":
            second = replace(second, bind=lambda _: pytest.fail("Unsupported binders must not execute"))
        elif kind == "settings":
            second = replace(second, settings=PluginSettings(read=lambda: ()))
        elif kind == "nonreport":
            second = Plugin("second", "Bank", bank=lambda _: pytest.fail("Unsupported bank factory must not execute"), contract=PluginContract(lifecycle="live"))
        else:
            second = replace(second, contract=PluginContract(lifecycle="live", state_version=2))
        install(monkeypatch, first, second)
        app = create_app(store=store, settings=settings(tmp_path), plugins=PluginRegistry((sqlite_plugin, first)))
        async with running(app) as client:
            response = await client.post("/api/plugins/management/transition", json={"plugins": ["second"]})
            assert response.status_code == 409 and response.json()["code"] == "restart_required"
            assert "restart" in response.json()["error"].lower()
            assert read_selection(tmp_path) is None
            assert app.state.budget_runtime.status()["generation"] == 1
            assert app.state.live_plugins.status()["operations"] == []
            assert (await client.get(export_url("first"))).status_code == 200

    asyncio.run(scenario())


def test_invalid_resolution_fails_before_hooks_or_configuration_writes(tmp_path, store, monkeypatch):
    async def scenario():
        first = report("first", "old")
        second = replace(report("second", "new"), contract=PluginContract(
            lifecycle="live", requires=(Requirement("unavailable.capability"),),
        ))
        install(monkeypatch, first, second)
        app = create_app(store=store, settings=settings(tmp_path), plugins=PluginRegistry((sqlite_plugin, first)))
        async with running(app) as client:
            response = await client.post("/api/plugins/management/transition", json={"plugins": ["second"]})
            assert response.status_code == 409
            assert response.json()["code"] == "missing_requirement"
            assert read_selection(tmp_path) is None
            assert app.state.budget_runtime.status()["generation"] == 1
            assert (await client.get(export_url("first"))).status_code == 200

    asyncio.run(scenario())


def test_simultaneous_scheduled_changes_serialize_and_do_not_hold_the_request_lease(tmp_path, store, monkeypatch):
    async def scenario():
        first, second, third = report("first", "one"), report("second", "two"), report("third", "three")
        install(monkeypatch, first, second, third)
        app = create_app(store=store, settings=settings(tmp_path), plugins=PluginRegistry((sqlite_plugin, first)))
        async with running(app) as client:
            async with app.state.budget_runtime.request():
                one = app.state.live_plugins.schedule(("second",))
                two = app.state.live_plugins.schedule(("third",))
            await settled(app)
            status = (await client.get("/api/plugins/runtime")).json()
            assert {row["id"]: row["state"] for row in status["operations"]} == {one: "complete", two: "complete"}
            assert status["runtime"]["generation"] == 3
            assert read_selection(tmp_path) == ("third",)
            assert "three" in (await client.get(export_url("third"))).text

    asyncio.run(scenario())


@pytest.mark.parametrize("write_committed", [False, True])
def test_failed_persist_cannot_report_activation_and_keeps_uncertain_runtime_gated(write_committed, tmp_path, store, monkeypatch):
    async def scenario():
        first, second = report("first", "old"), report("second", "new")
        install(monkeypatch, first, second)
        app = create_app(store=store, settings=settings(tmp_path), plugins=PluginRegistry((sqlite_plugin, first)))
        persist = app.state.live_plugins.persist

        def uncertain(*args):
            if write_committed:
                persist(*args)
            raise OSError("synthetic private error")

        app.state.live_plugins.persist = uncertain
        async with running(app) as client:
            response = await client.post("/api/plugins/management/transition", json={"plugins": ["second"]})
            assert response.status_code == 202
            await settled(app)
            status = (await client.get("/api/plugins/runtime")).json()
            assert status["runtime"]["generation"] == 1
            assert status["runtime"]["admission"] == "gated"
            assert status["runtime"]["health"] == "recovery_required"
            assert status["operations"][-1]["state"] == "failed"
            assert status["operations"][-1]["error"]["code"] == "commit_uncertain"
            assert "synthetic private error" not in str(status)
            assert read_selection(tmp_path) == (("second",) if write_committed else None)
            authority = "candidate" if write_committed else "active"
            await app.state.budget_runtime.recover(authority, lambda *_: (
                read_selection(tmp_path) == (("second",) if write_committed else None)
            ))
            expected = "second" if write_committed else "first"
            assert (await client.get(export_url(expected))).status_code == 200

    asyncio.run(scenario())


def test_unavailable_and_disabled_plugin_payloads_survive_live_selection(tmp_path, monkeypatch):
    async def scenario():
        first, second = report("first", "old"), report("second", "new")
        install(monkeypatch, first, second)
        session = BudgetSession.create(tmp_path, "sandbox", "Fictional budget", ("first", "unavailable"))
        session.save_plugin_data("unavailable", settings={"opaque": "preserved"}, state={"schema": 99})
        session.save_plugin_data("first", state={"opaque": "keep when disabled"})
        unknown = session.plugin_record("unavailable").copy()
        app = create_app(settings=settings(tmp_path), budget_session=session)
        async with running(app) as client:
            response = await client.post("/api/plugins/management/transition", json={"plugins": ["unavailable", "second"]})
            assert response.status_code == 202
            await settled(app)
            assert session.plugin_record("unavailable") == unknown
            assert session.plugin_record("first")["enabled"] is False
            assert session.plugin_record("first")["state"] == {"opaque": "keep when disabled"}
            assert (await client.get(export_url("second"))).status_code == 200
        reopened = BudgetSession.open(tmp_path, "sandbox", session.path)
        assert reopened.selection == ("unavailable", "second")
        assert reopened.plugin_record("unavailable") == unknown
        assert reopened.plugin_record("first")["state"] == {"opaque": "keep when disabled"}

    asyncio.run(scenario())


def test_live_routes_keep_https_origin_payload_and_demo_guards(tmp_path, store, monkeypatch):
    async def scenario():
        first, second = report("first", "old"), report("second", "new")
        install(monkeypatch, first, second)
        app = create_app(store=store, settings=settings(tmp_path), plugins=PluginRegistry((sqlite_plugin, first)))
        path = "/api/plugins/management/transition"
        async with running(app) as client:
            assert (await client.post(path, json={"plugins": ["second"]}, headers={"origin": "https://foreign.invalid"})).status_code == 403
            assert (await client.post(path, content="{}", headers={"content-type": "text/plain"})).status_code == 415
            assert (await client.post(path, json={"plugins": "second"})).status_code == 400
            assert (await client.post("/budgets/demo" + path, json={"plugins": ["second"]})).status_code == 403
            assert (await client.get("/api/plugins/runtime", headers={"host": "foreign.invalid"})).status_code == 403
            assert (await client.get("http://127.0.0.1:5173/api/plugins/runtime")).status_code == 400
            assert app.state.live_plugins.status()["operations"] == []
            assert read_selection(tmp_path) is None

    asyncio.run(scenario())


def test_remote_queries_are_leased_for_default_and_secondary_budgets(tmp_path, monkeypatch):
    async def scenario():
        from whab_plugin_server.plugin import plugin as server

        from whab.budgets.host import BudgetDispatch
        from whab.services.ledger import LedgerService

        token = "synthetic-live-runtime-token-" + "x" * 32
        monkeypatch.setenv("SERVER_TOKEN", token)
        first, second = report("first", "old"), report("second", "new")
        install(monkeypatch, first, second, server)
        app = create_app(settings=settings(tmp_path, ("first", "server")))
        host = next(item.kwargs["host"] for item in app.user_middleware if item.cls is BudgetDispatch)
        original = LedgerService.query
        observed = []

        def query(ledger, *args, **kwargs):
            application, _ = host.applications[ledger.budget_id]
            observed.append((ledger.budget_id, application.state.budget_runtime.status()["active_leases"]))
            return original(ledger, *args, **kwargs)

        monkeypatch.setattr(LedgerService, "query", query)
        async with running(app) as client:
            created = await client.post("/api/budget/new", json={"name": "Fictional server budget"})
            identifier = created.json()["id"]
            headers = {"authorization": "Bearer " + token}
            for target in ("default", identifier):
                path = f"/remote/v1/budgets/{target}/queries/accounts?workspace=personal"
                assert (await client.get(path)).status_code == 401
                response = await client.get(path, headers=headers)
                assert response.status_code == 200 and response.json()["budgetId"] == target
            assert observed == [("default", 1), (identifier, 1)]

    asyncio.run(scenario())


def test_unchanged_bank_instances_and_branding_caches_are_retained(tmp_path, store, monkeypatch):
    async def scenario():
        from types import SimpleNamespace

        from whab.plugins import BankLogos, PluginSettings

        configured, closed = [], []

        async def noop(*_):
            return None

        async def close():
            closed.append(True)

        bank = SimpleNamespace(config={}, link_token=noop, exchange=noop, sync=noop,
                               disconnect=noop, webhook=noop, close=close)
        ingestion = Plugin("bank", "Fictional bank", bank=lambda _: bank)
        branding = Plugin("branding", "Fictional artwork", bank_logos=BankLogos(
            resolve=lambda _: None, fetch=lambda _: None,
            configure_cache=lambda path: configured.append(path),
        ), settings=PluginSettings(read=lambda: (Insight("Cache", "retained"),)))
        first, second = report("first", "old"), report("second", "new")
        install(monkeypatch, first, second, ingestion, branding)
        app = create_app(store=store, settings=settings(tmp_path, ("first", "bank", "branding")),
                         plugins=PluginRegistry((sqlite_plugin, first, ingestion, branding)))
        async with running(app) as client:
            active_bank = app.state.banks["bank"]
            before = (await client.get("/api/plugins/branding/settings")).json()
            assert await app.state.live_plugins.activate(("second", "bank", "branding")) == 2
            assert app.state.banks["bank"] is active_bank is bank
            assert not closed and len(configured) == 1
            assert (await client.get("/api/plugins/branding/settings")).json() == before
        assert closed == [True]

    asyncio.run(scenario())


def test_insights_pages_use_current_report_without_recreating_web_resources(tmp_path, store, monkeypatch):
    async def scenario():
        from whab_plugin_web.plugin import plugin as web

        first = report("first", "Fictional first live report")
        second = report("second", "Fictional second live report")
        install(monkeypatch, first, second, web)
        app = create_app(store=store, settings=replace(settings(tmp_path), ui="web"),
                         plugins=PluginRegistry((sqlite_plugin, first, web)))
        path = "/budgets/home/insights/2026-10/usd"
        async with running(app) as client:
            before = await client.get(path)
            assert before.status_code == 200 and "Fictional first live report" in before.text
            assert await app.state.live_plugins.activate(("second",)) == 2
            after = await client.get(path)
            assert after.status_code == 200 and "Fictional second live report" in after.text
            assert "Fictional first live report" not in after.text
            assert app.state.ui_plugin == "web"
            assert (await client.get("/static/client.js")).status_code == 200

    asyncio.run(scenario())


def test_injected_backend_does_not_acquire_an_invented_storage_registration(tmp_path, store, monkeypatch):
    async def scenario():
        first, second = report("first", "old"), report("second", "new")
        install(monkeypatch, first, second)
        app = create_app(store=store, settings=settings(tmp_path), plugins=PluginRegistry((first,)))
        async with running(app) as client:
            assert await app.state.live_plugins.activate(("second",)) == 2
            assert [row["id"] for row in (await client.get("/api/plugins")).json()["plugins"]] == ["second"]
            assert app.state.storage_plugin is None
            assert (await client.get(export_url("second"))).status_code == 200

    asyncio.run(scenario())
