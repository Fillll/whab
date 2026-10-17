# Plugin contributor folders

Each plugin belongs to one category and owns one folder containing its Python implementation, assets, tests and documentation:

```text
plugins/<category>/<id>/
  README.md
  CHANGELOG.md           # Dated history for this distribution
  pyproject.toml         # Version, dependencies, entry point and package data
  src/<python_package>/
    __init__.py
    plugin.py
    ...                  # Services, helpers, templates and static assets as needed
  tests/
    conftest.py          # Plugin-specific fixtures, when needed
    test_*.py
  docs/                  # Optional detailed documentation
```

There is no one-file limit. Keep supporting modules and resources within the same plugin folder. A normal contribution to an existing plugin should change that folder alone. Core API changes and behavior spanning plugins also need the corresponding core implementation or contract tests.

## Categories and names

Category folders use singular names:

| Category | Primary responsibility |
| --- | --- |
| [`interface/`](interface/README.md) | Visual presentation, API interfaces and branding |
| [`ingestion/`](ingestion/README.md) | Bringing external data into WHAB |
| [`insight/`](insight/README.md) | Reports, analysis and exports |
| [`storage/`](storage/README.md) | Persistence and backend migrations |
| [`automation/`](automation/README.md) | Categorization rules, smart suggestions and configured workflows |
| [`extension/`](extension/README.md) | Specialized financial models and workflows, such as mortgages or investments |
| [`example/`](example/README.md) | Teaching and reference plugins |

Categories identify the primary source ownership of a plugin. They do not change the SDK descriptor kinds (`user_interface`, `storage`, `data_ingestion`, `reporting` and `branding`) or add runtime capabilities. A plugin can expose multiple supported capabilities while keeping one category folder.

Use lowercase, hyphen-separated names for plugin IDs and folders, such as `bank-logos`. IDs must be unique across categories and match their entry-point names. Use `whab_` followed by the name with underscores for its Python package, such as `whab_bank_logos`. The existing example package retains `whab_example_plugin`. Display names use readable words or the provider's brand, such as “Bank logos” or “Plaid”. Keep category prefixes out of plugin IDs: moving a folder must preserve its ID, saved settings and routes.

| Folder | Plugin ID | Python package | Owns |
| --- | --- | --- | --- |
| `storage/sqlite/` | `sqlite` | `whab_sqlite` | Default ledger backend, schema upgrades and demo seeding |
| `ingestion/plaid/` | `plaid` | `whab_plaid` | Bank normalization, credentials, connection UI and synchronization |
| `ingestion/ynab/` | `ynab` | `whab_ynab` | Local YNAB ZIP parsing, migration preview and import controls |
| `interface/web/` | `web` | `whab_web` | View models, icons, templates and browser assets |
| `interface/bank-logos/` | `bank-logos` | `whab_bank_logos` | Curated institution lookup and public icon fetching |
| `interface/server/` | `server` | `whab_server` | HTTPS bearer-authorized public ledger API interface |
| `storage/client/` | `client` | `whab_client` | Remote storage and portable connection payload |
| `example/example/` | `example` | `whab_example_plugin` | Separately installed developer teaching report/export |

## Test ownership

Put implementation tests in `plugins/<category>/<id>/tests/`, including tests that run the plugin through the host. Keep shared domain, SDK, discovery, host security and cross-plugin contracts in the application's central `tests/` tree. Importing a reference backend in a contract test does not make that test belong to the backend.

Run from the application directory (`whab/app/`):

```sh
.venv/bin/python -m pytest plugins/ingestion/plaid/tests
.venv/bin/python -m pytest
.venv/bin/python scripts/check_plugin_coverage.py
.venv/bin/python -m ruff check src scripts tests plugins conftest.py
.venv/bin/python scripts/check_distribution.py
```

Replace `ingestion/plaid` with the category and plugin folder being changed. The complete pytest command discovers central and plugin-local tests; CI runs both. Shared composition fixtures live in application-root `conftest.py`; provider payloads and transports stay in the owning plugin's test fixtures. Use fictional data, temporary homes and mocked remote requests.

Every plugin must have **100% Python statement and branch coverage**. The coverage command measures every file in each `plugins/*/*/src/` tree using the complete local/shared suite; an unimported module or any missed branch fails the gate. It discovers new plugin folders automatically. Use `scripts/check_plugin_coverage.py --plugin plaid` to measure only that plugin against the same suite. Keep the regression tests that close gaps inside the owning plugin's `tests/` folder. Type-only imports are outside runtime coverage; runtime exclusions and coverage pragmas are unsupported. Rendering, browser behavior and distribution checks verify templates and static assets.

## Packaging and contracts

The `whab` distribution contains only the host/core and public SDK. All seven supported plugins are independent distributions; each owns its `pyproject.toml` entry point, package data, dependency compatibility range and `project.version`. Their initial independent version is 1.0.0. The teaching example is an eighth separate distribution.

| Plugin ID | Distribution | Default enablement |
| --- | --- | --- |
| `web` | `whab-web-plugin` | Selected visual interface |
| `sqlite` | `whab-sqlite-plugin` | Selected storage for the original workspace |
| `plaid` | `whab-plaid-plugin` | Optional, enabled unless overridden |
| `bank-logos` | `whab-bank-logos-plugin` | Disabled |
| `ynab` | `whab-ynab-plugin` | Disabled |
| `server` | `whab-server-plugin` | Disabled |
| `client` | `whab-client-plugin` | Not selected; choose as storage for a remote connection |
| `example` | `whab-example-plugin` | Separately installed, hidden developer teaching plugin |

From the application directory (`whab/app/`), install or refresh the complete local supported set:

```sh
.venv/bin/python scripts/install.py --dev
```

Add `--example` to install the teaching package too; omit `--dev` when development dependencies are unnecessary. The helper supplies all local package paths together, so unpublished local plugin packages do not need to resolve from a package index. When compatible host/plugin distributions are available in the chosen package index or local wheelhouse, `pip install 'whab[bundled]'` selects the seven supported plugins; the source helper works directly from this checkout without requiring those releases to be published. A single plugin can be installed from its own folder, such as `.venv/bin/python -m pip install -e plugins/interface/bank-logos`. Installing a package does not enable it; see the [developer-only example activation](../../docs/plugins.md#install-the-working-example).

A material plugin-only change bumps that plugin's `pyproject.toml::project.version` and adds a dated entry to its own `CHANGELOG.md`. It does not bump the application or change the application changelog. Host/core/public-SDK changes update the application version/changelog; spanning changes update each affected owner. Apply SemVer within each distribution. Adding a plugin to the supported default set requires maintainer wiring in the host's `bundled` extra. The source installer automatically discovers concrete plugin `pyproject.toml` files and includes the teaching example only with `--example`; do not add concrete source roots or entry points to the host wheel.

Every populated changelog entry must link its originating PR(s), including the teaching example. Use a section-wide PR link for a single PR and attribute individual change bullets when a version combines PRs. Obtain the real URL from a draft PR and follow the [repository-wide changelog policy](../CONTRIBUTING.md#changelog-pr-links).

Use the public `whab.plugins` SDK and core domain records. Concrete plugin modules are implementation details; plugins must not import each other's implementation or host internals. The `src/whab/plugins/` package owns the SDK/runtime and is distinct from these contributor folders. See [the plugin API](../../docs/plugins.md) and [contribution rules](../CONTRIBUTING.md).
