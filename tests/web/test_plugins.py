"""Plugin execution through the real HTTP and Jinja boundaries."""

import csv
import io
from dataclasses import asdict
from pathlib import Path

from fastapi.testclient import TestClient

from whab.app import create_app
from whab.config import Settings
from whab.interfaces.web.plugin import plugin as web_plugin
from whab.plugins import ExportTable, Insight, Plugin, PluginRegistry
from whab.storage.sqlite import Store


def make_client(store, registry):
    settings = Settings(
        Path(":memory:"),
        5173,
        "sandbox",
        "",
        "",
        "",
        False,
        "",
        "",
    )
    return TestClient(
        create_app(store=store, settings=settings, plugins=PluginRegistry((*registry._plugins, web_plugin))),
        base_url="http://127.0.0.1:5173",
    )


def test_plugin_gets_only_selected_workspace_month_without_connection_secrets():
    store = Store(":memory:", month="2026-10")
    account = store.add_account(
        "personal", {"name": "Fictional cash", "type": "depository", "balance": 10000}
    )
    for day in ("2026-09-20", "2026-10-20"):
        store.add_transaction(
            "personal",
            {
                "accountId": account,
                "date": day,
                "merchant": "Fictional grocer",
                "amount": 100,
                "categoryId": "groceries",
            },
        )
    store.save_item("fake-item", "fictional-encrypted-token", "Fake institution")
    seen = []

    def insights(snapshot):
        seen.append(snapshot)
        return (Insight("<b>Heading</b>", "<script>value</script>"),)

    registry = PluginRegistry((Plugin("test", "Test report", insights=insights),))
    try:
        with make_client(store, registry) as client:
            # Hooks do not run on unrelated pages.
            assert client.get("/?page=budget&workspace=personal").status_code == 200
            assert not seen
            response = client.get("/?page=insights&workspace=personal&month=2026-10")
            assert response.status_code == 200
            assert "&lt;script&gt;value&lt;/script&gt;" in response.text
            assert "<script>value</script>" not in response.text
            snapshot = seen[-1]
            assert snapshot.workspace == "personal"
            assert [a.id for a in snapshot.accounts] == [account]
            assert [t.date for t in snapshot.transactions] == ["2026-10-20"]
            assert "token" not in str(asdict(snapshot))
            assert "Fake institution" not in str(asdict(snapshot))
            response = client.get("/?page=insights&workspace=demo&month=2026-10")
            assert response.status_code == 200
            assert all(a.id != account for a in seen[-1].accounts)
    finally:
        store.close()


def test_plugin_csv_is_scoped_escaped_and_guarded_like_core_routes():
    store = Store(":memory:", month="2026-10")

    def export(snapshot):
        assert snapshot.workspace == "demo" and snapshot.month == "2026-10"
        return ExportTable(("=Heading", "amount_cents"), (("\t=SUM(1,2)", -1234),))

    registry = PluginRegistry((Plugin("test", "Test export", export=export),))
    try:
        with make_client(store, registry) as client:
            path = "/api/plugins/test/export?workspace=demo&month=2026-10"
            result = client.get(path)
            assert result.status_code == 200
            rows = list(csv.reader(io.StringIO(result.text)))
            assert rows == [["'=Heading", "amount_cents"], ["'\t=SUM(1,2)", "-1234"]]
            assert result.headers["cache-control"] == "no-store"
            assert "whab-test-demo-2026-10.csv" in result.headers["content-disposition"]
            assert client.get(path, headers={"host": "evil.example"}).status_code == 403
            assert client.get(path, headers={"origin": "https://evil.example"}).status_code == 403
            assert (
                client.get(path.replace("workspace=demo", "workspace=unknown")).status_code == 400
            )
            assert client.get(path.replace("2026-10", "2026-13")).status_code == 400
            assert client.get(path.replace("/test/", "/disabled/")).status_code == 404
            assert client.get("/api/plugins").json()["plugins"][0]["has_export"] is True
    finally:
        store.close()


def test_plugin_failures_do_not_expose_exception_or_break_core_pages():
    store = Store(":memory:", month="2026-10")

    def fail(_snapshot):
        raise RuntimeError("FICTIONAL_SECRET_DO_NOT_EXPOSE")

    registry = PluginRegistry((Plugin("broken", "Broken report", insights=fail, export=fail),))
    try:
        with make_client(store, registry) as client:
            page = client.get("/?page=insights&workspace=demo&month=2026-10")
            assert page.status_code == 200
            assert "could not be generated" in page.text
            assert "FICTIONAL_SECRET" not in page.text
            export = client.get("/api/plugins/broken/export?workspace=demo&month=2026-10")
            assert export.status_code == 400
            assert "FICTIONAL_SECRET" not in export.text
            assert client.get("/?page=budget").status_code == 200
    finally:
        store.close()
