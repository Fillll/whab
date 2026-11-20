# Monthly spending

Monthly spending is an optional [Insights plugin](../README.md). Enable **Monthly
spending** in the budget's plugin settings and open **Insights**. Choose the
first and last months, then compare categories or their parent groups. The range
includes both endpoints and can span up to 24 months. Each column represents one
month, including gaps with no activity. The report uses the selected currency.

The report includes posted on-budget Expense activity and uncategorized outflows.
Categorized refunds reduce spending in the month they post and can make a series
negative. Pending and deleted entries, off-budget accounts, transfers, income and
opening balances are excluded. Imported activity before the budget's first month
is included. Category group membership follows the current budget hierarchy.

Categories use qualified names and stable keys so equally named categories in
different groups remain separate. The chart displays up to 100 series, including
Uncategorized when present. Over that limit it keeps the series with the most
activity across the selected months and combines the rest into an explicitly
labeled Other series. The complete monthly net total is preserved.

## Operation documentation

The [packaged operation contract](src/whab_plugin_monthly_spending/operations.json)
describes the read-only `insights` hook and supplies fictional examples.
The host's reporting capability version 2 supplies a scoped monthly spending
projection. This plugin builds a structured chart from that projection. It
does not fetch transactions, modify the budget, store preferences or contact a
provider. Rendering is handled by a compatible visual interface.

## Checks

From the application directory, run the focused plugin tests:

```sh
.venv/bin/python -m pytest plugins/insight/monthly-spending/tests
```
