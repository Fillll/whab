"""Installed bundled registration identities, independent of plugin execution."""

# Registration identity is package metadata, so disabled plugins remain unloaded.
_BUNDLED_METADATA = {
    ("ai-credentials", "whab_plugin_ai_credentials.plugin:plugin"): ("automation", "AI credentials"),
    ("llm-categorization", "whab_plugin_llm_categorization.plugin:plugin"): ("automation", "AI transaction categorization"),
    ("subscriptions", "whab_plugin_subscriptions.plugin:plugin"): ("automation", "Subscriptions"),
    ("subscriptions-ai", "whab_plugin_subscriptions_ai.plugin:plugin"): ("automation", "AI subscription classification"),
    ("cli", "whab_plugin_cli.plugin:plugin"): ("interface", "Command line interface"),
    ("random-forest-categorization", "whab_plugin_random_forest_categorization.plugin:plugin"): ("automation", "Random forest categorization"),
    ("bank-logos", "whab_plugin_bank_logos.plugin:plugin"): ("interface", "Bank logos"),
    ("web", "whab_plugin_web.plugin:plugin"): ("interface", "WHAB web"),
    ("plaid", "whab_plugin_plaid.plugin:plugin"): ("ingestion", "Plaid"),
    ("ynab", "whab_plugin_ynab.plugin:plugin"): ("ingestion", "Import from YNAB ZIP file"),
    ("sqlite", "whab_plugin_sqlite.plugin:plugin"): ("storage", "SQLite"),
    ("json", "whab_plugin_json.plugin:plugin"): ("storage", "JSON"),
    ("client", "whab_plugin_client.plugin:plugin"): ("storage", "Remote budget client"),
    ("server", "whab_plugin_server.plugin:plugin"): ("interface", "Budget API server"),
    ("example", "whab_plugin_example:plugin"): ("example", "Month totals example"),
    ("example-branding", "whab_plugin_example_branding:plugin"): ("example", "Fictional branding"),
    ("example-file-import", "whab_plugin_example_file_import:plugin"): ("example", "Fictional account import"),
    ("example-categorization", "whab_plugin_example_categorization:plugin"): ("example", "Exact merchant teaching model"),
    ("example-api", "whab_plugin_example_api:plugin"): ("example", "Fictional API interface"),
    ("example-ui", "whab_plugin_example_ui:plugin"): ("example", "Fictional visual interface"),
    ("example-bank", "whab_plugin_example_bank:plugin"): ("example", "Offline fictional bank"),
    ("example-remote-client", "whab_plugin_example_remote_client:plugin"): ("example", "Fictional remote ledger client"),
    ("example-storage", "whab_plugin_example_storage:plugin"): ("example", "Example independent snapshot storage"),
}


def bundled_metadata(name, entries):
    """Resolve only unambiguous bundled registration identities."""
    if len(entries) == 1:
        return _BUNDLED_METADATA.get((name, getattr(entries[0], "value", None)))
    return None


def management_tool(name, entries):
    """The installed console manages budgets independently of their selections."""
    return name == "cli" and bundled_metadata(name, entries) is not None

