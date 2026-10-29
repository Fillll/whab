"""Budget-scoped visual preferences, carried by the selected UI's namespace."""

import json
from threading import RLock

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict, Field, field_validator

from whab.domain.errors import StoreError
from whab.domain.money import currency_code
from whab.web.schemas import Workspace


class PreferencesBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    workspace: Workspace
    defaultCurrency: str

    @field_validator("defaultCurrency")
    @classmethod
    def valid_currency(cls, value):
        return currency_code(value)


class SavedPreferences(PreferencesBody):
    hiddenAccountIds: list[str] = Field(default_factory=list, max_length=10000)

    @field_validator("hiddenAccountIds")
    @classmethod
    def valid_accounts(cls, value):
        if any(not identifier or len(identifier) > 200 for identifier in value):
            raise ValueError("Invalid account identifier.")
        return sorted(set(value))


class SidebarAccountBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    workspace: Workspace
    visible: bool


class UIPreferences:
    def __init__(self, context):
        self.context = context
        self._lock = RLock()

    def read(self, workspace):
        context = self.context()
        raw = context.read_file(f"view-preferences-{workspace}.json") if context is not None else None
        hidden = context.read_file(f"sidebar-accounts-{workspace}.json") if context is not None else None
        try:
            value = PreferencesBody(workspace=workspace, **(json.loads(raw) if raw is not None else {"defaultCurrency": "USD"}))
            return SavedPreferences(workspace=workspace, defaultCurrency=value.defaultCurrency,
                                    hiddenAccountIds=json.loads(hidden) if hidden is not None else []).model_dump(exclude={"workspace"})
        except (ValueError, TypeError):
            raise StoreError("The budget's visual preferences could not be read.") from None

    def write(self, body):
        with self._lock:
            value = {**self.read(body.workspace), "defaultCurrency": body.defaultCurrency}
            self.context(writable=True).write_file(f"view-preferences-{body.workspace}.json", json.dumps({"defaultCurrency": body.defaultCurrency}).encode())
        return value

    def sidebar(self, account_id, body):
        with self._lock:
            value = self.read(body.workspace)
            hidden = set(value["hiddenAccountIds"])
            if body.visible:
                hidden.discard(account_id)
            else:
                hidden.add(account_id)
            if len(hidden) > 10000:
                raise StoreError("Too many hidden accounts.")
            value["hiddenAccountIds"] = sorted(hidden)
            self.context(writable=True).write_file(f"sidebar-accounts-{body.workspace}.json", json.dumps(value["hiddenAccountIds"]).encode())
            return value

    def router(self):
        router = APIRouter()

        @router.get("/api/ui/preferences")
        def read(workspace: Workspace):
            return self.read(workspace)

        @router.put("/api/ui/preferences")
        def write(body: PreferencesBody):
            return self.write(body)

        @router.patch("/api/ui/preferences/accounts/{account_id}")
        def sidebar(body: SidebarAccountBody, account_id: str = Path(min_length=1, max_length=200)):
            return self.sidebar(account_id, body)

        return router
