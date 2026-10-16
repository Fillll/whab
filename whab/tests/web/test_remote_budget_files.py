"""Connection references stay offline until a user reads or changes remote data."""

import base64
import io
import json
import zipfile
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from whab_client import plugin as client_plugin

from whab.app import create_app
from whab.budgets.container import decode_container, encode_container
from whab.budgets.credentials import encrypt_credentials
from whab.budgets.document import new_manifest
from whab.config import Settings

BASE = "https://127.0.0.1:5173"
ZIP = {"content-type": "application/zip"}
TOKEN = "synthetic-bound-device-token-" + "x" * 32
PASSWORD = "synthetic password for budgets"
PROFILE = {"CLIENT_URL": "https://budget.example", "CLIENT_BUDGET_ID": "remote-household", "CLIENT_TOKEN": TOKEN}


def configuration(home):
    return Settings(
        home=home, port=5173, plaid_env="sandbox", plaid_client_id="", plaid_secret="",
        encryption_key="", allow_live=False, webhook_url="", redirect_uri="", plugins=(), ui="",
    )


def password_headers(password=PASSWORD):
    return {
        "X-Budget-Password": base64.b64encode(password.encode()).decode(),
        "X-Budget-Password-Encoding": "base64",
    }


def remote_file(*, password=None, manifest=None, files=None, profile=PROFILE):
    manifest = manifest or new_manifest("Remote household", storage="client")
    files = files or {"plugins/client/connection.json": json.dumps({
        "formatVersion": 1, "url": PROFILE["CLIENT_URL"], "budgetId": PROFILE["CLIENT_BUDGET_ID"],
    }).encode()}
    vault = encrypt_credentials(manifest["id"], {"client": profile}, password) if password else None
    return encode_container(manifest, files, vault)


@pytest.fixture
def remote(monkeypatch):
    for key in PROFILE:
        monkeypatch.delenv(key, raising=False)
    constructor = client_plugin.RemoteStore
    requests = []
    opened = []

    def request(request):
        requests.append(request)
        return httpx.Response(200, json={
            "budgetId": PROFILE["CLIENT_BUDGET_ID"], "revision": "advanced-after-file-save",
            "data": {"accounts": [], "latest": True},
        })

    def bound(url, token, budget_id):
        opened.append((url, token, budget_id))
        return constructor(url, token, budget_id, transport=httpx.MockTransport(request))

    monkeypatch.setattr(client_plugin, "RemoteStore", bound)
    return requests, opened


def device_profile(monkeypatch, **changes):
    for key, value in {**PROFILE, **changes}.items():
        monkeypatch.setenv(key, value)


def test_open_save_download_and_copy_reference_do_not_fetch_remote_ledger(tmp_path, monkeypatch, remote):
    requests, opened = remote
    device_profile(monkeypatch)
    with TestClient(create_app(settings=configuration(tmp_path)), base_url=BASE) as client:
        response = client.post("/api/budget/open", content=remote_file(), headers=ZIP)
        assert response.status_code == 200, response.text
        assert len(opened) == 1 and requests == []
        assert client.post("/api/budget/save", json={}).status_code == 200
        assert client.post("/api/budget/copies", json={
            "name": "Reference backup", "path": str(tmp_path / "reference-backup.whab"),
        }).status_code == 200
        downloaded = client.get("/api/budget/export")
        assert downloaded.status_code == 200 and requests == []
        data = decode_container(downloaded.content)
        assert data.legacy_ledger is None and data.credential_blob is None
        assert data.files.keys() == {"plugins/client/connection.json"}
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            assert all(not name.endswith(("ledger.json", ".sqlite")) for name in archive.namelist())
            assert all(TOKEN.encode() not in archive.read(name) for name in archive.namelist())
        assert client.get("/api/workspace?workspace=personal").json()["latest"] is True
        assert len(requests) == 1


