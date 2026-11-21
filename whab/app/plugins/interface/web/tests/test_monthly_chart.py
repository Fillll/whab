import re
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
    assert 'aria-labelledby="monthly-insight-2-title monthly-insight-2-description"' in result
    assert result.count('data-private-content="Chart and amounts hidden"') == 2
    assert '<td>-JPY 200</td>' in result and 'Category</th>' in result


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
        report = client.get(route)
        assert report.status_code == 200
        assert 'value="group" checked' in report.text
        assert 'Household' in report.text
        assert 'Fictional metric' in report.text
        assert 'Spending over time' not in report.text
        assert report.text.count('class="monthly-chart-point"') == 6
        selection = {"insight_from": "2026-10", "insight_to": "2026-11", "insight_group": "category"}
        fragment = client.get(route, params=selection, headers={"X-WHAB-Page-Fragment": "1"})
        assert fragment.status_code == 200 and fragment.headers["X-WHAB-Page-Fragment"] == "1"
        assert 'value="category" checked' in fragment.text
        assert 'Groceries' in fragment.text and fragment.text.count('class="monthly-chart-point"') == 2
        assert fragment.text.count('aria-controls="monthly-insight-1"') == 1
        invalid = client.get(route, params={**selection, "insight_from": "2026-12"})
        assert invalid.status_code == 200 and 'data-monthly-insight-error' in invalid.text
        assert 'data-monthly-plot' not in invalid.text and 'Spending over time' in invalid.text


def test_scoped_picker_choices_render_and_filter_the_chart(store, tmp_path, client_for):
    ledger = LedgerService(store)
    account = ledger.add_account("personal", {"name": "Café & cash", "type": "depository", "balance": 0})
    other = ledger.add_account("personal", {"name": "Other wallet", "type": "depository", "balance": 0})
    foreign = ledger.add_account("personal", {
        "name": "Euro wallet", "type": "depository", "currency": "EUR", "balance": 0,
    })
    category = ledger.add_category("personal", {"name": "Café", "group": "Food & drink", "target": 0})
    for account_id, amount, category_id in ((account, 133700, category), (other, 88800, category), (account, 25200, None)):
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
        initial = client.get(route)
        assert initial.status_code == 200
        form = re.search(r'<form [^>]*data-monthly-insight-filters[^>]*>(.*?)</form>', initial.text, re.S)[1]
        assert f'<option value="{account}">Café &amp; cash</option>' in form
        assert f'<option value="{foreign}">' not in form
        assert f'<option value="category:{category}">Food &amp; drink / Café</option>' in form
        group = re.search(r'<option value="([^"]+)">Food &amp; drink</option>', form)[1]
        params = {
            "insight_from": "2026-11", "insight_to": "2026-11", "insight_group": "category",
            "insight_account": account, "insight_category_group": group, "insight_category": f"category:{category}",
        }
        response = client.get(route, params=params, headers={"X-WHAB-Page-Fragment": "1"})
        assert response.status_code == 200 and response.headers["X-WHAB-Page-Fragment"] == "1"
        assert f'<option value="{account}" selected>' in response.text and f'<option value="{group}" selected>' in response.text
        assert f'<option value="category:{category}" selected>' in response.text
        assert '<td>$1,337</td>' in response.text and '<td>$2,477</td>' not in response.text
        empty = client.get(route, params={**params, "insight_category": "uncategorized"})
        assert empty.status_code == 200 and 'No spending matches these filters.' in empty.text
        assert '<option value="uncategorized" selected>' in empty.text
