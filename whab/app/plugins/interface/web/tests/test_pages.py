"""Bundled Web pages, visual assets and escaped user content."""

from whab import __version__
from whab.plugins import Insight, Plugin


def test_all_pages_render_and_escape_user_content(client):
    for page in ("budget", "transactions", "insights", "accounts"):
        response = client.get(f"/budgets/demo/{page}/2026-10/usd")
        assert response.status_code == 200
        assert "Sample data" in response.text
        assert "/static/app.js" in response.text
        assert "WHAB · We Have A Budget" in response.text
        assert f"WHAB · {__version__}" in response.text
        assert 'id="whab-context"' in response.text
    assert client.app.version == __version__
    name = '<script>alert("x")</script>'
    assert (
        client.post(
            "/api/accounts",
            json={"workspace": "personal", "name": name, "type": "depository", "balance": 10000},
        ).status_code
        == 200
    )
    text = client.get("/budgets/home/accounts/current-month/usd").text
    assert name not in text
    assert "&lt;script&gt;" in text
    assert "Sandbox mode" in text


def test_extension_insights_render_and_escape_content(store, tmp_path, client_for):
    def insights(_snapshot):
        return (Insight("<b>Heading</b>", "<script>value</script>"),)

    def fail(_snapshot):
        raise RuntimeError("FICTIONAL_SECRET_DO_NOT_EXPOSE")

    with client_for(
        store,
        tmp_path,
        Plugin("test", "Test report", insights=insights),
        Plugin("broken", "Broken report", insights=fail),
    ) as client:
        response = client.get("/budgets/demo/insights/2026-10/usd")
        assert response.status_code == 200
        assert 'class="plugin-metrics"' in response.text
        assert "&lt;script&gt;value&lt;/script&gt;" in response.text
        assert "<script>value</script>" not in response.text
        assert "could not be generated" in response.text
        assert "FICTIONAL_SECRET" not in response.text


def test_default_formats_and_only_children_in_category_choices(client):
    import re

    text = client.get("/budgets/demo/transactions/2026-10/usd").text
    assert re.search(r'class="date-cell">2026-10-\d{2}</td>', text)
    assert 'Date (YYYY-MM-DD)' in text
    assert 'name="date" type="text" pattern="[0-9]{4}-[0-9]{2}-[0-9]{2}"' in text
    assert 'aria-label="Budget currency" data-navigation="currency"' in text
    assert '<optgroup label="Everyday">' in text
    category_selects = re.findall(r'<select name="categoryId"[^>]*>(.*?)</select>', text, re.S)
    assert category_selects
    assert all('<option value="Everyday"' not in select for select in category_selects)
    assert 'data-minor-units="3"' in text
    assert 'data-minor-units="0"' in text
    assert 'name="currency"' in text
    assert re.search(r'\$[0-9,]+\.[0-9]{2}', text)
    assert '-$64.25' in text and '$8,200.00' in text
    assert '−$' not in text and '+$' not in text
    assert 'Parent category<input name="group"' in text
    assert 'Child category name' in text
    assert 'type="date"' not in text and 'type="month"' not in text


def test_pages_keep_currency_totals_and_categories_separate(client):
    accounts = {}
    categories = {}
    for currency, balance, target in (("USD", 123456, 50000), ("JPY", 2345, 600), ("KWD", 4567, 1000)):
        response = client.post(
            "/api/accounts",
            json={"workspace": "personal", "name": f"{currency} wallet", "type": "depository",
                  "balance": balance, "currency": currency},
        )
        assert response.status_code == 200
        accounts[currency] = response.json()["accountId"]
        response = client.post(
            "/api/categories",
            json={"workspace": "personal", "name": f"{currency} food", "group": "Living",
                  "target": target, "currency": currency},
        )
        assert response.status_code == 200
        categories[currency] = next(
            row["id"] for row in client.get("/api/workspace?workspace=personal&month=2026-10").json()["categories"]
            if row["name"] == f"{currency} food"
        )
        assert client.post(
            "/api/transactions",
            json={"workspace": "personal", "accountId": accounts[currency], "date": "2026-10-05",
                  "merchant": f"{currency} market", "amount": target, "categoryId": categories[currency]},
        ).status_code == 200

    for currency, money, amount_input in (("USD", "$734.56", "0.00"), ("JPY", "JPY 1,745", "0"), ("KWD", "KWD 3.567", "0.000")):
        text = client.get(f"/budgets/home/budget/2026-10/{currency.lower()}").text
        assert money in text
        assert f'{currency} food' in text and f'{currency} wallet' in text
        assert f'value="{amount_input}"' in text
        assert f'/2026-10/{currency.lower()}' in text
        assert '<option value="JPY"' in text and '<option value="KWD"' in text
        for other in accounts.keys() - {currency}:
            assert f'{other} wallet' not in text and f'{other} food' not in text
        transactions = client.get(
            f"/budgets/home/transactions/2026-10/{currency.lower()}"
        ).text
        assert f"{currency} market" in transactions
        assert '<optgroup label="Living">' in transactions
        assert '2026-10-05' in transactions


