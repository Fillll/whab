"""Desktop readiness is private to local HTTPS and independent of banking."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from whab import __version__
from whab.app import create_app
from whab.config import Settings
from whab.plugins import PluginRegistry


def application(tmp_path, monkeypatch, store, build="a" * 40, instance=None):
    monkeypatch.setenv("WHAB_BUILD_COMMIT", build)
    monkeypatch.setenv("WHAB_INSTANCE_ID", instance or "")
    settings = Settings(
        home=tmp_path, port=5173, plaid_env="sandbox", plaid_client_id="",
        plaid_secret="", encryption_key="", allow_live=False, webhook_url="",
        redirect_uri="", ui="", plugins=(),
    )
    return create_app(store=store, settings=settings, plugins=PluginRegistry())


def test_readiness_returns_only_validated_application_identity(tmp_path, monkeypatch, store):
    instance = str(uuid4())
    app = application(tmp_path, monkeypatch, store, instance=instance)
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {
            "application": "whab", "ready": True, "version": __version__,
            "build": "a" * 40, "instance": instance,
        }
        assert response.headers["cache-control"] == "no-store"


def test_unmanaged_readiness_cannot_echo_arbitrary_environment_values(tmp_path, monkeypatch, store):
    app = application(tmp_path, monkeypatch, store, build="synthetic-private-value", instance="invalid")
    with TestClient(app, base_url="https://127.0.0.1:5173") as client:
        result = client.get("/api/health").json()
        assert result["build"] == "development"
        assert result["instance"] is None


@pytest.mark.parametrize(("url", "headers", "expected"), [
    ("http://127.0.0.1:5173/api/health", {"X-Forwarded-Proto": "https"}, 400),
    ("https://127.0.0.1:5173/api/health", {"Host": "untrusted.invalid"}, 403),
    ("https://127.0.0.1:5173/api/health", {"Origin": "https://untrusted.invalid"}, 403),
    ("https://127.0.0.1:5173/api/health", {"Sec-Fetch-Site": "cross-site"}, 403),
])
def test_readiness_retains_https_host_and_origin_guards(tmp_path, monkeypatch, store, url, headers, expected):
    with TestClient(application(tmp_path, monkeypatch, store)) as client:
        assert client.get(url, headers=headers).status_code == expected
