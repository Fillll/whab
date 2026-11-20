"""Build one stacked chart from WHAB's scoped monthly spending projection."""

from hashlib import sha256

from whab.plugins import (
    BudgetSnapshot,
    Capability,
    ChartSeries,
    Insight,
    Plugin,
    PluginContract,
    StackedMonthlyChart,
)

MAX_SERIES = 100


def _series_key(category, by_group):
    if by_group:
        return category.parent_id, category.group
    # Keep stable keys bounded even for imported category IDs at the SDK limit.
    key = sha256(category.id.encode("utf-8", "surrogatepass")).hexdigest()
    return f"category:{key}", f"{category.group} / {category.name}"


def _visible_series(labels, amounts, uncategorized, by_group):
    active = [ChartSeries(key, labels[key], tuple(values)) for key, values in amounts.items() if any(values)]
    available = MAX_SERIES - bool(any(uncategorized))
    remainder = []
    if len(active) > available:
        # Rank by activity across the range, including the magnitude of refunds.
        active.sort(key=lambda series: (-sum(map(abs, series.values)), series.id))
        active, remainder = active[:available - 1], active[available - 1:]
    active.sort(key=lambda series: (series.label.casefold(), series.id))
    if remainder:
        label = "Other groups" if by_group else "Other categories"
        values = tuple(map(sum, zip(*(series.values for series in remainder), strict=True)))
        active.append(ChartSeries("system:other", f"{label} ({len(remainder)})", values))
    if any(uncategorized):
        active.append(ChartSeries("system:uncategorized", "Uncategorized", uncategorized))
    return tuple(active)


def insights(snapshot: BudgetSnapshot) -> tuple[Insight, ...]:
    if not snapshot.spending:
        return ()  # The generic lifecycle probe has no selected period.

    months = tuple(month.month for month in snapshot.spending)
    by_group = snapshot.spending_by == "group"
    keys = {row.id: _series_key(row, by_group) for row in snapshot.categories}
    labels = {}
    amounts = {}
    for index, month in enumerate(snapshot.spending):
        for row in month.categories:
            key, label = keys[row.category_id]
            if key not in amounts:
                labels[key] = label
                amounts[key] = [0] * len(months)
            amounts[key][index] += row.amount_cents

    uncategorized = tuple(month.uncategorized_cents for month in snapshot.spending)
    chart = StackedMonthlyChart(snapshot.currency, months, _visible_series(labels, amounts, uncategorized, by_group))
    return (Insight(
        "Monthly spending", "", "Posted on-budget spending by month. Refunds reduce spending in their posting month.", chart=chart,
    ),)


plugin = Plugin(
    id="monthly-spending",
    display_name="Monthly spending",
    insights=insights,
    contract=PluginContract(provides=(Capability("reporting.insights", 2, "additive"),), lifecycle="live"),
)
