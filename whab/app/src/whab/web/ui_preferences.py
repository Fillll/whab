"""Budget-scoped visual preferences, carried by the selected UI's namespace."""

import json

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, field_validator

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


class UIPreferences:
    def __init__(self, context):
        self.context = context

    def read(self, workspace):
        context = self.context()
        raw = context.read_file(f"view-preferences-{workspace}.json") if context is not None else None
        if raw is None:
            return {"defaultCurrency": "USD"}
        try:
            value = json.loads(raw)
            return PreferencesBody(workspace=workspace, **value).model_dump(exclude={"workspace"})
        except (ValueError, TypeError):
            raise StoreError("The budget's visual preferences could not be read.") from None

    def write(self, body):
        value = body.model_dump(exclude={"workspace"})
        self.context(writable=True).write_file(f"view-preferences-{body.workspace}.json", json.dumps(value).encode())
        return value

    def router(self):
        router = APIRouter()

        @router.get("/api/ui/preferences")
        def read(workspace: Workspace):
            return self.read(workspace)

        @router.put("/api/ui/preferences")
        def write(body: PreferencesBody):
            return self.write(body)

        return router
