"""Data-only inputs for a visual worker; its Python/Jinja helpers stay inside it."""

import json
import math
from dataclasses import fields
from urllib.parse import urlencode

from starlette.requests import Request

from whab.web.navigation import page_url

from .contracts import Capability, Requirement
from .sdk import Insight, PluginDescription, PluginInsightResult
from .wire import MAX_METADATA_BYTES, Frame, WireError

_RECORDS = (PluginDescription, PluginInsightResult, Insight, Capability, Requirement)
_HEADERS = frozenset({"x-whab-page-fragment", "x-whab-budget-fragment",
                      "x-whab-transactions-fragment", "x-whab-transactions-sidebar"})
_ROUTING = frozenset({"key", "personal_key", "workspace", "page", "month_token", "currency", "account_slugs"})
_INPUT = frozenset({"data", "status", "items", "workspace", "page", "month", "selected"})


def _public(value, budget, depth=0):
    budget[0] -= 1
    if depth > 16 or budget[0] < 0:
        raise WireError()
    if type(value) in _RECORDS:
        value = {field.name: getattr(value, field.name) for field in fields(value)}
    if type(value) is dict:
        if len(value) > 100000 or any(type(key) is not str or not key or len(key) > 200 for key in value):
            raise WireError()
        _reserve(budget, 2 + max(0, len(value) - 1) + len(value))
        return {_public(key, budget, depth + 1): _public(item, budget, depth + 1) for key, item in value.items()}
    if type(value) in (tuple, list):
        if len(value) > 100000:
            raise WireError()
        _reserve(budget, 2 + max(0, len(value) - 1))
        return [_public(item, budget, depth + 1) for item in value]
    if type(value) is str:
        if len(value) > 100000 or "\x00" in value:
            raise WireError()
    elif type(value) in (int, float):
        if abs(value) > 2**53 - 1 or type(value) is float and not math.isfinite(value):
            raise WireError()
    elif value is not None and type(value) is not bool:
        raise WireError()
    try:
        _reserve(budget, len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")))
    except (ValueError, UnicodeError):
        raise WireError() from None
    return value


def _reserve(budget, size):
    budget[1] -= size
    if budget[1] < 0:
        raise WireError()


def public_value(value):
    """Copy public records with aggregate allocation and encoded-byte budgets."""
    return _public(value, [1000000, MAX_METADATA_BYTES])


def render_arguments(inputs, context, routing, request, templates=None):
    """Encode public page projections and selected request fields, never cookies."""
    if type(inputs) is not dict or inputs.keys() != _INPUT or type(routing) is not dict or routing.keys() != _ROUTING:
        raise WireError()
    value = {
        "input": inputs, "context": context, "routing": routing,
        "request": {"path": request.url.path, "query": list(map(list, request.query_params.multi_items())),
                    "headers": {name: request.headers[name] for name in _HEADERS if name in request.headers}},
        "templates": dict(templates or {}),
    }
    # Reuse the negotiated frame's strict scalar/depth/size validation before
    # the parent allocates or writes a worker message.
    value = public_value(value)
    read_arguments(value)
    return value


def read_arguments(value):
    value = public_value(value)
    if type(value) is not dict or value.keys() != {"input", "context", "routing", "request", "templates"}:
        raise WireError()
    inputs, routing, request = value["input"], value["routing"], value["request"]
    if (type(inputs) is not dict or inputs.keys() != _INPUT or type(value["context"]) is not dict
            or type(routing) is not dict or routing.keys() != _ROUTING
            or type(request) is not dict or request.keys() != {"path", "query", "headers"}):
        raise WireError()
    if (type(inputs["data"]) is not dict or type(inputs["status"]) is not dict or type(inputs["items"]) is not list
            or inputs["workspace"] not in {"demo", "personal"} or inputs["page"] not in {"budget", "transactions", "insights", "accounts"}
            or any(type(inputs[name]) is not str for name in ("workspace", "page", "month"))
            or inputs["selected"] is not None and type(inputs["selected"]) is not str):
        raise WireError()
    if (any(type(routing[name]) is not str for name in _ROUTING - {"account_slugs"})
            or routing["workspace"] != inputs["workspace"] or routing["page"] != inputs["page"]
            or type(routing["account_slugs"]) is not dict
            or any(type(key) is not str or type(slug) is not str for key, slug in routing["account_slugs"].items())):
        raise WireError()
    if (type(request["path"]) is not str or not request["path"].startswith("/") or len(request["path"]) > 4096
            or type(request["headers"]) is not dict or request["headers"].keys() - _HEADERS
            or any(type(item) is not str or len(item) > 100 for item in request["headers"].values())
            or type(request["query"]) is not list or len(request["query"]) > 128
            or any(type(pair) is not list or len(pair) != 2 or any(type(item) is not str or len(item) > 4096 for item in pair)
                   for pair in request["query"])):
        raise WireError()
    templates = value["templates"]
    if (type(templates) is not dict or len(templates) > 256
            or any(type(name) is not str or not name.startswith(("plugins/", "imports/"))
                   or any(part in {"", ".", ".."} for part in name.split("/")) or "\\" in name
                   or type(content) is not str for name, content in templates.items())
            or sum(len(content.encode("utf-8")) for content in templates.values()) > 4 * 1024 * 1024):
        raise WireError()
    Frame(value).encode()
    return value


def render_page(ui, arguments):
    """Run inside the trusted visual worker; no Python object crosses IPC."""
    from jinja2 import ChoiceLoader, DictLoader
    from starlette.templating import Jinja2Templates

    value = read_arguments(arguments)
    inputs, routing, request = value["input"], value["routing"], value["request"]
    context = ui.build_context(inputs["data"], inputs["status"], inputs["items"],
                               **{name: inputs[name] for name in ("workspace", "page", "month", "selected")})
    if type(context) is not dict:
        raise WireError()
    client = context.setdefault("client_context", {})
    if type(client) is not dict:
        raise WireError()
    client.update(value["context"].get("client_context", {}))
    context.update({name: item for name, item in value["context"].items() if name != "client_context"})

    def url(next_page=routing["page"], **overrides):
        workspace = overrides.pop("workspace", routing["workspace"])
        key = routing["key"] if workspace == routing["workspace"] else ("demo" if workspace == "demo" else routing["personal_key"])
        currency = overrides.pop("currency", routing["currency"])
        account = overrides.pop("account", None)
        slug = routing["account_slugs"].get(account) if workspace == routing["workspace"] and next_page == "transactions" else None
        if account is not None and slug is None:
            overrides["account"] = account
        return page_url(key, next_page, overrides.pop("month", routing["month_token"]), currency, account_slug=slug, **overrides)

    context["url"] = url
    scope = {"type": "http", "scheme": "https", "method": "GET", "path": request["path"],
             "query_string": urlencode([tuple(pair) for pair in request["query"]]).encode("utf-8"),
             "headers": [(key.encode("ascii"), item.encode("latin-1")) for key, item in request["headers"].items()],
             "server": ("localhost", 443)}
    # The request deliberately has no app, credential, session or backend state.
    context["request"] = Request(scope)
    templates = Jinja2Templates(directory=ui.templates)
    templates.env.loader = ChoiceLoader([templates.env.loader, DictLoader(value["templates"])])
    output = templates.env.get_template("base.html").render(context).encode("utf-8")
    if len(output) > 16 * 1024 * 1024:
        raise WireError()
    return output
