"""Bundled Web pages, visual assets and escaped user content."""

from whab import __version__
from whab.plugins import Insight, Plugin


def test_all_pages_render_and_escape_user_content(client):
    for page in ("budget", "transactions", "insights", "accounts"):
        response = client.get("/", params={"page": page, "workspace": "demo", "month": "2026-10"})
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
    text = client.get("/?page=accounts&workspace=personal").text
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
        response = client.get("/?page=insights&workspace=demo&month=2026-10")
        assert response.status_code == 200
        assert 'class="plugin-metrics"' in response.text
        assert "&lt;script&gt;value&lt;/script&gt;" in response.text
        assert "<script>value</script>" not in response.text
        assert "could not be generated" in response.text
        assert "FICTIONAL_SECRET" not in response.text


def test_default_formats_and_only_children_in_category_choices(client):
    import re

    text = client.get("/?page=transactions&workspace=demo&month=2026-10").text
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
        text = client.get(f"/?page=budget&workspace=personal&month=2026-10&currency={currency}").text
        assert money in text
        assert f'{currency} food' in text and f'{currency} wallet' in text
        assert f'value="{amount_input}"' in text
        assert f'currency={currency}' in text
        assert '<option value="JPY"' in text and '<option value="KWD"' in text
        for other in accounts.keys() - {currency}:
            assert f'{other} wallet' not in text and f'{other} food' not in text
        transactions = client.get(
            f"/?page=transactions&workspace=personal&month=2026-10&currency={currency}"
        ).text
        assert f"{currency} market" in transactions
        assert '<optgroup label="Living">' in transactions
        assert '2026-10-05' in transactions


def test_empty_selected_currency_remains_selected_when_switching_workspaces(client):
    text = client.get("/?page=budget&workspace=demo&month=2026-10&currency=EUR").text
    assert '<option value="EUR" selected>EUR</option>' in text
    assert 'EUR 0.00' in text
    assert 'currency=EUR' in text
    assert 'name="currency" value="EUR"' in text
