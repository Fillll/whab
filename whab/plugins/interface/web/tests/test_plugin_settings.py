"""Plugin settings are discoverable, escaped and separate from pending selection."""

import re

import pytest


def plugin(identifier, **changes):
    return {
        "id": identifier,
        "display_name": identifier.title(),
        "distribution": f"whab-{identifier}",
        "version": "1.0.0",
        "category": "extension",
        "kinds": [],
        "active": False,
        "enabled": False,
        "required": False,
        "manageable": True,
        "reason": None,
        **changes,
    }


def dialog(text, identifier):
    return re.search(rf'<dialog\b[^>]*\bid="{identifier}".*?</dialog>', text, re.S)[0]


def render(client, monkeypatch, *plugins, restart_required=False, error=None):
    state = {"plugins": list(plugins), "restart_required": restart_required, "error": error}
    monkeypatch.setattr("whab.plugins.management.PluginManager.projection", lambda _: state)
    response = client.get("/?workspace=demo")
    assert response.status_code == 200
    return response.text


def test_settings_links_to_installed_plugins_with_all_categories_as_default(client, monkeypatch):
    text = render(
        client, monkeypatch,
        plugin("web", active=True, enabled=True, required=True, manageable=False, category="interface"),
        plugin("report", category="insight"),
    )
    settings = dialog(text, "settings-dialog")
    assert 'data-dialog="plugins-dialog"' in settings
    assert "Installed plugins and their options" in settings
    assert "data-plugin-selection" not in settings
    installed = dialog(text, "plugins-dialog")
    assert 'data-plugin-category-filter aria-label="Plugin category"' in installed
    assert '<option value="all" selected>All categories</option>' in installed
    for category, label in (
        ("interface", "Interfaces"), ("ingestion", "Ingestion"), ("insight", "Insights"),
        ("storage", "Storage"), ("automation", "Automation"),
        ("extension", "Extensions"), ("example", "Examples"),
    ):
        assert f'<option value="{category}">{label}</option>' in installed
    assert 'data-plugin-category="interface"' in installed
    assert 'data-plugin-category="insight"' in installed
    assert 'value="web" checked disabled' in installed
    assert 'value="report" name="plugin"' in installed
    assert 'data-dialog="plugin-options-web" data-plugin-options' in installed
    assert 'data-dialog="plugin-options-report" data-plugin-options' in installed
    assert 'data-plugin-category-empty hidden' in installed
    assert 'data-close-dialog' in installed and '>Settings</button>' in installed


@pytest.mark.parametrize(("changes", "selection", "runtime", "guidance"), [
    ({"active": True, "enabled": True}, "Enabled", "Running", None),
    ({"active": True}, "Will disable after restart", "Running", None),
    ({"enabled": True}, "Will enable after restart", "Not running", "Restart WHAB to view"),
    ({}, "Disabled", "Not running", "Enable this plugin, save your selection, and restart"),
    ({"active": True, "enabled": True, "required": True}, "Required", "Running", None),
])
def test_each_plugin_has_options_with_saved_and_running_state(client, monkeypatch, changes, selection, runtime, guidance):
    text = render(client, monkeypatch, plugin("sample", **changes), restart_required=True)
    options = dialog(text, "plugin-options-sample")
    assert '<h2>Sample</h2>' in options and "Plugin options" in options
    assert '<dt>Selection</dt><dd>' + selection in options
    assert '<dt>Runtime</dt><dd>' + runtime in options
    assert '<dt>Plugin</dt><dd>whab-sample · 1.0.0</dd>' in options
    assert '>Installed plugins</button>' in options
    assert f'data-plugin-active="{str(changes.get("active", False)).lower()}"' in options
    if guidance:
        assert guidance in options
    else:
        assert '<section class="plugin-settings" aria-label="Plugin settings" data-plugin-settings></section>' in options
    assert "Restart WHAB to apply your saved plugin selection" in dialog(text, "plugins-dialog")


def test_metadata_and_errors_are_escaped_and_disabled_selection_is_locked(client, monkeypatch):
    text = render(
        client, monkeypatch,
        plugin("unsafe", display_name="<script>name</script>", distribution="<b>package</b>",
               version=None, manageable=False, reason="<img src=x onerror=alert(1)>"),
        error="<script>failure</script>",
    )
    assert '<script>name</script>' not in text and '&lt;script&gt;name&lt;/script&gt;' in text
    assert '&lt;b&gt;package&lt;/b&gt;' in text
    assert '<img src=x onerror=alert(1)>' not in text
    assert '&lt;img src=x onerror=alert(1)&gt;' in text
    assert '&lt;script&gt;failure&lt;/script&gt;' in text
    assert 'value="unsafe" name="plugin" disabled' in text
    assert 'aria-label="Options for &lt;script&gt;name&lt;/script&gt;"' in text
    assert 'id="plugin-options-unsafe"' in text


def test_empty_installed_list_explains_what_is_available(client, monkeypatch):
    text = render(client, monkeypatch)
    assert "No installed plugins were found." in dialog(text, "plugins-dialog")
    assert "data-plugin-options" not in text