@pytest.mark.parametrize("machine", [
    {"CLIENT_TOKEN": TOKEN},
    {**PROFILE, "CLIENT_URL": "https://other.example"},
    {**PROFILE, "CLIENT_BUDGET_ID": "other-household"},
])
def test_imported_connection_cannot_borrow_unbound_machine_credentials(tmp_path, monkeypatch, remote, machine):
    requests, opened = remote
    for key, value in machine.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app(settings=configuration(tmp_path)), base_url=BASE) as client:
        response = client.post("/api/budget/open", content=remote_file(), headers=ZIP)
        assert response.status_code == 400 and "exact server and budget" in response.text
        assert requests == [] and opened == []


@pytest.mark.parametrize("origin", ["https://budget.example/", "https://BUDGET.EXAMPLE:443/"])
def test_matching_device_origin_normalizes_trailing_slash_case_and_default_port(tmp_path, monkeypatch, remote, origin):
    requests, opened = remote
    device_profile(monkeypatch, CLIENT_URL=origin)
    with TestClient(create_app(settings=configuration(tmp_path)), base_url=BASE) as client:
        response = client.post("/api/budget/open", content=remote_file(), headers=ZIP)
        assert response.status_code == 200, response.text
        assert opened == [(PROFILE["CLIENT_URL"], TOKEN, PROFILE["CLIENT_BUDGET_ID"])] and requests == []


def test_matching_unlocked_origin_normalizes_trailing_slash(tmp_path, remote):
    requests, opened = remote
    raw = remote_file(password=PASSWORD, profile={**PROFILE, "CLIENT_URL": PROFILE["CLIENT_URL"] + "/"})
    with TestClient(create_app(settings=configuration(tmp_path)), base_url=BASE) as client:
        response = client.post("/api/budget/open", content=raw, headers={**ZIP, **password_headers()})
        assert response.status_code == 200, response.text
        assert opened == [(PROFILE["CLIENT_URL"], TOKEN, PROFILE["CLIENT_BUDGET_ID"])] and requests == []


def test_encrypted_profile_unlocks_matching_connection_without_machine_credentials(tmp_path, remote):
    requests, opened = remote
    raw = remote_file(password=PASSWORD)
    with TestClient(create_app(settings=configuration(tmp_path)), base_url=BASE) as client:
        assert client.post("/api/budget/open", content=raw, headers=ZIP).status_code == 400
        assert client.post("/api/budget/open", content=raw, headers={**ZIP, **password_headers("incorrect password")}).status_code == 400
        assert opened == []
        response = client.post("/api/budget/open", content=raw, headers={**ZIP, **password_headers()})
        assert response.status_code == 200, response.text
        assert opened == [(PROFILE["CLIENT_URL"], TOKEN, PROFILE["CLIENT_BUDGET_ID"])] and requests == []
        assert decode_container(client.get("/api/budget/export").content).credential_blob is None
        assert client.post("/api/budget/save", json={}).status_code == 200
        encrypted = client.get("/api/budget/export?include_credentials=true", headers=password_headers())
        assert encrypted.status_code == 200, encrypted.text
        assert decode_container(encrypted.content, PASSWORD).credentials == {"client": PROFILE}
        assert requests == []


@pytest.mark.parametrize("change", [
    {"url": "https://untrusted.example"}, {"budgetId": "other-household"},
])
def test_tampered_public_target_cannot_redirect_unlocked_token(tmp_path, remote, change):
    requests, opened = remote
    original = decode_container(remote_file(password=PASSWORD), PASSWORD)
    changed = {"plugins/client/connection.json": json.dumps({
        "formatVersion": 1, "url": PROFILE["CLIENT_URL"], "budgetId": PROFILE["CLIENT_BUDGET_ID"], **change,
    }).encode()}
    raw = encode_container(original.manifest, changed, original.credential_blob)
    with TestClient(create_app(settings=configuration(tmp_path)), base_url=BASE) as client:
        response = client.post("/api/budget/open", content=raw, headers={**ZIP, **password_headers()})
        assert response.status_code == 400 and "different server or budget" in response.text
        assert requests == [] and opened == []


