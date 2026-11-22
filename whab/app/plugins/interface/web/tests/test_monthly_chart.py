import json
import re
from html import unescape
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from whab_plugin_web import views
from whab_plugin_web.monthly_chart import series_color, stacked_monthly_plot

from whab.plugins import (
    Capability,
    ChartSeries,
    Insight,
    Plugin,
    PluginContract,
    StackedMonthlyChart,
)
from whab.services.ledger import LedgerService


def test_stacked_monthly_geometry_keeps_zero_months_and_separates_refund_stack():
    months = ("2026-09", "2026-10", "2026-11")
    plot = stacked_monthly_plot(months, ((34000, 0, 12), (12500, 0, -8500), (-10000, 0, -4240)), "USD")
    first, empty, last = plot["columns"]
    baseline = next(tick for tick in plot["ticks"] if tick["amount"] == 0)["y"]
    expense, another_expense, refund = first["segments"]
    assert expense["y"] + expense["height"] == pytest.approx(baseline)
    assert another_expense["y"] + another_expense["height"] == pytest.approx(expense["y"])
    assert refund["y"] == baseline
    assert first["net"] == 36500 and first["net_y"] < baseline
    assert empty["segments"] == [] and empty["net_y"] == baseline
    assert last["net"] == -12728 and last["net_y"] > baseline
    assert tuple(column["month"] for column in plot["columns"]) == months
    assert [row["series"] for row in last["segments"]] == [0, 1, 2]


def test_report_uses_chart_currency_and_stable_colors():
    chart = stacked_monthly_plot(("2026-09",), ((123456,),), "JPY")
    assert chart["columns"][0]["net"] == 123456
    assert [tick["amount"] for tick in chart["ticks"]] == [0, 50000, 100000, 150000]
    assert series_color("group:household") == series_color("group:household")
    assert series_color("group:household") != series_color("category:household")


def test_visual_reports_have_isolated_accessible_ids_escaped_labels_and_privacy():
    # The visual worker transports typed records as plain JSON objects.
    environment = Environment(
        loader=FileSystemLoader(Path(views.__file__).parent / "templates"),
        autoescape=select_autoescape(("html",)),
    )
    template = environment.from_string(
        "{% for insight in reports %}"
        "{% set chart_id = 'monthly-insight-' ~ loop.index %}"
        "{% set show_chart_heading = true %}"
        "{% include 'monthly-insight.html' %}{% endfor %}"
    )
    result = template.render(
        reports=[
            {"title": 'Fictional <travel>', "detail": "", "chart": {
                "currency": "JPY", "months": ["2026-10", "2026-11"],
                "series": [{"id": "one", "label": "<script>one</script>", "values": [500, -200]}],
            }},
            {"title": "Second report", "detail": "", "chart": {
                "currency": "JPY", "months": ["2026-10"], "series": [
                    {"id": "two", "label": 'Test & Travel', "values": [200]},
                ],
            }},
        ],
        insight_options={"group_by": "category"},
        month_label=views.month_label,
        stacked_monthly_plot=stacked_monthly_plot,
        series_color=series_color,
        currency_money=views.display_money,
    )
    assert '<script>' not in result and '&lt;script&gt;one&lt;/script&gt;' in result
    assert 'Fictional &lt;travel&gt;' in result and 'Test &amp; Travel' in result
    assert 'aria-controls="monthly-insight-1"' in result and 'aria-controls="monthly-insight-2"' in result
    assert 'aria-label="Second report · JPY" aria-describedby="monthly-insight-2-description"' in result
    assert "<title" not in result
    assert result.count('data-private-content="Chart and amounts hidden"') == 2
    assert '<td>-JPY 200</td>' in result and 'Category</th>' in result


