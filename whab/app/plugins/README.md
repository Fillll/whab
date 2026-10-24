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

Categories identify the primary source ownership of a plugin. They do not change the SDK descriptor kinds (`user_interface`, `storage`, `data_ingestion`, `reporting`, `branding` and `automation`) or add runtime capabilities. A plugin can expose multiple supported capabilities while keeping one category folder.

Use lowercase, hyphen-separated names for plugin IDs and folders, such as `bank-logos`. IDs must be unique across categories and match their entry-point names. Name both the Python package and the declared distribution `whab_plugin_<id>`, replacing ID hyphens with underscores: `sqlite` uses `whab_plugin_sqlite`, and `bank-logos` uses `whab_plugin_bank_logos`. Teaching plugins follow the same convention, including `whab_plugin_example`. Python packaging tools may display normalized distribution names with hyphens, such as `whab-plugin-sqlite`; this is the same distribution identity. Display names use readable words or the provider's brand, such as “Bank logos” or “Plaid”. Keep category prefixes out of plugin IDs: moving a folder or renaming a package must preserve its ID, saved settings and routes.

| Folder | Plugin ID | Python package | Owns |
| --- | --- | --- | --- |
| `storage/sqlite/` | `sqlite` | `whab_plugin_sqlite` | Default ledger backend, schema upgrades and demo seeding |
| `storage/json/` | `json` | `whab_plugin_json` | Independent single-file local ledger, receipts and encrypted connection checkpoints |
| `ingestion/plaid/` | `plaid` | `whab_plugin_plaid` | Bank normalization, credentials, connection UI and synchronization |
| `ingestion/ynab/` | `ynab` | `whab_plugin_ynab` | Local YNAB ZIP parsing, migration preview and import controls |
| `interface/web/` | `web` | `whab_plugin_web` | View models, icons, templates and browser assets |
| `interface/bank-logos/` | `bank-logos` | `whab_plugin_bank_logos` | Curated institution lookup and public icon fetching |
| `interface/server/` | `server` | `whab_plugin_server` | HTTPS bearer-authorized public ledger API interface |
| `storage/client/` | `client` | `whab_plugin_client` | Remote storage and portable connection payload |
| `automation/random-forest-categorization/` | `random-forest-categorization` | `whab_plugin_random_forest_categorization` | Reviewed-history models and provisional category suggestions |
| `example/example/` | `example` | `whab_plugin_example` | Separately installed developer teaching report/export |
| `example/example-branding/` | `example-branding` | `whab_plugin_example_branding` | Offline fictional institution branding |
| `example/example-file-import/` | `example-file-import` | `whab_plugin_example_file_import` | Bounded fictional account parser |
| `example/example-categorization/` | `example-categorization` | `whab_plugin_example_categorization` | Currency-scoped saved teaching model |

## Test ownership

Put implementation tests in `plugins/<category>/<id>/tests/`, including tests that run the plugin through the host. Keep shared domain, SDK, discovery, host security and cross-plugin contracts in the application's central `tests/` tree. Importing a reference backend in a contract test does not make that test belong to the backend.

Run from the application directory (`whab/app/`):

```sh
.venv/bin/python -m pytest plugins/ingestion/plaid/tests
.venv/bin/python scripts/check.py --quick
.venv/bin/python scripts/check.py
```

Replace `ingestion/plaid` with the category and plugin folder being changed. Focused pytest and `--quick` support iteration; the full runner is the final source of required application checks. It includes central/plugin tests, lint, browser-module tests, plugin coverage, isolated distributions and documentation checks. Use `--docs` for documentation-only changes; see the [development runbook](../../docs/development.md) for prerequisites and check selection. Shared composition fixtures live in application-root `conftest.py`; provider payloads and transports stay in the owning plugin's test fixtures. Use fictional data, temporary homes and mocked remote requests.

