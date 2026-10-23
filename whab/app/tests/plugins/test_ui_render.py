from copy import deepcopy
from dataclasses import dataclass

import pytest
from starlette.requests import Request

from whab.plugins import Insight, Plugin, PluginRegistry, UserInterface
from whab.plugins.ui_render import public_value, read_arguments, render_arguments, render_page
from whab.plugins.wire import WireError


def arguments():
    scope = {"type": "http", "scheme": "https", "method": "GET", "path": "/budgets/home/transactions/current-month/usd",
             "query_string": b"q=fictional&q=second", "server": ("localhost", 443),
             "headers": [(b"cookie", b"FICTIONAL_PRIVATE"), (b"authorization", b"Bearer FICTIONAL_PRIVATE"),
                         (b"x-whab-page-fragment", b"1")]}
    inputs = {"data": {"balance": 1234}, "status": {}, "items": [], "workspace": "personal",
              "page": "transactions", "month": "2026-10", "selected": None}
    routing = {"key": "home", "personal_key": "fictional", "workspace": "personal", "page": "transactions",
               "month_token": "current-month", "currency": "USD", "account_slugs": {"RawID": "fictional-bank"}}
    return render_arguments(inputs, {"client_context": {"host": True}}, routing, Request(scope),
                            {"imports/fictional/accounts.html": "<span>{{ amount(1234) }}</span>"})


def test_ui_helpers_and_template_execution_stay_inside_renderer(tmp_path):
    root = tmp_path / "templates"
    root.mkdir()
    (root / "base.html").write_text(
        '{{ amount(data.balance) }}|{{ url(account="RawID") }}|{{ url(workspace="demo", account="RawID") }}|'
        '{{ request.headers.get("x-whab-page-fragment") }}|{{ request.query_params.getlist("q")|join(",") }}|'
        '{{ client_context.plugin }}{{ client_context.host }}|{% include "imports/fictional/accounts.html" %}|'
        '{{ unsafe }}|{{ request.headers.get("authorization", "absent") }}', encoding="utf-8")
    ui = UserInterface(root, tmp_path, lambda data, status, items, **kw: {
        "amount": lambda number: f"${number / 100:.2f}", "data": data,
        "client_context": {"plugin": True}, "unsafe": "<script>fictional</script>",
    })
    value = arguments()
    assert "FICTIONAL_PRIVATE" not in repr(value)
    output = render_page(ui, value).decode()
    assert output == ('$12.34|/budgets/home/fictional-bank/transactions/current-month/usd|'
                      '/budgets/demo/transactions/current-month/usd?account=%52aw%49%44|1|fictional,second|'
                      'TrueTrue|<span>$12.34</span>|&lt;script&gt;fictional&lt;/script&gt;|absent')


def test_only_known_public_presentation_records_cross_the_boundary():
    plugin = Plugin("fictional", "Fictional report", insights=lambda snapshot: (Insight("Title", "Value"),))
    description = PluginRegistry((plugin,)).describe()
    copied = public_value({"plugins": description, "insight": Insight("Title", "Value")})
    assert copied["plugins"][0]["id"] == "fictional"
    assert copied["insight"]["title"] == "Title"
    @dataclass
    class Private:
        secret: str
    for value in (object(), lambda: None, Private("fictional"), {"handle": object()}):
        with pytest.raises(WireError):
            public_value(value)


def test_aggregate_encoded_budget_stops_before_copying_or_serializing_large_fanout(monkeypatch):
    import whab.plugins.ui_render as rendering
    original = rendering.json.dumps
    observed = []
    def encode(value, **options):
        observed.append(value)
        assert len(observed) < 100
        return original(value, **options)
    monkeypatch.setattr(rendering.json, "dumps", encode)
    with pytest.raises(WireError):
        public_value(["x" * 100000] * 10000)
    assert len(observed) < 90


@pytest.mark.parametrize("change", [
    lambda value: value.update(extra=True),
    lambda value: value["request"]["headers"].update(authorization="private"),
    lambda value: value["request"].update(path="relative"),
    lambda value: value["request"].update(query=[["q"]]),
    lambda value: value["routing"].update(workspace="demo"),
    lambda value: value["routing"].update(account_slugs={"raw": True}),
    lambda value: value["input"].update(page="unknown"),
    lambda value: value["input"].update(items={}),
    lambda value: value["templates"].update({"imports/../private": "bad"}),
    lambda value: value["templates"].update({"/absolute": "bad"}),
    lambda value: value["context"].update(nonfinite=float("nan")),
    lambda value: value["context"].update(money=2**53),
])
def test_malformed_ui_requests_fail_before_rendering(change):
    value = deepcopy(arguments())
    change(value)
    with pytest.raises(WireError):
        read_arguments(value)
