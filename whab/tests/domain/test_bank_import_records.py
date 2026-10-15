"""Bank import artwork uses opaque public keys without provider-specific domain rules."""

import pytest

from whab.domain.errors import StoreError
from whab.domain.imports import annotated_transaction, validate_imported_transaction


def transaction(**extra):
    return {
        "id": "transaction", "accountId": "account", "date": "2026-10-01",
        "merchant": "Synthetic merchant", "amount": 1000, "suggestedCategoryId": "groceries",
        "pending": False, "source": "provider", "inferredTransfer": False,
        "pendingTransactionId": None, **extra,
    }


@pytest.mark.parametrize("key", [None, "A", "logo_0123-ABC", "a" * 200])
def test_merchant_logo_accepts_none_and_bounded_public_keys(key):
    imported = transaction(merchantLogo=key)
    validate_imported_transaction(imported)
    assert annotated_transaction(imported, {})["merchantLogo"] == key


def test_merchant_logo_defaults_to_none_and_does_not_come_from_user_annotations():
    assert annotated_transaction(transaction(), {"merchantLogo": "old-logo"})["merchantLogo"] is None


@pytest.mark.parametrize("key", [
    "", "a" * 201, "_logo", "-logo", "logo.png", "logo/one", "logo one", "logo\n",
    "logo\x00", "é", "https://example.test/logo.png", 1, True, [], {},
])
def test_merchant_logo_rejects_nonkeys_without_coercion(key):
    with pytest.raises(StoreError, match="Invalid normalized bank record"):
        validate_imported_transaction(transaction(merchantLogo=key))


def test_merchant_logo_rejects_custom_string_subclasses():
    class Key(str):
        pass

    with pytest.raises(StoreError, match="Invalid normalized bank record"):
        validate_imported_transaction(transaction(merchantLogo=Key("logo")))
