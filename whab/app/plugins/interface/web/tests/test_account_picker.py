"""Account choices retain accessible names and use only scoped local logo URLs."""

import re

from tests.support.bank_logos import LOGO_PATH, PNG
from tests.support.bank_logos import imported_store as imported_store
from whab.plugins import BankLogos, LogoImage, Plugin


def picker_options(text):
    return re.findall(r'<div class="account-picker-option".*?</div>', text, re.S)


def test_picker_keeps_native_fallback_and_selected_account_without_logos(client, store):
    identifier = store.add_account("personal", {
        "name": "Wallet <shared> & savings", "type": "depository", "balance": 0,
    })
    text = client.get(
        f"/budgets/home/transactions/2026-10/usd?account={identifier}"
    ).text
    assert '<select aria-label="Filter by account" id="account-filter" multiple size="1">' in text
    assert f'<option value="{identifier}" selected>Wallet &lt;shared&gt; &amp; savings</option>' in text
    assert '<div class="account-picker" data-account-picker hidden>' in text
    toggle = re.search(r'<button id="account-filter-toggle".*?</button>', text, re.S)[0]
    assert 'role="combobox"' in toggle and 'aria-label="Filter by account"' in toggle
    assert 'aria-expanded="false"' in toggle and 'aria-controls="account-filter-options"' in toggle
    assert 'data-account-picker-label>Wallet &lt;shared&gt; &amp; savings</span>' in toggle
    assert 'data-bank-logo' not in toggle
    options = picker_options(text)
    assert len(options) == 2
    assert 'data-value="all" aria-selected="false"' in options[0]
    assert f'data-value="{identifier}" aria-selected="true"' in options[1]
    assert 'role="option"' in options[1]
    assert 'data-bank-logo' not in options[1]
    all_accounts = client.get("/budgets/home/transactions/2026-10/usd").text
    assert 'data-value="all" aria-selected="true"' in picker_options(all_accounts)[0]
    assert 'aria-multiselectable="true"' in text
    assert 'data-account-picker-popup hidden' in text
    assert 'data-account-picker-apply>Apply</button>' in text


def test_picker_uses_scoped_bank_logos_without_fetching_while_rendering(imported_store, tmp_path, client_for):
    fetched = []

    def fetch(key):
        fetched.append(key)
        return LogoImage(PNG, "image/png")

    plugin = Plugin("logos", "Fictional bank logos", bank_logos=BankLogos(
        lambda institution: "chase" if institution == "Chase" else None, fetch,
    ))
    with client_for(imported_store, tmp_path, plugin) as client:
        text = client.get(
            "/budgets/home/transactions/2026-10/usd?account=synthetic-checking"
        ).text
        options = picker_options(text)
        assert len(options) == 4
        known = [option for option in options if 'data-bank-logo' in option]
        assert len(known) == 2
        assert all(f'src="/budgets/home{LOGO_PATH}"' in option for option in known)
        toggle = re.search(r'<button id="account-filter-toggle".*?</button>', text, re.S)[0]
        assert f'src="/budgets/home{LOGO_PATH}"' in toggle
        assert 'alt=""' in toggle and 'decoding="async" hidden' in toggle
        assert 'data-account-picker-label>Fictional checking</span>' in toggle
        unknown = next(option for option in options if 'data-value="synthetic-unknown"' in option)
        assert 'data-bank-logo' not in unknown
        assert 'account-picker-mark sidebar-bank-mark' in unknown
        assert fetched == []
        assert "SYNTHETIC_ENCRYPTED_TOKEN" not in text


def test_picker_does_not_offer_accounts_from_another_currency_or_budget(client, store):
    usd = store.add_account("personal", {"name": "Dollar wallet", "type": "depository", "balance": 0})
    euro = store.add_account("personal", {
        "name": "Euro wallet", "type": "depository", "balance": 0, "currency": "EUR",
    })
    text = client.get("/budgets/home/transactions/2026-10/usd").text
    options = picker_options(text)
    assert any(f'data-value="{usd}"' in option for option in options)
    assert all(f'data-value="{euro}"' not in option for option in options)
    demo = picker_options(client.get("/budgets/demo/transactions/2026-10/usd").text)
    assert all(f'data-value="{usd}"' not in option for option in demo)


def test_picker_shows_multiple_selected_accounts_without_making_one_current(client, store):
    identifiers = [store.add_account("personal", {
        "name": name, "type": "depository", "balance": 0,
    }) for name in ("First wallet", "Second wallet", "Third wallet")]
    text = client.get(
        "/budgets/home/transactions/2026-10/usd?accounts=" + identifiers[0]
        + "&accounts=" + identifiers[1]
    ).text
    options = picker_options(text)
    assert 'data-value="all" aria-selected="false"' in options[0]
    assert f'data-value="{identifiers[0]}" aria-selected="true"' in options[1]
    assert f'data-value="{identifiers[1]}" aria-selected="true"' in options[2]
    assert f'data-value="{identifiers[2]}" aria-selected="false"' in options[3]
    toggle = re.search(r'<button id="account-filter-toggle".*?</button>', text, re.S)[0]
    assert 'data-account-picker-label>2 accounts</span>' in toggle
    assert 'Account settings for' not in text
    assert 'aria-current="page"' not in re.search(r'<div class="sidebar-accounts">.*?</div>\s*<div class="sidebar-bottom">', text, re.S)[0]


def test_period_picker_has_presets_and_an_open_ended_custom_range(client):
    text = client.get(
        "/budgets/demo/transactions/2026-10/usd?period=custom&start_date=2026-09-15"
    ).text
    assert 'data-period-picker' in text and 'data-period-popup hidden' in text
    assert 'data-period-choice="current-month" aria-pressed="false">This month</button>' in text
    assert 'data-period-choice="previous-month" aria-pressed="false">Previous month</button>' in text
    assert '<form class="transaction-period-form" data-period-form>' in text
    assert 'name="start_date" aria-label="Start date" min="1900-01-01" max="2200-12-31" value="2026-09-15"' in text
    assert 'name="end_date" aria-label="End date" min="1900-01-01" max="2200-12-31" value=""' in text
    assert '>Apply dates</button>' in text
    assert 'id="all-dates-filter"' not in text and 'aria-label="Transaction month"' not in text
    previous = client.get("/budgets/demo/transactions/2026-10/usd?period=previous-month").text
    assert 'data-period-choice="previous-month" aria-pressed="true"' in previous


def test_review_filter_explains_its_three_states(client):
    states = (("all", "All", "Needs review"), ("needs-review", "Needs review", "Reviewed"), ("reviewed", "Reviewed", "All"))
    for state, label, following in states:
        text = client.get(f"/budgets/demo/transactions/2026-10/usd?review={state}").text
        button = re.search(r'<button class="filter-button transaction-review-filter".*?</button>', text, re.S)[0]
        assert f'data-review-state="{state}"' in button
        assert f'aria-label="Review status: {label}. Click for {following}."' in button
        assert 'data-review-letter aria-hidden="true">R</span>' in button
        assert f'data-review-label>{label}</span>' in button
