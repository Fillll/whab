"""Example API v1 plugin: selected-month totals and a structured transaction export."""

from whab.plugins import BudgetSnapshot, ExportTable, Insight, Plugin, PluginContract, format_money


def insights(snapshot: BudgetSnapshot) -> tuple[Insight, ...]:
    kinds = {category.id: category.kind for category in snapshot.categories}
    budget_accounts = {account.id for account in snapshot.accounts if account.on_budget}
    income_accounts = {account.id for account in snapshot.accounts if account.on_budget and account.type != "credit"}
    cleared = tuple(
        row for row in snapshot.transactions
        if row.account_id in budget_accounts and not row.pending and not row.is_transfer
        and kinds.get(row.category_id) not in {"transfer", "opening_balance"}
    )
    spending = sum(row.amount_cents for row in cleared if row.amount_cents > 0 and kinds.get(row.category_id) != "income")
    refunds = -sum(
        row.amount_cents for row in cleared if row.amount_cents < 0 and row.category_id is not None
        and kinds.get(row.category_id) != "income"
    )
    income = -sum(
        row.amount_cents for row in cleared if row.account_id in income_accounts
        and (kinds.get(row.category_id) == "income" or row.amount_cents < 0 and row.category_id is None)
    )
    return (
        Insight(
            "Net spending",
            format_money(spending - refunds, snapshot.currency),
            f"{snapshot.month}: on-budget purchases less categorized refunds; pending, transfers and opening balances excluded.",
        ),
        Insight(
            "Income",
            format_money(income, snapshot.currency),
            "Explicit Income activity and uncategorized cleared on-budget cash inflows. This is analytics, not extra assignable cash.",
        ),
    )


def export(snapshot: BudgetSnapshot) -> ExportTable:
    accounts = {row.id: row.name for row in snapshot.accounts}
    categories = {row.id: row.name for row in snapshot.categories}
    return ExportTable(
        columns=(
            "date", "payee", "account", "category", "amount_cents", "pending", "is_transfer", "currency",
        ),
        rows=tuple(
            (
                row.date,
                row.payee,
                accounts[row.account_id],
                categories.get(row.category_id, ""),
                row.amount_cents,
                row.pending,
                row.is_transfer,
                snapshot.currency,
            )
            for row in snapshot.transactions
        ),
    )


plugin = Plugin(
    id="example",
    display_name="Month totals example",
    api_version=1,
    insights=insights,
    export=export,
    contract=PluginContract(lifecycle="live"),
)
