"""The visible month range affects presentation, not financial projections."""

import re

import pytest
from whab_plugin_web.views import budget_month_count


@pytest.mark.parametrize("value,expected", [(None, 3), ("", 3), ("bogus", 3), ("0", 3), ("6", 3), ("1", 1), ("2", 2), ("3", 3), ("4", 4), ("5", 5)])
def test_bounded_range(value, expected):
    assert budget_month_count(value) == expected


@pytest.mark.parametrize("count", range(1, 6))
def test_visible_columns_and_range_highlights_match(client, count):
    text = client.get(f"/budgets/demo/budget/2026-10/usd?months={count}").text
    assert f'aria-label="Category budget across {count} month{"s" if count != 1 else ""}"' in text
    assert text.count('class="month-link is-selected ') == 1
    assert text.count('class="month-link in-range ') == count - 1
    assert len(re.findall(r'id="assigned-total-', text)) == count
    assert 'data-range-handle="start"' in text and 'data-range-handle="end"' in text
    assert f'2026-11/usd?months={count}' in text


def test_final_calendar_month_limits_the_range_to_available_columns(client):
    text = client.get("/budgets/demo/budget/2200-12/usd?months=5").text
    assert 'data-month-count="1"' in text
    assert 'aria-label="Category budget across 1 month"' in text
    assert text.count('class="month-link in-range ') == 0
