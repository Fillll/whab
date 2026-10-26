"""Visual settings use addressed host controls and coherent public resources."""

import json
import re
from html.parser import HTMLParser
from importlib.metadata import version

import pytest


def dialog(text):
    return re.search(r'<article\b[^>]*id="plugins-dialog".*?</article>', text, re.S)[0]


class Tags(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


@pytest.mark.parametrize("page", ["budget", "transactions", "accounts", "insights"])
@pytest.mark.parametrize("workspace", ["personal", "demo"])
def test_visual_pane_is_accessible_addressed_and_demo_cannot_mutate(client, page, workspace):
    key = "home" if workspace == "personal" else "demo"
    response = client.get(f"/budgets/{key}/{page}/2026-10/usd")
    assert response.status_code == 200
    pane = dialog(response.text)
    assert '<h3 id="visual-settings-title">Visual interface</h3>' in pane
    assert f'data-visual-settings data-workspace="{workspace}"' in pane
    tags = Tags(pane).tags
    if workspace == "personal":
        message = next(attrs for _, attrs in tags if "data-visual-message" in attrs)
        assert message["role"] == "status" and message["aria-live"] == "polite"
        error = next(attrs for _, attrs in tags if "data-visual-error" in attrs)
        assert error["role"] == "alert" and "hidden" in error
        target = next(attrs for _, attrs in tags if "data-visual-target" in attrs)
        assert target["maxlength"] == "64" and target["aria-describedby"] == "visual-target-hint"
        assert 'value="headless"' in pane and "Preview interface change" in pane
        assert "Apply reviewed change" in pane and "Review refreshed status" in pane
        assert "API interface plugins use the optional selection below" in pane
        assert "Review live selection" in pane and "Save selection for restart" in pane
        assert "Saved selections apply when you restart WHAB" not in pane
    else:
        assert "Demo is read-only" in pane
        assert "data-visual-form" not in pane and "data-visual-apply" not in pane
        button = next(attrs for _, attrs in tags if "data-plugin-live-review" in attrs)
        assert "disabled" in button
    context = json.loads(re.search(r'<script id="whab-context"[^>]*>(.*?)</script>', response.text, re.S)[1])
    asset = f"/budgets/{key}/ui/{context['uiGeneration']}/static/visual-settings.js"
    assert f'{asset}?v={version("whab_plugin_web")}' in response.text
    module = client.get(asset)
    assert module.status_code == 200 and "createVisualSettings" in module.text
    assert "innerHTML" not in module.text
    app = client.get(asset.replace("visual-settings.js", "app.js")).text
    assert "initVisualSettings" in app and "source: 'plugin-settings'" in app
    assert "dialog.id !== 'settings-dialog'" in app
    assert "?.id !== 'plugins-dialog'" in app


def test_control_status_is_host_confirmed_and_demo_mutations_are_rejected(client):
    status = client.get("/budgets/home/api/ui/status")
    assert status.status_code == 200
    assert status.json()["active"]["plugin_id"] == "web"
    assert status.json()["active"]["resource_generation"] == status.json()["configured"]["resource_generation"]
    preview = client.post("/budgets/home/api/ui/preview", json={"pluginId": "web", "useInstalled": True})
    assert preview.status_code == 200 and preview.json()["code"] == "installed"
    assert preview.json()["requires_saved_views"] is True
    refused = client.post("/budgets/demo/api/ui/preview", json={"pluginId": None})
    assert refused.status_code == 403
    assert client.get("/budgets/home/api/ui/status").json()["operations"] == []
