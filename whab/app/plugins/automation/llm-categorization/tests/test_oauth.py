"""Real OIDC signatures and one-time loopback callbacks; no OpenAI calls."""

import http.client
import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from whab_plugin_llm_categorization import oauth
from whab_plugin_llm_categorization.private import Vault

from whab.plugins import StoreError


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def attempt(context):
    value = oauth.SignIn(Vault(context))
    try:
        yield value
    finally:
        value.close()


def responses(monkeypatch, attempt, key, **claims):
    now = int(time.time())
    identity = {"sub": "fictional-person", "aud": "issued-client", "iss": oauth.AUTH,
                "iat": now, "exp": now + 3600, "nonce": attempt.nonce, **claims}
    token = jwt.encode(identity, key, algorithm="RS256", headers={"kid": "test-key"})
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public["kid"] = "test-key"
    issued = {"access_token": "fictional-access", "refresh_token": "fictional-refresh", "id_token": token,
              "token_type": "Bearer", "expires_in": 3600, "scope": oauth.SCOPE}
    calls = []
    def request(method, url, **values):
        calls.append((method, url, values))
        return {"keys": [public]} if "jwks" in url else issued
    monkeypatch.setattr(oauth, "request", request)
    return calls, issued


def callback(attempt, query, *, path="/auth/callback", host=None):
    connection = http.client.HTTPConnection("127.0.0.1", attempt.server.server_port, timeout=3)
    try:
        connection.request("GET", path + "?" + query, headers={"Host": host or attempt.host})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_native_sign_in_verifies_real_signature_and_stores_no_tokens_in_url(attempt, signing_key, monkeypatch):
    params = parse_qs(urlsplit(attempt.url).query)
    assert params["client_id"] == ["dynamic_agent_client"]
    assert params["agent_name_hint"] == ["WHAB"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["resource"] == [oauth.RESOURCE]
    assert params["redirect_uri"] == [attempt.redirect]
    assert attempt.saved["host_id"].startswith("urn:uuid:")
    calls, _ = responses(monkeypatch, attempt, signing_key)
    for query, path, host in [("state=wrong", "/auth/callback", None),
                              (urlencode({"state": attempt.state}), "/wrong", None),
                              (urlencode({"state": attempt.state}), "/auth/callback", "attacker.example"),
                              ("state=" + attempt.state + "&state=duplicate", "/auth/callback", None)]:
        assert callback(attempt, query, path=path, host=host)[0] == 400
        assert attempt.status == "pending"
    query = urlencode({"state": attempt.state, "code": "fictional-code", "client_id": "issued-client"})
    status, body = callback(attempt, query)
    assert status == 200 and b"Connected" in body
    attempt.thread.join(timeout=2)
    assert not attempt.thread.is_alive()
    saved = attempt.vault.read("chatgpt")
    assert saved["subject"] == "fictional-person" and saved["client_id"] == "issued-client"
    assert calls[0][2]["data"]["code_verifier"] == attempt.verifier
    assert calls[0][2]["data"]["redirect_uri"] == attempt.redirect
    assert "fictional-access" not in attempt.url
    returning = oauth.SignIn(attempt.vault)
    try:
        params = parse_qs(urlsplit(returning.url).query)
        assert params["client_id"] == ["issued-client"] and "agent_name_hint" not in params
        assert params["ext_agent_host_id"] == [saved["host_id"]]
        responses(monkeypatch, returning, signing_key)
        returning.finish({"code": "another-code"})
    finally:
        returning.close()


@pytest.mark.parametrize("values", [{"error": "access_denied"}, {}, {"code": "x"},
                                   {"code": "x", "client_id": "dynamic_agent_client"}])
def test_incomplete_registration_does_not_exchange_or_save_tokens(attempt, values):
    with pytest.raises(StoreError):
        attempt.finish(values)
    assert not attempt.vault.read("chatgpt").get("access_token")


def test_returning_client_cannot_be_replaced(attempt):
    attempt.saved["client_id"] = "issued-client"
    with pytest.raises(StoreError):
        attempt.finish({"code": "x", "client_id": "other-client"})


@pytest.mark.parametrize("claims", [{"nonce": "wrong"}, {"sub": ""}, {"iss": "https://attacker.example"},
                                   {"aud": "other-client"}, {"exp": 1}])
def test_invalid_oidc_claims_are_rejected(attempt, signing_key, monkeypatch, claims):
    responses(monkeypatch, attempt, signing_key, **claims)
    with pytest.raises((StoreError, jwt.InvalidTokenError)):
        attempt.finish({"code": "x", "client_id": "issued-client"})
    assert not attempt.vault.read("chatgpt").get("access_token")


def test_returning_subject_must_match_and_failed_callback_is_safe(attempt, signing_key, monkeypatch):
    responses(monkeypatch, attempt, signing_key)
    attempt.saved["subject"] = "another-person"
    query = urlencode({"state": attempt.state, "code": "x", "client_id": "issued-client"})
    status, body = callback(attempt, query)
    assert status == 200 and b"could not complete" in body
    assert attempt.status == "failed" and attempt.error
    assert b"fictional-" not in body


def test_signature_tampering_is_rejected(attempt, signing_key, monkeypatch):
    _, issued = responses(monkeypatch, attempt, signing_key)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issued["id_token"] = jwt.encode({"sub": "attacker"}, other, algorithm="RS256", headers={"kid": "test-key"})
    with pytest.raises(jwt.InvalidSignatureError):
        attempt.finish({"code": "x", "client_id": "issued-client"})


def test_canceled_sign_in_cannot_restore_credentials(attempt, signing_key, monkeypatch):
    responses(monkeypatch, attempt, signing_key)
    attempt.close()
    with pytest.raises(StoreError, match="canceled or expired"):
        attempt.finish({"code": "x", "client_id": "issued-client"})
    assert not attempt.vault.read("chatgpt").get("access_token")


def test_receiver_expires_without_retaining_a_listener(attempt):
    attempt.deadline = 0
    attempt.thread.join(timeout=2)
    assert not attempt.thread.is_alive()
    assert attempt.status == "failed" and "expired" in attempt.error


@pytest.mark.parametrize("change", [{"scope": "openid"}, {"access_token": ""}, {"token_type": "other"},
                                   {"expires_in": -1}, {"expires_in": True}, {"expires_in": 100000},
                                   {"expires_in": "3600"}])
def test_plan_permission_and_token_expiry_are_required(change):
    with pytest.raises(StoreError):
        oauth.token_record({"scope": oauth.SCOPE, "access_token": "x", "token_type": "Bearer", "expires_in": 3600, **change}, {})


def test_refresh_rotates_tokens_once_and_disconnect_revokes(context, monkeypatch):
    vault = Vault(context)
    with pytest.raises(StoreError, match="Continue with ChatGPT"):
        oauth.access_token(vault)
    saved = {"access_token": "old", "refresh_token": "old-refresh", "client_id": "issued-client",
             "subject": "fictional-person", "host_id": "urn:uuid:fictional", "expires_at": 1,
             "scopes": oauth.SCOPE.split()}
    vault.write("chatgpt", saved)
    calls = []
    def request(method, url, **values):
        calls.append((url, values))
        return {"access_token": "new", "refresh_token": "new-refresh", "token_type": "Bearer", "expires_in": 3600}
    monkeypatch.setattr(oauth, "request", request)
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert list(workers.map(lambda _: oauth.access_token(vault), range(2))) == ["new", "new"]
    assert len(calls) == 1
    assert calls[0][1]["data"]["client_id"] == "issued-client"
    assert "scope" not in calls[0][1]["data"]
    assert vault.read("chatgpt")["refresh_token"] == "new-refresh"
    assert oauth.disconnect(vault)
    assert calls[-1][1]["data"]["token"] == "new-refresh"
    assert set(vault.read("chatgpt")) == {"host_id", "subject", "client_id"}
    assert oauth.disconnect(vault)
    vault.write("chatgpt", {**saved, "refresh_token": ""})
    with pytest.raises(StoreError, match="expired"):
        oauth.access_token(vault)
    vault.write("chatgpt", saved)
    def failed(*args, **kwargs):
        raise StoreError("Fictional provider outage")
    monkeypatch.setattr(oauth, "request", failed)
    assert oauth.disconnect(vault) is False
    assert "access_token" not in vault.read("chatgpt")
