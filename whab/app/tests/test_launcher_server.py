"""The native launcher's compatibility runner retains the local HTTPS boundary."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SERVER = Path(__file__).resolve().parents[2] / "launchers/macos/manager/server.py"
SPEC = importlib.util.spec_from_file_location("whab_launcher_server", SERVER)
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


@pytest.fixture
def client():
    application = FastAPI()

    @application.get("/ordinary")
    def ordinary():
        return {"delegated": True}

    wrapped = server.ManagedApplication(
        application, port=5173, version="2.2.0", build="a" * 40, instance=str(uuid4()),
    )
    with TestClient(wrapped, base_url="https://127.0.0.1:5173") as client:
        yield client


def test_legacy_application_gets_process_identity_without_bank_or_budget_routes(client):
    result = client.get("/api/health")
    assert result.status_code == 200
    assert result.json()["application"] == "whab"
    assert result.json()["version"] == "2.2.0"
    assert result.json()["build"] == "a" * 40
    assert result.headers["cache-control"] == "no-store"
    assert client.get("/ordinary").json() == {"delegated": True}


@pytest.mark.parametrize(("url", "headers", "expected"), [
    ("http://127.0.0.1:5173/api/health", {"X-Forwarded-Proto": "https"}, 400),
    ("https://127.0.0.1:5173/api/health", {"Host": "attacker.invalid"}, 403),
    ("https://127.0.0.1:5173/api/health", {"Host": "[invalid"}, 403),
    ("https://127.0.0.1:5173/api/health", {"Origin": "http://127.0.0.1:5173"}, 403),
    ("https://127.0.0.1:5173/api/health", {"Origin": "https://attacker.invalid"}, 403),
    ("https://127.0.0.1:5173/api/health", {"Sec-Fetch-Site": "cross-site"}, 403),
])
def test_wrapper_readiness_rejects_plaintext_and_cross_origin_requests(client, url, headers, expected):
    assert client.get(url, headers=headers).status_code == expected


def test_wrapper_readiness_has_no_remote_process_control(client):
    response = client.post("/api/health", json={"command": "stop"})
    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