Every plugin must have **100% Python statement and branch coverage**. The full runner's coverage gate measures every file in each `plugins/*/*/src/` tree using the complete local/shared suite; an unimported module or any missed branch fails the gate. It discovers new plugin folders automatically. Keep the regression tests that close gaps inside the owning plugin's `tests/` folder. Type-only imports are outside runtime coverage; runtime exclusions and coverage pragmas are unsupported. Rendering, browser behavior and distribution checks verify templates and static assets. Budget creation/import, routing, binders and plugin persistence also need the [two-budget isolation checks](../../docs/architecture.md#budget-isolation-verification); UI changes need the owning [web acceptance checks](interface/web/README.md#ui-acceptance-criteria).

## Packaging and contracts

The `whab` distribution contains only the host/core and public SDK. Supported plugins are independent distributions; each owns its `pyproject.toml` entry point, package data, dependency compatibility range and `project.version`. Their initial independent version is 1.0.0. The teaching example is a separate distribution.

## Supported plugins

This is the canonical supported-set inventory. Keep it aligned with the host's `bundled` extra and each plugin's package metadata; other guides link here instead of repeating a package count.

| Plugin ID | Distribution | Default enablement |
| --- | --- | --- |
| `web` | `whab_plugin_web` | Selected visual interface |
| `sqlite` | `whab_plugin_sqlite` | Selected storage for the original workspace |
| `json` | `whab_plugin_json` | Not selected; choose explicitly for a separate local budget |
| `plaid` | `whab_plugin_plaid` | Optional, enabled unless overridden |
| `bank-logos` | `whab_plugin_bank_logos` | Disabled |
| `ynab` | `whab_plugin_ynab` | Disabled |
| `server` | `whab_plugin_server` | Disabled |
| `client` | `whab_plugin_client` | Not selected; choose as storage for a remote connection |
| `random-forest-categorization` | `whab_plugin_random_forest_categorization` | Disabled |

## Teaching examples

The [teaching category](example/README.md) contains four independently packaged public-SDK examples: reporting/export, branding, file import and categorization. They are excluded from the supported bundled set and hidden from ordinary Settings. Install them with `scripts/install.py --dev --example`, then explicitly select the desired IDs; installation does not activate them. See the category guide and [activation instructions](../../docs/plugins.md#install-the-working-example).

## Installation and compatibility

From the application directory (`whab/app/`), install or refresh the complete local supported set:

```sh
.venv/bin/python scripts/install.py --dev
```

Add `--example` to install the teaching packages too; omit `--dev` when development dependencies are unnecessary. The helper supplies all local package paths together, so unpublished local plugin packages do not need to resolve from a package index. When compatible host/plugin distributions are available in the chosen package index or local wheelhouse, `pip install 'whab[bundled]'` selects the [supported plugins](#supported-plugins); the source helper works directly from this checkout without requiring those releases to be published. A single plugin can be installed from its own folder, such as `.venv/bin/python -m pip install -e plugins/interface/bank-logos`. Installing a package does not enable it; see the [developer-only example activation](../../docs/plugins.md#install-the-working-example).

To upgrade from the former `whab-<id>-plugin` distributions, rerun the source installer. It installs the standardized packages before removing detected legacy official distributions and preserves an installed teaching example by including its replacement. For wheel installations, use a fresh environment or remove the legacy distributions when installing the new packages; keeping both would register the same runtime IDs twice. Runtime IDs, enabled selections, saved settings and routes remain stable through the package rename.

A material plugin-only change bumps that plugin's `pyproject.toml::project.version` and adds a dated entry to its own `CHANGELOG.md`. It does not bump the application or change the application changelog. Host/core/public-SDK changes update the application version/changelog; spanning changes update each affected owner. Apply SemVer within each distribution. Adding a plugin to the supported default set requires maintainer wiring in the host's `bundled` extra. The source installer automatically discovers concrete plugin `pyproject.toml` files and includes the teaching example with `--example` or when replacing its installed legacy distribution; do not add concrete source roots or entry points to the host wheel.

Every populated changelog entry must link its originating PR(s), including the teaching example. Use a section-wide PR link for a single PR and attribute individual change bullets when a version combines PRs. Obtain the real URL from a draft PR and follow the [repository-wide changelog policy](../CONTRIBUTING.md#changelog-pr-links).

Use the public `whab.plugins` SDK and core domain records. Concrete plugin modules are implementation details; plugins must not import each other's implementation or host internals. The `src/whab/plugins/` package owns the SDK/runtime and is distinct from these contributor folders. See [the plugin API](../../docs/plugins.md) and [contribution rules](../CONTRIBUTING.md).
