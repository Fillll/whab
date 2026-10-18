"""Register counts retain the row query's scope while avoiding broad joins."""

import pytest


@pytest.fixture
def count_ledger(store):
    accounts = [
        ("same-id", "personal", "USD"), ("Case", "personal", "USD"),
        ("case", "personal", "EUR"), ("yen", "personal", "JPY"),
        ("café", "personal", "USD"), ("null\x00suffix", "personal", "USD"),
        ("same-id", "demo", "EUR"), ("demo-only", "demo", "USD"),
    ]
    store.db.executemany(
        "INSERT INTO accounts(id,workspace,name,institution,type,balance,source,currency) "
        "VALUES(?,?,?,'Synthetic Bank','depository',0,'manual',?)",
        [(identifier, workspace, identifier, currency) for identifier, workspace, currency in accounts],
    )
    rows = []
    for number, (identifier, workspace, _) in enumerate(accounts):
        for offset in range(17):
            rows.append((
                f"count-{number}-{offset:02}", workspace, identifier,
                ("2026-09-30", "2026-10-01", "2026-10-31", "2026-11-01")[offset % 4],
                "Straße 50% shop" if offset % 3 == 0 else "Synthetic purchase",
                "CAFÉ a_b" if offset % 2 == 0 else "ordinary memo",
                offset % 5 == 0, offset % 3 == 0, offset % 7 == 0,
            ))
    rows.extend([
        ("orphan-count", "personal", "missing", "2026-10-01", "Straße 50% shop", "CAFÉ a_b", 0, 0, 0),
        ("other-workspace-count", "personal", "demo-only", "2026-10-01", "Straße 50% shop", "CAFÉ a_b", 0, 0, 0),
    ])
    store.db.executemany(
        "INSERT INTO transactions(id,workspace,account_id,date,merchant,note,pending,reviewed,deleted,"
        "amount,source) VALUES(?,?,?,?,?,?,?,?,?,100,'manual')", rows,
    )
    return store


def read(store, **selection):
    options = {
        "workspace": "personal", "start_date": None, "end_date": None, "currency": "USD",
        "account_ids": None, "search": "", "review": "all", "page": 1, "page_size": 7,
        **selection,
    }
    return options, store.transaction_register_page(**options)


@pytest.mark.parametrize("selection", [
    {}, {"currency": "EUR"}, {"currency": "JPY"}, {"currency": "usd"},
    {"currency": "ZZZ"}, {"workspace": "demo"},
    {"start_date": "2026-10-01", "end_date": "2026-10-31"},
    {"start_date": "2026-10-01", "end_date": "2026-10-01"},
    {"start_date": "2026-11-01"}, {"end_date": "2026-09-30"},
    {"start_date": "2026-11-01", "end_date": "2026-09-30"},
    {"search": " STRASSE "}, {"search": "%"}, {"search": "_"},
    {"search": "café", "review": "needs-review", "page": 2},
    {"search": "memo", "review": "reviewed", "page": 999},
    {"search": "no match"}, {"review": "unrecognized"},
    {"account_ids": ()}, {"account_ids": ("missing", "demo-only")},
    {"account_ids": ("same-id", "case")},
    {"account_ids": ("same-id", "same-id"), "review": "needs-review"},
    {"account_ids": ("Case", "case"), "search": "%", "page_size": 2},
    {"account_ids": ("café", "null\x00suffix"), "end_date": "2026-10-31"},
])
def test_optimized_counts_preserve_full_register_results(count_ledger, selection):
    store = count_ledger
    options, actual = read(store, **selection)
    expected = store.transaction_register_filtered(
        options["workspace"], options["start_date"], options["end_date"],
        options["currency"], options["account_ids"],
    )
    transactions = expected["transactions"]
    query = options["search"].strip().casefold()
    matching = [row for row in transactions
                if (not query or query in f"{row['merchant']} {row['note']}".casefold())
                and (options["review"] != "needs-review" or not row["reviewed"])
                and (options["review"] != "reviewed" or row["reviewed"])]
    size, count = options["page_size"], len(matching)
    pages = max(1, (count + size - 1) // size)
    page = max(1, min(options["page"], pages))
    offset = (page - 1) * size
    assert actual == {
        **expected, "transactions": matching[offset:offset + size],
        "transactionPage": {
            "total": len(transactions), "count": count, "page": page, "pages": pages,
            "start": offset + 1 if count else 0, "end": min(offset + size, count),
            "toReview": sum(not row["reviewed"] and not row["pending"] for row in transactions),
        },
    }


@pytest.mark.parametrize("account_ids", [None, (), ("same-id",)])
def test_only_unselected_counts_resolve_currency_accounts_once(count_ledger, account_ids):
    statements = []
    count_ledger.db.set_trace_callback(statements.append)
    try:
        read(count_ledger, account_ids=account_ids, search="STRASSE", review="needs-review")
    finally:
        count_ledger.db.set_trace_callback(None)
    count = next(sql for sql in statements if sql.startswith("SELECT COUNT(*) AS total"))
    rows = next(sql for sql in statements if sql.startswith("SELECT t.id,t.account_id AS accountId"))
    if account_ids is None:
        assert "t.account_id IN (SELECT id FROM accounts WHERE workspace='personal' AND currency='USD')" in count
        assert "JOIN accounts" not in count
    else:
        assert "JOIN accounts a ON a.workspace=t.workspace AND a.id=t.account_id" in count
        assert "SELECT id FROM accounts" not in count
    # Page rows retain their original currency projection, stable order and limit.
    assert "JOIN accounts a ON a.workspace=t.workspace AND a.id=t.account_id" in rows
    assert "ORDER BY t.date DESC,t.id DESC LIMIT 7 OFFSET 0" in rows
