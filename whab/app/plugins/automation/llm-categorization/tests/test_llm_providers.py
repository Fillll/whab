"""Provider wire formats, complete streams, errors and request bounds."""

import json

import httpx
import pytest
from whab_plugin_llm_categorization import providers

from whab.plugins import StoreError


@pytest.fixture
def wire(monkeypatch):
    original = httpx.Client
    requests = []
    def install(response, status=200):
        def handle(request):
            requests.append(request)
            if isinstance(response, Exception):
                raise response
            return httpx.Response(status, content=response if isinstance(response, bytes) else json.dumps(response).encode())
        def client(**kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
            return original(**kwargs, transport=httpx.MockTransport(handle))
        monkeypatch.setattr(providers.httpx, "Client", client)
        return requests
    return install


@pytest.mark.parametrize("provider", ["openai", "anthropic", "openrouter", "chatgpt"])
def test_model_catalog_uses_correct_auth_and_account_model_ids(context, wire, provider, monkeypatch):
    from whab_plugin_llm_categorization.private import Vault
    vault = Vault(context)
    vault.write(provider, {"api_key": "fictional-key"})
    if provider == "chatgpt":
        monkeypatch.setattr("whab_plugin_llm_categorization.oauth.access_token", lambda _: "fictional-oauth")
    catalog = {"models": [{"slug": "account-model", "display_name": "Account model", "visibility": "list"},
                          {"slug": "hidden", "visibility": "hide"}]} if provider == "chatgpt" else {
                              "data": [{"id": "model-id", "name": "Text model"}, {"id": "plain"}]}
    sent = wire(catalog)
    result = providers.models(vault, provider)
    assert result[0]["id"] == ("account-model" if provider == "chatgpt" else "model-id")
    assert len(result) == (1 if provider == "chatgpt" else 2)
    request = sent[0]
    assert str(request.url) == providers.ORIGINS[provider] + "/models"
    if provider == "anthropic":
        assert request.headers["x-api-key"] == "fictional-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
    else:
        assert request.headers["Authorization"].startswith("Bearer fictional-")


@pytest.mark.parametrize("provider", ["openai", "anthropic", "openrouter", "chatgpt"])
def test_completion_wire_formats_and_no_tools_or_storage(engine, wire, provider, monkeypatch):
    engine.vault.write(provider, {"api_key": "fictional-key"})
    monkeypatch.setattr("whab_plugin_llm_categorization.oauth.access_token", lambda _: "fictional-oauth")
    output = '{"suggestions":[]}'
    response = {"status": "completed", "output": [{"type": "reasoning"}, {"type": "message", "content": [
        {"type": "refusal"}, {"type": "output_text", "text": output}]}]}
    if provider == "chatgpt":
        response = b'data: {"type":"response.output_text.delta","delta":"ignored","output_index":0,"content_index":0}\n\n' + (
            "data: " + json.dumps({"type": "response.completed", "response": response}) + "\n\ndata: [DONE]\n").encode()
    elif provider == "anthropic":
        response = {"stop_reason": "end_turn", "content": [{"type": "thinking"}, {"type": "text", "text": output}]}
    elif provider == "openrouter":
        response = {"choices": [{"finish_reason": "stop", "message": {"content": output}}]}
    sent = wire(response)
    assert providers.complete(engine.vault, provider, "test-model", {"transactions": []}) == output
    payload = json.loads(sent[0].content)
    assert "tools" not in payload and payload["model"] == "test-model"
    if provider in {"openai", "chatgpt"}:
        assert payload["store"] is False
        assert payload["stream"] is (provider == "chatgpt")
        assert "max_output_tokens" not in payload
    else:
        assert payload["max_tokens"] == 4096


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_errors_are_authored_and_never_include_provider_bodies(wire, status):
    wire(b"private transaction fictional-secret", status)
    with pytest.raises(StoreError) as error:
        providers.request("GET", "https://api.openai.com/v1/models")
    assert "private" not in str(error.value) and "fictional-secret" not in str(error.value)


@pytest.mark.parametrize("response", [b"not-json", b"\xff", b"x" * (2 * 1024 * 1024 + 1),
                                      httpx.ConnectError("private-payload")])
def test_network_malformed_and_oversized_responses_are_safe(wire, response):
    wire(response)
    with pytest.raises(StoreError):
        providers.request("GET", "https://api.openai.com/v1/models")


@pytest.mark.parametrize("event", ["response.failed", "response.incomplete", "error", "missing", "broken"])
def test_stream_requires_terminal_success(wire, event):
    raw = ("event: message\n\ndata: " + json.dumps({"type": event}) + "\n").encode()
    if event == "broken":
        raw = b"data: invalid\n"
    wire(raw)
    with pytest.raises(StoreError):
        providers.request("POST", "https://api.openai.com/v1/responses", stream=True)


def test_empty_response_is_supported_for_revocation(wire):
    wire(b"")
    assert providers.request("POST", "https://auth.openai.com/api/accounts/oauth/revoke") == {}


@pytest.mark.parametrize("provider,response", [
    ("openai", {}), ("openai", {"status": "completed"}), ("openai", {"status": "incomplete"}),
    ("anthropic", {"stop_reason": "max_tokens"}), ("anthropic", {"stop_reason": "end_turn"}),
    ("openrouter", {"choices": []}), ("openrouter", {"choices": [{"finish_reason": "length"}]}),
    ("openrouter", {"choices": [{"finish_reason": "stop"}]}),
])
def test_unsupported_and_truncated_completion_fails(engine, wire, provider, response):
    engine.vault.write(provider, {"api_key": "fictional-key"})
    wire(response)
    with pytest.raises(StoreError):
        providers.complete(engine.vault, provider, "test-model", {})


def test_invalid_model_catalog(engine, wire):
    wire({})
    with pytest.raises(StoreError, match="model catalog"):
        providers.models(engine.vault, "openai")


def sse(*events):
    return b"".join(("data: " + json.dumps(event) + "\n\n").encode() for event in events)


@pytest.mark.parametrize("mode", ["deltas", "done", "both", "multiple_parts"])
def test_subscription_text_survives_empty_terminal_output(engine, wire, monkeypatch, mode):
    """The public subscription route can omit final output after streaming it."""
    monkeypatch.setattr("whab_plugin_llm_categorization.oauth.access_token", lambda _: "fictional-oauth")
    first, second = '{"suggestions":', '[]}'
    events = []
    if mode in {"deltas", "both"}:
        events.extend({"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": value}
                      for value in (first, second))
    if mode in {"done", "both"}:
        events.append({"type": "response.output_text.done", "output_index": 0, "content_index": 0, "text": first + second})
    if mode == "multiple_parts":
        events.extend([
            {"type": "response.output_text.done", "output_index": 1, "content_index": 0, "text": second},
            {"type": "response.output_text.done", "output_index": 0, "content_index": 1, "text": first[5:]},
            {"type": "response.output_text.done", "output_index": 0, "content_index": 0, "text": first[:5]},
        ])
    events.append({"type": "response.completed", "response": {"status": "completed", "output": []}})
    wire(sse(*events))
    assert providers.complete(engine.vault, "chatgpt", "fictional-model", {}) == first + second


@pytest.mark.parametrize("terminal", ["response.failed", "response.incomplete", "error", "missing"])
def test_text_events_never_bypass_terminal_success(wire, terminal):
    events = [{"type": "response.output_text.done", "output_index": 0, "content_index": 0, "text": '{"suggestions":[]}'}]
    events.append({"type": terminal})
    wire(sse(*events))
    with pytest.raises(StoreError):
        providers.request("POST", "https://api.openai.com/v1/responses", stream=True)


@pytest.mark.parametrize("event", [
    [], {"type": "response.output_text.delta"},
    {"type": "response.output_text.delta", "output_index": False, "content_index": 0, "delta": "x"},
    {"type": "response.output_text.delta", "output_index": 0, "content_index": -1, "delta": "x"},
    {"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": None},
    {"type": "response.output_text.done", "output_index": 0, "content_index": 0, "text": {}},
])
def test_malformed_stream_text_is_rejected_without_private_error_details(wire, event):
    wire(sse(event, {"type": "response.completed", "response": {"status": "completed", "output": []}}))
    with pytest.raises(StoreError, match="valid response"):
        providers.request("POST", "https://api.openai.com/v1/responses", stream=True)


@pytest.mark.parametrize("provider", ["openai", "chatgpt", "anthropic", "openrouter"])
@pytest.mark.parametrize("effort", ["default", "medium"])
def test_visible_prompt_and_effort_are_sent_on_the_provider_wire(engine, wire, monkeypatch, provider, effort):
    engine.vault.write(provider, {"api_key": "fictional-key"})
    monkeypatch.setattr("whab_plugin_llm_categorization.oauth.access_token", lambda _: "fictional-oauth")
    response = {"status": "completed", "output": []}
    if provider == "chatgpt":
        response = sse({"type": "response.completed", "response": response})
    elif provider == "anthropic":
        response = {"stop_reason": "end_turn", "content": []}
    elif provider == "openrouter":
        response = {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]}
    sent = wire(response)
    guidance = "Choose the closest category.\nAbstain when uncertain."
    providers.complete(engine.vault, provider, "test-model", {"transactions": []}, guidance, effort)
    payload = json.loads(sent[0].content)
    expected = guidance + "\n\n" + providers.INSTRUCTIONS
    assert expected == (payload["instructions"] if provider in {"chatgpt", "openai"} else payload["system"] if provider == "anthropic" else payload["messages"][0]["content"])
    key = "output_config" if provider == "anthropic" else "reasoning"
    if effort == "default":
        assert key not in payload
    else:
        assert payload[key] == {"effort": effort}
