# Contributing to WHAB

Start with [README.md](README.md) for setup, supported behavior and limitations, then read [the architecture guide](docs/architecture.md). Keep a change focused on the layer that owns its behavior. The repository's current visibility and licensing are unchanged by these contributor instructions.

## Development environment

WHAB supports Python 3.14 and newer; CI exercises Python 3.14 and 3.15. From the checkout:

```sh
cd whab
python3.14 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m whab --version
```

The editable install is required for the `src/` layout. There is no Node build or React runtime. The bundled visual interface's templates and static files belong to the installed `whab_web` package.

When upgrading an older checkout, obsolete `whab.egg-info/` or `ihab.egg-info/` directories at the repository root can mask the new distribution's entry points. Remove those specific root metadata directories if present, then rerun the editable install. The current layout generates its metadata under `src/`.

Runtime configuration lives under `WHAB_HOME`, or the launch directory when that setting is absent. Run from the application directory (`whab/`) for its local `.env` and `data/`, or set `WHAB_HOME` to an existing runtime home, including an older checkout root. Preserve that home's configuration, data and encryption key together. The app reads that directory's `.env` and passes its home/environment to the selected storage plugin. `WHAB_STORAGE=sqlite` is the default; its factory opens `data/ihab-sandbox.sqlite` or `data/ihab-production.sqlite` under that home. `WHAB_PLUGINS=` disables optional bank/report plugins. `WHAB_UI=web` selects the visual interface; `WHAB_UI=` runs headlessly with APIs and storage. Changing storage does not migrate data between backends. For experiments, use a fresh temporary home and fictional data. Never copy private credentials or a household database into test fixtures.

## Tests and checks

Run these checks before submitting code:

