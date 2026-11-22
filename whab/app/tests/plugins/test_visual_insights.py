"""Synthetic historical projections and the versioned visual report boundary."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from starlette.datastructures import QueryParams
from whab_plugin_json import Store as JsonStore
from whab_plugin_sqlite import Store as SqliteStore

from whab.domain.categories import category_group_id
from whab.domain.errors import StoreError
from whab.plugins import (
    AccountSnapshot,
    BudgetSnapshot,
    Capability,
    CategorySnapshot,
    ChartSeries,
    Insight,
    Plugin,
    PluginContract,
    PluginError,
    PluginRegistry,
    SpendingCategory,
    SpendingContributor,
    SpendingMonth,
    StackedMonthlyChart,
)
from whab.plugins.insight_wire import insight_data, read_insight
from whab.plugins.report_worker import ReportWorker, _snapshot, _snapshot_data
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


def test_contributors_cross_worker_boundaries_only_with_version_three(snapshot):
    candidate = SpendingContributor("food", "cash", "Chez Émile", 225)
    snapshot = replace(
        snapshot,
        accounts=(AccountSnapshot("cash", "Wallet", "depository", "checking", 100),),
        spending=(replace(snapshot.spending[0], contributors=(candidate,)), snapshot.spending[1]),
    )
    assert _snapshot(_snapshot_data(snapshot, include_spending=True, include_contributors=True)) == snapshot
    legacy = _snapshot(_snapshot_data(snapshot, include_spending=True))
    assert legacy.spending[0].contributors == ()

    series = ChartSeries("food", "Épicerie", (225, 0), ((candidate,), ()))
    chart = StackedMonthlyChart("USD", ("2026-09", "2026-10"), (series,))
    insight = Insight("Monthly spending", "", chart=chart)
    payload = insight_data(insight)
    assert public_value(insight) == payload
    assert read_insight(payload, include_chart=True, include_contributors=True) == insight
    with pytest.raises(WireError):
        read_insight(payload, include_chart=True)

    def check_legacy(data):
        assert data == legacy
        return (Insight("Legacy chart", "", chart=replace(chart, series=(replace(series, contributors=()),))),)

    def check_current(data):
        assert data == snapshot
        return (insight,)

    for version, hook, has_contributors in ((2, check_legacy, False), (3, check_current, True)):
        contract = PluginContract(provides=(Capability("reporting.insights", version),), lifecycle="live")
        registry = PluginRegistry((Plugin("report", "Report", insights=hook, contract=contract),))
        result = registry.insights(snapshot)[0]
        assert not result.error
        assert bool(result.insights[0].chart.series[0].contributors) is has_contributors

    with pytest.raises(ValueError, match="at most five"):
        replace(snapshot.spending[0], contributors=(candidate,) * 6)
    with pytest.raises(ValueError, match="nonzero chart"):
        replace(series, contributors=((), (candidate,)))
    with pytest.raises(ValueError, match="must align"):
        replace(series, contributors=((candidate,),))
    with pytest.raises(ValueError, match="reference accounts"):
        replace(snapshot, accounts=())


def test_oversized_contributors_can_be_narrowed_without_stopping_the_report_worker(snapshot, monkeypatch):
    snapshot = replace(
        snapshot,
        accounts=(AccountSnapshot("cash", "Wallet", "depository", "checking", 100),),
        spending=(replace(snapshot.spending[0], contributors=(
            SpendingContributor("food", "cash", "大" * 200, 225),
        )), snapshot.spending[1]),
    )
    base = replace(snapshot, spending=(), spending_by="group")
    query = QueryParams("insight_from=2026-09&insight_to=2026-10&insight_group=category")
    service = SimpleNamespace(insight_spending=lambda *args, **kwargs: snapshot.spending)
    with monkeypatch.context() as limits:
        limits.setattr("whab.web.insight_options.MAX_METADATA_BYTES", 1000)
        data, options = visual_insight_snapshot(base, query, service, include_contributors=True)
    assert data is base
    assert "Choose fewer months or narrow the filters." in options["error"]
    assert visual_insight_snapshot(base, query, service, include_contributors=True)[0] == snapshot

    worker = ReportWorker("monthly-spending")
    try:
        with monkeypatch.context() as limits:
            limits.setattr("whab.plugins.wire.MAX_METADATA_BYTES", 1000)
            with pytest.raises(PluginError, match="Choose fewer months or narrow the filters.") as raised:
                worker.insights(snapshot)
            assert raised.value.code == "insight_range_too_large"
        assert worker.healthy
        result, = worker.insights(snapshot)
        assert result.chart.series[0].contributors == ((snapshot.spending[0].contributors[0],), ())
    finally:
        worker.close()


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

    def bounded(workspace, start, end, currency, **filters):
        assert (workspace, start, end, currency) == ("personal", "2026-09", "2026-10", "USD")
        assert filters == {"account_ids": [], "group_ids": [], "category_ids": ()}
        return snapshot.spending

    selected = QueryParams("insight_from=2026-09&insight_to=2026-10&insight_group=category")
    service.insight_spending = bounded
    data, options = visual_insight_snapshot(base, selected, service)
    assert data == snapshot
    assert (options["start_month"], options["end_month"], options["group_by"]) == ("2026-09", "2026-10", "category")
    assert (options["account_ids"], options["category_group_ids"], options["category_ids"]) == ([], [], [])

    def too_many(*args, **filters):
        raise InsightRangeTooLarge("Choose fewer months.")

    service.insight_spending = too_many
    data, options = visual_insight_snapshot(base, selected, service)
    assert data is base and options["error"] == "Choose fewer months."


def test_filter_controls_offer_only_scoped_and_spend_eligible_choices(snapshot):
    base = replace(snapshot, accounts=(
        AccountSnapshot("cash", "Wallet", "depository", "checking", 100),
        AccountSnapshot("archive", "古い口座", "depository", "checking", 100, closed=True),
        AccountSnapshot("tracking", "Tracking", "depository", "checking", 100, on_budget=False),
    ), categories=snapshot.categories + (
        CategorySnapshot("uncategorized", "Épicerie", "Travel", 0, 0, 0),
        CategorySnapshot("salary", "Salary", "Income", 0, 0, 0, kind="income"),
    ))
    group = category_group_id("Living")
    selected = {"insight_account": "archive", "insight_category_group": group, "insight_category": "category:food"}
    calls = []

    def project(*args, **filters):
        calls.append((args, filters))
        return snapshot.spending

    service = SimpleNamespace(insight_spending=project)
    _, options = visual_insight_snapshot(base, QueryParams(selected), service)
    assert calls == [(('personal', '2026-05', '2026-10', 'USD'),
                      {"account_ids": ["archive"], "group_ids": [group], "category_ids": ("food",)})]
    assert options["account_ids"] == ["archive"] and options["category_group_ids"] == [group]
    assert options["category_ids"] == ["category:food"]
    assert (options["account_id"], options["category_group_id"], options["category_id"]) == ("archive", group, "category:food")
    assert options["accounts"] == [{"id": "cash", "label": "Wallet"}, {"id": "archive", "label": "古い口座 (closed)"}]
    assert options["categories"] == [
        {"id": "category:food", "label": "Living / Épicerie", "group_id": group},
        {"id": "category:uncategorized", "label": "Travel / Épicerie", "group_id": category_group_id("Travel")},
        {"id": "uncategorized", "label": "Uncategorized", "group_id": ""},
    ]
    assert options["category_groups"] == [
        {"id": group, "label": "Living"}, {"id": category_group_id("Travel"), "label": "Travel"},
    ]
    for value, resolved in (("uncategorized", None), ("category:uncategorized", "uncategorized")):
        visual_insight_snapshot(base, QueryParams({"insight_category": value}), service)
        assert calls[-1][1]["category_ids"] == (resolved,)

    second_group = category_group_id("Travel")
    repeated = QueryParams([
        ("insight_account", "archive"), ("insight_account", ""), ("insight_account", "cash"), ("insight_account", "archive"),
        ("insight_category_group", group), ("insight_category_group", second_group), ("insight_category_group", group),
        ("insight_category", "category:food"), ("insight_category", "uncategorized"),
        ("insight_category", "category:uncategorized"), ("insight_category", "uncategorized"),
    ])
    _, options = visual_insight_snapshot(base, repeated, service)
    assert options["account_ids"] == ["archive", "cash"]
    assert options["category_group_ids"] == [group, second_group]
    assert options["category_ids"] == ["category:food", "uncategorized", "category:uncategorized"]
    assert calls[-1][1] == {"account_ids": ["archive", "cash"], "group_ids": [group, second_group],
                            "category_ids": ("food", None, "uncategorized")}
    _, options = visual_insight_snapshot(base, QueryParams("insight_account=&insight_category_group=&insight_category="), service)
    assert options["account_ids"] == options["category_group_ids"] == options["category_ids"] == []
    assert calls[-1][1] == {"account_ids": [], "group_ids": [], "category_ids": ()}

    for query in ("insight_account=archive&insight_account=tracking", "insight_account=missing", "insight_category=category:salary",
                  f"insight_category_group={group}&insight_category_group={category_group_id('Income')}",
                  "insight_category=category:food&insight_category=food"):
        data, options = visual_insight_snapshot(base, QueryParams(query), service)
        assert data is base and options["error"]
    assert len(calls) == 5


@pytest.fixture(params=("sqlite", "json"))
def insight_ledger(tmp_path, request):
    storage = (SqliteStore(":memory:", month="2026-04") if request.param == "sqlite"
               else JsonStore(tmp_path / "fictional.json", month="2026-04"))
    try:
        yield LedgerService(storage)
    finally:
        storage.close()


def test_spending_filters_intersect_before_aggregating_on_each_backend(insight_ledger):
    service = insight_ledger

    def account(name, **fields):
        return service.add_account("personal", {"name": name, "type": "depository", "balance": 0, **fields})

    def category(name, group, **fields):
        return service.add_category("personal", {"name": name, "group": group, "target": 0, **fields})

    def add(identifier, amount, category_id, date="2026-04-15"):
        service.add_transaction("personal", {
            "accountId": identifier, "amount": amount, "categoryId": category_id, "date": date, "merchant": "Fictional",
        })

    archived = account("Archived spending")
    active = account("Active spending")
    reserve = account("Reserve spending")
    tracking = account("Tracking", onBudget=False)
    euro = account("Euros", currency="EUR")
    food = category("友人", "Food")
    drinks = category("Drinks", "Food")
    travel = category("友人", "Travel")
    electric = category("Electricity", "Utilities")
    euro_food = category("Euros", "Foreign", currency="EUR")
    income = category("Pay", "Income", kind="income")
    add(archived, 500, food, "2026-04-01")
    add(archived, -100, food, "2026-04-30")
    add(archived, 200, drinks)
    add(archived, 100, travel)
    add(archived, 75, None)
    add(archived, -50, None)
    add(archived, -1000, income)
    add(active, 1000, food)
    add(active, 33, electric)
    add(reserve, 55, food)
    add(tracking, 250, food)
    add(euro, 999, euro_food)
    service.update_account("personal", archived, {"closed": True})
    revision = service.revision

    def result(workspace="personal", currency="USD", **filters):
        first, second = service.insight_spending(workspace, "2026-04", "2026-05", currency, **filters)
        assert first.month == "2026-04" and second == SpendingMonth("2026-05", 0, ())
        return first.uncategorized_cents, {row.category_id: row.amount_cents for row in first.categories}

    food_group = category_group_id("Food")
    travel_group = category_group_id("Travel")
    assert result() == (75, {food: 1455, drinks: 200, travel: 100, electric: 33})
    assert result(account_ids=[archived, active, active]) == (75, {food: 1400, drinks: 200, travel: 100, electric: 33})
    assert result(group_ids=(food_group, travel_group)) == (0, {food: 1455, drinks: 200, travel: 100})
    assert result(category_ids=(food, None)) == (75, {food: 1455})
    assert result(account_ids=(archived, active), group_ids=(food_group, travel_group),
                  category_ids=(food, travel, electric, None)) == (0, {food: 1400, travel: 100})
    assert result(account_ids=(archived,), group_ids=(food_group,), category_ids=(travel, None)) == (0, {})
    assert result(account_ids=(archived,), category_ids=(None,)) == (75, {})
    assert result(currency="EUR", account_ids=(euro,), category_ids=(euro_food,)) == (0, {euro_food: 999})
    for filters in ({"workspace": "demo", "account_ids": (archived,)}, {"account_ids": (archived, tracking)},
                    {"account_ids": (archived, euro)}, {"category_ids": (food, euro_food)},
                    {"category_ids": (food, income)}, {"group_ids": (food_group, category_group_id("Foreign"))}):
        with pytest.raises(StoreError, match="unavailable for this budget and currency"):
            result(**filters)
    assert service.revision == revision


def test_contributor_ranking_uses_the_filtered_spending_rows(store):
    service = LedgerService(store)
    cash = service.add_account("personal", {"name": "Wallet", "type": "depository", "balance": 0})
    off_budget = service.add_account("personal", {
        "name": "Tracking", "type": "depository", "balance": 0, "onBudget": False,
    })

    for payee, amount, account in (
        ("Fictional largest", 700, cash),
        ("Fictional refund", -600, cash),
        ("Fictional fourth", 300, cash),
        ("Fictional third", 350, cash),
        ("Fictional fifth", 200, cash),
        ("Fictional sixth", 150, cash),
        ("Excluded account", 900, off_budget),
    ):
        service.add_transaction("personal", {
            "accountId": account, "date": "2025-01-10", "merchant": payee, "amount": amount,
            "categoryId": "groceries",
        })

    service.add_transaction("personal", {
        "accountId": cash, "date": "2025-01-12", "merchant": "Ignored category", "amount": 800,
        "categoryId": None,
    })
    (month,) = service.insight_spending(
        "personal", "2025-01", "2025-01", "USD", category_ids=("groceries",), include_contributors=True,
    )
    assert month.uncategorized_cents == 0
    assert month.categories == (SpendingCategory("groceries", 1100),)
    assert [(row.payee, row.amount_cents) for row in month.contributors] == [
        ("Fictional largest", 700), ("Fictional refund", -600), ("Fictional third", 350),
        ("Fictional fourth", 300), ("Fictional fifth", 200),
    ]
    assert all(row.account_id == cash for row in month.contributors)


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


def test_report_rejects_repeated_transactions_across_pages(monkeypatch):
    service = LedgerService(SimpleNamespace(transaction_register_page=lambda *_: {}))

    def register(*_args, page=1, **_kwargs):
        return {
            "transactionPage": {"page": page, "pages": 2, "total": 1001},
            "accounts": [], "categories": [],
            "transactions": [{"id": "repeated", "accountId": "not-on-budget"}],
        }

    monkeypatch.setattr(service, "_transaction_page", register)
    with pytest.raises(StoreError, match="Transaction data changed"):
        service.insight_spending("personal", "2026-01", "2026-01", "USD")
