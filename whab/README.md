# WHAB — We Have A Budget

A local Python budgeting app with monthly envelopes and rollover, transaction review, spending analytics, manual accounts, CSV export, and optional Plaid Transactions imports. FastAPI serves Jinja pages with a small browser script; no Node.js build is required.

See the [proposed architecture and Mermaid diagram](../README.md#architecture) and [current implementation](docs/architecture.md). Current version: **1.3.2** ([changelog](CHANGELOG.md)).

## Run locally

Requires **Python 3.14 or newer**. Run these commands from this application directory (`whab/`):

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python scripts/setup.py
.venv/bin/python -m whab
```

Open [127.0.0.1:5173](http://127.0.0.1:5173). Explore fictional data in the demo workspace, or switch to **My budget** and add a manual account. No Plaid credentials are needed for manual budgeting. Use `--reload` while developing or `--port 5174` for another local port.

Setup creates a private `.env` and token encryption key if missing. Runtime files live in the launch directory, or the absolute directory set by `WHAB_HOME`. Keep the existing runtime home, data and encryption key together when upgrading, and restart after configuration changes. See [runtime setup](docs/usage.md#runtime-setup) for upgrades and existing IHAB data.

## Configuration and plugins

The core owns budgeting rules, HTTP access checks and plugin contracts. Concrete implementations live under `plugins/<id>/`, each with its own source, assets, tests and documentation.

| Setting | Default | Purpose |
| --- | --- | --- |
| `WHAB_HOME` | Launch directory | Location of `.env`, `plugins.json` and data |
| `WHAB_ENV` | `sandbox` | Sandbox or production data; `PLAID_ENV` is the legacy fallback |
| `WHAB_STORAGE` | `sqlite` | Exactly one required storage backend |
| `WHAB_UI` | `web` | Visual interface; an empty value runs APIs headlessly |
| `WHAB_PLUGINS` | `plaid` | Optional extensions when no saved `plugins.json` exists |

Use **Settings → Plugin manager** to enable or disable installed optional plugins, save, and restart. Its saved selection takes precedence over `WHAB_PLUGINS`. Disabling a bank plugin retains imported history and saved connections. Bank logos are bundled but disabled by default; the [example report plugin](plugins/example/README.md) is separately installable. Plugins run as trusted server code.

For bank imports, follow the [Plaid Sandbox guide](docs/usage.md#connect-plaid-sandbox). Keep `WHAB_ENV=sandbox` and `ALLOW_LIVE_PLAID=false` for test data. Production access requires deliberate configuration and institution access; a successful Sandbox flow does not establish live access.

## Limits and data safety

- **Local only:** WHAB binds to `127.0.0.1` and checks Host/origin, but has no family authentication or authorization. Shared deployment needs both.
- **USD only:** money uses integer cents. Pre-budget history remains available for analytics; imported income is not added again to bank balance snapshots.
- **Initial budgeting model:** credit-card handling conservatively reserves debt. Payment categories, splits, manual/import reconciliation, editable transfer matching, merchant rules, recurring bills and goals editing are not implemented.
- **Transactions only:** investment holdings are not imported. Bank history and refresh timing depend on the institution and Plaid.
- **Private local files:** keep `.env`, `plugins.json`, `data/`, `work/` and `.venv/` out of Git. Databases contain private financial data. Back up the database and encryption key separately; replacing the key makes existing bank tokens unreadable.

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
- [Target architecture](docs/target-architecture.md) and [current implementation](docs/architecture.md)
- [Plugin author guide](docs/plugins.md) and [folder ownership](plugins/README.md)
- [Contributing](CONTRIBUTING.md) — checks, PR workflow, SemVer compatibility and pending branch protection

## License

WHAB's code, public plugin SDK, bundled plugins, example plugin, documentation and assets use the [MIT License](LICENSE). Commercial use, proprietary plugins and commercial forks are permitted with the required notices. Third-party dependencies, services and institution logos retain their own licenses or terms.
