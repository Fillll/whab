"""Strict report records shared by the report worker and local projections."""

from dataclasses import asdict

from .sdk import (
    MAX_CHART_SERIES,
    MAX_INSIGHT_CONTRIBUTORS,
    ChartSeries,
    Insight,
    SpendingContributor,
    StackedMonthlyChart,
)
from .wire import WireError


def insight_data(insight):
    value = {"title": insight.title, "value": insight.value, "detail": insight.detail}
    chart = insight.chart
    if chart is not None:
        value["chart"] = {
            "currency": chart.currency, "months": list(chart.months),
            "series": [{
                "id": row.id, "label": row.label, "values": list(row.values),
                **({"contributors": [[asdict(item) for item in bucket] for bucket in row.contributors]}
                   if row.contributors else {}),
            } for row in chart.series],
        }
    return value


def read_insight(value, *, include_chart=False, include_contributors=False):
    required = {"title", "value"}
    allowed = required | ({"detail", "chart"} if include_chart else {"detail"})
    if type(value) is not dict or not required <= value.keys() <= allowed:
        raise WireError()
    chart = value.get("chart")
    if "chart" not in value:
        return Insight(**value)
    if (type(chart) is not dict or chart.keys() != {"currency", "months", "series"}
            or type(chart["months"]) is not list or type(chart["series"]) is not list
            or len(chart["series"]) > MAX_CHART_SERIES):
        raise WireError()
    series = []
    required_series = {"id", "label", "values"}
    allowed_series = required_series | {"contributors"} if include_contributors else required_series
    for row in chart["series"]:
        if type(row) is not dict or not required_series <= row.keys() <= allowed_series or type(row["values"]) is not list:
            raise WireError()
        contributors = row.get("contributors", [])
        if (type(contributors) is not list or len(contributors) > len(chart["months"])
                or any(type(bucket) is not list or len(bucket) > MAX_INSIGHT_CONTRIBUTORS for bucket in contributors)):
            raise WireError()
        series.append(ChartSeries(
            row["id"], row["label"], tuple(row["values"]),
            tuple(tuple(SpendingContributor(**item) for item in bucket) for bucket in contributors),
        ))
    return Insight(value["title"], value["value"], value.get("detail", ""),
                   StackedMonthlyChart(chart["currency"], tuple(chart["months"]), tuple(series)))
