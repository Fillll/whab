"""Host-owned authorization and financial dispatch for data-only API plugins."""

from hmac import compare_digest
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from starlette.routing import Match

from whab.budgets.runtime import RuntimeResource
from whab.plugins.interface_adapter import RemoteLedgerInterface, validate_interface_owners
from whab.plugins.runtime import PluginError
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
        self._closed = False

    def close(self):
        self._closed = True
        self._token = b""

    def _available(self):
        if self._closed:
            raise HTTPException(503, "Remote interface is unavailable.")

    def authorize(self, request):
        self._available()
        if request.url.scheme != "https":
            raise HTTPException(400, "Remote budget connections require HTTPS.")
        supplied = request.headers.get("authorization", "").encode("utf-8")
        if not compare_digest(supplied, b"Bearer " + self._token):
            raise HTTPException(401, "Invalid remote budget credentials.")

    def check_budget(self, identifier):
        self._available()
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


class InterfaceGeneration:
    """Host-private prepared interface state owned by a runtime generation."""

    def __init__(self, owner=None, descriptor=None, binding=None, legacy=False):
        self.owner, self.descriptor, self.binding, self.legacy = owner, descriptor, binding, legacy
        self.resource = RuntimeResource(owner, binding.close) if binding is not None else None

    def current(self):
        return self.binding

    def enabled(self):
        return self.binding is not None or self.legacy

    def legacy_routes(self):
        return self.legacy


def prepare_interfaces(registry, context, previous=None):
    """Prepare private host bindings; no credentials or services reach a worker."""
    plugins = registry.interface_plugins()
    validate_interface_owners(plugins)
    for plugin in plugins:
        if plugin.interface.adapter is not None:
            if previous is not None and previous.owner == plugin.id and previous.descriptor is plugin.interface:
                return previous
            try:
                binding = BoundRemoteInterface(plugin.interface.adapter, context(plugin.id))
            except Exception:
                raise PluginError("Interface plugin could not start. Check its host configuration.",
                                  code="interface_startup_failed", plugin_id=plugin.id) from None
            return InterfaceGeneration(plugin.id, plugin.interface, binding)
    return InterfaceGeneration(legacy=bool(plugins))


def create_remote_router(resolve, legacy=lambda: False):
    """Resolve one generation-owned host adapter inside the request's lease."""
    def binding(request: Request):
        selected = resolve()
        if selected is None:
            raise HTTPException(404, "Remote interface is unavailable.")
        selected.authorize(request)
        return selected

    class OptionalInterfaceRoute(APIRoute):
        def matches(self, scope):
            # Legacy factories keep their documented routes when no data-only
            # owner is selected. An empty selection still reaches our 404.
            if scope["type"] == "http" and legacy() and resolve() is None:
                return Match.NONE, {}
            return super().matches(scope)

    router = APIRouter(prefix="/remote/v1", route_class=OptionalInterfaceRoute)

    @router.get("/budgets/{budget_id}/queries/{operation}")
    def query(budget_id: str, operation: str, workspace: Literal["demo", "personal"],
              month: str | None = None, selected: BoundRemoteInterface = Depends(binding)):
        return selected.query(budget_id, operation, workspace, month)

    @router.post("/budgets/{budget_id}/commands")
    def command(budget_id: str, body: RemoteCommand, selected: BoundRemoteInterface = Depends(binding)):
        return selected.command(budget_id, body)

    return router
