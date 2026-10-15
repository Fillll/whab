"""Web plugin composition fixtures use the selected reference storage backend."""

import pytest
from fastapi.testclient import TestClient
from whab_web.plugin import plugin as web_plugin

from whab.app import create_app
from whab.config import Settings
from whab.plugins import PluginRegistry


@pytest.fixture
def client(store, tmp_path):
    settings = Settings(tmp_path, 5173, "sandbox", "", "", "", False, "", "", ())
    with TestClient(
        create_app(store=store, settings=settings, plugins=PluginRegistry((web_plugin,))),
        base_url="http://127.0.0.1:5173",
    ) as client:
        yield client


@pytest.fixture
def client_for():
    def make(store, home, *optional):
        settings = Settings(home, 5173, "sandbox", "", "", "", False, "", "", ())
        return TestClient(
            create_app(store=store, settings=settings, plugins=PluginRegistry((web_plugin, *optional))),
            base_url="http://127.0.0.1:5173",
        )

    return make
