"""Insights detail links resolve only reports available in the active budget."""

from dataclasses import asdict

import pytest
from starlette.datastructures import QueryParams
from whab_plugin_web.insight_selection import insight_selection

from whab.plugins import Capability, Insight, PluginDescription, PluginInsightResult


def plugin(identifier, *, has_insights=True, capabilities=()):
    return PluginDescription(identifier, identifier, 1, has_insights, True, False, False, False, (),
                             capabilities=capabilities)


@pytest.fixture(params=("sdk", "worker"))
def row(request):
    # Both the inline renderer and the visual worker receive these fields.
    return asdict if request.param == "worker" else lambda record: record


@pytest.mark.parametrize("view", ("overview", "categories", "payees"))
def test_builtin_views_and_default_gallery_require_no_report_output(view):
    assert insight_selection(QueryParams("ignored=value"), (), ()) == {
        "report": "gallery", "result": None, "visual": False,
    }
    assert insight_selection(QueryParams(f"insight_report={view}&insight_from=2026-01"), (), ()) == {
        "report": view, "result": None, "visual": False,
    }


@pytest.mark.parametrize("query", ("insight_report=", "insight_report=gallery", "insight_report=plugin:disabled",
                                  "insight_report=plugin:export-only", "insight_report=overview&insight_report=overview"))
def test_unknown_and_ambiguous_selections_cannot_open_an_unrelated_report(query, row):
    plugins = (row(plugin("export-only", has_insights=False)), row(plugin("another")))
    results = (row(PluginInsightResult("disabled", "Disabled", (Insight("Other", "1"),))),
               row(PluginInsightResult("another", "Another", (Insight("Another", "2"),))))
    assert insight_selection(QueryParams(query), plugins, results) == {
        "report": "unavailable", "result": None, "visual": False,
    }


def test_explicit_report_and_builtin_can_share_identifier_and_result_stays_scoped(row):
    plugins = (row(plugin("overview", capabilities=(Capability("reporting.export"), Capability("reporting.insights")))),)
    wanted = row(PluginInsightResult("overview", "Extension", (Insight("Result", "Provided"),)))
    other = row(PluginInsightResult("other", "Other", (Insight("Result", "Different"),)))
    assert insight_selection(QueryParams("insight_report=overview"), plugins, (wanted, other))["result"] is None
    assert insight_selection(QueryParams("insight_report=plugin%3Aoverview&insight_group=group"), plugins, (other, wanted)) == {
        "report": "plugin:overview", "result": wanted, "visual": False,
    }


@pytest.mark.parametrize("version", (2, 3))
def test_legacy_filtered_links_prefer_monthly_spending_and_allow_first_other_visual(row, version):
    chart = (Capability("reporting.insights", version),)
    monthly = row(plugin("monthly-spending", capabilities=chart))
    first_visual = row(plugin("first", capabilities=chart))
    another_visual = row(plugin("second", capabilities=chart))
    text = row(plugin("text", capabilities=(Capability("reporting.insights"),)))
    error = row(PluginInsightResult("monthly-spending", "Monthly spending", (), error="Report failed."))
    query = QueryParams("insight_account=first&insight_account=second")
    assert insight_selection(query, (text, first_visual, another_visual, monthly), (error,)) == {
        "report": "plugin:monthly-spending", "result": error, "visual": True,
    }
    assert insight_selection(query, (text, first_visual, another_visual), (error,)) == {
        "report": "plugin:first", "result": None, "visual": True,
    }
    assert insight_selection(query, (text,), (error,)) == {"report": "gallery", "result": None, "visual": False}
