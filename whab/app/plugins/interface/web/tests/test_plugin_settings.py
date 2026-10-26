"""Plugin settings are discoverable, escaped and separate from pending selection."""

import re
from html.parser import HTMLParser
from urllib.parse import urlparse

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
        "installed": True,
        "dependencies": [],
        **changes,
    }


def dialog(text, identifier):
    return re.search(rf'<dialog\b[^>]*\bid="{identifier}".*?</dialog>', text, re.S)[0]


class Tags(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def render(client, monkeypatch, *plugins, restart_required=False, error=None):
    state = {"plugins": list(plugins), "restart_required": restart_required, "error": error}
    monkeypatch.setattr("whab.plugins.management.PluginManager.projection", lambda _: state)
    response = client.get("/budgets/demo/budget/current-month/usd")
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
    assert "Installed plugins and their settings" in settings
    assert "data-plugin-selection" not in settings
    installed = dialog(text, "plugins-dialog")
    assert 'data-plugin-category-filter aria-label="Plugin category"' in installed
    assert '<option value="all" selected>All categories</option>' in installed
    for category, label in (
        ("interface", "Interfaces"), ("ingestion", "Ingestion"), ("insight", "Insights"),
        ("storage", "Storage"), ("automation", "Automation"),
        ("extension", "Extensions"),
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


@pytest.mark.parametrize(("workspace", "label"), [("demo", "Demo budget"), ("personal", "My budget")])
def test_budget_selection_lives_in_settings_and_preserves_page_context(client, workspace, label):
    response = client.get(
        f"/budgets/{workspace.replace('personal', 'home')}/transactions/2026-09/eur?category=old-category"
    )
    assert response.status_code == 200
    text = response.text
    settings = dialog(text, "settings-dialog")
    assert re.findall(r'data-dialog="([^"]+)"', settings) == [
        "budgets-dialog", "plugins-dialog", "about-dialog",
    ]
    assert f"Current budget · {label}" in settings
    budgets = dialog(text, "budgets-dialog")
    switch = re.search(r'<form\b[^>]*\bdata-budget-switch\b.*?</form>', budgets, re.S)[0]
    attributes = next(attrs for tag, attrs in Tags(switch).tags if tag == "form")
    for destination in ("personal", "demo"):
        href = attributes[f"data-{destination}-url"]
        address = urlparse(href)
        key = "my-budget" if destination == "personal" else "demo"
        assert address.path == f"/budgets/{key}/transactions/2026-09/eur"
        assert address.query == ""
        switched = client.get(href)
        assert switched.status_code == 200
        next_label = "Demo budget" if destination == "demo" else "My budget"
        assert f"Current budget · {next_label}" in dialog(switched.text, "settings-dialog")
    fieldset = re.search(r'<fieldset\b[^>]*\bdata-available-budgets\b.*?</fieldset>', switch, re.S)[0]
    assert 'class="budget-list"' in fieldset
    choices = [attrs for tag, attrs in Tags(fieldset).tags if tag == "input"]
    assert len(choices) == 2
    assert [choice["data-workspace"] for choice in choices] == ["personal", "demo"]
    for choice in choices:
        assert choice["type"] == "radio" and choice["name"] == "budget"
        assert choice["data-budget-id"]
        assert choice["value"] == f'{choice["data-budget-id"]}:{choice["data-workspace"]}'
        assert ("checked" in choice) == (choice["data-workspace"] == workspace)
    assert budgets.count('class="budget-current"') == 1
    labels = re.findall(r'<label\b[^>]*\bclass="budget-choice-select".*?</label>', fieldset, re.S)
    assert len(labels) == 2
    for row_label in labels:
        assert len([tag for tag, _ in Tags(row_label).tags if tag == "input"]) == 1
        assert "data-budget-download" not in row_label
    downloads = [attrs for tag, attrs in Tags(fieldset).tags if tag == "button"]
    assert len(downloads) == 2
    for download, name, destination in zip(
        downloads, ("My budget", "Demo budget"), ("personal", "demo"), strict=True,
    ):
        assert download["type"] == "button"
        assert download["class"] == "button secondary"
        assert "data-budget-download" in download
        assert download["data-budget-id"] == choices[0]["data-budget-id"]
        assert download["data-workspace"] == destination
        assert download["data-name"] == name
        assert download["aria-label"] == f"Download {name}"
    assert 'type="password" name="password"' in switch
    assert '<button class="button primary" type="submit">Open selected budget</button>' in switch
    assert "<select" not in switch
    assert 'class="workspace-switch"' not in text
    assert 'class="mobile-workspace"' not in text
    assert 'data-navigation="workspace"' not in text


@pytest.mark.parametrize("workspace", ["personal", "demo"])
def test_budget_creation_and_file_opening_are_inline_actions_that_leave_the_modal(client, workspace):
    text = client.get(f"/budgets/{workspace.replace('personal', 'home')}/budget/current-month/usd").text
    budgets = dialog(text, "budgets-dialog")
    assert "new-budget-dialog" not in text
    assert budgets.index('id="open-budget-title">Open budget') < budgets.index("data-budget-switch")
    assert budgets.index("data-budget-switch") < budgets.index("Create new budget")
    assert budgets.index("Create new budget") < budgets.index("data-budget-new")
    assert budgets.index("data-budget-new") < budgets.index('id="add-budget-title">Add budget')
    assert budgets.index('id="add-budget-title">Add budget') < budgets.index("data-budget-open")
    assert budgets.index("data-budget-open") < budgets.index('class="budget-advanced"')
    creation = re.search(r'<form\b[^>]*\bdata-budget-new\b.*?</form>', budgets, re.S)[0]
    assert 'class="modal-form" data-budget-new' in creation
    assert 'New budget name<input name="name" required maxlength="100"' in creation
    assert re.search(r'<button class="button primary" type="submit">Create (?:new )?budget</button>', creation)
    assert 'role="alert" hidden' in creation
    opening = re.search(r'<form\b[^>]*\bdata-budget-open\b.*?</form>', budgets, re.S)[0]
    opening_inputs = {attrs["name"]: attrs for tag, attrs in Tags(opening).tags if tag == "input"}
    assert opening_inputs["budget"]["type"] == "file"
    assert opening_inputs["budget"]["accept"] == ".whab,application/zip"
    assert "required" in opening_inputs["budget"]
    assert opening_inputs["password"]["type"] == "password"
    assert opening_inputs["as_copy"]["type"] == "checkbox"
    assert "Add as a separate budget" in opening
    assert '<button class="button primary" type="submit">Add budget</button>' in opening
    assert 'role="alert" hidden' in opening


def test_plugin_categories_and_names_are_sorted_and_examples_are_hidden(client, monkeypatch):
    text = render(
        client, monkeypatch,
        plugin("sqlite", category="storage", display_name="SQLite"),
        plugin("web", category="interface", display_name="WHAB web"),
        plugin("plaid", category="ingestion", display_name="Plaid"),
        plugin("bank-logos", category="interface", display_name="Bank logos"),
        plugin("ynab", category="ingestion", display_name="Import from YNAB ZIP file"),
        plugin("example", category="extension", enabled=True),
        plugin("another-example", category="example"),
        plugin("unavailable-example", category="example", enabled=True, manageable=False),
    )
    installed = dialog(text, "plugins-dialog")
    assert re.findall(r'data-plugin-category="([^"]+)"', installed) == [
        "interface", "ingestion", "insight", "storage", "automation", "extension",
    ]
    assert re.findall(r'<strong>([^<]+)</strong>', installed) == [
        "Bank logos", "WHAB web", "Import from YNAB ZIP file", "Plaid", "SQLite",
    ]
    assert '<h4 id="plugin-category-interface">Interfaces<span>2</span></h4>' in installed
    assert 'value="example">Examples' not in installed
    assert 'data-dialog="plugin-options-example"' not in text
    assert 'id="plugin-options-example"' not in text
    assert 'id="plugin-options-another-example"' not in text
    # Saving a visible selection must not silently disable an already selected example.
    assert '<input type="hidden" name="plugin" value="example">' in installed
    assert 'value="unavailable-example"' not in installed


@pytest.mark.parametrize(("changes", "selection", "runtime", "guidance"), [
    ({"active": True, "enabled": True}, "Enabled", "Running", None),
    ({"active": True}, "Will disable after restart", "Running", None),
    ({"enabled": True}, "Will enable after restart", "Not running", "Apply a supported live selection or restart WHAB to view"),
    ({}, "Disabled", "Not running", "Enable this plugin, then review a supported live selection or save it for restart"),
    ({"active": True, "enabled": True, "required": True}, "Required", "Running", None),
])
def test_each_plugin_has_settings_with_saved_and_running_state(client, monkeypatch, changes, selection, runtime, guidance):
    text = render(client, monkeypatch, plugin("sample", **changes), restart_required=True)
    options = dialog(text, "plugin-options-sample")
    assert '<h2>Sample</h2>' in options and "Plugin settings" in options
    assert 'aria-label="Sample settings"' in options
    assert '<dt>Selection</dt><dd>' + selection in options
    assert '<dt>Runtime</dt><dd>' + runtime in options
    assert '<dt>Plugin</dt><dd>whab-sample · 1.0.0</dd>' in options
    assert '>Installed plugins</button>' in options
    assert f'data-plugin-active="{str(changes.get("active", False)).lower()}"' in options
    if guidance:
        assert guidance in options
    else:
        assert '<section class="plugin-settings" aria-label="Plugin settings" data-plugin-settings></section>' in options
    installed = dialog(text, "plugins-dialog")
    assert "Restart WHAB to apply your saved plugin selection" in installed
    row = re.search(r'<div class="plugin-row">(.*?)</div>', installed, re.S)[1]
    label = re.search(r'<label class="plugin-toggle">(.*?)</label>', row, re.S)[1]
    assert '<strong>Sample</strong>' in label
    assert "whab-sample" not in label  # Package details do not clutter the checkbox's accessible name.
    assert row.index('</label>') < row.index('whab-sample · 1.0.0') < row.index(
        f'<span class="plugin-state">{selection}</span>'
    ) < row.index('aria-label="Settings for Sample"')
    assert re.findall(r'<button\b[^>]*aria-label="([^"]+)"', row) == ["Settings for Sample"]
    assert "Plugin options" not in text and "Options for" not in text


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
    assert 'aria-label="Settings for &lt;script&gt;name&lt;/script&gt;"' in text
    assert 'id="plugin-options-unsafe"' in text


def test_empty_installed_list_explains_what_is_available(client, monkeypatch):
    text = render(client, monkeypatch, plugin("example", category="example"))
    assert "No installed plugins were found." in dialog(text, "plugins-dialog")
    assert "data-plugin-options" not in text


def test_six_empty_sections_marketplace_and_dependency_details_preserve_missing_selections(client, monkeypatch):
    text = render(client, monkeypatch,
                  plugin('missing', installed=False, enabled=True),
                  plugin('consumer', dependencies=[{'id': 'provider', 'version': '>=1,<2'}]),
                  plugin('provider'))
    installed = dialog(text, 'plugins-dialog')
    assert len(re.findall(r'data-plugin-category="', installed)) == 6
    assert installed.count('No installed plugins</p>') == 5
    assert 'data-dialog="plugin-marketplace-dialog"' in installed
    assert 'data-dialog="plugin-options-missing"' not in installed
    assert 'Missing is unavailable' in installed
    assert '<input type="hidden" name="plugin" value="missing">' in installed
    assert 'provider &gt;=1,&lt;2' in dialog(text, 'plugin-options-consumer')
    assert 'Required by: Consumer' in dialog(text, 'plugin-options-provider')
    assert 'data-marketplace' in dialog(text, 'plugin-marketplace-dialog')