def test_credential_export_is_explicit_and_requires_password(tmp_path, monkeypatch, remote):
    requests, _ = remote
    device_profile(monkeypatch)
    with TestClient(create_app(settings=configuration(tmp_path)), base_url=BASE) as client:
        assert client.post("/api/budget/open", content=remote_file(), headers=ZIP).status_code == 200
        assert client.get("/api/budget/export?include_credentials=true").status_code == 400
        raw = client.get("/api/budget/export?include_credentials=true", headers=password_headers()).content
        assert decode_container(raw, PASSWORD).credentials == {"client": PROFILE}
        assert requests == []


def test_open_as_copy_reencrypts_vault_for_new_budget_identity(tmp_path, remote):
    requests, _ = remote
    raw = remote_file(password=PASSWORD)
    original = decode_container(raw, PASSWORD)
    with TestClient(create_app(settings=configuration(tmp_path)), base_url=BASE) as client:
        response = client.post("/api/budget/open?as_copy=true", content=raw, headers={**ZIP, **password_headers()})
        assert response.status_code == 200, response.text
        assert response.json()["id"] != original.manifest["id"]
        exported = client.get("/api/budget/export?include_credentials=true", headers=password_headers()).content
        copied = decode_container(exported, PASSWORD)
        assert copied.credentials == original.credentials
        assert copied.manifest["id"] != original.manifest["id"] and requests == []


def test_unknown_plugin_payload_round_trips_unchanged_with_remote_reference(tmp_path, monkeypatch, remote):
    requests, _ = remote
    device_profile(monkeypatch)
    manifest = new_manifest("Remote plus extra", plugins=("unavailable-fixture",), storage="client")
    files = decode_container(remote_file()).files
    files["plugins/unavailable-fixture/custom.bin"] = b"opaque fixture bytes\x00\x01\xff"
    raw = remote_file(manifest=manifest, files=files)
    with TestClient(create_app(settings=configuration(tmp_path)), base_url=BASE) as client:
        response = client.post("/api/budget/open", content=raw, headers=ZIP)
        assert response.status_code == 200, response.text
        assert client.get("/api/budget").json()["missingPlugins"] == ["unavailable-fixture"]
        assert decode_container(client.get("/api/budget/export").content).files == files
        assert requests == []


def test_cli_password_field_opens_encrypted_reference_without_exposing_secret(tmp_path, remote):
    requests, _ = remote
    path = tmp_path / "encrypted-reference.whab"
    path.write_bytes(remote_file(password=PASSWORD))
    settings = replace(configuration(tmp_path / "runtime"), budget_path=path, budget_password=PASSWORD)
    assert PASSWORD not in repr(settings)
    with TestClient(create_app(settings=settings), base_url=BASE) as client:
        assert client.get("/api/budget").json()["storage"] == "client"
        assert requests == []


def test_recent_encrypted_reference_requires_unlock_after_restart(tmp_path, remote):
    requests, _ = remote
    raw = remote_file(password=PASSWORD)
    budget_id = decode_container(raw, PASSWORD).manifest["id"]
    with TestClient(create_app(settings=configuration(tmp_path)), base_url=BASE) as client:
        assert client.post("/api/budget/open", content=raw, headers={**ZIP, **password_headers()}).status_code == 200
        assert client.post("/api/budget/save", json={}).status_code == 200
    index = (tmp_path / "budgets" / "index-sandbox.json").read_text()
    assert PASSWORD not in index and TOKEN not in index
    with TestClient(create_app(settings=configuration(tmp_path)), base_url=BASE) as client:
        assert client.post(f"/api/budgets/{budget_id}/activate", json={}).status_code == 400
        response = client.post(f"/api/budgets/{budget_id}/activate", json={}, headers=password_headers())
        assert response.status_code == 200, response.text
        assert client.get("/api/budget").json()["id"] == budget_id
        assert requests == []


