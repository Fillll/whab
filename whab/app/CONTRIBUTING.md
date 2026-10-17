# Contributing to WHAB

Start with [README.md](README.md) for setup, supported behavior and limitations, then read [the architecture guide](../docs/architecture.md). Keep a change focused on the layer that owns its behavior. The repository's current visibility and licensing are unchanged by these contributor instructions.

## Development environment

WHAB supports Python 3.14 and newer; CI exercises Python 3.14 and 3.15. Run all application commands below from the application directory (`whab/app/`):

```sh
python3.14 -m venv .venv
.venv/bin/python scripts/install.py --dev
.venv/bin/python -m whab --version
```

The source installer installs the host and all seven supported plugins as editable local distributions; add `--example` for the teaching plugin. For wheel installations, `pip install 'whab[bundled]'` selects the supported set when compatible distributions are available in the chosen package index or local wheelhouse; this does not imply these releases have been published. The host alone contains the core and SDK and needs installed storage/UI plugins for the default launch. There is no Node build or React runtime. The bundled visual interface's templates and static files belong to the installed `whab_web` package.

When upgrading an older checkout, rerun `.venv/bin/python scripts/install.py --dev`. After a successful combined install, the helper removes only verified obsolete generated application-root `whab.egg-info` metadata that could mask current registrations; it preserves current per-distribution metadata. No manual metadata deletion is needed for the standard upgrade.

