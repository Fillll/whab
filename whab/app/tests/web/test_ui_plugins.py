"""UI replacement and headless mode exercise the real host without default visuals."""

import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
from whab.config import Settings
from whab.plugins import Plugin, PluginError, PluginRegistry, UserInterface


def settings(home, ui):
    return Settings(home, 5173, "sandbox", "", "", "", False, "", "", (), ui=ui)


def test_headless_keeps_ledger_api_without_importing_any_visual_plugin(tmp_path):
    script = """
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from whab.app import create_app
from whab.config import Settings
settings = Settings(Path(sys.argv[1]), 5173, "sandbox", "", "", "", False, "", "", (), ui="")
with TestClient(create_app(settings=settings), base_url="https://127.0.0.1:5173") as client:
    assert client.get("/").status_code == 404
    assert client.get("/static/app.js").status_code == 404
    assert client.get("/api/workspace?workspace=demo").status_code == 200
    assert client.get("/api/export?workspace=demo").status_code == 200
    plugins = client.get("/api/plugins").json()["plugins"]
    assert [(p["id"], p["kinds"]) for p in plugins] == [("sqlite", ["storage"])]
assert not any(name.startswith(("whab_plugin_web", "whab_plugin_plaid")) for name in sys.modules)
"""
    subprocess.run(
        [sys.executable, "-I", "-c", script, str(tmp_path)],
        check=True, capture_output=True, text=True,
    )


def test_another_ui_supplies_its_views_templates_assets_and_keeps_host_guards(tmp_path, store):
    templates, static = tmp_path / "templates", tmp_path / "static"
    templates.mkdir()
    static.mkdir()
    (templates / "base.html").write_text("Alternative UI {{ label }} {{ month }} {{ version }}")
    (static / "custom.css").write_text("body { color: navy; }")
    seen = []

    def build(data, status, items, **options):
        seen.append((data, options))
        return {"label": "<script>fictional</script>"}

    ui = UserInterface(templates, static, build)
    registry = PluginRegistry((Plugin("alternate", "Alternate UI", ui=ui),))
    with TestClient(
        create_app(store=store, settings=settings(tmp_path, "alternate"), plugins=registry),
        base_url="https://127.0.0.1:5173",
    ) as client:
        for page in ("budget", "transactions", "insights", "accounts"):
            result = client.get(f"/budgets/demo/{page}/2026-10/usd")
            assert result.status_code == 200
            assert "Alternative UI &lt;script&gt;fictional&lt;/script&gt; 2026-10" in result.text
            assert seen[-1][1]["workspace"] == "demo"
            assert "periods" not in seen[-1][0]
        assert client.get("/static/custom.css").status_code == 200
        assert client.get("/static/app.js").status_code == 404
        assert client.get("/api/workspace?workspace=demo").status_code == 200
        assert client.get("/", headers={"host": "foreign.example"}).status_code == 403
        assert client.get("/").headers["cache-control"] == "no-store"


@pytest.mark.parametrize("month,expected", [
    ("2026-12", ["2026-12", "2027-01", "2027-02"]),
    ("2200-11", ["2200-11", "2200-12"]),
    ("2200-12", ["2200-12"]),
])
def test_multi_month_ui_receives_currency_scoped_core_periods_only_for_budget(tmp_path, store, month, expected):
    templates, static = tmp_path / "templates", tmp_path / "static"
    templates.mkdir()
    static.mkdir()
    (templates / "base.html").write_text("Alternate budget {{ month }} {{ data.currency }}")
    store.add_account("personal", {
        "name": "Euro cash", "type": "depository", "balance": 50000, "currency": "EUR",
    })
    euro_category = store.add_category("personal", {
        "name": "Euro food", "group": "Euro living", "target": 0, "currency": "EUR",
    })
    seen = []

    def build(data, status, items, **options):
        seen.append(data)
        return {}

    ui = UserInterface(templates, static, build, budget_months=3)
    registry = PluginRegistry((Plugin("alternate", "Alternate UI", ui=ui),))
    with TestClient(
        create_app(store=store, settings=settings(tmp_path, "alternate"), plugins=registry),
        base_url="https://127.0.0.1:5173",
    ) as client:
        result = client.get(f"/budgets/home/budget/{month}/eur")
        assert result.status_code == 200
        assert f"Alternate budget {month} EUR" in result.text
        data = seen[-1]
        assert [period["month"] for period in data["periods"]] == expected
        service = client.app.state.ledger_service
        for period in data["periods"]:
            assert "periods" not in period
            assert period["currency"] == "EUR"
            assert {row["currency"] for row in period["accounts"]} == {"EUR"}
            assert {row["id"] for row in period["categories"]} == {euro_category}
            assert period["summary"] == service.workspace("personal", period["month"])["summariesByCurrency"]["EUR"]
        assert {key: value for key, value in data.items() if key != "periods"} == data["periods"][0]
        for page in ("transactions", "insights", "accounts"):
            assert client.get(f"/budgets/home/{page}/{month}/eur").status_code == 200
            assert "periods" not in seen[-1]


def test_missing_ui_fails_instead_of_falling_back(tmp_path, store):
    with pytest.raises(PluginError, match="not enabled"):
        create_app(store=store, settings=settings(tmp_path, "absent"), plugins=PluginRegistry())


@pytest.mark.parametrize("failure", ["builder", "template-helper", "template-syntax"])
def test_ui_failure_is_sanitized_and_api_stays_usable(tmp_path, store, failure):
    templates, static = tmp_path / "templates", tmp_path / "static"
    templates.mkdir()
    static.mkdir()
    (templates / "base.html").write_text(
        "{% if FICTIONAL_PRIVATE_UI_ERROR" if failure == "template-syntax" else "{{ fail() }}"
    )

    def fail(*args, **kwargs):
        raise RuntimeError("FICTIONAL_PRIVATE_UI_ERROR")

    builder = fail if failure == "builder" else lambda *args, **kwargs: {"fail": fail}
    registry = PluginRegistry((Plugin("broken", "Broken UI", ui=UserInterface(templates, static, builder)),))
    with TestClient(
        create_app(store=store, settings=settings(tmp_path, "broken"), plugins=registry),
        base_url="https://127.0.0.1:5173",
    ) as client:
        response = client.get("/")
        assert response.status_code == 400
        assert "could not render" in response.text
        assert "FICTIONAL_PRIVATE" not in response.text
        assert client.get("/api/workspace?workspace=demo").status_code == 200
