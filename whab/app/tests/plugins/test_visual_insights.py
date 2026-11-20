"""Synthetic historical projections and the versioned visual report boundary."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from starlette.datastructures import QueryParams

from whab.plugins import (
    BudgetSnapshot,
    Capability,
    CategorySnapshot,
    ChartSeries,
    Insight,
    Plugin,
    PluginContract,
    PluginRegistry,
    SpendingCategory,
    SpendingMonth,
    StackedMonthlyChart,
)
from whab.plugins.insight_wire import insight_data, read_insight
from whab.plugins.report_worker import _snapshot, _snapshot_data
from whab.plugins.ui_render import public_value
from whab.plugins.wire import WireError
from whab.services.ledger import InsightRangeTooLarge, LedgerService
from whab.web.insight_options import visual_insight_snapshot


@pytest.fixture
def snapshot():
    return BudgetSnapshot(
        "personal", "2026-10", (), (CategorySnapshot("food", "Épicerie", "Living", 0, 0, 0),), (),
        spending=(SpendingMonth("2026-09", 15, (SpendingCategory("food", 225),)), SpendingMonth("2026-10", 0, ())),
        spending_by="category",
    )


def test_visual_records_cross_both_worker_boundaries_and_legacy_stays_text_only(snapshot):
    assert _snapshot(_snapshot_data(snapshot, include_spending=True)) == snapshot
    legacy = _snapshot_data(snapshot)
    assert legacy.keys() == {"workspace", "month", "currency", "accounts", "categories", "transactions"}
    assert _snapshot(legacy) == replace(snapshot, spending=(), spending_by="group")
    chart = StackedMonthlyChart("USD", ("2026-09", "2026-10"), (ChartSeries("food", "Épicerie", (225, 0)),))
    output = Insight("Spending", "Monthly", chart=chart)
    assert read_insight(insight_data(output), include_chart=True) == output
    assert public_value(output) == insight_data(output)
    with pytest.raises(WireError):
        read_insight(insight_data(output))
    assert read_insight({"title": "Legacy", "value": "Accepted"}) == Insight("Legacy", "Accepted")


def test_registry_only_shares_history_with_opted_in_reports(snapshot):
    def plain(data):
        assert not data.spending
        return (Insight("Selected month", data.month),)

    def visual(data):
        assert data == snapshot
        return (Insight("Monthly", "Spending", chart=StackedMonthlyChart(
            data.currency, tuple(row.month for row in data.spending), (ChartSeries("food", "Épicerie", (225, 0)),),
        )),)

    contract = PluginContract(provides=(Capability("reporting.insights", 2),), lifecycle="live")
    registry = PluginRegistry((Plugin("plain", "Plain", insights=plain),
                               Plugin("visual", "Visual", insights=visual, contract=contract)))
    results = {result.plugin_id: result for result in registry.insights(snapshot)}
    assert results["plain"].insights == (Insight("Selected month", "2026-10"),)
    assert results["visual"].insights[0].chart.series[0].values == (225, 0)
    for chart in (
        StackedMonthlyChart("EUR", ("2026-09", "2026-10"), ()),
        StackedMonthlyChart("USD", ("2026-10",), ()),
    ):
        invalid = replace(registry.descriptors()[1], insights=lambda _: (Insight("Bad chart", "", chart=chart),))
        assert PluginRegistry((invalid,)).insights(snapshot)[0].error


def test_month_range_controls_validate_and_keep_legacy_snapshot_available(snapshot):
    service = SimpleNamespace(insight_spending=lambda *args: pytest.fail(f"Unexpected projection: {args}"))
    base = replace(snapshot, spending=(), spending_by="group")
    for query in ("insight_from=2026-11", "insight_from=2020-01", "insight_to=Oct", "insight_group=merchant",
                  "insight_to=2026-10&insight_to=2026-09"):
        data, options = visual_insight_snapshot(base, QueryParams(query), service)
        assert data is base and options["error"]

    def bounded(workspace, start, end, currency):
        assert (workspace, start, end, currency) == ("personal", "2026-09", "2026-10", "USD")
        return snapshot.spending

    selected = QueryParams("insight_from=2026-09&insight_to=2026-10&insight_group=category")
    service.insight_spending = bounded
    data, options = visual_insight_snapshot(base, selected, service)
    assert data == snapshot
    assert options == {"start_month": "2026-09", "end_month": "2026-10", "group_by": "category"}

    def too_many(*args):
        raise InsightRangeTooLarge("Choose fewer months.")

    service.insight_spending = too_many
    data, options = visual_insight_snapshot(base, selected, service)
    assert data is base and options["error"] == "Choose fewer months."


def test_atomic_read_projects_on_budget_categorized_refunds_and_empty_months(store):
    service = LedgerService(store)
    cash = service.add_account("personal", {"name": "Cash", "type": "depository", "balance": 0})
    property_account = service.add_account("personal", {"name": "Tracking", "type": "depository", "onBudget": False, "balance": 0})
    euro = service.add_account("personal", {"name": "Euro", "type": "depository", "currency": "EUR", "balance": 0})
    income = service.add_category("personal", {"name": "Work", "group": "Income", "kind": "income", "target": 0})

    def add(date, amount, category=None, account=cash):
        service.add_transaction("personal", {
            "accountId": account, "date": date, "merchant": "Fictional", "amount": amount,
            "categoryId": category,
        })

    add("2024-12-31", 2000, "groceries")
    add("2025-01-01", 200, "groceries")
    add("2025-01-31", -300, "groceries")
    add("2025-01-05", 75)
    add("2025-01-08", -250)
    add("2025-01-12", -400, income)
    add("2025-01-15", 123, account=property_account)
    add("2025-01-18", 234, account=euro)
    add("2025-03-01", 900, "groceries")
    before = service.revision
    expected = (SpendingMonth("2025-01", 75, (SpendingCategory("groceries", -100),)), SpendingMonth("2025-02", 0, ()))
    assert service.insight_spending("personal", "2025-01", "2025-02", "USD") == expected
    assert service.insight_spending("personal", "2025-01", "2025-01", "EUR") == (SpendingMonth("2025-01", 234, ()),)
    assert all(month.uncategorized_cents == 0 and not month.categories
               for month in service.insight_spending("demo", "2025-01", "2025-02", "USD"))
    assert service.revision == before

    fallback = LedgerService(SimpleNamespace(accounts=store.accounts, categories=store.categories, transactions=store.transactions))
    assert fallback.insight_spending("personal", "2025-01", "2025-02", "USD") == expected


def test_native_range_limit_rejects_instead_of_returning_a_partial_plot(monkeypatch):
    service = LedgerService(SimpleNamespace(transaction_register_page=lambda *_: {}))
    monkeypatch.setattr(service, "_transaction_page", lambda *_args, **_kwargs: {"transactionPage": {"total": 30_001}})
    with pytest.raises(InsightRangeTooLarge, match="30,000 transactions"):
        service.insight_spending("personal", "2026-01", "2026-01", "USD")
