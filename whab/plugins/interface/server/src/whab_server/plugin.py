"""A narrow remote ledger interface; bank secrets never enter this context."""

from hmac import compare_digest
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from whab.plugins import (
    Interface,
    InterfaceContext,
    Plugin,
    RequestConflict,
    RevisionConflict,
    StoreError,
)


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: StrictStr
    workspace: Literal["demo", "personal"]
    payload: dict
    request_id: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    expected_revision: Annotated[StrictStr, Field(min_length=1, max_length=200)]


def create_interface(context: InterfaceContext) -> APIRouter:
    token = context.config.get("SERVER_TOKEN", "")
    if len(token) < 32:
        raise StoreError("Server plugin requires SERVER_TOKEN with at least 32 characters.")

    def authorize(request: Request):
        if request.url.scheme != "https":
            raise HTTPException(400, "Remote budget connections require HTTPS.")
        supplied = request.headers.get("authorization", "")
        if not compare_digest(supplied.encode(), ("Bearer " + token).encode()):
            raise HTTPException(401, "Invalid remote budget credentials.")

    router = APIRouter(prefix="/remote/v1", dependencies=[Depends(authorize)])

    def check_budget(budget_id):
        if budget_id != context.budget_id:
            raise HTTPException(404, "Budget not found.")

    @router.get("/budgets/{budget_id}/queries/{operation}")
    def query(
        budget_id: str, operation: str, workspace: Literal["demo", "personal"],
        month: str | None = None,
    ):
        check_budget(budget_id)
        try:
            result = context.service.query(operation, workspace, month)
        except StoreError as error:
            raise HTTPException(400, str(error)) from None
        except Exception:
            raise HTTPException(503, "Budget storage is unavailable.") from None
        return {"budgetId": context.budget_id, **result}

    @router.post("/budgets/{budget_id}/commands")
    def command(budget_id: str, body: Command):
        check_budget(budget_id)
        try:
            result = context.service.command(
                body.operation, body.workspace, body.payload,
                request_id=body.request_id, expected_revision=body.expected_revision,
            )
        except (RevisionConflict, RequestConflict) as error:
            raise HTTPException(409, str(error)) from None
        except StoreError as error:
            raise HTTPException(400, str(error)) from None
        except Exception:
            raise HTTPException(503, "Budget storage is unavailable. Retry the same request.") from None
        return {"budgetId": context.budget_id, **result}

    return router


plugin = Plugin(id="server", display_name="Remote budget server", interface=Interface(create_interface))