```sh
.venv/bin/python -m pytest
.venv/bin/python scripts/check_plugin_coverage.py
.venv/bin/python -m ruff check src scripts tests plugins conftest.py
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
| `plugins/storage/sqlite/tests/` | SQLite ledger, rollback, migrations and saved-data compatibility |
| `plugins/ingestion/plaid/tests/` | Mocked Plaid pagination, update mode, token encryption, signed webhooks and connection UI |
| `plugins/interface/web/tests/` | Bundled Web rendering, escaping and visual fallbacks |
| `plugins/interface/bank-logos/tests/` | Curated institution lookup and bounded mocked icon requests |
| `plugins/example/example/tests/` | Example reporting/export implementation |
| `tests/storage/` | Shared provider-scoping and bank-facade contracts |
| `tests/web/` | Host HTTP validation, local access checks, CSV, selection and plugin routes |
| `tests/plugins/` | Public SDK, discovery, hook validation and plugin management |
| `tests/test_cli.py` | Application launch and version contract |
| `tests/test_architecture.py` | Pure domain boundaries and core independence from concrete storage/bank implementations |

Keep an existing plugin's source, assets, documentation and implementation tests in its own `plugins/<category>/<id>/` folder. Keep shared API contracts and interactions between plugins in `tests/`; a host-contract change may require edits there too. New bundled plugins need maintainer registration in the application `pyproject.toml`; independently distributed plugins own their entry point in their own `pyproject.toml`, as shown by `plugins/example/example/`. See [plugin folder ownership](plugins/README.md).

The application-root `conftest.py` provides shared isolated fixtures to both test locations. Plugin-specific records and mock transports belong in that plugin's `tests/conftest.py`. Tests must not read `.env`, open the user's database, need a Plaid account or make live provider requests. Use `tmp_path` for compatibility fixtures and explicitly set `WHAB_HOME` when testing configuration. Make a regression test demonstrate an observable failure or invariant; avoid tests that merely reproduce implementation steps.

Run all tests with `.venv/bin/python -m pytest`, or just one plugin with `.venv/bin/python -m pytest plugins/ingestion/plaid/tests` (replace `ingestion/plaid` with its category and plugin ID). CI discovers both the central and plugin-local suites and lints all plugin sources. Run these commands from the application directory (`whab/`).

Every plugin must maintain **100% Python statement and branch coverage** across its complete `src/` tree. Run `.venv/bin/python scripts/check_plugin_coverage.py` before submitting; it uses the full local/shared test suite and fails for any missing line, missing branch or unmeasured source file. It discovers source roots automatically, including independently distributed plugins. Use `--plugin plaid` to measure one plugin against the same suite. Keep new regressions with their plugin. Static type-only imports are outside runtime coverage; do not exclude runtime code or add coverage pragmas to meet the gate. Templates and browser assets remain covered by rendering, behavior and installed-wheel checks.

Browser checks should use demo or Sandbox data. Verify the particular flow affected by a UI change, including its saved result. A successful Link screen alone does not prove successful synchronization or production access.

## Code boundaries

Keep money and envelope rules in `whab.domain`; they must not import web, storage, interfaces, configuration, provider or plugin runtimes. Keep SQLite transactions, additive schema upgrades and demo seeding in `whab_sqlite`, provider HTTP and normalization in `whab_plaid`, and visual presentation/assets in `whab_web`. The `whab.web` package owns transport, request schemas, middleware and safe projections. `whab.storage.bank` scopes providers over the SDK's `StorageBackend` protocol. App, host web and generic plugin-runtime code must not import concrete storage, bank or visual implementations. Assemble selected descriptors/factories in `whab.app.create_app` with injectable backends/services for tests.

External extensions use the documented [`whab.plugins` API v1](docs/plugins.md). Descriptors advertise user-interface, storage, data-ingestion and reporting capabilities. Reports receive immutable snapshots; bank factories receive a privileged scoped store; storage factories return a complete backend; server-rendered UI descriptors provide trusted resources and a presentation builder. The bundled Web, SQLite and Plaid plugins use those same boundaries. Storage must preserve cents, isolation, annotations, budgeting and atomic imports/cursors. Do not silently fall back when a selected plugin fails. Arbitrary imports, native UI runtimes and general mutation hooks are not provided. Enabled Python plugins are trusted code, not sandboxed code.

Preserve these invariants when changing the corresponding layer:

- Use integer USD cents: positive transactions are outflows and negative transactions are inflows. Avoid floats for stored money.
- Keep demo/personal workspaces and Sandbox/production databases separate. Bank balance snapshots already include income; importing income must not add it again to assignable cash.
- Preserve `budgetStartMonth`, category overrides, notes and review state. Pending-to-posted matches must retain annotations, and all synchronization changes and their cursor must commit together.
- Keep existing SQLite filenames, the opaque Plaid household identity, browser OAuth keys and encrypted-token compatibility unless the change includes an explicit migration. The package and launch command are only `whab`.
- Keep credentials on the server. Public responses, plugin snapshots, CSV files and exception messages must exclude tokens and configuration secrets.
- Keep HTTP access local, validate request bodies, escape user and plugin text, and protect CSV headers and text cells from spreadsheet formulas. Authentication and authorization are prerequisites for shared network deployment.

## Pull requests and releases

WHAB follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html): patch releases fix compatible behavior, minor releases add compatible features, and major releases introduce breaking changes. The compatibility contract covers documented HTTP/CSV interfaces and the ability to open existing ledgers and encrypted connections through supported migrations. A schema change with a supported automatic migration can be compatible; dropping or changing documented fields, requiring manual data conversion, or invalidating existing tokens requires a major release. The public plugin API in `whab.plugins` is also covered: breaking its documented records or hook behavior requires a major release. Internal modules are not a stable import API. Internal refactoring and visual changes do not by themselves break this contract.

Use a feature branch and pull request for every change to `main`. Describe the concrete problem, resulting behavior and relevant validation. Include a fixture-based regression for a behavior fix and update the owning documentation when a contract or limitation changes. Do not commit `.env`, databases, private exports, generated build files or unrelated workspace changes; stage source paths explicitly.

Every material change includes a SemVer bump in `src/whab/__init__.py` and a dated entry in `CHANGELOG.md` in the same PR. Use a patch for compatible fixes or maintenance, a minor for compatible features, and a major for breaking the documented compatibility contract. Runtime and dependency support changes require a version bump too. Small documentation-only changes may wait for the next version bump.

The package version, `python -m whab --version`, FastAPI metadata and About dialog derive from that one version source. Review the final diff and pass CI before squash merging. The sole maintainer has authorized routine review and merging without another confirmation; no second-person approval is required because GitHub cannot approve a PR through its author's account. Create an immutable `vX.Y.Z` tag and GitHub release on the merged commit only when the user explicitly requests a release; version bumps and merges do not require publishing one. Do not reuse or move an existing release tag.

## Branch protection

**Branch protection is pending.** GitHub rejected protection for this private repository on the current account plan; the maintainer chose to leave enforcement pending. [Private repository protection requires Pro or an eligible organization plan](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches). The PR workflow is a project convention until Pro is enabled. `.github/main-protection.json` records the intended rule: require PRs and passing CI, apply it to administrators, prevent force pushes/deletion, and require resolved conversations and linear history. After enabling Pro, apply it with:

```sh
gh api --method PUT repos/Fillll/whab/branches/main/protection \
  --input ../.github/main-protection.json
```

## Reporting sensitive bugs

Keep reports reproducible with synthetic records, redacted responses and public error messages. Do not put financial records, institution account identifiers, Plaid credentials, access tokens, encryption keys or private `.env` contents in an issue, PR, screenshot or log attachment. Report a suspected credential exposure privately to the repository maintainer rather than publishing the sensitive material.

## License

Contributions to WHAB's own code, documentation and assets are accepted under the [MIT License](LICENSE). Preserve applicable third-party notices. Independently written plugins may use their authors' chosen licenses.
