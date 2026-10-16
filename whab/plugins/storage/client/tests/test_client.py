"""Remote compatibility preserves retry identity and never forwards bank secrets."""

from pathlib import Path

import httpx
import pytest
from whab_client.plugin import RemoteStore, create_storage, plugin

from whab.plugins import StorageContext, StoreError

TOKEN = "synthetic-client-token-" + "b" * 32


def body(data=None, revision="r1", budget="budget-1"):
    return {"budgetId": budget, "revision": revision, "data": data}


def make(handler):
    return RemoteStore("https://budget.example", TOKEN, "budget-1", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("url", ["http://budget.example", "https://u:p@budget.example", "https://budget.example/path", "https://budget.example?x=1", "https://budget.example#x", "https://"])
def test_invalid_origin(url):
    with pytest.raises(StoreError, match="HTTPS origin"):
        RemoteStore(url, TOKEN, "budget-1")


def test_configuration_descriptor_and_private_methods():
    assert plugin.id == "client" and plugin.storage is create_storage
    with pytest.raises(StoreError, match="CLIENT_TOKEN"):
        RemoteStore("https://budget.example", "short", "budget-1")
    for budget in ("", "a" * 201, "bad\x00id"):
        with pytest.raises(StoreError, match="CLIENT_BUDGET_ID"):
            RemoteStore("https://budget.example", TOKEN, budget)
    remote = create_storage(StorageContext(Path("/tmp"), "sandbox", {
        "CLIENT_URL": "https://budget.example", "CLIENT_TOKEN": TOKEN,
        "CLIENT_BUDGET_ID": "budget-1",
    }))
    assert remote.items() == []
    for method in ("get_item", "save_item", "set_item_status", "disconnect_item", "apply_sync"):
        with pytest.raises(StoreError, match="budget server"):
            getattr(remote, method)("anything")
    with pytest.raises(StoreError, match="manual transactions"):
        remote.add_transaction("personal", {}, source="plaid")
    remote.close()


def test_public_queries_and_commands():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer " + TOKEN
        return httpx.Response(200, json=body("result"))

    remote = make(handler)
    assert remote.accounts("demo") == "result"
    assert remote.categories("demo") == "result"
    assert remote.transactions("personal", "2026-10") == "result"
    assert requests[-1].url.params["month"] == "2026-10"
    assert remote.workspace("personal") == "result"
    assert remote.add_account("personal", {"name": "cash"}) == "result"
    assert remote.add_category("personal", {"name": "food"}) == "result"
    assert remote.add_transaction("personal", {"merchant": "grocer"}) == "result"
    remote.edit_transaction("personal", "tx")
    remote.edit_transaction("personal", "tx", category_id=None, reviewed=False)
    remote.assign("personal", "cat", "2026-10", 100)
    remote.close()


def test_initial_revision_and_uncertain_retry_identity():
    import json

    calls = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=body({}))
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            raise httpx.ReadTimeout("sensitive upstream content")
        return httpx.Response(200, json=body("account", "r2"))

    remote = make(handler)
    payload = {"name": "cash"}
    with pytest.raises(StoreError, match="result is unknown"):
        remote.add_account("personal", payload)
    with pytest.raises(StoreError, match="previous remote change"):
        remote.add_account("personal", {"name": "different"})
    assert remote.add_account("personal", payload) == "account"
    assert calls[0] == calls[1]
    assert calls[0]["expected_revision"] == "r1"


def test_query_connection_failure():
    def handler(request):
        raise httpx.ConnectError("private-address")

    with pytest.raises(StoreError, match="connection failed"):
        make(handler).workspace("personal")


def test_portable_export_validation(store):
    snapshot = store.export_budget()
    remote = make(lambda request: httpx.Response(200, json=body(snapshot)))
    assert remote.export_budget() == snapshot


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 409, 503, 302])
def test_response_errors_and_pending_recovery(status):
    responses = [httpx.Response(200, json=body({})), httpx.Response(status, text="private-server-error")]
    remote = make(lambda request: responses.pop(0))
    with pytest.raises(StoreError) as error:
        remote.add_account("personal", {"name": "cash"})
    assert "private" not in str(error.value)
    assert (remote._pending is not None) == (status in {503, 302})
    if status == 409:
        assert remote._revision is None


@pytest.mark.parametrize("status", [400, 409])
def test_query_error_preserves_uncertain_command(status):
    remote = make(lambda request: httpx.Response(status))
    remote._pending = {"request_id": "uncertain"}
    with pytest.raises(StoreError):
        remote.accounts("personal")
    assert remote._pending == {"request_id": "uncertain"}


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="not json"),
    httpx.Response(200, json=[]),
    httpx.Response(200, json=body(budget="wrong")),
    httpx.Response(200, json=body(revision=12)),
    httpx.Response(200, json=body(revision="")),
    httpx.Response(200, json={"budgetId": "budget-1", "revision": "r1"}),
])
def test_malformed_responses(response):
    with pytest.raises(StoreError, match="invalid response"):
        make(lambda request: response).workspace("personal")
