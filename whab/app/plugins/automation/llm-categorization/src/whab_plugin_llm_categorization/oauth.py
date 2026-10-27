"""OpenAI's public OSS OAuth flow; no browser cookies or Codex credential reuse."""

import base64
import hashlib
import hmac
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import jwt

from whab.plugins import StoreError

from .private import LOCK
from .providers import request

AUTH = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
SCOPE = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"


def token_record(value, previous):
    scopes = value.get("scope", " ".join(previous.get("scopes", []))).split()
    if ("chatgpt.tokens.use.direct" not in scopes or not value.get("access_token")
            or value.get("token_type", "").lower() != "bearer"
            or type(value.get("expires_in")) not in (int, float) or not 0 < value["expires_in"] <= 86400):
        raise StoreError("ChatGPT plan use was not authorized. Sign in and allow WHAB to use your plan.")
    return {**previous, **{key: value[key] for key in ("access_token", "refresh_token", "id_token") if key in value},
            "scopes": scopes, "expires_at": time.time() + value["expires_in"]}


def access_token(vault):
    with LOCK:
        saved = vault.read("chatgpt")
        if not saved.get("access_token"):
            raise StoreError("Continue with ChatGPT in LLM plugin settings on this machine first.")
        if saved["expires_at"] <= time.time() + 60:
            if not saved.get("refresh_token"):
                raise StoreError("Your ChatGPT session expired. Sign in again.")
            value = request("POST", AUTH + "/api/accounts/oauth/token",
                            data={"grant_type": "refresh_token", "client_id": saved["client_id"],
                                  "refresh_token": saved["refresh_token"], "resource": RESOURCE})
            saved = token_record(value, saved)
            vault.write("chatgpt", saved)
        return saved["access_token"]


def disconnect(vault):
    saved = vault.read("chatgpt")
    confirmed = True
    try:
        if saved.get("refresh_token"):
            request("POST", AUTH + "/api/accounts/oauth/revoke",
                    data={"token": saved["refresh_token"], "token_type_hint": "refresh_token",
                          "client_id": saved["client_id"]})
    except StoreError:
        confirmed = False
    vault.write("chatgpt", {key: saved[key] for key in ("host_id", "client_id", "subject") if key in saved})
    return confirmed


class SignIn:
    """One short-lived loopback OAuth receiver. Never serves WHAB or financial data."""

    def __init__(self, vault):
        self.vault = vault
        self.saved = vault.read("chatgpt")
        if not self.saved.get("host_id"):
            self.saved["host_id"] = "urn:uuid:" + str(uuid4())
            vault.write("chatgpt", self.saved)
        self.state, self.nonce, self.verifier = (secrets.token_urlsafe(32) for _ in range(3))
        self.status, self.error = "pending", None
        self.deadline = time.monotonic() + 300
        attempt = self

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Authorization codes and query strings never enter logs.

            def do_GET(self):
                url = urlsplit(self.path)
                values = parse_qs(url.query)
                if (self.headers.get("Host") != attempt.host or url.path != "/auth/callback"
                        or any(len(value) != 1 for value in values.values())
                        or not hmac.compare_digest(values.get("state", [""])[0], attempt.state)):
                    self.send_error(400, "Invalid sign-in callback")
                    return
                try:
                    attempt.finish({key: value[0] for key, value in values.items()})
                    message = b"Connected. You can close this tab and return to WHAB."
                    attempt.status = "connected"
                except Exception:
                    attempt.status = "failed"
                    attempt.error = "ChatGPT sign-in failed or plan access was declined. Start sign-in again."
                    message = b"Sign-in could not complete. Return to WHAB and try again."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'none'")
                self.end_headers()
                self.wfile.write(message)

        self.server = HTTPServer(("127.0.0.1", 0), Callback)
        self.server.timeout = 0.5
        self.host = f"127.0.0.1:{self.server.server_port}"
        self.redirect = f"http://{self.host}/auth/callback"
        values = {"client_id": self.saved.get("client_id", "dynamic_agent_client"),
                  "ext_agent_host_id": self.saved["host_id"], "response_type": "code", "redirect_uri": self.redirect,
                  "scope": SCOPE, "resource": RESOURCE, "state": self.state, "nonce": self.nonce,
                  "code_challenge_method": "S256",
                  "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b"=").decode()}
        if not self.saved.get("client_id"):
            values["agent_name_hint"] = "WHAB"
        self.url = AUTH + "/api/accounts/authorize?" + urlencode(values)
        self.thread = Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            while self.status == "pending" and time.monotonic() < self.deadline:
                self.server.handle_request()
            if self.status == "pending":
                self.status, self.error = "failed", "Sign-in expired. Continue with ChatGPT again."
        finally:
            self.server.server_close()

    def close(self):
        with LOCK:
            self.status = "canceled"
        self.thread.join(timeout=2)

    def finish(self, values):
        client = self.saved.get("client_id") or values.get("client_id")
        if (values.get("error") or not values.get("code") or not client or client == "dynamic_agent_client"
                or values.get("client_id", client) != client):
            raise StoreError("ChatGPT authorization was not completed.")
        value = request("POST", AUTH + "/api/accounts/oauth/token",
                        data={"grant_type": "authorization_code", "client_id": client, "code": values["code"],
                              "code_verifier": self.verifier, "redirect_uri": self.redirect, "resource": RESOURCE})
        # Use a maintained JWT implementation; verify signature and all OIDC claims.
        token = value["id_token"]
        kid = jwt.get_unverified_header(token)["kid"]
        keys = request("GET", AUTH + "/.well-known/jwks.json")["keys"]
        key = next(key for key in keys if key.get("kid") == kid)
        identity = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=["RS256"],
                              issuer=AUTH, audience=client, leeway=5,
                              options={"require": ["sub", "exp", "iat", "nonce"]})
        if (not identity["sub"] or not hmac.compare_digest(identity["nonce"], self.nonce)
                or self.saved.get("subject", identity["sub"]) != identity["sub"]):
            raise StoreError("The signed-in ChatGPT identity did not match this connection.")
        saved = token_record(value, {**self.saved, "client_id": client, "subject": identity["sub"]})
        with LOCK:
            if self.status != "pending" or time.monotonic() >= self.deadline:
                raise StoreError("This sign-in attempt was canceled or expired. Start again.")
            self.vault.write("chatgpt", saved)
