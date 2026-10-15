"""Run the actual example package code without requiring it to be globally installed."""

from importlib import util
from pathlib import Path

import pytest

from whab.plugins import AccountSnapshot, BudgetSnapshot, TransactionSnapshot


def example_module():
    source = (
        Path(__file__).resolve().parents[1]
        / "src/whab_example_plugin/__init__.py"
    )
    spec = util.spec_from_file_location("whab_example_plugin_fixture", source)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_example_totals_and_export_keep_integer_cents_and_exclude_pending_transfers():
    from whab.plugins import CategorySnapshot

    snapshot = BudgetSnapshot(
        "demo",
        "2026-10",
        (AccountSnapshot("wallet", "Fictional wallet", "depository", "checking", 10000),),
        (CategorySnapshot("food", "Food", "Everyday", 10000, 1500, 8500),),
        (
            TransactionSnapshot("purchase", "2026-10-01", "Shop", 2000, "wallet", "food"),
            TransactionSnapshot("refund", "2026-10-02", "Shop", -500, "wallet", "food"),
            TransactionSnapshot("income", "2026-10-03", "Employer", -10000, "wallet", None),
            TransactionSnapshot(
                "pending", "2026-10-04", "Shop", 5000, "wallet", None, pending=True
            ),
            TransactionSnapshot(
                "transfer", "2026-10-05", "Transfer", 9000, "wallet", None, is_transfer=True
            ),
        ),
    )
    module = example_module()
    assert module.plugin.id == "example"
    assert module.plugin.api_version == 1
    totals = module.insights(snapshot)
    assert [(row.title, row.value) for row in totals] == [
        ("Net spending", "$15.00"),
        ("Income", "$100.00"),
    ]
    table = module.export(snapshot)
    assert len(table.rows) == 5
    assert table.rows[0][4] == 2000
    assert type(table.rows[0][4]) is int
    assert table.rows[-1][-2] is True
    assert table.columns[-1] == "currency"
    assert {row[-1] for row in table.rows} == {"USD"}


def test_example_empty_month_and_large_amounts_have_no_float_rounding():
    module = example_module()
    snapshot = BudgetSnapshot("personal", "2026-10", (), (), ())
    assert [row.value for row in module.insights(snapshot)] == ["$0.00", "$0.00"]
    assert module.export(snapshot).rows == ()
    snapshot = BudgetSnapshot(
        "personal", "2026-10",
        (AccountSnapshot("wallet", "Fictional wallet", "depository", "checking", 0),), (),
        tuple(TransactionSnapshot(f"large-{n}", "2026-10-01", "Large expense", 999999999999,
                                  "wallet", None) for n in range(2)),
    )
    assert module.insights(snapshot)[0].value == "$19,999,999,999.98"


@pytest.mark.parametrize(
    ("currency", "net", "income"),
    [("EUR", "EUR 12.34", "EUR 56.78"), ("JPY", "JPY 1,234", "JPY 5,678"),
     ("KWD", "KWD 1.234", "KWD 5.678")],
)
def test_example_uses_snapshot_currency_and_exports_unconverted_minor_units(currency, net, income):
    snapshot = BudgetSnapshot(
        "personal", "2026-10",
        (AccountSnapshot("wallet", "Fictional wallet", "depository", "checking", 10000),), (),
        (TransactionSnapshot("expense", "2026-10-01", "Shop", 1234, "wallet", None),
         TransactionSnapshot("income", "2026-10-02", "Employer", -5678, "wallet", None)),
        currency=currency,
    )
    module = example_module()
    assert [row.value for row in module.insights(snapshot)] == [net, income]
    table = module.export(snapshot)
    assert table.columns == (
        "date", "payee", "account", "category", "amount_cents", "pending", "is_transfer", "currency",
    )
    assert [(row[4], row[-1]) for row in table.rows] == [(1234, currency), (-5678, currency)]


def test_host_snapshot_and_registry_keep_other_workspace_currencies_out_of_the_report(store):
    from whab.domain.budget import select_currency
    from whab.plugins import PluginRegistry
    from whab.web.plugin_snapshot import build_snapshot

    euro = store.add_account("personal", {
        "name": "Euro wallet", "type": "depository", "balance": 10000, "currency": "EUR",
    })
    dollar = store.add_account("personal", {
        "name": "Dollar wallet", "type": "depository", "balance": 10000, "currency": "USD",
    })
    for account, amount, merchant in ((euro, 1234, "Euro shop"), (dollar, 9999, "Dollar shop")):
        store.add_transaction("personal", {
            "accountId": account, "amount": amount, "merchant": merchant, "date": "2026-10-01",
        })
    snapshot = build_snapshot("personal", select_currency(store.workspace("personal", "2026-10"), "EUR"))
    assert snapshot.currency == "EUR"
    assert [account.id for account in snapshot.accounts] == [euro]
    assert [row.payee for row in snapshot.transactions] == ["Euro shop"]
    registry = PluginRegistry((example_module().plugin,))
    result = registry.insights(snapshot)
    assert result[0].error is None
    assert [row.value for row in result[0].insights] == ["EUR 12.34", "EUR 0.00"]
    table = registry.export("example", snapshot)
    assert len(table.rows) == 1
    assert table.rows[0][1] == "Euro shop"
    assert table.rows[0][-1] == "EUR"
