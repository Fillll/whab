"""Account currencies and provider settings have distinct visible homes."""

from html.parser import HTMLParser

from whab_plugin_plaid.plugin import plugin as plaid


class Placement(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.parents = []
        self.provider_owner = None
        self.feed(text)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if attributes.get("data-bank-provider") == "plaid":
            self.provider_owner = next((attrs.get("data-plugin-id") for kind, attrs in reversed(self.parents) if kind == "article"), None)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr", "path", "line", "polyline", "circle", "rect"}:
            self.parents.append((tag, attributes))

    def handle_endtag(self, tag):
        for index in range(len(self.parents) - 1, -1, -1):
            if self.parents[index][0] == tag:
                del self.parents[index:]
                break


def test_accounts_show_all_currencies_but_provider_controls_only_in_plugin_settings(store, client_for, tmp_path):
    for code, balance in (("USD", 12500), ("JPY", 3456), ("KWD", 1234)):
        store.add_account("personal", {"name": f"Fictional {code}", "type": "depository", "balance": balance, "currency": code})
    with client_for(store, tmp_path, plaid) as client:
        response = client.get("/budgets/home/accounts/2026-10/usd")
        assert response.status_code == 200
        text = response.text
        content = text.split('<div class="page-content"', 1)[1].split('<div id="toast"', 1)[0]
        assert 'data-bank-provider="plaid"' not in content
        assert Placement(text).provider_owner == "plaid"
        assert '<dialog class="modal"\n        id="plaid-disconnect-dialog"' not in text
        assert 'class="plaid-disconnect-confirm" hidden' in text
        assert 'aria-label="Budget currency"' not in text
        for code in ("USD", "JPY", "KWD"):
            assert f'Account totals in {code}' in content
            assert f'Fictional {code}' in content and f' · {code} · ' in content
        assert '<strong>$125.00</strong>' in content
        assert '<strong>JPY 3,456</strong>' in content
        assert '<strong>KWD 1.234</strong>' in content
