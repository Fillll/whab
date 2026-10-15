# Plugin contributor folders

Each plugin belongs to one category and owns one folder containing its Python implementation, assets, tests and documentation:

```text
plugins/<category>/<id>/
  README.md
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
| [`interface/`](interface/README.md) | User-facing presentation, visual interfaces and branding |
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
| `interface/web/` | `web` | `whab_web` | View models, icons, templates and browser assets |
| `interface/bank-logos/` | `bank-logos` | `whab_bank_logos` | Curated institution lookup and public icon fetching |
| `example/example/` | `example` | `whab_example_plugin` | Independently installable reporting/export example |

## Test ownership

Put implementation tests in `plugins/<category>/<id>/tests/`, including tests that run the plugin through the host. Keep shared domain, SDK, discovery, host security and cross-plugin contracts in the application's central `tests/` tree. Importing a reference backend in a contract test does not make that test belong to the backend.

Run from the application directory (`whab/`):

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

The four bundled plugins ship in the same `whab` wheel, so the normal app installation still supplies its default storage and interface. Each has its own Python namespace and is loaded through a `whab.plugins` entry point. Adding a bundled plugin requires maintainer wiring in the application `pyproject.toml`: register its entry point and source root, plus package data when it has assets. Refresh an editable installation after moving packages or changing registrations:

```sh
.venv/bin/python -m pip install --no-deps -e .
```

An independently distributed plugin owns a `pyproject.toml` in its own folder. `example/example/` demonstrates this; install it with `.venv/bin/python -m pip install -e plugins/example/example`. Installing a plugin does not automatically enable it.

Use the public `whab.plugins` SDK and core domain records. Concrete plugin modules are implementation details; plugins must not import each other's implementation or host internals. The `src/whab/plugins/` package owns the SDK/runtime and is distinct from these contributor folders. See [the plugin API](../docs/plugins.md) and [contribution rules](../CONTRIBUTING.md).
