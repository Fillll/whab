"""Server-only Plaid REST client, encrypted tokens and verified webhooks."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import os
import re
import time
from collections.abc import Mapping
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from whab.domain.errors import StoreError
from whab.plugins import BankError, BankStore, WebhookResult

from .mapping import normalize_account, normalize_changes


class PlaidError(BankError):
    """Safe, intentionally authored Plaid error."""


class PlaidRemoteError(Exception):
    def __init__(self, code: str = ""):
        super().__init__("Plaid request could not complete.")
        self.code = code


def plaid_configuration(env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    environment = env.get("PLAID_ENV", "sandbox")
    missing = []
    if environment not in {"sandbox", "production"}:
        missing.append("PLAID_ENV")
    if not env.get("PLAID_CLIENT_ID"):
        missing.append("PLAID_CLIENT_ID")
    if not env.get("PLAID_SECRET"):
        missing.append("PLAID_SECRET")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", env.get("TOKEN_ENCRYPTION_KEY", "")):
        missing.append("TOKEN_ENCRYPTION_KEY")
    if environment == "production" and env.get("ALLOW_LIVE_PLAID") != "true":
        missing.append("ALLOW_LIVE_PLAID")
    return {
        "environment": "production" if environment == "production" else "sandbox",
        "liveEnabled": environment == "production" and env.get("ALLOW_LIVE_PLAID") == "true",
        "missingConfig": missing,
        "plaidConfigured": not missing,
    }


def _key(key_hex: str) -> bytes:
    if not isinstance(key_hex, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", key_hex):
        raise PlaidError("Plaid encryption key missing.")
    return bytes.fromhex(key_hex)


def encrypt_token(token: str, key_hex: str, item_id: str) -> str:
    nonce = os.urandom(12)
    encrypted = AESGCM(_key(key_hex)).encrypt(nonce, token.encode(), item_id.encode())
    ciphertext, tag = encrypted[:-16], encrypted[-16:]
    return ".".join(base64.b64encode(part).decode("ascii") for part in (nonce, tag, ciphertext))


def decrypt_token(encoded: str, key_hex: str, item_id: str) -> str:
    try:
        nonce, tag, ciphertext = [
            base64.b64decode(part, validate=True) for part in encoded.split(".")
        ]
        if len(nonce) != 12 or len(tag) != 16:
            raise ValueError
        return AESGCM(_key(key_hex)).decrypt(nonce, ciphertext + tag, item_id.encode()).decode()
    except Exception:
        raise PlaidError(
            "Stored bank connection could not be unlocked. Check the local encryption key."
        ) from None


def _base64url(value: str) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
        raise ValueError("Invalid encoding")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class PlaidService:
    def __init__(
        self, store: BankStore, env: dict | None = None, client: httpx.AsyncClient | None = None
    ):
        self.store = store
        self.env = dict(os.environ if env is None else env)
        self.config = plaid_configuration(self.env)
        self._owns_client = client is None
        self.client = (
            client
            if client is not None
            else httpx.AsyncClient(
                base_url=f"https://{self.config['environment']}.plaid.com",
                headers={
                    "PLAID-CLIENT-ID": self.env.get("PLAID_CLIENT_ID", ""),
                    "PLAID-SECRET": self.env.get("PLAID_SECRET", ""),
                },
                timeout=httpx.Timeout(30, connect=10),
                follow_redirects=False,
            )
        )
        self._locks: dict[str, asyncio.Lock] = {}
        self._keys: dict[str, dict] = {}
        self._queued: dict[str, dict] = {}

    async def close(self) -> None:
        tasks = [state["task"] for state in self._queued.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._owns_client:
            await self.client.aclose()

    def require_configured(self) -> None:
        if not self.config["plaidConfigured"]:
            raise PlaidError("Plaid is not configured. Check local environment settings.")

    async def _call(self, endpoint: str, payload: dict) -> dict:
        try:
            response = await self.client.post(endpoint, json=payload)
            data = response.json()
            if response.status_code != 200:
                code = data.get("error_code", "") if isinstance(data, dict) else ""
                raise PlaidRemoteError(
                    code if isinstance(code, str) and re.fullmatch(r"[A-Z_]{1,100}", code) else ""
                )
            if not isinstance(data, dict):
                raise PlaidRemoteError()
            return data
        except PlaidRemoteError:
            raise
        except Exception:
            raise PlaidRemoteError() from None

    def token(self, item_id: str) -> str:
        item = self.store.get_item(item_id)
        if not item or not item.get("token"):
            raise PlaidError("Connection not found.")
        return decrypt_token(item["token"], self.env.get("TOKEN_ENCRYPTION_KEY", ""), item_id)

    async def link_token(self, item_id: str | None = None) -> str:
        self.require_configured()
        payload = {
            # This opaque household identity stays stable through rebranding.
            "user": {"client_user_id": "ihab-local-household"},
            "client_name": "WHAB",
            "country_codes": ["US"],
            "language": "en",
        }
        if item_id:
            payload["access_token"] = self.token(item_id)
        else:
            payload.update(products=["transactions"], transactions={"days_requested": 730})
        webhook = self.env.get("PLAID_WEBHOOK_URL")
        if webhook:
            url = urlparse(webhook)
            if url.scheme != "https" or not url.hostname or url.username or url.password:
                raise PlaidError("Webhook URL must use HTTPS.")
            payload["webhook"] = webhook
        redirect = self.env.get("PLAID_REDIRECT_URI")
        if redirect:
            url = urlparse(redirect)
            sandbox_local = (
                self.config["environment"] == "sandbox"
                and url.scheme == "http"
                and url.hostname in {"localhost", "127.0.0.1"}
            )
            if (
                not url.hostname
                or url.username
                or url.password
                or (url.scheme != "https" and not sandbox_local)
            ):
                raise PlaidError("Invalid redirect URI.")
            payload["redirect_uri"] = redirect
        try:
            data = await self._call("/link/token/create", payload)
            return data["link_token"]
        except (PlaidRemoteError, KeyError):
            raise PlaidError("Bank connection setup could not complete. Try again.") from None

    async def exchange(self, public_token: str, institution_name: str | None = None) -> None:
        self.require_configured()
        try:
            data = await self._call("/item/public_token/exchange", {"public_token": public_token})
            item_id, token = data["item_id"], data["access_token"]
            self.store.save_item(
                item_id,
                encrypt_token(token, self.env["TOKEN_ENCRYPTION_KEY"], item_id),
                institution_name or "Connected institution",
            )
        except (PlaidRemoteError, KeyError):
            raise PlaidError("Bank connection could not complete. Try again.") from None
        try:
            await self.sync(item_id)
        except PlaidError:
            pass  # Connection is saved; the safe Item status tells the user to retry.

    async def sync(self, item_id: str | None = None) -> dict:
        self.require_configured()
        if not item_id:
            results = await asyncio.gather(*(self.sync(item["id"]) for item in self.store.items()))
            return {
                kind: sum(result[kind] for result in results)
                for kind in ("added", "modified", "removed")
            }
        async with self._locks.setdefault(item_id, asyncio.Lock()):
            try:
                token = self.token(item_id)
                original_cursor = self.store.get_item(item_id).get("cursor")
                attempt = 0
                while True:
                    cursor = original_cursor
                    changes = {kind: [] for kind in ("added", "modified", "removed")}
                    historical_complete = False
                    try:
                        for _ in range(2000):
                            payload = {"access_token": token, "count": 500}
                            if cursor:
                                payload["cursor"] = cursor
                            data = await self._call("/transactions/sync", payload)
                            for kind in changes:
                                changes[kind].extend(data[kind])
                            cursor = data["next_cursor"]
                            historical_complete = (
                                data.get("transactions_update_status")
                                == "HISTORICAL_UPDATE_COMPLETE"
                            )
                            if not data["has_more"]:
                                break
                        else:
                            raise PlaidRemoteError()
                        break
                    except PlaidRemoteError as error:
                        if (
                            error.code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"
                            and attempt < 2
                        ):
                            attempt += 1
                            continue
                        raise
                accounts = await self._call("/accounts/get", {"access_token": token})
                institution_name = self.store.get_item(item_id)["institutionName"]
                return self.store.apply_sync(
                    item_id,
                    normalize_changes(changes),
                    cursor,
                    [
                        normalize_account(account, item_id, institution_name)
                        for account in accounts["accounts"]
                    ],
                    historical_complete,
                    expected_cursor=original_cursor,
                )
            except Exception as error:
                reconnect = (
                    isinstance(error, PlaidRemoteError) and error.code == "ITEM_LOGIN_REQUIRED"
                )
                self.store.set_item_status(
                    item_id,
                    "reconnect" if reconnect else "error",
                    "Reconnect this institution through Plaid Link."
                    if reconnect
                    else "Sync could not complete. Try again.",
                )
                raise PlaidError(
                    "Bank connection needs reconnection."
                    if reconnect
                    else "Bank sync could not complete. Try again."
                ) from None

    async def disconnect(self, item_id: str) -> None:
        self.require_configured()
        async with self._locks.setdefault(item_id, asyncio.Lock()):
            try:
                await self._call("/item/remove", {"access_token": self.token(item_id)})
                self.store.disconnect_item(item_id)
            except (PlaidRemoteError, StoreError):
                raise PlaidError("Bank disconnection could not complete. Try again.") from None

    async def verify_webhook(self, raw_body: bytes, signed_jwt: str | None) -> bool:
        if not self.config["plaidConfigured"]:
            return False
        try:
            if not isinstance(signed_jwt, str) or len(signed_jwt) > 8192:
                return False
            header64, payload64, signature64 = signed_jwt.split(".")
            header = json.loads(_base64url(header64))
            if (
                not isinstance(header, dict)
                or header.get("alg") != "ES256"
                or not isinstance(header.get("kid"), str)
                or not 0 < len(header["kid"]) <= 200
            ):
                return False
            key = self._keys.get(header["kid"])
            if not key or key.get("expired_at"):
                data = await self._call("/webhook_verification_key/get", {"key_id": header["kid"]})
                key = data["key"]
                if (
                    key.get("kid") != header["kid"]
                    or key.get("alg") != "ES256"
                    or key.get("kty") != "EC"
                    or key.get("crv") != "P-256"
                ):
                    return False
                self._keys[header["kid"]] = key
            public_key = ec.EllipticCurvePublicNumbers(
                int.from_bytes(_base64url(key["x"])),
                int.from_bytes(_base64url(key["y"])),
                ec.SECP256R1(),
            ).public_key()
            signature = _base64url(signature64)
            if len(signature) != 64:
                return False
            der_signature = encode_dss_signature(
                int.from_bytes(signature[:32]), int.from_bytes(signature[32:])
            )
            public_key.verify(
                der_signature, f"{header64}.{payload64}".encode(), ec.ECDSA(hashes.SHA256())
            )
            payload = json.loads(_base64url(payload64))
            issued = payload.get("iat")
            if isinstance(issued, bool) or not isinstance(issued, (int, float)):
                return False
            expiry = key.get("expired_at")
            if expiry is not None and (not isinstance(expiry, (int, float)) or issued >= expiry):
                return False
            age = time.time() - issued
            body_hash = payload.get("request_body_sha256", "")
            if (
                not math.isfinite(age)
                or not -30 <= age <= 300
                or not isinstance(body_hash, str)
                or not re.fullmatch(r"[a-fA-F0-9]{64}", body_hash)
            ):
                return False
            return hmac.compare_digest(hashlib.sha256(raw_body).digest(), bytes.fromhex(body_hash))
        except Exception:
            return False

    async def webhook(self, raw_body: bytes, headers: Mapping[str, str]) -> WebhookResult:
        verification = next(
            (value for name, value in headers.items() if name.lower() == "plaid-verification"),
            None,
        )
        if not await self.verify_webhook(raw_body, verification):
            return WebhookResult(False, status_code=401, error="Invalid webhook")
        try:
            event = json.loads(raw_body)
        except (ValueError, UnicodeDecodeError):
            return WebhookResult(False, status_code=400, error="Invalid webhook")
        if not isinstance(event, dict) or not isinstance(event.get("item_id"), str):
            return WebhookResult(False, status_code=400, error="Invalid webhook")
        item_id = event["item_id"]
        if not self.store.get_item(item_id):
            return WebhookResult(True)
        if (
            event.get("webhook_type") == "TRANSACTIONS"
            and event.get("webhook_code") == "SYNC_UPDATES_AVAILABLE"
        ):
            self.queue_sync(item_id)
        if event.get("webhook_type") == "ITEM":
            code = event.get("webhook_code")
            error = event.get("error") or {}
            if code in {"PENDING_DISCONNECT", "PENDING_EXPIRATION"} or (
                code == "ERROR"
                and isinstance(error, dict)
                and error.get("error_code") == "ITEM_LOGIN_REQUIRED"
            ):
                self.store.set_item_status(
                    item_id, "reconnect", "Reconnect this institution through Plaid Link."
                )
        return WebhookResult(True)

    def queue_sync(self, item_id: str) -> None:
        existing = self._queued.get(item_id)
        if existing:
            existing["again"] = True
            return
        state = {"again": True}
        self._queued[item_id] = state

        async def worker():
            try:
                while state["again"]:
                    state["again"] = False
                    try:
                        await self.sync(item_id)
                    except PlaidError:
                        pass
            finally:
                self._queued.pop(item_id, None)

        state["task"] = asyncio.create_task(worker())