def test_monthly_contributors_are_escaped_scoped_and_omitted_from_gallery():
    environment = Environment(
        loader=FileSystemLoader(Path(views.__file__).parent / "templates"),
        autoescape=select_autoescape(("html",)),
    )
    months = ("2026-10", "2026-11")
    chart = {"currency": "USD", "months": months, "series": [{
        "id": "house", "label": "House", "values": (12000, -4900), "contributors": [
            [], [{"account_id": "closed-cash", "payee": '<img src=x onerror="evil">', "amount_cents": -4900,
                  "category_id": "fixtures"}],
        ],
    }]}
    context = {
        "insight": {"title": "Fictional spending", "detail": "", "chart": chart},
        "chart_id": "test-chart", "insight_options": {"group_by": "group"},
        "month_label": views.month_label, "series_color": series_color,
        "stacked_monthly_plot": stacked_monthly_plot, "currency_money": views.display_money,
        "accounts_by_id": {"closed-cash": {"name": "Old & café", "closed": True}},
    }
    detail = environment.get_template("monthly-insight.html").render(**context)
    assert detail.count('data-monthly-contributor-trigger=') == 1
    assert 'data-contributor-month="2026-11"' in detail
    assert 'data-monthly-contributor-preview popover="manual" role="tooltip" hidden' in detail
    assert 'aria-label="November 2026 · House · -$49"' in detail
    assert '<img src=' not in detail
    serialized = re.search(r'data-monthly-contributor-trigger="([^\"]+)"', detail)[1]
    assert json.loads(unescape(serialized)) == [["Old & café", '<img src=x onerror="evil">', "-$49", "fixtures"]]
    assert 'Payee / Category</th>' in detail and '<title' not in detail
    assert 'data-private-content="Chart and amounts hidden"' in detail
    assert "data-monthly-contributor-categories" not in detail

    choices = [
        {"id": "category:fixtures", "label": "House / Fixtures & repairs"},
        {"id": "category:furniture", "label": "House / <Furniture>"},
        {"id": "category:garden", "label": "House / Garden"},
    ]
    template = environment.get_template("monthly-insight.html")
    for selection, label in (
        ({"category_ids": ["category:furniture", "category:fixtures"]}, "House / Fixtures & repairs, House / <Furniture>"),
        ({"category_id": "category:furniture"}, "House / <Furniture>"),
    ):
        filtered = template.render(**{
            **context, "insight_options": {"group_by": "group", "categories": choices, **selection},
        })
        category_scope = re.search(r"<p data-monthly-contributor-categories>(.*?)</p>", filtered)[1]
        assert unescape(category_scope) == f"Category filter · {label}"
        assert "<Furniture>" not in category_scope
        assert "House / Garden" not in category_scope

    preview = environment.get_template("monthly-insight-plot.html").render(
        **context, chart=chart, chart_preview=True, plot=stacked_monthly_plot(months, [(12000, -4900)], "USD"),
    )
    assert 'data-insight-preview-chart' in preview and 'data-monthly-contributor-trigger' not in preview
    assert 'closed-cash' not in preview and 'evil' not in preview and '<title' not in preview


def test_range_and_group_filters_preserve_useful_fallbacks(store, tmp_path, client_for):
    def fictional_chart(snapshot):
        if not snapshot.spending:
            return ()
        months = tuple(row.month for row in snapshot.spending)
        label = "Groceries" if snapshot.spending_by == "category" else "Household"
        chart = StackedMonthlyChart(snapshot.currency, months, (ChartSeries("household", label, (100,) * len(months)),))
        return (Insight("Monthly report", "", chart=chart),)

    visual = Plugin("fictional-chart", "Fictional chart", insights=fictional_chart,
                    contract=PluginContract(provides=(Capability("reporting.insights", 2, "additive"),)))
    legacy = Plugin("fictional-text", "Fictional text", insights=lambda _: (Insight("Fictional metric", "Expected"),))
    with client_for(store, tmp_path, visual, legacy) as client:
        route = "/budgets/demo/insights/2026-11/usd"
        gallery = client.get(route)
        assert gallery.status_code == 200 and 'data-insight-gallery' in gallery.text
        assert gallery.text.count('data-insight-tile') == 5
        assert 'data-insight-preview-chart' in gallery.text and 'Fictional metric' in gallery.text
        assert 'data-monthly-insight-filters' not in gallery.text
        assert 'aria-label="Insights month"' in gallery.text
        overview = client.get(route, params={"insight_report": "overview"})
        assert overview.status_code == 200 and 'aria-label="Insights month"' in overview.text
        report = client.get(route, params={"insight_report": "plugin:fictional-chart"})
        assert report.status_code == 200 and 'data-insight-back' in report.text
        assert 'name="insight_report" value="plugin:fictional-chart"' in report.text
        assert 'value="group" checked' in report.text
        assert 'Household' in report.text
        assert 'Fictional metric' not in report.text and 'Spending over time' not in report.text
        assert report.text.count('class="monthly-chart-point"') == 6
        selection = {"insight_from": "2026-10", "insight_to": "2026-11", "insight_group": "category"}
        fragment = client.get(route, params=selection, headers={"X-WHAB-Page-Fragment": "1"})
        assert fragment.status_code == 200 and fragment.headers["X-WHAB-Page-Fragment"] == "1"
        assert 'value="category" checked' in fragment.text
        assert 'Groceries' in fragment.text and fragment.text.count('class="monthly-chart-point"') == 2
        assert fragment.text.count('aria-controls="monthly-insight-1"') == 1
        invalid = client.get(route, params={**selection, "insight_from": "2026-12"})
        assert invalid.status_code == 200 and 'data-monthly-insight-error' in invalid.text
        assert 'data-monthly-plot' not in invalid.text and 'data-insight-back' in invalid.text
        text = client.get(route, params={"insight_report": "plugin:fictional-text"})
        assert text.status_code == 200 and 'Fictional metric' in text.text and 'Expected' in text.text
        assert 'data-monthly-plot' not in text.text and 'data-insight-gallery' not in text.text
        assert 'aria-label="Insights month"' in text.text
        for ranged in (report, fragment, invalid):
            assert 'aria-label="Insights month"' not in ranged.text
            assert 'name="insight_from"' in ranged.text and 'name="insight_to"' in ranged.text
            assert '<span class="subtle-label">USD</span>' in ranged.text
        unavailable = client.get(route, params={"insight_report": "plugin:disabled"})
        assert unavailable.status_code == 200 and 'data-insight-back' in unavailable.text
        assert 'data-monthly-plot' not in unavailable.text
        ambiguous = client.get(route, params=[("insight_report", "overview"), ("insight_report", "categories")])
        assert ambiguous.status_code == 200 and 'data-insight-back' in ambiguous.text
        assert '"canonicalUrl": null' in ambiguous.text


