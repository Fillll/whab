"""Appearance belongs to the running web plugin and its own browser assets."""

import re
from html.parser import HTMLParser
from importlib.metadata import version
from importlib.resources import files
from urllib.parse import parse_qs, urlparse

import pytest


class Tags(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def dialog(text, identifier):
    return re.search(rf'<dialog\b[^>]*\bid="{identifier}".*?</dialog>', text, re.S)[0]


def render_plugins(client, monkeypatch, *, web_active=True, web_enabled=True):
    plugins = [
        {"id": "web", "display_name": "WHAB web", "category": "interface",
         "active": web_active, "enabled": web_enabled},
        {"id": "report", "display_name": "Report", "category": "insight",
         "active": True, "enabled": True},
    ]
    for plugin in plugins:
        plugin.update(distribution=f"whab-{plugin['id']}", version="1.0.0", kinds=[],
                      required=False, manageable=True, reason=None)
    state = {"plugins": plugins, "restart_required": False, "error": None}
    monkeypatch.setattr("whab.plugins.management.PluginManager.projection", lambda _: state)
    response = client.get("/budgets/demo/budget/current-month/usd")
    assert response.status_code == 200
    return response.text


def test_appearance_is_an_accessible_two_choice_setting_only_in_web_options(client, monkeypatch):
    text = render_plugins(client, monkeypatch)
    options = dialog(text, "plugin-options-web")
    fieldsets = re.findall(r"<fieldset\b.*?</fieldset>", options, re.S)
    assert len(fieldsets) == 1
    appearance = fieldsets[0]
    assert re.findall(r"<legend>(.*?)</legend>", appearance) == ["Appearance"]

    inputs = [attrs for tag, attrs in Tags(appearance).tags if tag == "input"]
    assert [attrs.get("type") for attrs in inputs] == ["radio", "radio"]
    assert [attrs.get("value") for attrs in inputs] == ["light", "dark"]
    assert inputs[0]["name"] == inputs[1]["name"]
    # Each native radio has a visible, enclosing label and can use arrow keys.
    labels = re.findall(r"<label\b[^>]*>(.*?)</label>", appearance, re.S)
    assert len(labels) == 2
    for label, choice in zip(labels, ("Light", "Dark"), strict=True):
        assert len([tag for tag, _ in Tags(label).tags if tag == "input"]) == 1
        assert re.sub(r"<[^>]*>", "", label).strip().startswith(choice)
    statuses = [attrs for _, attrs in Tags(appearance).tags if attrs.get("role") == "status"]
    assert len(statuses) == 1 and statuses[0].get("aria-live") == "polite"
    assert "immediately" in appearance and "this browser" in appearance

    # Appearance survives the separately loaded generic settings section.
    generic_settings = re.search(r"<section\b[^>]*data-plugin-settings.*?</section>", options, re.S)[0]
    assert "data-appearance-choice" not in generic_settings
    assert "data-appearance-choice" not in dialog(text, "settings-dialog")
    assert "data-appearance-choice" not in dialog(text, "plugins-dialog")
    assert "data-appearance-choice" not in dialog(text, "plugin-options-report")


@pytest.mark.parametrize("enabled", [True, False])
def test_inactive_web_does_not_offer_appearance_controls(client, monkeypatch, enabled):
    text = render_plugins(client, monkeypatch, web_active=False, web_enabled=enabled)
    assert "data-appearance-choice" not in dialog(text, "plugin-options-web")
    assert "data-appearance-choice" not in dialog(text, "plugin-options-report")


@pytest.mark.parametrize("page", ["budget", "transactions", "accounts", "insights"])
def test_theme_bootstrap_precedes_styles_and_uses_web_plugin_asset_version(client, monkeypatch, page):
    # A host-only version change must not masquerade as the web plugin version.
    monkeypatch.setattr("whab.web.routes.pages.__version__", "98.7.6")
    response = client.get(f"/budgets/demo/{page}/current-month/usd")
    assert response.status_code == 200
    head = response.text.split("</head>", 1)[0]
    tags = Tags(head).tags
    script_index, script = next(
        (index, attrs) for index, (tag, attrs) in enumerate(tags)
        if tag == "script" and urlparse(attrs.get("src", "")).path == "/budgets/demo/static/theme.js"
    )
    style_index, stylesheet = next(
        (index, attrs) for index, (tag, attrs) in enumerate(tags)
        if tag == "link" and attrs.get("rel") == "stylesheet"
    )
    assert script_index < style_index
    assert not {"async", "defer", "type"}.intersection(script)
    plugin_version = version("whab-web-plugin")
    for attrs in (script, stylesheet):
        assert parse_qs(urlparse(attrs.get("src", attrs.get("href"))).query) == {"v": [plugin_version]}
    assert "WHAB · 98.7.6" in response.text

    asset = client.get(script["src"])
    assert asset.status_code == 200
    assert "javascript" in asset.headers["content-type"]
    assert asset.content == files("whab_web").joinpath("static/theme.js").read_bytes()
