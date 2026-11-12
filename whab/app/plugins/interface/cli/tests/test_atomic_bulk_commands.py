"""Bulk CLI diagnostics, strict files, previews and uncertain intent retention."""

import io
import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from whab_plugin_cli import bulk_commands as bulk
from whab_plugin_cli import console
from whab_plugin_sqlite import Store

from whab.plugins import LedgerService


@pytest.fixture
def endpoint():
    store = Store(":memory:", month="2026-11")
    service = LedgerService(store, budget_id="budget")
    account = service.add_account(
        "personal", {"name": "🍏旅行", "type": "other", "onBudget": False, "balance": 10000}
    )
    category = service.add_category("personal", {"name": "食料品", "group": "家族", "target": 0})
    transaction = service.add_transaction(
        "personal",
        {
            "accountId": account,
            "date": "2026-11-01",
            "merchant": "Shop",
            "amount": 100,
            "categoryId": None,
        },
    )
    state = {"fault": None, "posts": [], "preview_fault": None}

    def exchange(connection, method, path, **kwargs):
        if method == "GET":
            result = service.query(path.rsplit("/", 1)[1], "personal")
        else:
            body = kwargs["json"]
            if path.endswith("/queries"):
                result = service.query(
                    body["operation"], body["workspace"], parameters=body["parameters"]
                )
                if state["preview_fault"] == "revision":
                    result["revision"] = "changed"
            else:
                state["posts"].append(deepcopy(body))
                fault = state["fault"]
                if fault in (400, 409):
                    failure = console.CommandError("Fictional refusal")
                    failure.status_code = fault
                    raise failure
                if fault == "lost":
                    raise httpx.ReadError("private credential")
                result = service.command(
                    body["operation"],
                    body["workspace"],
                    body["payload"],
                    request_id=body["request_id"],
                    expected_revision=body["expected_revision"],
                    history=body.get("history"),
                )
                if fault == "foreign":
                    return {"budgetId": "another", **result}
                if fault == "wrong":
                    result["data"]["changes"][0]["after"]["note"] = "wrong"
                if fault == "none":
                    result["data"] = None
                if fault == "count":
                    result["data"]["count"] = 99
                if fault == "ids":
                    result["data"]["changes"][0]["id"] = "wrong"
                if fault == "missing":
                    result["data"].pop("changes")
        return {"budgetId": "budget", **result}

    yield state, service, account, category, transaction, exchange
    store.close()


def args(**changes):
    return SimpleNamespace(**{
        'target':'Fictional', 'filter':'edit-many', 'agent':None, 'batch':None,
        'revision':None, 'request_id':None, 'dry_run':False, **changes,
    })


def execute(endpoint, options, edits=None):
    state, service, account, category, transaction, exchange = endpoint
    return bulk.execute_bulk(
        None,
        "budget",
        "/budget",
        options,
        exchange,
        console.CommandError,
        "edit_transactions",
        edits if edits is not None else [{"transactionId": transaction, "appendNote": "exact"}],
        service.revision,
    )


@pytest.mark.parametrize(
    "fault", [400, 409, "lost", "foreign", "wrong", "none", "count", "ids", "missing"]
)
def test_write_failures_never_retry_and_retain_exact_intent(endpoint, fault):
    state, *_ = endpoint
    state["fault"] = fault
    with pytest.raises(console.CommandError) as failure:
        execute(endpoint, args())
    message = str(failure.value)
    assert (
        "No automatic retry" in message
        and "original intent" in message
        and "private credential" not in message
    )
    assert len(state["posts"]) == 1
    assert ("no new items were saved" in message) == (fault in (400, 409))


