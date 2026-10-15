"""Presentation totals include cleared uncategorized purchases without counting transfers."""

from whab_web.views import build_context


def test_uncategorized_spending_counts_only_cleared_purchases():
    def transaction(identifier, amount, **changes):
        return {
            "id": identifier, "date": "2026-10-01", "merchant": identifier,
            "amount": amount, "categoryId": None, "pending": False, "reviewed": False,
            **changes,
        }

    data = {
        "categories": [], "budget": [], "accounts": [],
        "trend": [{"month": "2026-10", "spent": 2500}],
        "summary": {"spent": 2500},
        "transactions": [
            transaction("purchase", 2500),
            transaction("income", -10000),
            transaction("pending", 800, pending=True),
            transaction("transfer", 900, isTransfer=True),
        ],
    }
    context = build_context(
        data, {}, [], workspace="personal", page="insights", month="2026-10"
    )
    assert context["uncategorized"] == 2500
    assert context["purchases"] == 1
    assert context["payees"] == [("purchase", 2500)]
    assert context["selected"] is None and context["selected_budget"] is None