Runtime configuration lives under `WHAB_HOME`, or the launch directory when that setting is absent. Run from the application directory (`whab/app/`) for its local `.env` and `data/`, or set `WHAB_HOME` to an existing runtime home, including an older checkout root. Preserve that home's configuration, data and encryption key together. After the source move into `whab/app/`, follow the [existing-checkout migration instructions](../README.md#existing-development-checkouts) to keep private files under the previous `whab/` runtime home and reinstall editable packages in a fresh virtual environment. The app reads that directory's `.env` and passes its home/environment to the selected storage plugin. `WHAB_STORAGE=sqlite` is the default for the original workspace and new command-line budgets; an existing portable budget selects storage from its manifest and Settings creates new SQLite budgets. For the original workspace, its factory reopens a single `data/*-{environment}.sqlite` file in place, validating the base schema for a noncanonical filename. It creates `data/whab-{environment}.sqlite` only when no matching file exists. Multiple matches fail safely, even if one has the canonical filename. For the original workspace without `plugins.json`, `WHAB_PLUGINS=` disables optional bank/report plugins; portable budgets use their saved manifest. `WHAB_UI=web` selects the visual interface; `WHAB_UI=` runs headlessly with APIs and storage. Changing storage does not migrate data between backends. Every listener requires TLS; run `scripts/setup.py` to provision local certificates, then [trust its CA explicitly](../docs/usage.md#local-https). Setup never changes OS trust, and the launcher rejects missing or invalid TLS material. For experiments, use a fresh temporary home and fictional data. Never copy private credentials or a household database into test fixtures.

## Tests and checks

Run these checks before submitting code:

```sh
.venv/bin/python -m pytest
.venv/bin/python scripts/check_changelog_prs.py
.venv/bin/python scripts/check_plugin_coverage.py
.venv/bin/python -m ruff check src scripts tests plugins conftest.py
```

For packaging or plugin changes, also run:

```sh
.venv/bin/python scripts/check_distribution.py
```

That script builds the host, all seven supported plugins and the teaching example, installs their independent wheels into a temporary target, and checks pages, packaged assets, version metadata, runtime paths and entry-point loading from an unrelated directory. It uses fictional data and does not connect a financial institution. Building may require downloading build dependencies.

Tests are grouped by the contract they verify:

| Location | Responsibility |
| --- | --- |
| `tests/domain/` | Money validation and budgeting rules without database or HTTP dependencies |
| `plugins/storage/sqlite/tests/` | SQLite ledger, rollback, migrations and saved-data compatibility |
| `plugins/ingestion/plaid/tests/` | Mocked Plaid pagination, update mode, token encryption, signed webhooks and connection UI |
| `plugins/interface/web/tests/` | Bundled Web rendering, escaping and visual fallbacks |
| `plugins/interface/bank-logos/tests/` | Curated institution lookup and bounded mocked icon requests |
| `plugins/ingestion/ynab/tests/` | Local archive parsing, migration preview/import and import controls |
| `plugins/interface/server/tests/` | HTTPS bearer authorization, remote routes, revisions and retries |
| `plugins/storage/client/tests/` | Remote commands, connection payloads and credential-target binding |
| `plugins/example/example/tests/` | Example reporting/export implementation |
| `tests/storage/` | Shared provider-scoping and bank-facade contracts |
| `tests/web/` | Host HTTP validation, local access checks, CSV, selection and plugin routes |
| `tests/plugins/` | Public SDK, discovery, hook validation and plugin management |
| `tests/test_cli.py` | Application launch and version contract |
| `tests/test_architecture.py` | Pure domain boundaries and core independence from concrete storage/bank implementations |

Keep an existing plugin's source, assets, documentation and implementation tests in its own `plugins/<category>/<id>/` folder. Keep shared API contracts and interactions between plugins in `tests/`; a host-contract change may require edits there too. Every concrete plugin owns its entry point, package data, dependency compatibility range and version in its own `pyproject.toml`, together with a local dated `CHANGELOG.md`. Changes to the supported default set also need maintainer wiring in the host's `bundled` extra. The source installer discovers plugin `pyproject.toml` files automatically and includes the teaching example only with `--example`; concrete packages never move into the host wheel. See [plugin folder ownership](plugins/README.md).

The application-root `conftest.py` provides shared isolated fixtures to both test locations. Plugin-specific records and mock transports belong in that plugin's `tests/conftest.py`. Tests must not read `.env`, open the user's database, need a Plaid account or make live provider requests. Use `tmp_path` for compatibility fixtures and explicitly set `WHAB_HOME` when testing configuration. Make a regression test demonstrate an observable failure or invariant; avoid tests that merely reproduce implementation steps.

Run all tests with `.venv/bin/python -m pytest`, or just one plugin with `.venv/bin/python -m pytest plugins/ingestion/plaid/tests` (replace `ingestion/plaid` with its category and plugin ID). CI discovers both the central and plugin-local suites and lints all plugin sources. Run these commands from the application directory (`whab/app/`).

Every plugin must maintain **100% Python statement and branch coverage** across its complete `src/` tree. Run `.venv/bin/python scripts/check_plugin_coverage.py` before submitting; it uses the full local/shared test suite and fails for any missing line, missing branch or unmeasured source file. It discovers source roots automatically, including independently distributed plugins. Use `--plugin plaid` to measure one plugin against the same suite. Keep new regressions with their plugin. Static type-only imports are outside runtime coverage; do not exclude runtime code or add coverage pragmas to meet the gate. Templates and browser assets remain covered by rendering, behavior and installed-wheel checks.

Browser checks should use demo or Sandbox data. Verify the particular flow affected by a UI change, including its saved result. A successful Link screen alone does not prove successful synchronization or production access.

## Code boundaries

Keep money and envelope rules in `whab.domain`; they must not import web, storage, interfaces, configuration, provider or plugin runtimes. Keep SQLite transactions, additive schema upgrades and demo seeding in `whab_sqlite`, provider HTTP and normalization in `whab_plaid`, and visual presentation/assets in `whab_web`. The `whab.web` package owns transport, request schemas, middleware and safe projections. `whab.storage.bank` scopes providers over the SDK's `StorageBackend` protocol. App, host web and generic plugin-runtime code must not import concrete storage, bank or visual implementations. Assemble selected descriptors/factories in `whab.app.create_app` with injectable backends/services for tests.

External extensions use the documented [`whab.plugins` API v1](../docs/plugins.md). Descriptors support visual `ui`, transport `interface`, `storage`, `bank`, `file_import`, `insights`, `export` and `bank_logos` capabilities, with optional `bind`, `settings` and bank-owned `merchant_logos` hooks. Computed kinds are `user_interface` (including transport interfaces), `storage`, `data_ingestion`, `reporting` and `branding`. Reports receive immutable snapshots; bank factories receive a privileged scoped store; storage factories return a complete backend; server-rendered UI descriptors provide trusted resources and a presentation builder. The bundled Web, SQLite and Plaid plugins use those same boundaries. Storage must preserve integer currency minor units, isolation, annotations, budgeting and atomic imports/cursors. Do not silently fall back when a selected plugin fails. Arbitrary imports, native UI runtimes and general mutation hooks are not provided. Enabled Python plugins are trusted code, not sandboxed code.

Preserve these invariants when changing the corresponding layer:

- Use integer currency minor units (USD cents): positive transactions are outflows and negative transactions are inflows. Keep totals and allocations separate by currency and reject account/category currency mismatches. Avoid floats for stored money. Parent category groups are organizational; transactions, targets and allocations refer only to children.
- Keep demo/personal workspaces and Sandbox/production databases separate. Bank balance snapshots already include income; importing income must not add it again to assignable cash.
- Preserve `budgetStartMonth`, category overrides, notes and review state. Pending-to-posted matches must retain annotations, and all synchronization changes and their cursor must commit together.
- Preserve compatible SQLite ledgers in place and retain encrypted-token compatibility. New Plaid Link tokens use `whab-local-household`, and browser OAuth keys use `whab`; finish in-progress Link flows before upgrading. The package and launch command are only `whab`.
- Keep credentials on the server. Public responses, plugin snapshots, CSV files and exception messages must exclude tokens and configuration secrets.
- Require HTTPS everywhere, including localhost and webhooks, and retain loopback Host/origin checks for ordinary routes. Only explicitly enabled remote protocol routes and registered signed webhooks bypass those local checks; the former require bearer authorization and the latter provider signature verification. TLS is transport encryption, not user authentication. Validate request bodies, escape user and plugin text, and protect CSV headers and text cells from spreadsheet formulas.

## Pull requests and releases

WHAB follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html): patch releases fix compatible behavior, minor releases add compatible features, and major releases introduce breaking changes. The compatibility contract covers documented HTTP/CSV interfaces and the ability to open existing ledgers and encrypted connections through supported migrations. A schema change with a supported automatic migration can be compatible; dropping or changing documented fields, requiring manual data conversion, or invalidating existing tokens requires a major release. The public plugin API in `whab.plugins` is also covered: breaking its documented records or hook behavior requires a major release. Internal modules are not a stable import API. Internal refactoring and visual changes do not by themselves break this contract.

