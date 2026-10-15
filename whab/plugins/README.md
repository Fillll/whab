# Plugin contributor folders

Each plugin owns one folder containing its Python implementation, assets, tests and documentation:

```text
plugins/<id>/
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

| Folder | Python package | Owns |
| --- | --- | --- |
| `sqlite/` | `whab_sqlite` | Default ledger backend, schema upgrades and demo seeding |
| `plaid/` | `whab_plaid` | Bank normalization, credentials, connection UI and synchronization |
| `web/` | `whab_web` | View models, icons, templates and browser assets |
| `bank-logos/` | `whab_bank_logos` | Curated institution lookup and public icon fetching |
| `example/` | `whab_example_plugin` | Independently installable reporting/export example |

## Test ownership

Put implementation tests in `plugins/<id>/tests/`, including tests that run the plugin through the host. Keep shared domain, SDK, discovery, host security and cross-plugin contracts in the application's central `tests/` tree. Importing a reference backend in a contract test does not make that test belong to the backend.

Run from the application directory (`whab/`):

```sh
.venv/bin/python -m pytest plugins/plaid/tests
.venv/bin/python -m pytest
.venv/bin/python scripts/check_plugin_coverage.py
.venv/bin/python -m ruff check src scripts tests plugins conftest.py
.venv/bin/python scripts/check_distribution.py
```

Replace `plaid` with the folder being changed. The complete pytest command discovers central and plugin-local tests; CI runs both. Shared composition fixtures live in application-root `conftest.py`; provider payloads and transports stay in the owning plugin's test fixtures. Use fictional data, temporary homes and mocked remote requests.

Every plugin must have **100% Python statement and branch coverage**. The coverage command measures every file in each `plugins/*/src/` tree using the complete local/shared suite; an unimported module or any missed branch fails the gate. It discovers new plugin folders automatically. Use `scripts/check_plugin_coverage.py --plugin plaid` to measure only that plugin against the same suite. Keep the regression tests that close gaps inside the owning plugin's `tests/` folder. Type-only imports are outside runtime coverage; runtime exclusions and coverage pragmas are unsupported. Rendering, browser behavior and distribution checks verify templates and static assets.

## Packaging and contracts

The four bundled plugins ship in the same `whab` wheel, so the normal app installation still supplies its default storage and interface. Each has its own Python namespace and is loaded through a `whab.plugins` entry point. Adding a bundled plugin requires maintainer wiring in the application `pyproject.toml`: register its entry point and source root, plus package data when it has assets. Refresh an editable installation after moving packages or changing registrations:

```sh
.venv/bin/python -m pip install --no-deps -e .
```

An independently distributed plugin owns a `pyproject.toml` in its own folder. `example/` demonstrates this; install it with `.venv/bin/python -m pip install -e plugins/example`. Installing a plugin does not automatically enable it.

Use the public `whab.plugins` SDK and core domain records. Concrete plugin modules are implementation details; plugins must not import each other's implementation or host internals. The `src/whab/plugins/` package owns the SDK/runtime and is distinct from these contributor folders. See [the plugin API](../docs/plugins.md) and [contribution rules](../CONTRIBUTING.md).
