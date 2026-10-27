"""Local guarded settings transport. No credentials enter public ledger commands."""

from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from whab.plugins import StoreError

from . import oauth, providers
from .engine import text
from .private import PROVIDERS, Vault


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    workspace: Literal["personal", "demo"]
    action: Literal["status", "save-key", "models", "connect", "auth-status", "disconnect", "cancel-sign-in"]
    provider: Literal["chatgpt", "openai", "anthropic", "openrouter"] = "openai"
    api_key: str = Field(default="", max_length=4096, repr=False)


def router(context):
    attempts = {}

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            for attempt in attempts.values():
                attempt.close()

    routes = APIRouter(lifespan=lifespan)

    @routes.post("/api/plugin-settings/llm-categorization")
    def settings(body: SettingsRequest):
        budget = context.budget
        if budget is None:
            raise StoreError("LLM settings need a local budget namespace. Configure this plugin on the budget host.")
        if body.workspace == "demo":
            budget = replace(budget, read_file=lambda name: context.budget.read_file("demo/" + name),
                             write_file=lambda name, raw: context.budget.write_file("demo/" + name, raw))
        vault = Vault(budget)
        action, provider = body.action, body.provider
        if action == "status":
            categories = context.service.query("categories", body.workspace)["data"]
            return {"providers": [{"id": key, "name": name, "connected": bool(vault.read(key).get("access_token" if key == "chatgpt" else "api_key"))}
                                  for key, name in PROVIDERS.items()],
                    "promptRules": providers.INSTRUCTIONS, "efforts": providers.EFFORTS,
                    "categories": [{"id": row["id"], "name": row["name"], "currency": row.get("currency", "USD")}
                                   for row in categories if row.get("parentId") or row.get("group")]}
        if action == "save-key":
            if provider == "chatgpt" or not text(body.api_key, 4096) or any(c.isspace() for c in body.api_key):
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
            return {"status": attempt.status if attempt else "idle", "error": attempt.error if attempt else None}
        if body.workspace in attempts:
            attempts.pop(body.workspace).close()
        if provider == "chatgpt":
            confirmed = oauth.disconnect(vault)
            return {"ok": True, "message": "Disconnected." if confirmed else "Disconnected locally. Remote revocation was not confirmed; disconnect WHAB in ChatGPT Settings."}
        vault.clear(provider)
        return {"ok": True, "message": "API key removed from this machine."}

    return routes
