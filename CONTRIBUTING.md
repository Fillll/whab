# Contributing to WHAB

Start with [README.md](README.md) for setup, supported behavior and limitations, then read [the architecture guide](docs/architecture.md). Keep a change focused on the layer that owns its behavior. The repository's current visibility and licensing are unchanged by these contributor instructions.

## Development environment

WHAB supports Python 3.13 and newer; CI exercises Python 3.13, 3.14 and 3.15. From the checkout:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m whab --version
```

The editable install is required for the `src/` layout. There is no Node build or React runtime. The bundled visual interface's templates and static files belong to the installed `whab.interfaces.web` package.

When upgrading an older checkout, obsolete `whab.egg-info/` or `ihab.egg-info/` directories at the repository root can mask the new distribution's entry points. Remove those specific root metadata directories if present, then rerun the editable install. The current layout generates its metadata under `src/`.

Runtime configuration lives under `WHAB_HOME`, or the launch directory when that setting is absent. The app reads that directory's `.env` and passes its home/environment to the selected storage plugin. `WHAB_STORAGE=sqlite` is the default; its factory opens `data/ihab-sandbox.sqlite` or `data/ihab-production.sqlite` under that home. `WHAB_PLUGINS=` disables optional bank/report plugins. `WHAB_UI=web` selects the visual interface; `WHAB_UI=` runs headlessly with APIs and storage. Changing storage does not migrate data between backends. For experiments, use a fresh temporary home and fictional data. Never copy private credentials or a household database into test fixtures.

## Tests and checks

Run these checks before submitting code:

```sh
.venv/bin/python -m pytest
.venv/bin/python -m ruff check src scripts tests examples
```

For packaging or plugin changes, also run:

```sh
.venv/bin/python scripts/check_distribution.py
```

That script builds the application and example plugin, installs their wheels into a temporary target, and checks pages, packaged assets, version metadata, runtime paths and entry-point loading from an unrelated directory. It uses fictional data and does not connect a financial institution. Building may require downloading build dependencies.

Tests are grouped by the contract they verify:

| Location | Responsibility |
| --- | --- |
| `tests/domain/` | Money validation and budgeting rules without database or HTTP dependencies |
| `tests/storage/` | Backend contract, workspace isolation, mutations, SQLite rollback and saved-data compatibility |
| `tests/integrations/plaid/` | Mocked Plaid pagination, update mode, token encryption and signed webhooks |
| `tests/web/` | HTTP validation, local access checks, rendering, CSV and plugin routes |
| `tests/plugins/` | Public SDK, discovery, hook validation and example plugin behavior |
| `tests/test_cli.py` | Application launch and version contract |
| `tests/test_architecture.py` | Pure domain boundaries and core independence from concrete storage/bank implementations |

Shared fixtures in `tests/conftest.py` use in-memory SQLite, synthetic encrypted tokens and HTTP mock transports. Tests must not read `.env`, open the user's database, need a Plaid account or make live provider requests. Use `tmp_path` for compatibility fixtures and explicitly set `WHAB_HOME` when testing configuration. Make a regression test demonstrate an observable failure or invariant; avoid tests that merely reproduce implementation steps.

Browser checks should use demo or Sandbox data. Verify the particular flow affected by a UI change, including its saved result. A successful Link screen alone does not prove successful synchronization or production access.

## Code boundaries

Keep money and envelope rules in `whab.domain`; they must not import web, storage, interfaces, configuration, provider or plugin runtimes. Keep SQLite transactions, additive schema upgrades and demo seeding in `whab.storage.sqlite`, provider HTTP and normalization in `whab.integrations.plaid`, and visual presentation/assets in `whab.interfaces.web`. The `whab.web` package owns transport, request schemas, middleware and safe projections. `whab.storage.bank` scopes providers over the SDK's `StorageBackend` protocol. App, host web and generic plugin-runtime code must not import concrete storage, bank or visual implementations. Assemble selected descriptors/factories in `whab.app.create_app` with injectable backends/services for tests.

External extensions use the documented [`whab.plugins` API v1](docs/plugins.md). Descriptors advertise user-interface, storage, data-ingestion and reporting capabilities. Reports receive immutable snapshots; bank factories receive a privileged scoped store; storage factories return a complete backend; server-rendered UI descriptors provide trusted resources and a presentation builder. The bundled Web, SQLite and Plaid plugins use those same boundaries. Storage must preserve cents, isolation, annotations, budgeting and atomic imports/cursors. Do not silently fall back when a selected plugin fails. Arbitrary imports, native UI runtimes and general mutation hooks are not provided. Enabled Python plugins are trusted code, not sandboxed code.

Preserve these invariants when changing the corresponding layer:

- Use integer USD cents: positive transactions are outflows and negative transactions are inflows. Avoid floats for stored money.
- Keep demo/personal workspaces and Sandbox/production databases separate. Bank balance snapshots already include income; importing income must not add it again to assignable cash.
- Preserve `budgetStartMonth`, category overrides, notes and review state. Pending-to-posted matches must retain annotations, and all synchronization changes and their cursor must commit together.
- Keep existing SQLite filenames, the opaque Plaid household identity, browser OAuth keys and encrypted-token compatibility unless the change includes an explicit migration. The package and launch command are only `whab`.
- Keep credentials on the server. Public responses, plugin snapshots, CSV files and exception messages must exclude tokens and configuration secrets.
- Keep HTTP access local, validate request bodies, escape user and plugin text, and protect CSV headers and text cells from spreadsheet formulas. Authentication and authorization are prerequisites for shared network deployment.

## Pull requests and releases

Use a feature branch and pull request for every change to `main`. Describe the concrete problem, resulting behavior and relevant validation. Include a fixture-based regression for a behavior fix and update the owning documentation when a contract or limitation changes. Do not commit `.env`, databases, private exports, generated build files or unrelated workspace changes; stage source paths explicitly.

Every material change includes a SemVer bump in `src/whab/__init__.py` and a dated entry in `CHANGELOG.md` in the same PR. Use a patch for compatible fixes or maintenance, a minor for compatible features, and a major for breaking the documented compatibility contract. Runtime and dependency support changes require a release too. Small documentation-only changes may wait for the next release.

The package version, `python -m whab --version`, FastAPI metadata and About dialog derive from that one version source. Review the final diff and pass CI before squash merging. Then create an immutable `vX.Y.Z` tag and GitHub release on the merged commit. Do not reuse or move an existing release tag. The current branch-protection limitation and desired check names are recorded in [README.md](README.md) and [`.github/main-protection.json`](.github/main-protection.json).

## Reporting sensitive bugs

Keep reports reproducible with synthetic records, redacted responses and public error messages. Do not put financial records, institution account identifiers, Plaid credentials, access tokens, encryption keys or private `.env` contents in an issue, PR, screenshot or log attachment. Report a suspected credential exposure privately to the repository maintainer rather than publishing the sensitive material.
