"""Run the actual example package code without requiring it to be globally installed."""

from importlib import util
from pathlib import Path

from whab.plugins import AccountSnapshot, BudgetSnapshot, TransactionSnapshot


def example_module():
    source = (
        Path(__file__).resolve().parents[2]
        / "examples/whab-example-plugin/src/whab_example_plugin/__init__.py"
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
    assert table.rows[-1][-1] is True


def test_example_empty_month_and_large_amounts_have_no_float_rounding():
    module = example_module()
    snapshot = BudgetSnapshot("personal", "2026-10", (), (), ())
    assert [row.value for row in module.insights(snapshot)] == ["$0.00", "$0.00"]
    assert module.export(snapshot).rows == ()
    assert module._usd(100000000000000001) == "$1,000,000,000,000,000.01"
