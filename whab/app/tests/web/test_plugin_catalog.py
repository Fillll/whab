"""Static curated inspection remains usable without running installed hooks."""

from importlib import metadata

from fastapi.testclient import TestClient

from tests.web.test_budget_sessions import settings
from whab.app import create_app
from whab.plugins.catalog import CatalogError, catalog_snapshot


def test_catalog_http_uses_packaged_data_without_discovery_and_retains_https_local_guards(tmp_path, monkeypatch):
    app = create_app(settings=settings(tmp_path))
    expected = catalog_snapshot()
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        def forbidden(*_, **__):
            raise AssertionError("Static catalog inspection must not discover or load plugins")

        monkeypatch.setattr(metadata, "entry_points", forbidden)
        response = client.get("/api/plugins/catalog")
        assert response.status_code == 200 and response.json() == expected
        assert response.headers["cache-control"] == "no-store"
        assert client.get("http://127.0.0.1:5173/api/plugins/catalog").status_code == 400
        assert client.get("/api/plugins/catalog", headers={"Host": "outside.invalid"}).status_code == 403
        assert client.get("/api/plugins/catalog", headers={"Origin": "https://outside.invalid"}).status_code == 403


def test_invalid_catalog_returns_fixed_diagnostic_without_discovery(tmp_path, monkeypatch):
    app = create_app(settings=settings(tmp_path))
    def failed():
        raise CatalogError()

    monkeypatch.setattr("whab.web.routes.plugins.catalog_snapshot", failed)
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        response = client.get("/api/plugins/catalog")
        assert response.status_code == 503
        assert response.json() == {"detail": str(CatalogError())}


def test_catalog_remains_readable_during_an_uncertain_budget_handover(tmp_path):
    import asyncio

    import pytest

    from tests.web.test_live_plugins import running
    from whab.budgets.runtime import RuntimeCandidate, RuntimeTransitionError

    async def scenario():
        app = create_app(settings=settings(tmp_path))
        async with running(app) as client:
            runtime = app.state.budget_runtime
            def prepare(context):
                return RuntimeCandidate(context.active.services, context.active.resources)

            def commit(*_):
                raise OSError("fictional uncertain decision")

            with pytest.raises(RuntimeTransitionError):
                await runtime.transition(prepare, commit=commit, plugin_id="fictional")
            assert runtime.status()["admission"] == "gated"
            response = await client.get("/api/plugins/catalog")
            assert response.status_code == 200 and response.json()["plugins"]
            assert (await client.get("/api/plugins/catalog", headers={"Origin": "https://outside.invalid"})).status_code == 403
    asyncio.run(scenario())