Use a feature branch and pull request for every change to `main`. Describe the concrete problem, resulting behavior and relevant validation. Include a fixture-based regression for a behavior fix and update the owning documentation when a contract or limitation changes. Do not commit `.env`, databases, private exports, generated build files or unrelated workspace changes; stage source paths explicitly.

Version the owner of the material change in the same PR. Host/core/public-SDK changes bump `src/whab/__init__.py::__version__` and add a dated entry to the application `CHANGELOG.md`. Each independently packaged plugin instead owns `plugins/<category>/<id>/pyproject.toml::project.version` and its local `CHANGELOG.md`; a plugin-only change does not bump the app or change the app changelog. Changes spanning owners update each affected owner. Apply SemVer separately to each distribution: patch for compatible fixes or maintenance, minor for compatible features, major for a breaking documented contract. Runtime and dependency support changes require the owning distribution's version bump. Small documentation-only changes may wait for its next version bump.

The host package version, `python -m whab --version`, FastAPI metadata and About dialog derive from the host version source; installed plugin versions come from their own distribution metadata. Review the final diff and pass CI before requesting a squash merge. Final approval and merging belong to the human maintainer @Fillll. Agents must not approve or merge a PR unless @Fillll explicitly delegates that action for that specific PR. Create an immutable `vX.Y.Z` tag and GitHub release on the merged commit only when the user explicitly requests a release; version bumps and merges do not require publishing one. Do not reuse or move an existing release tag.

### Changelog PR links

Every populated version or **Unreleased** section in every `CHANGELOG.md` must link the pull request(s) that introduced the recorded changes. This applies repository-wide: host/core, supported and example plugins, platform launchers, and future components. Changelog updates go through the same feature-branch and PR workflow as other changes.

For a section introduced by one PR, put a shared reference immediately below the heading, for example `PR: [#38](https://github.com/Fillll/whab/pull/38)`. That reference applies to all changes in the section. When a section combines changes from multiple PRs, list those PRs and also attach the appropriate link to each change bullet so its origin is clear. Use full links to this repository's pull requests; issue links, bare numbers and placeholders are insufficient.

Open a draft PR to obtain its number and URL before finalizing a new changelog entry, then push the real reference to that PR. An empty **Unreleased** section may stay empty. Once it contains a change it needs a PR reference; retain those references when moving changes into a versioned section. Verify the final links before merging.

Historical entries cite the PRs that introduced their recorded changes, rather than the PR that later adds links or corrects the changelog. Preserve the original version, date and release description during provenance backfills. Documentation and contribution-tooling maintenance alone do not require inventing a new application or plugin version.

Run `.venv/bin/python scripts/check_changelog_prs.py` from `whab/app/`. CI checks all tracked changelogs for a PR link in each populated section, without network access. This verifies link structure and scope; authors and reviewers must verify that the linked PR exists and actually introduced the described change.

## Branch protection

The repository is public, and two active rulesets protect `main`:

- [Main safeguards](https://github.com/Fillll/whab/rules/25219005) requires a pull request, passing `CI (Python 3.14)` and `CI (Python 3.15)` checks from GitHub Actions, and an up-to-date branch. It also requires resolved conversations and squash merges with linear history, and blocks force pushes and branch deletion. There are no bypass actors, including the maintainer.
- [Owner approval and merge gate](https://github.com/Fillll/whab/rules/25219007) allows only @Fillll to merge into `main` through a pull request. It requires one approving review from the code owner and dismisses stale approvals after new reviewable commits. [CODEOWNERS](../../.github/CODEOWNERS) designates @Fillll as the owner of every path. Only @Fillll can bypass this gate, and only through a pull request; direct pushes remain blocked.

GitHub does not allow authors to approve their own pull requests. For PRs authored as @Fillll, the human maintainer uses the PR-only owner exception to make the final merge decision; all Main safeguards still apply. Rulesets identify GitHub accounts, so tools authenticated as @Fillll have the same technical permissions and must follow the human approval requirement above.

The reproducible ruleset configurations are [main-safeguards.json](../../.github/main-safeguards.json) and [owner-gate.json](../../.github/owner-gate.json).

## Reporting sensitive bugs

Keep reports reproducible with synthetic records, redacted responses and public error messages. Do not put financial records, institution account identifiers, Plaid credentials, access tokens, encryption keys or private `.env` contents in an issue, PR, screenshot or log attachment. Report a suspected credential exposure privately to the repository maintainer rather than publishing the sensitive material.

## License

Contributions to WHAB's own code, documentation and assets are accepted under the [MIT License](LICENSE). Preserve applicable third-party notices. Independently written plugins may use their authors' chosen licenses.
