"""Local guarded settings transport. No credentials enter public ledger commands."""

from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from whab.plugins import StoreError

from . import api as providers
from . import oauth
from .api import identifier
from .private import LOCK, PROVIDERS, Vault


def text(value, limit):
    return (
        type(value) is str
        and 0 < len(value.strip()) <= limit
        and not any(ord(c) < 32 for c in value)
    )


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    workspace: Literal["personal", "demo"]
    action: Literal[
        "status",
        "save-key",
        "models",
        "connect",
        "auth-status",
        "disconnect",
        "cancel-sign-in",
        "adopt",
    ]
    provider: Literal["chatgpt", "openai", "anthropic", "openrouter"] = "openai"
    connections: dict[str, str] = Field(default_factory=dict, max_length=4)
    api_key: str = Field(default="", max_length=4096, repr=False)


def router(context, *, route_id="ai-credentials", prompt_rules=""):
    attempts = {}

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            for attempt in attempts.values():
                attempt.close()

    routes = APIRouter(lifespan=lifespan)

    @routes.post("/api/plugin-settings/" + route_id)
    def settings(body: SettingsRequest):
        budget = context.budget
        if budget is None:
            raise StoreError(
                "LLM settings need a local budget namespace. Configure this plugin on the budget host."
            )
        if body.workspace == "demo":
            budget = replace(
                budget,
                read_file=lambda name: context.budget.read_file("demo/" + name),
                write_file=lambda name, raw: context.budget.write_file("demo/" + name, raw),
            )
        vault = Vault(budget)
        action, provider = body.action, body.provider
        if action == "adopt":
            if any(
                key not in PROVIDERS or not identifier(value)
                for key, value in body.connections.items()
            ):
                raise StoreError("Choose valid saved AI connections.")
            import json

            with LOCK:
                refs = {**body.connections, **vault.references()}
                budget.write_file("connections.json", json.dumps(refs).encode())
            return {"ok": True}
        if action == "status":
            categories = context.service.query("categories", body.workspace)["data"]
            return {
                "providers": [
                    {
                        "id": key,
                        "name": name,
                        "connectionId": vault.references().get(key, ""),
                        "connected": bool(
                            vault.read(key).get("access_token" if key == "chatgpt" else "api_key")
                        ),
                    }
                    for key, name in PROVIDERS.items()
                ],
                "promptRules": prompt_rules,
                "efforts": providers.EFFORTS,
                "categories": [
                    {"id": row["id"], "name": row["name"], "currency": row.get("currency", "USD")}
                    for row in categories
                    if row.get("parentId") or row.get("group")
                ],
            }
        if action == "save-key":
            if (
                provider == "chatgpt"
                or not text(body.api_key, 4096)
                or any(c.isspace() for c in body.api_key)
            ):
                raise StoreError("Enter this provider's API key without whitespace.")
            vault.write(provider, {"api_key": body.api_key})
            return {"ok": True}
        if action == "models":
            return {"models": providers.models(vault, provider)}
        if action == "connect":
            if provider != "chatgpt":
                raise StoreError("Use an API key for this provider.")
            if body.workspace in attempts:
                attempts.pop(body.workspace).close()
            attempt = oauth.SignIn(vault)
            attempts[body.workspace] = attempt
            return {"url": attempt.url}
        if action in {"auth-status", "cancel-sign-in"}:
            attempt = attempts.get(body.workspace)
            if action == "cancel-sign-in" and attempt:
                attempts.pop(body.workspace).close()
                return {"status": "canceled"}
            return {
                "status": attempt.status if attempt else "idle",
                "error": attempt.error if attempt else None,
            }
        if body.workspace in attempts:
            attempts.pop(body.workspace).close()
        if provider == "chatgpt":
            confirmed = oauth.disconnect(vault)
            return {
                "ok": True,
                "message": "Disconnected."
                if confirmed
                else "Disconnected locally. Remote revocation was not confirmed; disconnect WHAB in ChatGPT Settings.",
            }
        vault.clear(provider)
        return {"ok": True, "message": "API key removed from this machine."}

    return routes
