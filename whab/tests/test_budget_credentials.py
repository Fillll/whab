"""Portable remote credentials require an explicit password and budget identity."""

import base64
import json
from uuid import uuid4

import pytest

from whab.budgets.credentials import decrypt_credentials, encrypt_credentials, validate_credentials
from whab.domain.errors import StoreError

PASSWORD = "fictional strong password 🔑"


def profile():
    return {"client": {
        "CLIENT_URL": "https://budget.example",
        "CLIENT_TOKEN": "fictional-client-token-for-unit-tests-only",
        "CLIENT_BUDGET_ID": "default",
    }}


def test_vault_roundtrip_is_randomized_and_contains_no_plaintext_credentials():
    budget_id = str(uuid4())
    first = encrypt_credentials(budget_id, profile(), PASSWORD)
    second = encrypt_credentials(budget_id, profile(), PASSWORD)
    assert first != second
    assert decrypt_credentials(budget_id, first, PASSWORD) == profile()
    assert decrypt_credentials(budget_id, second, PASSWORD) == profile()
    assert b"fictional-client-token" not in first and b"budget.example" not in first
    envelope = json.loads(first)
    assert envelope.keys() == {"version", "salt", "nonce", "ciphertext"}
    assert len(base64.b64decode(envelope["salt"])) == 16
    assert len(base64.b64decode(envelope["nonce"])) == 12


@pytest.mark.parametrize("password", [None, "", "incorrect password"])
def test_missing_or_wrong_password_has_safe_authored_error(password):
    budget_id = str(uuid4())
    encrypted = encrypt_credentials(budget_id, profile(), PASSWORD)
    with pytest.raises(StoreError) as error:
        decrypt_credentials(budget_id, encrypted, password)
    assert profile()["client"]["CLIENT_TOKEN"] not in str(error.value)
    assert PASSWORD not in str(error.value)


def test_encrypted_credentials_cannot_be_moved_to_another_budget_identity():
    encrypted = encrypt_credentials(str(uuid4()), profile(), PASSWORD)
    with pytest.raises(StoreError, match="incorrect or.*damaged"):
        decrypt_credentials(str(uuid4()), encrypted, PASSWORD)


@pytest.mark.parametrize("password", [None, b"not-text", "", "short", "x" * 1025, "invalid\ud800password"])
def test_export_password_is_bounded_valid_unicode(password):
    with pytest.raises(StoreError):
        encrypt_credentials(str(uuid4()), profile(), password)


@pytest.mark.parametrize("budget_id", [None, "", "not-a-uuid", 1, "ABCDEF12-3456-4789-ABCD-0123456789AB"])
def test_vault_requires_a_canonical_budget_uuid(budget_id):
    with pytest.raises(StoreError, match="identity"):
        encrypt_credentials(budget_id, profile(), PASSWORD)


@pytest.mark.parametrize("change", [
    {"CLIENT_URL": "http://budget.example"},
    {"CLIENT_URL": "https://user:password@budget.example"},
    {"CLIENT_URL": "https://budget.example/path"},
    {"CLIENT_URL": "https://budget.example?redirect=elsewhere"},
    {"CLIENT_URL": "https://budget.example#fragment"},
    {"CLIENT_URL": "https://budget.example:invalid"},
    {"CLIENT_URL": "https://budget. example"},
    {"CLIENT_TOKEN": "short"},
    {"CLIENT_TOKEN": "x" * 4097},
    {"CLIENT_BUDGET_ID": ""},
    {"CLIENT_BUDGET_ID": "x\x00private"},
    {"CLIENT_BUDGET_ID": "x" * 201},
])
def test_vault_rejects_incomplete_or_invalid_client_profiles(change):
    credentials = profile()
    credentials["client"].update(change)
    with pytest.raises(StoreError, match="complete client"):
        validate_credentials(credentials)


@pytest.mark.parametrize("credentials", [
    None, {}, {"plaid": {"PLAID_SECRET": "fictional"}},
    {"client": {}}, {"client": None},
    {"client": {"CLIENT_TOKEN": "x" * 32}},
    {"client": {**profile()["client"], "PLAID_SECRET": "fictional"}},
])
def test_vault_only_accepts_the_complete_client_profile(credentials):
    with pytest.raises(StoreError):
        validate_credentials(credentials)


def test_validation_returns_an_independent_profile():
    original = profile()
    validated = validate_credentials(original)
    validated["client"]["CLIENT_TOKEN"] = "changed"
    assert original == profile()


@pytest.mark.parametrize("blob", [b"", b"not json", b"[]", b"{}", b"x" * (64 * 1024 + 1)])
def test_malformed_or_oversized_vault_has_a_safe_error(blob):
    with pytest.raises(StoreError, match="incorrect or.*damaged"):
        decrypt_credentials(str(uuid4()), blob, PASSWORD)


def test_ciphertext_and_parameters_are_authenticated_or_rejected():
    budget_id = str(uuid4())
    encrypted = encrypt_credentials(budget_id, profile(), PASSWORD)
    for field, value in (
        ("version", True), ("version", 2), ("salt", "invalid-base64"),
        ("nonce", base64.b64encode(b"short").decode()),
        ("ciphertext", base64.b64encode(b"x" * 32).decode()),
        ("n", 2**30),
    ):
        envelope = json.loads(encrypted)
        envelope[field] = value
        with pytest.raises(StoreError, match="incorrect or.*damaged"):
            decrypt_credentials(budget_id, json.dumps(envelope).encode(), PASSWORD)
