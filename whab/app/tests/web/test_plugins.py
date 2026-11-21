"""Plugin execution through the real HTTP and Jinja boundaries."""

import csv
import io
from dataclasses import asdict

from fastapi.testclient import TestClient
from whab_plugin_sqlite import Store

from tests.support.ui import make_ui
from whab.app import create_app
from whab.config import Settings
from whab.domain.categories import category_group_id
from whab.plugins import (
    Capability,
    ExportTable,
    Insight,
    Plugin,
    PluginContract,
    PluginRegistry,
    SpendingCategory,
    SpendingMonth,
)


def make_client(store, registry, home):
    settings = Settings(
        home,
        5173,
        "sandbox",
        "",
        "",
        "",
        False,
        "",
        "",
        ui="fixture-ui",
    )
    return TestClient(
        create_app(store=store, settings=settings, plugins=PluginRegistry((*registry._plugins, make_ui(home)))),
        base_url="https://127.0.0.1:5173",
    )


def test_plugin_gets_only_selected_workspace_month_without_connection_secrets(tmp_path):
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
        with make_client(store, registry, tmp_path) as client:
            # Hooks do not run on unrelated pages.
            assert client.get("/budgets/home/budget/current-month/usd").status_code == 200
            assert not seen
            response = client.get("/budgets/home/insights/2026-10/usd")
            assert response.status_code == 200
            snapshot = seen[-1]
            assert snapshot.workspace == "personal"
            assert [a.id for a in snapshot.accounts] == [account]
            assert [t.date for t in snapshot.transactions] == ["2026-10-20"]
            assert "token" not in str(asdict(snapshot))
            assert "Fake institution" not in str(asdict(snapshot))
            response = client.get("/budgets/demo/insights/2026-10/usd")
            assert response.status_code == 200
            assert all(a.id != account for a in seen[-1].accounts)
    finally:
        store.close()


def test_visual_report_filters_scope_the_aggregate_without_changing_legacy_inputs(store, tmp_path):
    categories = (store.add_category("personal", {"name": "Fuel", "group": "Travel"}),
                  store.add_category("personal", {"name": "Fuel", "group": "Home"}))
    accounts = tuple(store.add_account("personal", {"name": name, "type": "depository", "balance": 0})
                     for name in ("Fictional checking", "Fictional savings"))
    for account, category, month, amount in (
        (accounts[0], categories[0], "2026-09", 100),
        (accounts[0], categories[1], "2026-10", 200),
        (accounts[1], categories[0], "2026-10", 300),
    ):
        store.add_transaction("personal", {"accountId": account, "categoryId": category,
                                           "date": month + "-20", "merchant": "Fictional", "amount": amount})
    captured = {"visual": [], "legacy": []}

    def observe(kind):
        def report(snapshot):
            captured[kind].append(snapshot)
            return ()
        return report

    reports = PluginRegistry((
        Plugin("visual", "Visual", insights=observe("visual"),
               contract=PluginContract(provides=(Capability("reporting.insights", 2),))),
        Plugin("legacy", "Legacy", insights=observe("legacy")),
    ))
    selection = {"insight_from": "2026-09", "insight_to": "2026-10", "insight_account": accounts[0],
                 "insight_category_group": category_group_id("Travel"), "insight_category": f"category:{categories[0]}"}
    with make_client(store, reports, tmp_path) as client:
        path = "/budgets/home/insights/2026-10/usd"
        assert client.get(path, params=selection).status_code == 200
        visual = captured["visual"][-1]
        legacy = captured["legacy"][-1]
        assert visual.spending == (SpendingMonth("2026-09", 0, (SpendingCategory(categories[0], 100),)),
                                   SpendingMonth("2026-10", 0, ()))
        assert not legacy.spending and visual.transactions == legacy.transactions
        assert [row.date for row in legacy.transactions] == ["2026-10-20", "2026-10-20"]
        assert client.get(path, params={**selection, "insight_account": "absent"}).status_code == 200
        assert not captured["visual"][-1].spending
        assert client.get(path.replace("/home/", "/demo/"), params=selection).status_code == 200
        assert captured["visual"][-1].workspace == "demo" and not captured["visual"][-1].spending


def test_plugin_csv_is_scoped_escaped_and_guarded_like_core_routes(tmp_path):
    store = Store(":memory:", month="2026-10")

    def export(snapshot):
        assert snapshot.workspace == "demo" and snapshot.month == "2026-10"
        return ExportTable(("=Heading", "amount_cents"), (("\t=SUM(1,2)", -1234),))

    registry = PluginRegistry((Plugin("test", "Test export", export=export),))
    try:
        with make_client(store, registry, tmp_path) as client:
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
            descriptions = {row["id"]: row for row in client.get("/api/plugins").json()["plugins"]}
            assert descriptions["test"]["has_export"] is True
    finally:
        store.close()


def test_plugin_failures_do_not_expose_exception_or_break_core_pages(tmp_path):
    store = Store(":memory:", month="2026-10")

    def fail(_snapshot):
        raise RuntimeError("FICTIONAL_SECRET_DO_NOT_EXPOSE")

    registry = PluginRegistry((Plugin("broken", "Broken report", insights=fail, export=fail),))
    try:
        with make_client(store, registry, tmp_path) as client:
            page = client.get("/budgets/demo/insights/2026-10/usd")
            assert page.status_code == 200
            assert "could not be generated" in page.text
            assert "FICTIONAL_SECRET" not in page.text
            export = client.get("/api/plugins/broken/export?workspace=demo&month=2026-10")
            assert export.status_code == 400
            assert "FICTIONAL_SECRET" not in export.text
            assert client.get("/budgets/demo/budget/current-month/usd").status_code == 200
    finally:
        store.close()