def test_active_encrypted_budget_reopen_accepts_same_vault_and_rejects_changed_or_missing_vault(tmp_path, remote):
    requests, opened = remote
    original_raw = remote_file(password=PASSWORD)
    original = decode_container(original_raw, PASSWORD)
    rotated_password = "synthetic rotated budget password"
    rotated = remote_file(password=rotated_password, manifest=original.manifest, files=original.files)
    plain = encode_container(original.manifest, original.files)
    with TestClient(create_app(settings=configuration(tmp_path)), base_url=BASE) as client:
        assert client.post("/api/budget/open", content=original_raw, headers={**ZIP, **password_headers()}).status_code == 200
        assert client.post("/api/budget/open", content=original_raw, headers={**ZIP, **password_headers()}).status_code == 200
        assert len(opened) == 1
        for raw, headers in ((rotated, password_headers(rotated_password)), (plain, {})):
            response = client.post("/api/budget/open", content=raw, headers={**ZIP, **headers})
            assert response.status_code == 400 and "separate budget" in response.text
        path = tmp_path / "budgets" / original.manifest["id"] / "sandbox" / "opened.whab"
        assert decode_container(path.read_bytes(), PASSWORD).credential_blob == original.credential_blob
        assert len(opened) == 1 and requests == []


def test_saved_encrypted_revision_is_not_overwritten_by_rekeyed_or_uncredentialed_upload(tmp_path, remote):
    requests, _ = remote
    original_raw = remote_file(password=PASSWORD)
    original = decode_container(original_raw, PASSWORD)
    rotated_password = "synthetic rotated budget password"
    rotated = remote_file(password=rotated_password, manifest=original.manifest, files=original.files)
    plain = encode_container(original.manifest, original.files)
    with TestClient(create_app(settings=configuration(tmp_path)), base_url=BASE) as client:
        assert client.post("/api/budget/open", content=original_raw, headers={**ZIP, **password_headers()}).status_code == 200
    with TestClient(create_app(settings=configuration(tmp_path)), base_url=BASE) as client:
        for raw, headers in ((rotated, password_headers(rotated_password)), (plain, {})):
            response = client.post("/api/budget/open", content=raw, headers={**ZIP, **headers})
            assert response.status_code == 400 and "separate budget" in response.text
        path = tmp_path / "budgets" / original.manifest["id"] / "sandbox" / "opened.whab"
        assert decode_container(path.read_bytes(), PASSWORD).credential_blob == original.credential_blob
        assert requests == []


@pytest.mark.parametrize("secret,encoded", [
    (PASSWORD, False), ("synthetic café budget 🔒", True),
])
def test_password_headers_support_ascii_and_explicit_utf8_base64(tmp_path, remote, secret, encoded):
    requests, _ = remote
    headers = password_headers(secret) if encoded else {"X-Budget-Password": secret}
    with TestClient(create_app(settings=configuration(tmp_path)), base_url=BASE) as client:
        response = client.post("/api/budget/open", content=remote_file(password=secret), headers={**ZIP, **headers})
        assert response.status_code == 200, response.text
        assert requests == []


@pytest.mark.parametrize("headers", [
    {"X-Budget-Password-Encoding": "base64"},
    {"X-Budget-Password": "bad!", "X-Budget-Password-Encoding": "base64"},
    {"X-Budget-Password": "ascii", "X-Budget-Password-Encoding": "unknown"},
    {"X-Budget-Password": "/w==", "X-Budget-Password-Encoding": "base64"},
    {"X-Budget-Password": "x" * 8193},
])
def test_invalid_password_headers_fail_before_opening_runtime(tmp_path, headers, remote):
    requests, opened = remote
    with TestClient(create_app(settings=configuration(tmp_path)), base_url=BASE) as client:
        assert client.post("/api/budget/open", content=remote_file(), headers={**ZIP, **headers}).status_code == 400
        assert opened == [] and requests == []
