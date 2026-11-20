import json
from dataclasses import asdict
from importlib.resources import files

import pytest
from whab_plugin_monthly_spending import insights, plugin

from whab.plugins import (
    BudgetSnapshot,
    Capability,
    CategorySnapshot,
    SpendingCategory,
    SpendingMonth,
)


def category(identifier, name, group):
    return CategorySnapshot(identifier, name, group, 0, 0, 0)


def month(value, amounts=(), uncategorized=0):
    return SpendingMonth(value, uncategorized, tuple(SpendingCategory(*row) for row in amounts))


def snapshot(categories, spending, *, by="group", currency="USD"):
    return BudgetSnapshot(
        workspace="demo", month="2026-03", accounts=(), categories=categories, transactions=(),
        currency=currency, spending=spending, spending_by=by,
    )


def chart(categories, spending, **options):
    result, = insights(snapshot(categories, spending, **options))
    return result.chart


def test_lifecycle_probe_has_no_report():
    assert plugin.contract.provides == (Capability("reporting.insights", 2),)
    assert plugin.insights(BudgetSnapshot("demo", "2026-03", (), (), ())) == ()


def test_category_mode_keeps_ids_qualified_names_and_refunds_across_empty_months():
    left = category("x" * 200, "Trips", "Family")
    right = category("another", "Trips", "Work")
    data = (
        month("2026-01", ((left.id, 1425), (right.id, 4530)), 200),
        month("2026-02"),
        month("2026-03", ((left.id, -526),)),
    )
    report = chart((left, right), data, by="category", currency="KWD")
    assert report.currency == "KWD"
    assert report.months == ("2026-01", "2026-02", "2026-03")
    assert {point.label: point.values for point in report.series} == {
        "Family / Trips": (1425, 0, -526),
        "Work / Trips": (4530, 0, 0),
        "Uncategorized": (200, 0, 0),
    }
    assert len({point.id for point in report.series}) == 3
    narrower = chart((left, right), data[2:], by="category", currency="KWD")
    assert narrower.series[0].id == report.series[0].id


def test_group_mode_merges_children_and_drops_net_zero_series():
    food = category("a", "Groceries", "Household")
    repairs = category("b", "Repair", "Household")
    misc = category("c", "Net zero", "Unused")
    spending = (month("2026-03", ((food.id, 850), (repairs.id, -400), (misc.id, 0))),)
    report = chart((food, repairs, misc), spending, currency="JPY")
    series, = report.series
    assert report.currency == "JPY"
    assert (series.id, series.label, series.values) == (food.parent_id, "Household", (450,))


def test_empty_range_remains_a_chart_with_each_month():
    report = chart((), (month("2026-02"), month("2026-03")))
    assert report.months == ("2026-02", "2026-03")
    assert report.series == ()


@pytest.mark.parametrize(("mode", "uncategorized", "other_label"), [
    ("category", 500, "Other categories (3)"),
    ("group", 0, "Other groups (2)"),
])
def test_crowded_chart_preserves_monthly_totals_and_high_activity(mode, uncategorized, other_label):
    categories = tuple(category(f"c-{index}", "Spending", f"Group {index:03d}") for index in range(101))
    january = [(row.id, index + 1) for index, row in enumerate(categories)]
    march = [(categories[0].id, -2500)]
    report = chart(categories, (month("2026-01", january, uncategorized), month("2026-02"), month("2026-03", march)), by=mode)
    assert len(report.series) == 100
    assert len({series.id for series in report.series}) == 100
    assert next(series for series in report.series if series.id == "system:other").label == other_label
    assert tuple(sum(values) for values in zip(*(series.values for series in report.series), strict=True)) == (
        sum(value for _, value in january) + uncategorized, 0, -2500,
    )
    assert any(series.id != "system:other" and series.values[-1] == -2500 for series in report.series)


def test_packaged_example_matches_the_real_report():
    document = json.loads((files("whab_plugin_monthly_spending") / "operations.json").read_text())
    example, = document["operations"][0]["examples"]
    raw = example["input"]["snapshot"].copy()
    raw["accounts"] = tuple(raw["accounts"])
    raw["transactions"] = tuple(raw["transactions"])
    raw["categories"] = tuple(CategorySnapshot(**row) for row in raw["categories"])
    raw["spending"] = tuple(
        SpendingMonth(row["month"], row["uncategorized_cents"], tuple(SpendingCategory(**item) for item in row["categories"]))
        for row in raw["spending"]
    )
    assert json.loads(json.dumps([asdict(item) for item in insights(BudgetSnapshot(**raw))])) == example["output"]
