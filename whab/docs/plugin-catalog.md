# Curated plugin catalog

Generated from package metadata, explicit static declarations and reviewed verification evidence.
Catalog inspection imports no plugin and installs nothing. Installed plugins remain trusted Python code; workers contain failures without providing a security sandbox.

Inspect the packaged data through `GET /api/plugins/catalog` or `whab.plugins.catalog.catalog_snapshot()`. The endpoint retains the host's HTTPS and local-access guards and remains available while a budget transition is gated. A missing or damaged catalog returns a fixed diagnostic; discovery and installation are never fallback actions.

See [the canonical supported set](../app/plugins/README.md#supported-plugins) and [security reporting](../../SECURITY.md).

| Plugin | Distribution version | Host requirement | Lifecycle | Support |
| --- | --- | --- | --- | --- |
| [bank-logos](https://github.com/Fillll/whab/blob/22022509ea5aa5ad399d762add50167b62bdd097/whab/app/plugins/interface/bank-logos/README.md) | `whab_plugin_bank_logos 1.4.0` | `whab>=2.31,<3` | stateless-optional-worker | maintained |
| [client](https://github.com/Fillll/whab/blob/22022509ea5aa5ad399d762add50167b62bdd097/whab/app/plugins/storage/client/README.md) | `whab_plugin_client 1.6.0` | `whab>=2.39,<3` | declarative-remote-client-worker | maintained |
| [example](https://github.com/Fillll/whab/blob/22022509ea5aa5ad399d762add50167b62bdd097/whab/app/plugins/example/example/README.md) | `whab_plugin_example 1.4.0` | `whab>=2.24,<3` | stateless-report-worker | teaching |
| [example-api](https://github.com/Fillll/whab/blob/22022509ea5aa5ad399d762add50167b62bdd097/whab/app/plugins/example/example-api/README.md) | `whab_plugin_example_api 1.0.0` | `whab>=2.34,<3` | declarative-api-worker | teaching |
| [example-bank](https://github.com/Fillll/whab/blob/22022509ea5aa5ad399d762add50167b62bdd097/whab/app/plugins/example/example-bank/README.md) | `whab_plugin_example_bank 1.0.0` | `whab>=2.38,<3` | bank-provider-worker | teaching |
| [example-branding](https://github.com/Fillll/whab/blob/22022509ea5aa5ad399d762add50167b62bdd097/whab/app/plugins/example/example-branding/README.md) | `whab_plugin_example_branding 1.0.0` | `whab>=2.22,<3` | restart-required | teaching |
| [example-categorization](https://github.com/Fillll/whab/blob/22022509ea5aa5ad399d762add50167b62bdd097/whab/app/plugins/example/example-categorization/README.md) | `whab_plugin_example_categorization 1.0.0` | `whab>=2.22,<3` | restart-required | teaching |
| [example-file-import](https://github.com/Fillll/whab/blob/22022509ea5aa5ad399d762add50167b62bdd097/whab/app/plugins/example/example-file-import/README.md) | `whab_plugin_example_file_import 1.0.0` | `whab>=2.22,<3` | restart-required | teaching |
| [example-ui](https://github.com/Fillll/whab/blob/22022509ea5aa5ad399d762add50167b62bdd097/whab/app/plugins/example/example-ui/README.md) | `whab_plugin_example_ui 1.0.0` | `whab>=2.36,<3` | visual-ui-worker | teaching |
| [json](https://github.com/Fillll/whab/blob/22022509ea5aa5ad399d762add50167b62bdd097/whab/app/plugins/storage/json/README.md) | `whab_plugin_json 1.3.0` | `whab>=2.37,<3` | local-storage-worker | maintained |
| [plaid](https://github.com/Fillll/whab/blob/22022509ea5aa5ad399d762add50167b62bdd097/whab/app/plugins/ingestion/plaid/README.md) | `whab_plugin_plaid 1.3.0` | `whab>=2.38,<3` | bank-provider-worker | maintained |
| [random-forest-categorization](https://github.com/Fillll/whab/blob/22022509ea5aa5ad399d762add50167b62bdd097/whab/app/plugins/automation/random-forest-categorization/README.md) | `whab_plugin_random_forest_categorization 1.3.0` | `whab>=2.35,<3` | categorization-worker | maintained |
| [server](https://github.com/Fillll/whab/blob/22022509ea5aa5ad399d762add50167b62bdd097/whab/app/plugins/interface/server/README.md) | `whab_plugin_server 1.1.0` | `whab>=2.34,<3` | declarative-api-worker | maintained |
| [sqlite](https://github.com/Fillll/whab/blob/22022509ea5aa5ad399d762add50167b62bdd097/whab/app/plugins/storage/sqlite/README.md) | `whab_plugin_sqlite 1.12.0` | `whab>=2.37,<3` | local-storage-worker | maintained |
| [web](https://github.com/Fillll/whab/blob/22022509ea5aa5ad399d762add50167b62bdd097/whab/app/plugins/interface/web/README.md) | `whab_plugin_web 1.27.0` | `whab>=2.36,<3` | visual-ui-worker | maintained |
| [ynab](https://github.com/Fillll/whab/blob/22022509ea5aa5ad399d762add50167b62bdd097/whab/app/plugins/ingestion/ynab/README.md) | `whab_plugin_ynab 1.4.0` | `whab>=2.31,<3` | stateless-optional-worker | maintained |

Explicit stateless report, branding, file-parser, declarative API interface, pure categorization, typed bank-provider and participating visual descriptors advertise their supported live-worker adapters. Declarative remote-client workers return metadata only; the host retains their exact HTTPS authority, credentials, transport and financial intent. SQLite and JSON advertise compatible local storage workers; backend migration and copies remain explicit authority operations. Other entries require restart; activation is not inferred from package installation.

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

### pr89-host-categorization

Verified 2026-10-23; host `2.32.0`. Selected: `random-forest-categorization`.

The exact implementation passed 4430 Python tests with all thirteen runtime plugins at 100% statement/branch coverage, browser checks, minimum/current toolkit and generated external artifacts, replays and installed distributions. Fictional live HTTP tests exercised enable/train/busy replacement/finish/replace/disable/re-enable/reopen, saved-model preservation, read-only candidate factories, latest drained state, callback revocation, stable ledger/retry services, other budgets and storage copy/recovery integration. The independently packaged Random Forest engine retained its reviewed-only training and automatic cutoff behavior. This evidence covers host-process selection changes; compatible categorization code upgrades still require the forthcoming stateful worker adapter. It is not a native TLS or household deployment claim.

Tests: `tests/test_categorization_runtime.py`, `tests/test_categorization.py`, `tests/budgets/test_runtime.py`, `plugins/automation/random-forest-categorization/tests`, `scripts/check.py`.

Review: [PR #89](https://github.com/Fillll/whab/pull/89).

### pr92-live-api-interface

Verified 2026-10-23; host `2.34.0`. Selected: `server`.

The exact source passed 4561 Python tests, all thirteen runtime plugins at 100% statement/branch coverage, 280 browser checks, genuine minimum/current toolkit tests and external artifact/replay/distribution gates. HTTPS ASGI tests exercised live API selection, replacement, disabling, failed private token binding, full request draining, host-owned authorization and local guards, stable ledger revisions/retry responses, resource revocation and preserved categorization services. Actual separately approved immutable API worker versions exercised failed candidates, disable/re-enable and saved-version reopen. The bundled server declares the same host-owned adapter and has complete owning coverage; synthetic versioned fixtures separately exercise worker upgrade behavior. This source evidence does not establish native TLS transport, financial retry durability across host restart, household installation or bank connectivity.

Tests: `tests/web/test_live_api_interfaces.py`, `tests/plugins/test_environments.py::test_real_api_worker_upgrade_failed_preparation_disable_reenable_and_reopen`, `tests/test_remote_protocol.py`, `plugins/interface/server/tests`, `scripts/check.py`.

Review: [PR #92](https://github.com/Fillll/whab/pull/92).

### pr92-installed-native-api

Verified 2026-10-23; host `2.34.0`. Selected: `server`, `sqlite`.

Archived the exact committed source and installed genuine host2.34/server1.1/SQLite1.11 wheels outside the checkout. Native TLS1.3 explicitly trusted only the temporary runtime CA and rejected an untrusted CA. Verified bearer/Host/origin/budget guards and the bundled server worker, then four separately approved reviewed synthetic server versions: failed missing-token binding preserved the active environment/reference and exact same-host retry; query-only and command-only declarations rejected the other operation; all-command declarations enabled replacement and disable/re-enable. A test-only launcher paused an admitted public ledger command at an event-file barrier before financial dispatch; disabling drained until it acknowledged, preserving both accounts. Ordinary installed stock CLI restart restored the exact saved synthetic worker environment and both acknowledged accounts. Exact artifact/closure hashes and 17 HTTP outcomes were recorded. All listeners, workers and fictional homes were removed. This does not establish retry durability across host restart or provider/household connectivity.

Tests: `installed native HTTPS API interface upgrade/drain/reopen verification`.

Review: [PR #92](https://github.com/Fillll/whab/pull/92).

### pr94-storage-copy-controls

Verified 2026-10-23; host `2.34.0`. Selected: `web`.

The exact integrated source passed 4576 Python tests, all thirteen runtime plugins at 100% statement/branch coverage, 304 browser tests, 130 toolkit tests on each genuine minimum/current host, external generated native-runtime configurations and exact replays, and installed distribution checks. Storage-copy controls exercised current-primary status, financial and namespace watermarks, explicit empty-destination attachment and reviewed promotion, pending operation tracking, unknown-response reconciliation without mutation replay, stale-response guards, focus/edit barriers, unsupported hosts and demo read-only behavior. Browser visual inspection was unavailable; source/template and browser interaction checks do not establish visual acceptance or household installation.

Tests: `plugins/interface/web/tests/storage_copies.test.cjs`, `plugins/interface/web/tests/test_storage_copies.py`, `plugins/interface/web/tests/test_plugin_settings.py`, `scripts/check.py`.

Review: [PR #94](https://github.com/Fillll/whab/pull/94).

### pr95-categorization-workers

Verified 2026-10-23; host `2.35.0`. Selected: `random-forest-categorization`.

The exact integrated source passed 4618 Python tests, all thirteen runtime plugins at 100% statement/branch coverage, 304 browser tests, 130 toolkit tests against each genuine minimum/current artifact, independent generated native-runtime configurations/replays and installed distributions. Actual separately approved immutable categorizer versions exercised replacement, failed model readiness, unfinished training/cancel, saved-version reopen, missing model-index materialization recovery and child termination/explicit restart. Host regressions covered complete authoritative state/model snapshots, original result recovery without hook reexecution, private effect publication boundaries, failed cached-heap retirement across both workspaces, foreign saved-model refusal before dispatch, portable checkpoints/copies and disabled namespace edits. Random Forest retained reviewed-only eligibility and its existing cutoff with complete owning coverage. CLI creation/reopen released unstarted worker/journal owners. These HTTP source tests use an HTTPS ASGI scheme; this record does not establish native listener TLS or household/provider deployment.

Tests: `tests/plugins/test_categorization_worker.py`, `tests/plugins/test_environments.py`, `tests/test_namespace_effects_runtime.py`, `tests/test_categorization_runtime.py`, `tests/test_budget_cli.py`, `plugins/automation/random-forest-categorization/tests`, `scripts/check.py`.

Review: [PR #95](https://github.com/Fillll/whab/pull/95).

### pr96-api-teaching-toolkit

Verified 2026-10-23; host `2.34.0`. Selected: `example-api`.

The exact source passed 4569 Python tests, all fourteen runtime plugins at 100% statement/branch coverage, 280 browser checks, installed distributions and genuine minimum/current external gates. Toolkit 0.6 passed 164 installed tests per genuine WHAB 2.22/2.34 artifact with zero skips and complete statement/branch coverage. Five generated families built outside the checkout with required packages only, passed complete owning coverage and ten distinct constrained configurations with exact-artifact replay. Native API profiles used actual CA-verified HTTPS, host authorization and financial validation, enable/disable, same-candidate immutable worker activation and saved-version reopen. API projects use their genuine 2.34 minimum; older-host runtime N/A is never an API pass. The independently packaged example-api passed its own native public conformance and ten configurations/replays. These profiles do not claim distinct-version worker upgrades, restart-durable financial retries or household deployment; all budgets and providers were fictional.

Tests: `devtools/plugin-devkit/tests`, `plugins/example/example-api/tests`, `scripts/check_plugin_generation.py`, `scripts/check_distribution.py`, `scripts/check.py`.

Review: [PR #96](https://github.com/Fillll/whab/pull/96).

### pr97-visual-interface-source

Verified 2026-10-23; host `2.36.0`. Selected: `web`.

The exact source passed 4769 Python tests, all thirteen runtime plugins at 100% statement/branch coverage, 333 browser checks, 130 installed toolkit tests per genuine minimum/current artifact, generated configurations and exact replays, and installed distributions. Covered browser preparation barriers, stable ledger services, exact selection decisions, immutable resources, guarded headless mode, stopped repair, lost replies and retained cleanup ownership through partial constructors and retirement failures. Separate headless Chrome QA checked desktop/light and compact/dark Plugins settings without overflow and completed a browser-acknowledged handover of the same Web artifact. This source evidence does not claim distinct installed versions or household deployment.

Tests: `tests/plugins/test_ui_worker.py`, `tests/web/test_visual_interface.py`, `tests/plugins/test_ui_runtime.py`, `plugins/interface/web/tests`, `scripts/check.py`.

Review: [PR #97](https://github.com/Fillll/whab/pull/97).

### pr97-visual-interface-native-https

Verified 2026-10-23; host `2.36.0`. Selected: `web`, `sqlite`.

Built the exact committed runtime into wheels and installed outside the checkout. Native CA-verified HTTPS rejected cleartext, untrusted CA, foreign Host/origin and demo mutation. A dirty browser deferred handover while a separate acknowledged financial write remained admitted; duplicate commits retained the exact preview operation. Separately approved synthetic Web versions 1.27.1.dev1, broken dev2 and valid dev3 proved distinct immutable worker replacement, failed preparation preserving the prior worker, coherent four-page generation assets, headless acknowledged saves, old asset retention and exact saved dev3 restoration after an ordinary installed CLI reopen. All three acknowledged fictional accounts survived. Exact wheel/closure/script hashes and eight checks were retained; homes, host leaders and workers were removed. These synthetic provider versions differ from the bundled Web version listed here. This does not establish cross-host SDK upgrades, real provider connectivity or household installation.

Tests: `tests/plugins/test_ui_worker.py`, `tests/web/test_visual_interface.py`, `tests/plugins/test_ui_runtime.py`.

Review: [PR #97](https://github.com/Fillll/whab/pull/97).