def test_scoped_picker_choices_render_and_filter_the_chart(store, tmp_path, client_for):
    ledger = LedgerService(store)
    account = ledger.add_account("personal", {"name": "Café & cash", "type": "depository", "balance": 0})
    other = ledger.add_account("personal", {"name": "Other wallet", "type": "depository", "balance": 0})
    extra = ledger.add_account("personal", {"name": "Extra wallet", "type": "depository", "balance": 0})
    foreign = ledger.add_account("personal", {
        "name": "Euro wallet", "type": "depository", "currency": "EUR", "balance": 0,
    })
    category = ledger.add_category("personal", {"name": "Café", "group": "Food & drink", "target": 0})
    transit = ledger.add_category("personal", {"name": "Train", "group": "Travel", "target": 0})
    dessert = ledger.add_category("personal", {"name": "Dessert", "group": "Food & drink", "target": 0})
    for account_id, amount, category_id in (
        (account, 133700, category), (other, 88800, category), (other, 42100, transit),
        (account, 25200, None), (extra, 99900, category), (account, 20000, dessert),
    ):
        ledger.add_transaction("personal", {
            "accountId": account_id, "date": "2026-11-01", "amount": amount, "categoryId": category_id,
            "merchant": "Fictional cafe",
        })

    def fictional_chart(snapshot):
        values = tuple(month.uncategorized_cents + sum(item.amount_cents for item in month.categories)
                       for month in snapshot.spending)
        if not values:
            return ()
        series = (ChartSeries("testing", "Test spending", values),) if any(values) else ()
        chart = StackedMonthlyChart(snapshot.currency, tuple(month.month for month in snapshot.spending), series)
        return (Insight("Fictional report", "", chart=chart),)

    visual = Plugin("fictional-chart", "Fictional chart", insights=fictional_chart,
                    contract=PluginContract(provides=(Capability("reporting.insights", 2, "additive"),)))
    route = "/budgets/home/insights/2026-11/usd"
    with client_for(store, tmp_path, visual) as client:
        initial = client.get(route, params={"insight_report": "plugin:fictional-chart"})
        assert initial.status_code == 200
        assert 'data-monthly-filter-max-fields="128"' in initial.text
        assert 'Choose up to 124 options across Accounts, Category groups, and Categories.' in initial.text
        form = re.search(r'<form [^>]*data-monthly-insight-filters[^>]*>(.*?)</form>', initial.text, re.S)[1]
        assert form.count("data-monthly-filter-all>") == 3
        assert form.count("data-monthly-filter-select-all>") == 3
        assert form.count("data-monthly-filter-deselect-all>") == 3
        assert not re.search(r'type="checkbox"[^>]* checked', form)
        assert '<td>$4,097</td>' in initial.text
        assert re.search(rf'name="insight_account" value="{account}">\s*<span>Café &amp; cash</span>', form)
        assert f'name="insight_account" value="{foreign}"' not in form
        assert re.search(rf'value="category:{category}">\s*<span>Food &amp; drink / Café</span>', form)
        assert "All accounts" in form and "All groups" in form and "All categories" in form
        group = re.search(r'name="insight_category_group" value="([^"]+)">\s*<span>Food &amp; drink</span>', form)[1]
        travel = re.search(r'name="insight_category_group" value="([^"]+)">\s*<span>Travel</span>', form)[1]
        selected = {
            "insight_account": [account, other], "insight_category_group": [group, travel],
            "insight_category": [f"category:{category}", f"category:{transit}"],
        }
        params = {
            "insight_from": "2026-11", "insight_to": "2026-11", "insight_group": "category",
            **selected,
        }
        response = client.get(route, params=params, headers={"X-WHAB-Page-Fragment": "1"})
        assert response.status_code == 200 and response.headers["X-WHAB-Page-Fragment"] == "1"
        for name, choices in selected.items():
            assert all(f'name="{name}" value="{choice}" checked>' in response.text for choice in choices)
        assert response.text.count('data-monthly-filter-label>2 selected') == 3
        assert '<td>$2,646</td>' in response.text
        empty = client.get(route, params={**params, "insight_category": "uncategorized"})
        assert empty.status_code == 200 and 'No spending matches these filters.' in empty.text
        assert 'name="insight_category" value="uncategorized" checked>' in empty.text
        for name in ("insight_account", "insight_category_group", "insight_category"):
            restored = client.get(route, params={name + "_none": "1"})
            assert restored.status_code == 200 and 'No spending matches these filters.' in restored.text
            assert 'data-insight-report="plugin:fictional-chart"' in restored.text
            assert restored.text.count('data-monthly-filter-label>None selected') == 1
            assert f'<noscript><label><input type="checkbox" name="{name}_none" value="1" checked> Match none</label></noscript>' in restored.text
            assert not re.search(rf'type="checkbox" name="{name}"[^>]* checked', restored.text)
