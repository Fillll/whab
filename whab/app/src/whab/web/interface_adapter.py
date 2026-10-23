"""Host-owned authorization and financial dispatch for data-only API plugins."""

from hmac import compare_digest
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from whab.plugins.interface_adapter import RemoteLedgerInterface
from whab.services.ledger import RequestConflict, RevisionConflict


class RemoteCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: StrictStr
    workspace: Literal["demo", "personal"]
    payload: dict
    request_id: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    expected_revision: Annotated[StrictStr, Field(min_length=1, max_length=200)]


class BoundRemoteInterface:
    """Prepared host-only adapter. Tokens and service handles never cross IPC."""

    def __init__(self, adapter, context):
        if type(adapter) is not RemoteLedgerInterface:
            raise ValueError("Use a supported remote interface adapter.")
        from whab.domain.errors import StoreError

        token = context.config.get(adapter.token_setting, "")
        if type(token) is not str or len(token) < 32 or len(token) > 4096:
            raise StoreError("Remote interface requires a host bearer token with 32 to 4096 characters.")
        self.adapter, self.context, self._token = adapter, context, token.encode("utf-8")

    def authorize(self, request):
        if request.url.scheme != "https":
            raise HTTPException(400, "Remote budget connections require HTTPS.")
        supplied = request.headers.get("authorization", "").encode("utf-8")
        if not compare_digest(supplied, b"Bearer " + self._token):
            raise HTTPException(401, "Invalid remote budget credentials.")

    def check_budget(self, identifier):
        if identifier != self.context.budget_id:
            raise HTTPException(404, "Budget not found.")

    def query(self, identifier, operation, workspace, month):
        from whab.domain.errors import StoreError

        self.check_budget(identifier)
        if not self.adapter.queries:
            raise HTTPException(405, "Remote queries are disabled by this interface.")
        try:
            result = self.context.service.query(operation, workspace, month)
        except StoreError as error:
            raise HTTPException(400, str(error)) from None
        except Exception:
            raise HTTPException(503, "Budget storage is unavailable.") from None
        return {"budgetId": self.context.budget_id, **result}

    def command(self, identifier, body):
        from whab.domain.errors import StoreError

        self.check_budget(identifier)
        if not self.adapter.commands:
            raise HTTPException(405, "Remote commands are disabled by this interface.")
        try:
            result = self.context.service.command(body.operation, body.workspace, body.payload,
                request_id=body.request_id, expected_revision=body.expected_revision)
        except (RevisionConflict, RequestConflict) as error:
            raise HTTPException(409, str(error)) from None
        except StoreError as error:
            raise HTTPException(400, str(error)) from None
        except Exception:
            raise HTTPException(503, "Budget storage is unavailable. Retry the same request.") from None
        return {"budgetId": self.context.budget_id, **result}


def create_remote_router(resolve):
    """Resolve one generation-owned host adapter inside the request's lease."""
    def binding(request: Request):
        selected = resolve()
        if selected is None:
            raise HTTPException(404, "Remote interface is unavailable.")
        selected.authorize(request)
        return selected

    router = APIRouter(prefix="/remote/v1")

    @router.get("/budgets/{budget_id}/queries/{operation}")
    def query(budget_id: str, operation: str, workspace: Literal["demo", "personal"],
              month: str | None = None, selected: BoundRemoteInterface = Depends(binding)):
        return selected.query(budget_id, operation, workspace, month)

    @router.post("/budgets/{budget_id}/commands")
    def command(budget_id: str, body: RemoteCommand, selected: BoundRemoteInterface = Depends(binding)):
        return selected.command(budget_id, body)

    return router
