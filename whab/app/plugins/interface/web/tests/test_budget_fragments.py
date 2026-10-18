"""Lightweight refreshes keep server-rendered budget values bound to their page."""

import re
from html.parser import HTMLParser

import pytest

FRAGMENT = {"X-WHAB-Budget-Fragment": "1"}


class BudgetMarkup(HTMLParser):
    """Inspect stable rendering identities without requiring a browser runtime."""

    def __init__(self, text):
        super().__init__()
        self.keys = []
        self.tags = []
        self.siblings = [set()]
        self.open_tags = []
        self.duplicate_siblings = []
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        self.tags.append((tag, attributes))
        if "data-budget-key" in attributes:
            key = attributes["data-budget-key"]
            self.keys.append(key)
            if key in self.siblings[-1]:
                self.duplicate_siblings.append(key)
            self.siblings[-1].add(key)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
                       "meta", "param", "source", "track", "wbr"}:
            self.open_tags.append(tag)
            self.siblings.append(set())

    def handle_endtag(self, tag):
        if tag in self.open_tags:
            index = len(self.open_tags) - self.open_tags[::-1].index(tag) - 1
            self.open_tags[index:] = []
            self.siblings[index + 1:] = []


def category_assignment_forms(text):
    forms = []
    for api, body in re.findall(
        r'<form class="budget-assignment"[^>]*data-api="([^"]+)"[^>]*>(.*?)</form>',
        text, re.S,
    ):
        if api == "/budget/funding/next-month":
            continue
        forms.append({
            "api": api,
            "workspace": re.search(r'name="workspace" value="([^"]+)"', body)[1],
            "month": re.search(r'name="month" value="([^"]+)"', body)[1],
            "value": re.search(r'name="assigned"[^>]+value="([^"]+)"', body)[1],
        })
    return forms


def fragment(client, path):
    response = client.get(path, headers=FRAGMENT)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert 'class="budget-planner"' in response.text
    assert "<!DOCTYPE" not in response.text
    assert "<html" not in response.text and "<head" not in response.text
    assert 'class="sidebar"' not in response.text
    assert 'id="whab-context"' not in response.text
    assert "<script" not in response.text
    return response.text


def test_budget_fragment_matches_its_full_page_without_shell_or_duplicate_footer(client):
    path = "/budgets/demo/budget/2026-08/usd?category=utilities"
    text = fragment(client, path)
    full = client.get(path).text
    assert text.strip() in full
    assert 'aria-label="Category details"' in text
    assert 'value="2026-08"' in text
    for removed in (
        "budget-start-note", "budget-footnote", "Planning from", "Your budget begins",
        "Card debt is reserved from cash", "Budgeting never moves money between accounts",
    ):
        assert removed not in text and removed not in full
    assert text.count("All amounts in USD") == 1
    footer = re.search(r'<div class="budget-table-footer"[^>]*>(.*?)</div>', text, re.S)[1]
    assert "All amounts" not in footer and "Add category" in footer


@pytest.mark.parametrize("header", [{}, {"X-WHAB-Budget-Fragment": "true"}])
def test_full_pages_remain_default_and_fragment_header_only_applies_to_budget(client, header):
    full = client.get("/budgets/demo/budget/2026-08/usd", headers=header)
    assert full.status_code == 200 and "<!DOCTYPE html>" in full.text
    for page in ("transactions", "insights", "accounts"):
        full = client.get(f"/budgets/demo/{page}/2026-08/usd", headers=FRAGMENT)
        assert full.status_code == 200 and "<!DOCTYPE html>" in full.text


def test_budget_fragment_keeps_workspace_currency_period_and_selection_and_authoritative_totals(
    client, store,
):
    for currency in ("USD", "JPY"):
        store.add_account("personal", {
            "name": f"{currency} fixture cash", "type": "depository", "balance": 100000,
            "currency": currency,
        })
        category = store.add_category("personal", {
            "name": f"{currency} fixture food", "group": "Fixture living", "target": 0,
            "currency": currency,
        })
        if currency == "JPY":
            selected = category
    path = f"/budgets/home/budget/2026-10/jpy?category={selected}"
    before = fragment(client, path)
    assert "JPY fixture food" in before and "USD fixture food" not in before
    assert "Groceries" not in before and "Everyday" not in before
    assert "All amounts in JPY" in before
    forms = category_assignment_forms(before)
    assert len(forms) == 3
    form = next(form for form in forms if form["month"] == "2026-11")
    assert form["workspace"] == "personal"
    assert client.put("/budgets/home/api" + form["api"], json={
        "workspace": "personal", "month": "2026-11", "assigned": 1234,
    }).status_code == 200
    after = fragment(client, path)
    assert {
        form["month"]: form["value"] for form in category_assignment_forms(after)
    } == {"2026-10": "0", "2026-11": "1234", "2026-12": "0"}
    assert "1,234" in after and "98,766" in after
    assert 'aria-label="Category details"' in after
    assert f"category={selected}" in after
    demo = fragment(client, "/budgets/demo/budget/2026-08/jpy")
    assert "fixture food" not in demo


def test_budget_fragment_escapes_labels_in_text_attributes_and_stable_keys(client, store):
    label = '<img src=x onerror="alert(1)"> & "quoted"'
    identifier = store.add_category("personal", {
        "name": label, "group": '<script>alert("group")</script>', "target": 0,
    })
    text = fragment(client, f"/budgets/home/budget/2026-08/usd?category={identifier}")
    assert label not in text and "<script>" not in text
    assert "&lt;img" in text and "&lt;script&gt;" in text
    markup = BudgetMarkup(text)
    assert not [attributes for tag, attributes in markup.tags if tag in {"img", "script"}]
    assert not [name for _, attributes in markup.tags for name in attributes if name.startswith("on")]
    assert markup.keys and not markup.duplicate_siblings


@pytest.mark.parametrize("header,status", [
    ({"Origin": "https://untrusted.invalid"}, 403),
    ({"Sec-Fetch-Site": "cross-site"}, 403),
])
def test_fragment_header_does_not_bypass_existing_local_request_guards(client, header, status):
    response = client.get("/budgets/demo/budget/2026-08/usd", headers={**FRAGMENT, **header})
    assert response.status_code == status
    assert "budget-planner" not in response.text
