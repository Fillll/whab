# Curated plugin catalog

Generated from package metadata, explicit static declarations and reviewed verification evidence.
Catalog inspection imports no plugin and installs nothing. Installed plugins remain trusted Python code; workers contain failures without providing a security sandbox.

Inspect the packaged data through `GET /api/plugins/catalog` or `whab.plugins.catalog.catalog_snapshot()`. The endpoint retains the host's HTTPS and local-access guards and remains available while a budget transition is gated. A missing or damaged catalog returns a fixed diagnostic; discovery and installation are never fallback actions.

See [the canonical supported set](../app/plugins/README.md#supported-plugins) and [security reporting](../../SECURITY.md).

| Plugin | Distribution version | Host requirement | Lifecycle | Support |
| --- | --- | --- | --- | --- |
| [bank-logos](https://github.com/Fillll/whab/blob/692d757cfcf6e9d3838ed1b8ea3ecb4c286d41df/whab/app/plugins/interface/bank-logos/README.md) | `whab_plugin_bank_logos 1.4.0` | `whab>=2.31,<3` | stateless-optional-worker | maintained |
| [client](https://github.com/Fillll/whab/blob/692d757cfcf6e9d3838ed1b8ea3ecb4c286d41df/whab/app/plugins/storage/client/README.md) | `whab_plugin_client 1.5.0` | `whab>=2.22,<3` | restart-required | maintained |
| [example](https://github.com/Fillll/whab/blob/692d757cfcf6e9d3838ed1b8ea3ecb4c286d41df/whab/app/plugins/example/example/README.md) | `whab_plugin_example 1.4.0` | `whab>=2.24,<3` | stateless-report-worker | teaching |
| [example-branding](https://github.com/Fillll/whab/blob/692d757cfcf6e9d3838ed1b8ea3ecb4c286d41df/whab/app/plugins/example/example-branding/README.md) | `whab_plugin_example_branding 1.0.0` | `whab>=2.22,<3` | restart-required | teaching |
| [example-categorization](https://github.com/Fillll/whab/blob/692d757cfcf6e9d3838ed1b8ea3ecb4c286d41df/whab/app/plugins/example/example-categorization/README.md) | `whab_plugin_example_categorization 1.0.0` | `whab>=2.22,<3` | restart-required | teaching |
| [example-file-import](https://github.com/Fillll/whab/blob/692d757cfcf6e9d3838ed1b8ea3ecb4c286d41df/whab/app/plugins/example/example-file-import/README.md) | `whab_plugin_example_file_import 1.0.0` | `whab>=2.22,<3` | restart-required | teaching |
| [json](https://github.com/Fillll/whab/blob/692d757cfcf6e9d3838ed1b8ea3ecb4c286d41df/whab/app/plugins/storage/json/README.md) | `whab_plugin_json 1.2.0` | `whab>=2.30,<3` | restart-required | maintained |
| [plaid](https://github.com/Fillll/whab/blob/692d757cfcf6e9d3838ed1b8ea3ecb4c286d41df/whab/app/plugins/ingestion/plaid/README.md) | `whab_plugin_plaid 1.2.0` | `whab>=2.27,<3` | restart-required | maintained |
| [random-forest-categorization](https://github.com/Fillll/whab/blob/692d757cfcf6e9d3838ed1b8ea3ecb4c286d41df/whab/app/plugins/automation/random-forest-categorization/README.md) | `whab_plugin_random_forest_categorization 1.2.0` | `whab>=2.32,<3` | categorization-host | maintained |
| [server](https://github.com/Fillll/whab/blob/692d757cfcf6e9d3838ed1b8ea3ecb4c286d41df/whab/app/plugins/interface/server/README.md) | `whab_plugin_server 1.0.1` | `whab>=2,<3` | Legacy; restart required | maintained |
| [sqlite](https://github.com/Fillll/whab/blob/692d757cfcf6e9d3838ed1b8ea3ecb4c286d41df/whab/app/plugins/storage/sqlite/README.md) | `whab_plugin_sqlite 1.11.0` | `whab>=2.30,<3` | restart-required | maintained |
| [web](https://github.com/Fillll/whab/blob/692d757cfcf6e9d3838ed1b8ea3ecb4c286d41df/whab/app/plugins/interface/web/README.md) | `whab_plugin_web 1.25.1` | `whab>=2.19,<3` | Legacy; restart required | maintained |
| [ynab](https://github.com/Fillll/whab/blob/692d757cfcf6e9d3838ed1b8ea3ecb4c286d41df/whab/app/plugins/ingestion/ynab/README.md) | `whab_plugin_ynab 1.4.0` | `whab>=2.31,<3` | stateless-optional-worker | maintained |

Explicit stateless report, branding and file-parser descriptors advertise their supported live-worker adapters. Categorization supports host-process selection changes; its code upgrades still require an application restart. Other entries require restart; backend migration and copies are not inferred from package installation.

## Recorded verification

Evidence applies only to its exact host, artifacts and selection. Installed-only artifacts are not proof that their capabilities executed. Draft PR/source verification is not a release or deployment claim.

### pr82-installed-sqlite-web

Verified 2026-10-23; host `2.25.0`. Selected: `bank-logos`, `example`, `plaid`, `sqlite`, `web`, `ynab`.

Built the exact source commit into wheels and installed outside the checkout. The selected SQLite/Web application exercised fictional account writes, pages/assets, optional selection metadata, CSV export, a mocked bank-logo fetch/cache flush and portable-budget creation/export. Plaid credential refusal was tested without creating a bank connection; YNAB controls/assets loaded without importing a financial archive. Other listed artifacts were installed and discovered, not proof their full capabilities executed. HTTP tests used TestClient with an HTTPS scheme; native TLS was not tested by this smoke. No storage migration or copy-authority claim.

Tests: `scripts/check_distribution.py::SMOKE`, `scripts/check_distribution.py::INDEPENDENT_VERSION`.

Review: [PR #81](https://github.com/Fillll/whab/pull/81), [PR #82](https://github.com/Fillll/whab/pull/82).

### pr82-installed-json-web

Verified 2026-10-23; host `2.25.0`. Selected: `json`, `web`.

A separate fictional runtime selected JSON storage and Web with all optional plugins disabled. Installed host endpoints saved an account and transaction, verified balance, exported personal and demo JSON-native payloads and reopened the saved state without creating SQLite files. HTTP tests used TestClient with an HTTPS scheme, not native TLS. The other installed artifacts were not selected in this runtime. No storage migration or copy-authority claim.

Tests: `scripts/check_distribution.py::SMOKE`.

Review: [PR #79](https://github.com/Fillll/whab/pull/79), [PR #82](https://github.com/Fillll/whab/pull/82).

### pr79-json-native-https

Verified 2026-10-23; host `2.23.0`. Selected: `json`.

Installed host 2.23.0, JSON 1.0.0 and toolkit 0.1.0 were tested in an isolated temporary runtime with optional plugins and UI disabled. Native HTTPS explicitly trusted the generated CA. Fictional account/transaction saves, a fresh-runtime portable restore, restart and JSON-native demo export/reopen passed while SQLite imports were prohibited. This historical artifact test does not verify later host/backend combinations, live adapters, migration or copy authority.

Tests: `plugins/storage/json/tests/test_json_https.py::test_json_https_save_export_fresh_restore_restart_and_demo_storage`.

Review: [PR #79](https://github.com/Fillll/whab/pull/79).

### pr86-installed-sqlite-web

Verified 2026-10-23; host `2.29.0`. Selected: `bank-logos`, `example`, `plaid`, `sqlite`, `web`, `ynab`.

The exact committed candidate built and installed host, all thirteen plugins and their MIT resources outside the checkout. The selected six-plugin SQLite/Web application saved fictional accounts, served pages/assets, checked settings, exported CSV and containers, and used mocked bank-logo transport. Packaged catalog rows were compared against installed public contracts without entry-point discovery during catalog reads. Other installed packages were discovered but not selected; no bank connection was created. HTTP used an HTTPS TestClient scheme rather than a native TLS listener. This evidence does not claim migration/copy or other-family live behavior.

Tests: `scripts/check_distribution.py::SMOKE`.

Review: [PR #86](https://github.com/Fillll/whab/pull/86).

### pr86-installed-json-web

Verified 2026-10-23; host `2.29.0`. Selected: `json`, `web`.

A separate installed-wheel runtime selected JSON/Web with optional plugins disabled. Fictional account/transaction save, balance, native personal/demo exports and saved-state reopen passed without SQLite files. Packaged catalog inspection executed no plugin hook. HTTP used an HTTPS TestClient scheme, not native TLS. Other installed packages were not selected in this runtime; no migration/copy or other-family live claim.

Tests: `scripts/check_distribution.py::SMOKE`.

Review: [PR #86](https://github.com/Fillll/whab/pull/86).

### pr87-installed-copy-promotion

Verified 2026-10-23; host `2.30.0`. Selected: `sqlite`, `json`.

Built the exact committed host and two backend wheels into a fresh environment outside the checkout. Native HTTPS explicitly trusted only each temporary runtime CA. Separate fictional named-budget runs alternated SQLite-primary/JSON-copy and JSON-primary/SQLite-copy; these are explicit primary/copy roles, never two authoritative registrations in one registry. Verified preview/attachment, saved watermark, namespace-only revision with financial generation unchanged, late acknowledged primary writes, explicit promotion with fresh epoch and source fence, acknowledged destination writes, two complete process reopens, opaque model/file and exact original-container preservation. No bank/provider plugin or connection was used. Source tests additionally exercised fourteen process-termination promotion boundaries. The full gate passed 4356 Python tests, all thirteen plugins at 100% statement/branch coverage, browser checks and external minimum/current artifacts; visual copy controls and storage-worker upgrades were not implemented.

Tests: `tests/web/test_storage_replicas.py`, `tests/budgets/test_namespace_archives.py`, `installed native HTTPS copy/promotion/reopen verification`, `scripts/check.py`.

Review: [PR #87](https://github.com/Fillll/whab/pull/87).

### pr88-live-branding-parser

Verified 2026-10-23; host `2.31.0`. Selected: `bank-logos`, `ynab`.

The exact source passed 4403 Python tests with all thirteen runtime plugins at 100% statement/branch coverage, browser tests, genuine minimum/current toolkit checks, generated configurations/replays and installed-wheel checks. The concrete branding and YNAB suites covered live declarations, per-budget/environment cache isolation, parser identity and immutable resources using fictional transports/data. Separate real approved-wheel upgrade tests exercised reviewed synthetic branding and parser providers; native CA-verified HTTPS then verified parser4/failed4.1/5 and branding6/failed6.1/7, stale-preview refusal, idempotent import, preserved old assets/cache, exact acknowledged ledger writes and full parent reopen. Those synthetic provider versions are not the bundled distribution versions listed here. Actual archived WHAB2.25 protocol1 was also tested against the actual candidate for explicit current-host replacement with exact original environment/reference preservation and lost-reply retry. No real provider/artwork connection was used. Categorization/bank/storage/visual/API code-upgrade support is outside this evidence.

Tests: `plugins/interface/bank-logos/tests`, `plugins/ingestion/ynab/tests`, `tests/plugins/test_environments.py`, `tests/plugins/test_worker_replacement.py`, `tests/web/test_live_plugins.py`, `scripts/check.py`.

Review: [PR #88](https://github.com/Fillll/whab/pull/88).
