"""Complete searches include every public transaction state and never write."""

import json
from contextlib import contextmanager
from copy import deepcopy

import httpx
import pytest
from whab_plugin_cli import console


def transaction(identifier, **changes):
    return {"id": identifier, "date": "2026-08-01", "merchant": "Fictional Village",
            "accountId": "checking", "amount": 12345, "currency": "USD",
            "categoryId": "housing", "pending": False, "reviewed": True,
            "note": "Preserve 🏠 note", "source": "fictional", "categoryAgent": "Fictional agent",
            "isTransfer": False, "inferredTransfer": False, **changes}


@pytest.fixture
def search_transport(monkeypatch, tmp_path):
    state = {"transactions": [
        transaction("before", date="2026-07-31"),
        transaction("boundary-reviewed"),
        transaction("pending", pending=True, reviewed=False, categoryId=None),
        transaction("refund", amount=-321, merchant="Fictional Village REFUND"),
        transaction("upper-boundary", date="2026-08-31", merchant="Fictional Mortgage"),
        transaction("after", date="2026-09-01", merchant="Fictional Mortgage"),
        transaction("different-lender", merchant="Another Mortgage lender"),
        transaction("memo-only", merchant="Unrelated shop", note="Fictional Village"),
        transaction("deleted", deleted=True),
        transaction("tracking", isTransfer=True, accountId="tracking", currency="JPY", amount=-456,
                    categoryId="unavailable-category", date="2026-08-02"),
        transaction("unicode", merchant="🍏 工作室 Straße", accountId="unavailable-account", categoryId=None),
    ], "envelopes": {}, "failure": None}
    calls = []

    def respond(request):
        if request.url.path.endswith("/transaction-search"):
            return httpx.Response(404)
        calls.append(request)
        assert request.method == "GET", "Search may not mutate or sync"
        if request.url.path.endswith("/budgets"):
            return httpx.Response(200, json={"budgets": [
                {"id": "budget-a", "name": "Fictional 家庭"}, {"id": "budget-b", "name": "Other budget"}]})
        name = request.url.path.rsplit("/", 1)[1]
        assert name in {"transactions", "accounts", "categories"}
        if state["failure"] == name:
            return httpx.Response(503)
        if state["failure"] == "oversized":
            return httpx.Response(200, content=b" " * (16 * 1024 * 1024 + 1))
        data = {"transactions": state["transactions"], "accounts": [
            {"id": "checking", "name": "Fictional checking"}, {"id": "tracking", "name": "Fictional tracking", "onBudget": False}],
            "categories": [{"id": "housing", "name": "Fictional home", "group": "Housing"}]}[name]
        if "/budget-b/" in request.url.path:
            data = []
        if name == "transactions" and request.url.params.get("month"):
            data = [row for row in data if row["date"].startswith(request.url.params["month"])]
        result = {"budgetId": "budget-b" if "/budget-b/" in request.url.path else "budget-a", "revision": "one", "data": data,
                  **state["envelopes"].get(name, {})}
        return httpx.Response(200, json=result)

    @contextmanager
    def connection(*args, **kwargs):
        with httpx.Client(base_url="https://localhost", transport=httpx.MockTransport(respond)) as client:
            yield client

    monkeypatch.setattr(console, "local_connection", connection)
    monkeypatch.setattr(console.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("WHAB_HOME", raising=False)
    return state, calls


def read(capsys, *arguments, target="Fictional 家庭", alias="trnx"):
    assert console.main([target, alias, "list", *arguments]) == 0
    return json.loads(capsys.readouterr().out)


def test_complete_history_includes_every_state_and_preserves_fields(search_transport, capsys):
    state, calls = search_transport
    before = deepcopy(state)
    result = read(capsys)
    assert result["budgetId"] == "budget-a" and result["revision"] == "one"
    assert result["complete"] is True and result["total"] == result["returned"] == len(result["data"]) == 10
    assert result["filters"] == {"since": None, "until": None, "month": None, "merchant": [],
                                 "merchantMatch": "case-insensitive-literal-substring-any"}
    rows = {row["id"]: row for row in result["data"]}
    for original in state["transactions"]:
        if original.get("deleted"):
            assert original["id"] not in rows
        else:
            assert {key: rows[original["id"]][key] for key in original} == original
    assert rows["boundary-reviewed"]["categoryName"] == "Fictional home"
    assert rows["boundary-reviewed"]["categoryGroup"] == "Housing"
    assert rows["pending"]["categoryName"] is None
    assert rows["tracking"]["categoryName"] is None
    assert rows["tracking"]["accountName"] == "Fictional tracking"
    assert rows["unicode"]["accountName"] == "unavailable-account"
    assert state == before
    assert len(calls) == 4 and dict(calls[1].url.params) == {"workspace": "personal"}
    assert [row["id"] for row in result["data"]] == [row["id"] for row in sorted(rows.values(), key=lambda row: (row["date"], row["id"]), reverse=True)]


@pytest.mark.parametrize("arguments,expected", [
    (["--since", "2026-08-01", "--until", "2026-08-31", "--merchant", "FICTIONAL village", "--merchant", "Fictional Mortgage"],
     {"boundary-reviewed", "pending", "refund", "tracking", "upper-boundary"}),
    (["--since", "2026-08-31", "--merchant", "Fictional Mortgage"], {"upper-boundary", "after"}),
    (["--until", "2026-07-31"], {"before"}),
    (["--month", "2026-08", "--until", "2026-08-01", "--merchant", "village"], {"boundary-reviewed", "pending", "refund"}),
    (["--merchant", "🍏 工作室 STRASSE"], {"unicode"}),
    (["--merchant", "Village.*"], set()),
    (["--merchant", "Village%"], set()),
    (["--merchant", " Fictional Village "], set()),
    (["--since", "2026-08-01", "--until", "2026-08-01", "--merchant", "refund"], {"refund"}),
])
def test_inclusive_bounds_literal_or_matching_and_no_lender_inference(search_transport, capsys, arguments, expected):
    result = read(capsys, *arguments)
    assert {row["id"] for row in result["data"]} == expected
    assert result["total"] == result["returned"] == len(expected) and result["complete"] is True


def test_large_match_set_is_not_sampled(search_transport, capsys):
    state, _ = search_transport
    state["transactions"] = [transaction(f"fictional-{index:05}") for index in range(1201)]
    result = read(capsys, "--merchant", "Village")
    assert result["total"] == result["returned"] == len(result["data"]) == 1201
    assert result["data"][0]["id"] == "fictional-01200"


def test_alias_name_id_and_budget_isolation(search_transport, capsys):
    assert read(capsys, target="FICTIONAL 家庭", alias="transactions")["budgetId"] == "budget-a"
    assert read(capsys, target="budget-a")["total"] == 10
    result = read(capsys, target="Other budget")
    assert result["budgetId"] == "budget-b" and result["total"] == 0
    for target in ("missing", "Fictional"):
        assert console.main([target, "trnx", "list"]) == 1
        output = capsys.readouterr()
        assert not output.out and "Unknown or ambiguous" in output.err


@pytest.mark.parametrize("query,changes", [
    ("transactions", {"budgetId": "foreign"}), ("transactions", {"revision": None}),
    ("accounts", {"budgetId": "foreign"}), ("accounts", {"revision": "changed"}),
    ("categories", {"budgetId": "foreign"}), ("categories", {"revision": "changed"}),
])
def test_foreign_and_stale_results_fail_without_partial_output(search_transport, capsys, query, changes):
    state, _ = search_transport
    state["envelopes"][query] = changes
    assert console.main(["Fictional 家庭", "trnx", "list"]) == 1
    output = capsys.readouterr()
    assert not output.out and "error" in output.err


@pytest.mark.parametrize("failure", ["transactions", "accounts", "categories", "oversized"])
def test_failed_or_oversized_response_never_returns_truncated_success(search_transport, capsys, failure):
    state, _ = search_transport
    state["failure"] = failure
    assert console.main(["Fictional 家庭", "trnx", "list"]) == 1
    output = capsys.readouterr()
    assert not output.out and "error" in output.err
    if failure == "oversized":
        assert "exceeds 16 MiB" in output.err


@pytest.mark.parametrize("arguments", [
    ["--since", "20260801"], ["--until", "2026-02-30"], ["--since", "2026-8-01"],
    ["--merchant", ""], ["--merchant", "  "], ["--format", "csv"], ["--limit", "10"],
])
def test_invalid_inputs_fail_before_host_acquisition(search_transport, capsys, arguments):
    with pytest.raises(SystemExit) as failure:
        console.main(["Fictional 家庭", "trnx", "list", *arguments])
    assert failure.value.code == 2 and not search_transport[1]
    capsys.readouterr()


def test_reversed_dates_fail_before_host_acquisition(search_transport, capsys):
    assert console.main(["Fictional 家庭", "trnx", "list", "--since", "2026-09-01", "--until", "2026-08-01"]) == 1
    assert not search_transport[1] and "on or before" in capsys.readouterr().err


def test_table_shows_full_ids_categories_currencies_review_and_safe_names(search_transport, capsys):
    state, _ = search_transport
    state["transactions"][1]["id"] = "fictional-stable-transaction-id-" * 5
    state["transactions"][1]["merchant"] = "Fictional\x1b[31m\n" + "name" * 30
    assert console.main(["Fictional 家庭", "trnx", "list", "--format", "table"]) == 0
    output = capsys.readouterr().out
    for text in [state["transactions"][1]["id"], "10 matched, 10 shown (complete)", "Housing / Fictional home",
                 "unavailable-category", "Uncategorized", "JPY", "USD", "-$3.21", "Pending", "Posted", "Reviewed", "Unreviewed", "\\u001b", "\\u000a", "…"]:
        assert text in output
    assert "\x1b" not in output
    assert console.main(["Other budget", "transactions", "list", "--format", "table"]) == 0
    assert "0 matched, 0 shown (complete)\nNo matching transactions." in capsys.readouterr().out


def test_help_and_packaged_search_schema_are_offline(search_transport, capsys, monkeypatch):
    monkeypatch.setattr(console, "configuration", lambda args: pytest.fail("Help cannot load profiles"))
    with pytest.raises(SystemExit) as result:
        console.main(["--profile", "/missing/fictional.budget", "Fictional 家庭", "trnx", "list", "--help"])
    assert result.value.code == 0 and not search_transport[1]
    output = capsys.readouterr().out
    for text in ["inclusive", "literal substring", "OR", "reviewed and", "refunds", "complete=true", "16 MiB", "--month",
                 "No fuzzy", "Never combine currencies", "categorize TRANSACTION_ID", "persistent future categorization rule", "Help is offline"]:
        assert text.lower() in output.lower()


@pytest.mark.parametrize('bad', [None, [], {'budgetId': 'foreign', 'revision': 'one', 'data': []},
                              {'budgetId': 'budget-a', 'revision': None, 'data': []}])
def test_optimized_read_rejects_foreign_or_malformed_envelopes(monkeypatch, bad):
    from whab_plugin_cli.transaction_reads import optimized_read
    with pytest.raises(console.CommandError):
        optimized_read(None, 'budget-a', '/fixture', {}, lambda *a, **kw: bad, console.CommandError)


def test_optimized_selection_is_one_coherent_read(monkeypatch, tmp_path, capsys):
    from whab_plugin_cli.transaction_reads import optimized_read
    expected = {'budgetId': 'budget-a', 'revision': 'one', 'complete': True, 'total': 1, 'returned': 1,
                'data': [transaction('selected')], 'filters': {}}
    assert optimized_read(None, 'budget-a', '/fixture', {}, lambda *a, **kw: expected, console.CommandError) == expected
    def fail(*a, **kw):
        error = console.CommandError('Provider unavailable')
        error.status_code = 503
        raise error
    with pytest.raises(console.CommandError, match='unavailable'):
        optimized_read(None, 'budget-a', '/fixture', {}, fail, console.CommandError)


@pytest.mark.parametrize('counts', [dict(total=0, returned=1), dict(total=1, returned=0),
                                    dict(total=0, returned=0, complete=False)])
def test_optimized_read_never_accepts_partial_counts(counts):
    from whab_plugin_cli.transaction_reads import optimized_read
    response = {'budgetId': 'budget-a', 'revision': 'one', 'data': [], 'complete': True, **counts}
    with pytest.raises(console.CommandError, match='incomplete'):
        optimized_read(None, 'budget-a', '/fixture', {}, lambda *a, **kw: response, console.CommandError)


def test_long_merchant_selection_retains_legacy_complete_read(search_transport, capsys):
    result = read(capsys, '--merchant', 'Fictional' * 3000)
    assert result['complete'] and result['total'] == 0
