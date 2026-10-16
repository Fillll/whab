"""Remote budget files carry a connection and never read or retain ledger data."""

import json
from pathlib import Path
from types import MappingProxyType

import httpx
import pytest
from whab_client.plugin import RemoteStore, connection_configuration, create_storage, plugin

from whab.plugins import StorageContext, StoreError

TOKEN = "synthetic-unlocked-token-" + "x" * 32
CONNECTION = {"formatVersion": 1, "url": "https://budget.example", "budgetId": "budget-1"}


def reference(value=CONNECTION):
    return {"connection.json": json.dumps(value).encode()}


def offline():
    def no_network(request):
        raise AssertionError("Budget files must not perform remote requests")

    return RemoteStore(
        "https://budget.example", TOKEN, "budget-1", transport=httpx.MockTransport(no_network),
    )


def test_connection_file_export_and_restore_are_offline_and_contain_no_credentials_or_ledger():
    remote = offline()
    files = remote.export_budget_files()
    assert files.keys() == {"connection.json"}
    assert json.loads(files["connection.json"]) == CONNECTION
    assert TOKEN.encode() not in files["connection.json"]
    assert remote.restore_budget_files("personal", files) is None
    assert remote.export_budget_files() == files
    remote.close()


def test_connection_descriptor_accepts_immutable_files_without_reading_any_ledger():
    public = connection_configuration(MappingProxyType(reference()))
    assert public == {"CLIENT_URL": CONNECTION["url"], "CLIENT_BUDGET_ID": CONNECTION["budgetId"]}
    assert "CLIENT_TOKEN" not in public
    assert plugin.budget_connection is connection_configuration


@pytest.mark.parametrize("existing", [False, True])
def test_budget_file_preparation_is_offline_even_for_existing_remote_state(existing):
    remote = offline()
    remote.prepare_budget_files("personal", reference(), existing=existing)
    assert remote.export_budget_files() == reference()


def test_server_may_advance_after_reference_export_without_invalidating_open():
    requests = []

    def live(request):
        requests.append(request)
        return httpx.Response(200, json={
            "budgetId": "budget-1", "revision": "new-server-revision",
            "data": {"changedAfterFileExport": True},
        })

    remote = RemoteStore(
        "https://budget.example", TOKEN, "budget-1", transport=httpx.MockTransport(live),
    )
    files = remote.export_budget_files()
    remote.restore_budget_files("personal", files)
    assert requests == []
    assert remote.workspace("personal") == {"changedAfterFileExport": True}
    assert len(requests) == 1 and "/queries/workspace" in requests[0].url.path


def test_https_origin_comparison_normalizes_case_default_port_and_trailing_slash():
    remote = offline()
    remote.restore_budget_files("personal", reference({**CONNECTION, "url": "https://BUDGET.EXAMPLE:443/"}))
    assert json.loads(remote.export_budget_files()["connection.json"])["url"] == CONNECTION["url"]


@pytest.mark.parametrize("change", [
    {"url": "https://untrusted.example"}, {"url": "https://budget.example:8443"},
    {"budgetId": "other-budget"},
])
def test_imported_reference_cannot_rebind_existing_machine_or_unlocked_token(change):
    remote = offline()
    with pytest.raises(StoreError, match="does not match"):
        remote.restore_budget_files("personal", reference({**CONNECTION, **change}))
    assert remote.export_budget_files() == reference()


def test_complete_host_configuration_supports_explicitly_unlocked_credentials():
    # The host supplies this token only after verifying a password-encrypted
    # credential package is bound to the same origin and remote budget ID.
    remote = create_storage(StorageContext(Path("/tmp"), "sandbox", {
        "CLIENT_URL": CONNECTION["url"], "CLIENT_BUDGET_ID": CONNECTION["budgetId"],
        "CLIENT_TOKEN": TOKEN,
    }))
    remote.restore_budget_files("personal", reference())
    assert TOKEN.encode() not in remote.export_budget_files()["connection.json"]
    remote.close()


@pytest.mark.parametrize("workspace", ["demo", "other", None])
def test_budget_files_require_personal_workspace(workspace):
    remote = offline()
    with pytest.raises(StoreError, match="personal workspace"):
        remote.export_budget_files(workspace)
    with pytest.raises(StoreError, match="personal workspace"):
        remote.restore_budget_files(workspace, reference())


@pytest.mark.parametrize("files", [None, [], {}, {"other.json": b"{}"}, {"connection.json": b"{}", "ledger.json": b"{}"}])
def test_file_adapter_rejects_missing_or_additional_payloads(files):
    with pytest.raises(StoreError, match="exactly one"):
        offline().restore_budget_files("personal", files)


@pytest.mark.parametrize("raw", [
    None, "{}", b"", b"x" * 32769, b"\xff", b"[]", b"{}",
    b'{"formatVersion":1,"formatVersion":1,"url":"https://budget.example","budgetId":"budget-1"}',
    json.dumps({**CONNECTION, "formatVersion": "1"}).encode(),
    json.dumps({**CONNECTION, "formatVersion": True}).encode(),
    json.dumps({**CONNECTION, "formatVersion": 2}).encode(),
    json.dumps({**CONNECTION, "token": TOKEN}).encode(),
])
def test_reference_requires_bounded_exact_versioned_json(raw):
    with pytest.raises(StoreError, match="supported remote budget connection"):
        offline().restore_budget_files("personal", {"connection.json": raw})


@pytest.mark.parametrize("url", [
    None, "x" * 4097, "https://budget.example\n", "https://[::1", "https://budget.example:bad",
    "https://budget.example:99999", "http://budget.example", "https://u:p@budget.example",
])
def test_imported_reference_requires_valid_https_origin(url):
    with pytest.raises(StoreError, match="HTTPS origin"):
        offline().restore_budget_files("personal", reference({**CONNECTION, "url": url}))


@pytest.mark.parametrize("budget", [None, "", "a" * 201, "bad\x00id"])
def test_imported_reference_requires_valid_budget_identifier(budget):
    with pytest.raises(StoreError, match="CLIENT_BUDGET_ID"):
        offline().restore_budget_files("personal", reference({**CONNECTION, "budgetId": budget}))