def test_preview_concurrency_replay_and_receipt_validation(endpoint):
    state, service, account, category, transaction, exchange = endpoint
    state["preview_fault"] = "revision"
    with pytest.raises(console.CommandError, match="during name resolution"):
        execute(endpoint, args())
    state["preview_fault"] = None
    with pytest.raises(console.CommandError, match="Preview revision is stale"):
        execute(endpoint, args(dry_run=True, revision="old"))
    preview = execute(endpoint, args(dry_run=True))
    assert preview["dryRun"] and not state["posts"]
    saved = execute(endpoint, args(batch="group", agent="Codex"))
    options = args(
        revision=saved["expectedRevision"],
        request_id=saved["requestId"],
        batch="group",
        agent="Codex",
    )
    assert execute(endpoint, options)["data"] == saved["data"]
    assert execute(endpoint, options)["replayed"]
    for fault in ("none", "count", "ids", "missing"):
        state["fault"] = fault
        with pytest.raises(console.CommandError, match="not confirmed"):
            execute(endpoint, options)
    with pytest.raises(console.CommandError, match="1–100"):
        execute(endpoint, args(), [])
    state["fault"] = None
    unchanged = execute(endpoint, args(), [{"transactionId": transaction, "note": "exact"}])
    assert unchanged["data"]["changed"] == 0
    assert "unchanged" in bulk.bulk_table(unchanged)
    assert "note:" in bulk.bulk_table(saved)
    assert "Read-only preview" in bulk.bulk_table(preview)


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b'{"edits":[],"other":1}',
        b'{"edits":0}',
        b'{"edits":[],"edits":[]}',
        b"x" * (2 * 1024 * 1024 + 1),
    ],
)
def test_invalid_files(raw, tmp_path):
    path = tmp_path / "changes.json"
    path.write_bytes(raw)
    with pytest.raises(console.CommandError, match="Cannot read bulk edits"):
        bulk.read_edits(str(path), console.CommandError)


def test_file_stdin_and_resolution(endpoint, tmp_path, monkeypatch):
    state, service, account, category, transaction, exchange = endpoint
    value = {"edits": [{"transactionId": transaction, "category": "家族 / 食料品"}]}
    path = tmp_path / "changes.json"
    path.write_text(json.dumps(value))
    assert bulk.read_edits(str(path), console.CommandError) == value["edits"]
    monkeypatch.setattr(
        bulk.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(value).encode()))
    )
    assert bulk.read_edits("-", console.CommandError) == value["edits"]
    with pytest.raises(console.CommandError):
        bulk.read_edits(str(path / "missing"), console.CommandError)
    options = args(file=str(path), agent="Codex")
    result = bulk.execute_transactions(
        None, "budget", "/budget", options, exchange, console.CommandError
    )
    assert result["data"]["changes"][0]["after"]["categoryAgent"] == "Codex"
    for bad in ([None], [{"category": None}], [{"category": "x", "categoryId": None}]):
        with pytest.raises(console.CommandError):
            bulk.resolve_categories(
                None, "budget", "/budget", bad, options, exchange, console.CommandError
            )
    options = args(
        filter="categorize", transaction_id=[transaction], clear=True, needs_review=False
    )
    assert (
        bulk.execute_transactions(
            None, "budget", "/budget", options, exchange, console.CommandError
        )["data"]["changes"][0]["after"]["categoryId"]
        is None
    )
    options = args(
        filter="categorize",
        transaction_id=[transaction],
        clear=False,
        category=category,
        needs_review=True,
    )
    assert not bulk.execute_transactions(
        None, "budget", "/budget", options, exchange, console.CommandError
    )["data"]["changes"][0]["after"]["reviewed"]
    options = args(
        account=["🍏旅行"], type="credit", subtype="credit_card", on_budget=None, dry_run=True
    )
    result = bulk.execute_accounts(
        None, "budget", "/budget", options, exchange, console.CommandError
    )
    assert not result["data"]["changes"][0]["after"]["onBudget"]
    options.on_budget = True
    options.subtype = None
    assert bulk.execute_accounts(
        None, "budget", "/budget", options, exchange, console.CommandError
    )["data"]["changes"][0]["after"]["onBudget"]


def test_offline_parser_discovery():
    for parts in ([], ['Fictional','trnx'], ['Fictional','account'], ['Fictional','trnx','categorize'], ['Fictional','trnx','edit-many'], ['Fictional','account','classify']):
        with pytest.raises(SystemExit) as result:
            console.main([*parts,'--help'])
        assert result.value.code == 0
