"""Explicit retirement retry stays guarded and cannot decide financial authority."""

import json

import pytest
from fastapi.testclient import TestClient
from whab_plugin_sqlite.plugin import plugin as sqlite_plugin

from whab.app import create_app
from whab.budgets.runtime import RuntimeCandidate, RuntimeResource, RuntimeTransitionError
from whab.config import Settings
from whab.plugins import PluginRegistry


@pytest.fixture
def client(tmp_path, store):
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", (), ui="")
    app = create_app(store=store, settings=settings, plugins=PluginRegistry((sqlite_plugin,)))
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        yield client


def retain_failed_resource(client, *, permanent=False):
    attempts = []
    def close():
        attempts.append(True)
        if permanent or len(attempts) == 1:
            raise OSError("fictional private cleanup details")
    resource = RuntimeResource("fictional", close)
    runtime = client.app.state.budget_runtime
    async def fail_preparation():
        def prepare(preparation):
            preparation.own(resource)
            raise RuntimeError("fictional failed candidate")
        await runtime.transition(prepare)
    with pytest.raises(RuntimeTransitionError):
        client.portal.call(fail_preparation)
    assert runtime.status()["cleanup_failed"] == 1
    return attempts


@pytest.mark.parametrize("permanent", [False, True])
def test_cleanup_reports_remaining_owners_without_repeating_a_mutation(client, permanent):
    attempts = retain_failed_resource(client, permanent=permanent)
    runtime = client.app.state.budget_runtime
    generation = runtime.status()["generation"]
    response = client.post("/api/plugins/runtime/cleanup", json={})
    assert response.status_code == 200, response.text
    status = response.json()["status"]["runtime"]
    assert status["cleanup_failed"] == status["cleanup_pending"] == int(permanent)
    assert status["generation"] == generation and status["admission"] == "open"
    assert len(attempts) == 2
    assert "fictional private" not in response.text


def test_cleanup_control_remains_available_while_gated_and_cannot_resume_authority(client):
    retain_failed_resource(client)
    runtime = client.app.state.budget_runtime
    async def uncertain():
        def prepare(preparation):
            return RuntimeCandidate(preparation.active.services, preparation.active.resources)
        def commit(*_):
            raise OSError("fictional uncertain authority")
        await runtime.transition(prepare, commit=commit)
    with pytest.raises(RuntimeTransitionError):
        client.portal.call(uncertain)
    before = runtime._uncertain
    response = client.post("/api/plugins/runtime/cleanup", json={})
    assert response.status_code == 200, response.text
    status = response.json()["status"]["runtime"]
    assert status["cleanup_pending"] == 0
    assert status["admission"] == "gated" and status["health"] == "recovery_required"
    assert runtime._uncertain is before


@pytest.mark.parametrize("payload", [None, [], {"timeout": 0}, {"force": True}])
def test_cleanup_requires_strict_empty_body(client, payload):
    assert client.post("/api/plugins/runtime/cleanup", content=json.dumps(payload),
                       headers={"Content-Type": "application/json"}).status_code == 400


def test_cleanup_keeps_demo_host_origin_tls_and_content_guards(client):
    path = "/api/plugins/runtime/cleanup"
    assert client.post(path + "?workspace=demo", json={}).status_code == 403
    assert client.post(path, json={}, headers={"Host": "foreign.example"}).status_code == 403
    assert client.post(path, json={}, headers={"Origin": "https://foreign.example"}).status_code == 403
    assert client.post("http://127.0.0.1:5173" + path, json={}).status_code == 400
    assert client.post(path, content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
