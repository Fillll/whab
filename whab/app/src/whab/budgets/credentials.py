"""Opt-in remote client credentials, encrypted independently of budget data."""

import base64
import binascii
import json
import os
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from whab.domain.errors import StoreError

MAX_CREDENTIAL_BYTES = 64 * 1024
_FIELDS = {"CLIENT_URL", "CLIENT_TOKEN", "CLIENT_BUDGET_ID"}


def _aad(budget_id):
    try:
        if type(budget_id) is not str or str(UUID(budget_id)) != budget_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise StoreError("Remote credentials require a valid budget identity.") from None
    return b"WHAB remote client credentials v1\x00" + budget_id.encode("ascii")


def _password(value, *, exporting=False):
    if type(value) is not str or not value or len(value) > 1024:
        raise StoreError("Enter the budget password to unlock its remote credential.")
    if exporting and len(value) < 12:
        raise StoreError("Use a budget password of at least 12 characters (at most 1,024).")
    try:
        return value.encode("utf-8")
    except UnicodeError:
        raise StoreError("The budget password must contain valid Unicode characters.") from None


def validate_credentials(credentials):
    """Accept one complete client profile; never accept bank-provider secrets."""
    try:
        if type(credentials) is not dict or credentials.keys() != {"client"}:
            raise ValueError()
        profile = credentials["client"]
        if type(profile) is not dict or profile.keys() != _FIELDS:
            raise ValueError()
        limits = {"CLIENT_URL": 2048, "CLIENT_TOKEN": 4096, "CLIENT_BUDGET_ID": 200}
        for key, value in profile.items():
            if type(value) is not str or not value.strip() or len(value) > limits[key] or "\x00" in value:
                raise ValueError()
            value.encode("utf-8")
        if len(profile["CLIENT_TOKEN"]) < 32:
            raise ValueError()
        url = profile["CLIENT_URL"]
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment
            or any(character.isspace() or ord(character) < 32 for character in url)
        ):
            raise ValueError()
        parsed.port
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise StoreError("Remote credentials require a complete client connection profile.") from None
    return {"client": dict(profile)}


def _key(password, salt):
    return Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(password)


def encrypt_credentials(budget_id, credentials, password):
    """Encrypt an explicit client profile with fixed, bounded KDF parameters."""
    associated = _aad(budget_id)
    secret = _password(password, exporting=True)
    profile = validate_credentials(credentials)
    salt, nonce = os.urandom(16), os.urandom(12)
    plaintext = json.dumps(profile, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encrypted = AESGCM(_key(secret, salt)).encrypt(nonce, plaintext, associated)
    return json.dumps({
        "version": 1,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(encrypted).decode("ascii"),
    }, separators=(",", ":")).encode("ascii")


def _json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError()
            value[key] = item
        return value

    return json.loads(raw, object_pairs_hook=unique)


def decrypt_credentials(budget_id, blob, password):
    """Authenticate both the password and the budget identity before returning data."""
    associated = _aad(budget_id)
    secret = _password(password)
    try:
        if type(blob) is not bytes or not 1 <= len(blob) <= MAX_CREDENTIAL_BYTES:
            raise ValueError()
        envelope = _json(blob)
        if (
            type(envelope) is not dict or envelope.keys() != {"version", "salt", "nonce", "ciphertext"}
            or type(envelope["version"]) is not int or envelope["version"] != 1
        ):
            raise ValueError()
        salt, nonce, ciphertext = (
            base64.b64decode(envelope[key], validate=True)
            for key in ("salt", "nonce", "ciphertext")
        )
        if len(salt) != 16 or len(nonce) != 12 or not 16 <= len(ciphertext) <= MAX_CREDENTIAL_BYTES:
            raise ValueError()
        plaintext = AESGCM(_key(secret, salt)).decrypt(nonce, ciphertext, associated)
        return validate_credentials(_json(plaintext))
    except (ValueError, TypeError, KeyError, UnicodeError, InvalidTag, binascii.Error, RecursionError, StoreError):
        raise StoreError("The budget password is incorrect or its remote credential is damaged.") from None