def test_empty_selected_currency_remains_selected_when_switching_workspaces(client):
    text = client.get("/budgets/demo/budget/2026-10/eur").text
    assert '<option value="EUR" selected>EUR</option>' in text
    assert 'EUR 0.00' in text
    assert '/2026-10/eur' in text
    assert 'name="currency" value="EUR"' in text


def test_accounts_present_budget_scope_types_and_net_worth_with_reclassification(client):
    accounts = {}
    for name, kind, subtype, balance, on_budget in (
        ("Everyday cash", "depository", "checking", 50000, True),
        ("Long term investments", "investment", "brokerage", 250000, False),
        ("Retirement reserve", "retirement", "401k", 350000, False),
        ("Home financing", "loan", "mortgage", 100000, False),
        ("Fixed deposit", "deposit", "cd", 200000, False),
    ):
        response = client.post("/api/accounts", json={
            "workspace": "personal", "name": name, "type": kind, "subtype": subtype,
            "balance": balance, "onBudget": on_budget,
        })
        assert response.status_code == 200
        accounts[name] = response.json()["accountId"]
    text = client.get("/budgets/home/accounts/2026-10/usd").text
    assert 'aria-label="On-budget accounts"' in text
    assert 'aria-label="Off-budget accounts"' in text
    assert 'aria-label="Account totals in USD"' in text
    assert '<dt>Assets</dt><dd>$8,500.00</dd>' in text
    assert '<dt>Liabilities</dt><dd>$1,000.00</dd>' in text
    assert '<dt>Net worth</dt><dd>$7,500.00</dd>' in text
    assert 'On-budget cash balances · USD</span><strong>$500.00' in text
    assert '401(k)' in text and 'Brokerage' in text and 'Mortgage' in text
    assert 'Certificate of deposit (CD)' in text
    assert 'name="onBudget" data-boolean-value' in text
    assert 'data-edit-account="' + accounts["Long term investments"] + '"' in text
    assert 'data-account-subtype="brokerage"' in text
    assert 'Changing budget scope applies to all transactions' in text
    assert 'data-on-budget="false" data-liability="true" disabled hidden>Loan' in text
    assert client.patch("/api/accounts/" + accounts["Everyday cash"], json={
        "workspace": "personal", "type": "depository", "onBudget": False,
        "subtype": "savings",
    }).status_code == 200
    text = client.get("/budgets/home/accounts/2026-10/usd").text
    assert 'data-account-on-budget="false" data-account-subtype="savings"' in text
    assert 'On-budget cash balances · USD</span><strong>$0.00' in text
    assert '<dt>Net worth</dt><dd>$7,500.00</dd>' in text


def test_off_budget_transactions_remain_visible_and_marked_excluded(client):
    response = client.post("/api/accounts", json={
        "workspace": "personal", "name": "Investment reserve", "type": "investment",
        "subtype": "brokerage", "balance": 100000, "onBudget": False,
    })
    assert response.status_code == 200
    assert client.post("/api/transactions", json={
        "workspace": "personal", "accountId": response.json()["accountId"],
        "date": "2026-10-05", "merchant": "Investment fee", "amount": 500,
        "categoryId": None,
    }).status_code == 200
    text = client.get("/budgets/home/transactions/2026-10/usd").text
    assert "Investment fee" in text
    assert "Off-budget · excluded from budget" in text
    insights = client.get("/budgets/home/insights/2026-10/usd").text
    assert "Investment fee" not in insights
    assert "Insights use on-budget accounts" in insights


def test_portable_budget_name_labels_personal_pages_and_preserves_demo(client):
    import re

    name = "Household & travel"
    response = client.post("/api/budget/new", json={"name": name})
    assert response.status_code == 200
    personal = client.get(response.json()["url"]).text
    breadcrumb = re.search(r'<div class="breadcrumb">(.*?)</div>', personal, re.S)[1]
    assert "Household &amp; travel" in breadcrumb
    assert "Current budget · Household &amp; travel" in personal
    assert 'data-budget-copy hidden' in personal
    assert 'aria-label="Saved copies"' in personal
    demo = client.get("/budgets/demo/budget/current-month/usd").text
    breadcrumb = re.search(r'<div class="breadcrumb">(.*?)</div>', demo, re.S)[1]
    assert "Demo budget" in breadcrumb and "Household" not in breadcrumb
    assert "Current budget · Demo budget" in demo
