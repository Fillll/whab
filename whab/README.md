# WHAB

**We Have A Budget**

A highly extensible budgeting app built with Python, with monthly envelopes and rollover, transaction review, spending analytics and manual accounts. Plugins supply replaceable interfaces, storage, imports, reports and additional workflows.

See the [architecture and Mermaid diagrams](../README.md#architecture), [runtime architecture](docs/architecture.md) and [budget and plugin model](docs/target-architecture.md). Current version: **1.9.0** ([changelog](CHANGELOG.md)).

The [client/server deployment option](../README.md#clientserver-deployment) uses the same architecture: a client plugin connects an interface to a server plugin exposing an API interface capability.

## Run locally

Requires **Python 3.14 or newer**. Run these commands from this application directory (`whab/`):

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python scripts/setup.py
.venv/bin/python -m whab
```

Open [127.0.0.1:5173](http://127.0.0.1:5173). Explore fictional data in the demo workspace, or choose **Settings → Budgets → My budget** and add a manual account. Settings shows the current budget. No Plaid credentials are needed for manual budgeting. Use `--reload` while developing or `--port 5174` for another local port.

Setup creates a private `.env` and token encryption key if missing. Runtime files live in the launch directory, or the absolute directory set by `WHAB_HOME`. Keep the existing runtime home, data and encryption key together when upgrading, and restart after configuration changes. See [runtime setup](docs/usage.md#runtime-setup) for upgrades and saved-data compatibility.

## Configuration and plugins

The core owns budgeting rules, HTTP access checks and plugin contracts. Concrete implementations live under `plugins/<category>/<id>/`, each with its own source, assets, tests and documentation. The singular categories are `interface`, `ingestion`, `insight`, `storage`, `automation`, `extension` and `example`; see [plugin folders and naming](plugins/README.md).

| Setting | Default | Purpose |
| --- | --- | --- |
| `WHAB_HOME` | Launch directory | Location of private device configuration and runtime data |
| `WHAB_BUDGET` | Unset | Portable `.whab` file to open; also available as `--budget` |
| `WHAB_ENV` | `sandbox` | Sandbox or production data; `PLAID_ENV` is the legacy fallback |
| `WHAB_STORAGE` | `sqlite` | Required primary storage backend |
| `WHAB_UI` | `web` | Visual interface; an empty value runs APIs headlessly |
| `WHAB_PLUGINS` | `plaid` | Optional extensions when no saved `plugins.json` exists |

Use **Settings → Budgets** to create or open a portable `.whab` budget, switch between opened budgets, or download the active budget. A portable budget carries a required manifest and plugin-owned data: local SQLite budgets include their database, while remote budgets include connection information without downloading financial records. Credentials remain with the device by default; optional password-protected export can include the remote access credential. Bank-provider tokens are never included. See [portable budgets](docs/usage.md#portable-budgets) for command-line opening and additional saved copies.

Use **Settings → Plugins → Installed** to enable or disable installed optional plugins for the active budget, save, and restart. Portable budgets save their own selections. The original local workspace keeps its saved `plugins.json` selection, which takes precedence over `WHAB_PLUGINS`. Disabling a bank plugin retains imported history and saved connections. Bank logos are bundled but disabled by default; the [example report plugin](plugins/example/example/README.md) is separately installable and hidden from Settings. Valid existing example selections are preserved when saving other plugins; unavailable example registrations are dropped so they cannot block saving. Categories and plugin names are alphabetized, with distinct category headings. Choose **All categories** or a category to browse installed plugins, then open **Options** for plugin details and controls. Bank logos options include **Flush cache**; successful logos stay cached locally for 365 days across restarts and refresh on the next image request after expiry. Plugins run as trusted server code.

For bank imports, follow the [Plaid Sandbox guide](docs/usage.md#connect-plaid-sandbox). Keep `WHAB_ENV=sandbox` and `ALLOW_LIVE_PLAID=false` for test data. Production access requires deliberate configuration and institution access; a successful Sandbox flow does not establish live access.

Plaid merchant logos are included with the existing **Plaid** plugin. Transactions display the artwork supplied by Plaid when available; missing artwork leaves the payee text visible. Only public logo files are downloaded, through guarded local image routes with a bounded memory cache (24 hours for successes, five minutes for failures). No merchant lookup service, LLM key or separate merchant-logo plugin is needed. Existing imported transactions receive artwork only when Plaid supplies them again in an import or update; enabling this feature does not reconnect a bank or automatically backfill history. See the [Plaid plugin guide](plugins/ingestion/plaid/README.md).

For a YNAB ZIP dump, enable **ynab** in the plugin manager, save, and restart. In an empty **My budget → Accounts**, choose the ZIP, confirm USD, preview the counts and limitations, then explicitly import. Use a fresh `WHAB_HOME` for migration when your current budget already has financial data. Preview never saves financial records. See the [YNAB plugin guide](plugins/ingestion/ynab/README.md) for supported formats and migration limits.

## Limits and data safety

- **Default local access:** WHAB binds to `127.0.0.1` and checks Host/origin. The optional server/client pair exposes scoped remote budget commands over authenticated HTTPS; the visual app has no family login system.
- **Multiple currencies:** accounts and child categories declare an ISO currency; each currency has separate balances, activity, rollover and allocations. There is no automatic conversion. Money uses integer currency minor units. Plaid and the bundled YNAB importer currently accept USD only.
- **Two category levels:** parent groups organize child categories. Only children receive transactions, targets and allocations. Core dates use `YYYY-MM-DD`; USD money uses `$1,234.56` (other currencies use their ISO code and fixed minor-unit precision). A selected interface plugin can override presentation.
- **Initial budgeting model:** credit-card handling conservatively reserves debt. Payment categories, splits, manual/import reconciliation, editable transfer matching, merchant rules, recurring bills and goals editing are not implemented.
- **Transactions only:** investment holdings are not imported. Bank history and refresh timing depend on the institution and Plaid.
- **Private local files:** keep `.env`, `plugins.json`, `data/`, `cache/`, `work/` and `.venv/` out of Git. Databases contain private financial data. Back up the database and encryption key separately; replacing the key makes existing bank tokens unreadable.

See [budget behavior](docs/usage.md#budget-scope), [import handling](docs/usage.md#importing-and-data-handling) and [backup guidance](docs/usage.md#local-access-and-private-files) for details.

## Development

Run checks from this directory:

```sh
.venv/bin/python -m pytest
.venv/bin/python scripts/check_plugin_coverage.py
.venv/bin/python -m ruff check src scripts tests plugins conftest.py
.venv/bin/python scripts/check_distribution.py
```

Tests use fictional data and mocked services. CI checks Python 3.14 and 3.15 and requires 100% Python statement and branch coverage for every plugin.

- [Local setup and usage](docs/usage.md) — runtime files, plugin manager, Plaid, budgets and backups
- [Budget and plugin model](docs/target-architecture.md) and [runtime architecture](docs/architecture.md)
- [Plugin author guide](docs/plugins.md) and [folder ownership](plugins/README.md)
- [Contributing](CONTRIBUTING.md) — checks, PR workflow, SemVer compatibility and branch protection

## License

WHAB's code, public plugin SDK, bundled plugins, example plugin, documentation and assets use the [MIT License](LICENSE). Commercial use, proprietary plugins and commercial forks are permitted with the required notices. Third-party dependencies, services and institution logos retain their own licenses or terms.
