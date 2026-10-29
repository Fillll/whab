# Curated plugin catalog

Generated from package metadata, explicit static declarations and reviewed verification evidence.
Catalog inspection imports no plugin and installs nothing. Installed plugins remain trusted Python code; workers contain failures without providing a security sandbox.

Inspect the packaged data through `GET /api/plugins/catalog` or `whab.plugins.catalog.catalog_snapshot()`. The endpoint retains the host's HTTPS and local-access guards and remains available while a budget transition is gated. A missing or damaged catalog returns a fixed diagnostic; discovery and installation are never fallback actions.

See [the canonical supported set](../app/plugins/README.md#supported-plugins) and [security reporting](../../SECURITY.md).

| Plugin | Distribution version | Host requirement | Lifecycle | Support |
| --- | --- | --- | --- | --- |
| [bank-logos](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/interface/bank-logos/README.md) | `whab_plugin_bank_logos 1.4.1` | `whab>=2.31,<3` | stateless-optional-worker | maintained |
| [client](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/storage/client/README.md) | `whab_plugin_client 1.6.1` | `whab>=2.39,<3` | declarative-remote-client-worker | maintained |
| [example](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/example/example/README.md) | `whab_plugin_example 1.4.1` | `whab>=2.24,<3` | stateless-report-worker | teaching |
| [example-api](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/example/example-api/README.md) | `whab_plugin_example_api 1.0.1` | `whab>=2.34,<3` | declarative-api-worker | teaching |
| [example-bank](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/example/example-bank/README.md) | `whab_plugin_example_bank 1.0.1` | `whab>=2.38,<3` | bank-provider-worker | teaching |
| [example-branding](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/example/example-branding/README.md) | `whab_plugin_example_branding 1.0.1` | `whab>=2.22,<3` | restart-required | teaching |
| [example-categorization](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/example/example-categorization/README.md) | `whab_plugin_example_categorization 1.0.1` | `whab>=2.22,<3` | restart-required | teaching |
| [example-file-import](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/example/example-file-import/README.md) | `whab_plugin_example_file_import 1.0.1` | `whab>=2.22,<3` | restart-required | teaching |
| [example-remote-client](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/example/example-remote-client/README.md) | `whab_plugin_example_remote_client 1.0.1` | `whab>=2.40,<3` | declarative-remote-client-worker | teaching |
| [example-storage](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/example/example-storage/README.md) | `whab_plugin_example_storage 1.0.1` | `whab>=2.41,<3` | local-storage-worker | teaching |
| [example-ui](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/example/example-ui/README.md) | `whab_plugin_example_ui 1.0.1` | `whab>=2.36,<3` | visual-ui-worker | teaching |
| [json](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/storage/json/README.md) | `whab_plugin_json 1.5.0` | `whab>=2.47,<3` | local-storage-worker | maintained |
| [llm-categorization](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/automation/llm-categorization/README.md) | `whab_plugin_llm_categorization 1.1.0` | `whab>=2.43,<3` | restart-required | maintained |
| [plaid](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/ingestion/plaid/README.md) | `whab_plugin_plaid 1.4.0` | `whab>=2.38,<3` | bank-provider-worker | maintained |
| [random-forest-categorization](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/automation/random-forest-categorization/README.md) | `whab_plugin_random_forest_categorization 1.3.1` | `whab>=2.35,<3` | categorization-worker | maintained |
| [server](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/interface/server/README.md) | `whab_plugin_server 1.1.1` | `whab>=2.34,<3` | declarative-api-worker | maintained |
| [sqlite](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/storage/sqlite/README.md) | `whab_plugin_sqlite 1.13.0` | `whab>=2.47,<3` | local-storage-worker | maintained |
| [web](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/interface/web/README.md) | `whab_plugin_web 1.38.0` | `whab>=2.49,<3` | visual-ui-worker | maintained |
| [ynab](https://github.com/Fillll/whab/blob/60cdb140b54206aa3807ed0875daa462712ae21f/whab/app/plugins/ingestion/ynab/README.md) | `whab_plugin_ynab 1.4.1` | `whab>=2.31,<3` | stateless-optional-worker | maintained |

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

### pr99-visual-teaching-toolkit

Verified 2026-10-23; host `2.36.0`. Selected: `example-ui`, `example-api`.

The exact source passed 4779 Python tests, all fifteen runtime plugins at 100% statement/branch coverage, 333 browser checks, installed distributions and genuine minimum/current external gates. Toolkit 0.7 passed 192 installed tests per genuine WHAB 2.22/2.36 artifact with zero skips and complete statement/branch coverage. Six generated families built outside the checkout with required packages only, passed complete owning coverage and ten distinct configurations with every exact-artifact replay on each applicable genuine host floor/current matrix. Visual projects require the actual 2.36 SDK; API projects retain their actual 2.34 floor and other families retain 2.22. Older-host runtime N/A never counts as a native execution pass. The native visual profile verifies coherent pages/resources, browser preparation acknowledgements, exact approved same-candidate activation, disable/enable and saved-selection reopen. Distinct-version visual worker upgrades and household installation are separate evidence.

Tests: `devtools/plugin-devkit/tests`, `plugins/example/example-ui/tests`, `scripts/check_plugin_generation.py`, `scripts/check_distribution.py`, `scripts/check.py`.

Review: [PR #99](https://github.com/Fillll/whab/pull/99).

### pr106-remote-client-teaching

Verified 2026-10-23; host `2.41.0`. Selected: `example-remote-client`.

The exact clean committed source passed 290 installed toolkit tests on genuine WHAB 2.22 and current 2.41 with zero skips; candidate toolkit statement/branch coverage was complete. The maintained remote-client example and a separately generated project each passed installed owning behavior and complete plugin coverage on separate genuine WHAB 2.40 minimum and 2.41 current artifacts. Each of those four rows passed ten distinct native configurations and ten exact approved-artifact replays, with all fictional homes/listeners/workers cleaned up. The genuine 2.40 source was e8fa8585c16d2f8a93b1966315c0fee34eb11d35. Native profiles used host-owned authorization/financial services, two fictional HTTPS hosts and a reviewed public-transport overlay that explicitly trusted the temporary authority CA. They exercised candidate workers, varied peers/order/transitions, pause/resume, exact-version reopen and client restart after a dropped acknowledged result; explicit Finish retained the original request/CAS identity. This does not establish stock client CLI private-CA configuration, remote-server restart retry durability, public receipt lookup, automatic unknown reconciliation, distinct-version upgrades from a single candidate artifact, local migration/copy support or household deployment. The toolkit's genuine 2.22 import compatibility is separate from this optional family's 2.40 floor. The interrupted source canonical run is not certified as a complete pass.

Tests: `devtools/plugin-devkit/tests`, `plugins/example/example-remote-client/tests`, `scripts/check_plugin_devkit.py`, `scripts/check_plugin_generation.py`.

Review: [PR #106](https://github.com/Fillll/whab/pull/106).

### pr99-visual-teaching-installed-example

Verified 2026-10-23; host `2.36.0`. Selected: `example-ui`.

The exact maintained visual example and toolkit were built into approved wheels and installed outside the checkout. Its two owning behavioral tests passed with complete statement/branch coverage; ten distinct native HTTPS configurations and all ten exact-artifact replays passed using temporary fictional homes and an explicit temporary CA. Minimum and candidate are the same genuine WHAB 2.36 artifact, recorded as one host row serving both roles. Public host security guards, coherent four-page resources, exact approved-candidate activation, browser preparation and reopen executed. This is a same-artifact external-author profile, not distinct-version code-upgrade or production deployment evidence.

Tests: `plugins/example/example-ui/tests`, `scripts/check_plugin_generation.py`.

Review: [PR #99](https://github.com/Fillll/whab/pull/99).

### pr100-remote-client-lifecycle

Verified 2026-10-23; host `2.39.0`. Selected: `client`.

Focused tests prepared separately approved synthetic descriptor wheels 1.0, 1.1 and 2.0 against the real current host closure. A missing-setting candidate preserved the active worker; compatible replacement retained the host transport, pending original command and revision, preserved the previous immutable environment and restored the exact saved version after pause/reopen. Explicit original-request completion preserved one acknowledged fictional account. Paused startup executed no descriptor, an incompatible host expectation refused before interpreter execution, and a stopped same-binding repair preserved prior ready metadata. An embedded separate installed native listener test built the real host, SQLite and server wheels outside the checkout, explicitly verified its runtime CA, observed unauthenticated 401/authenticated 200, and exercised the public host-owned remote transport with one acknowledged account. That native transport uses an explicitly supplied test CA; this does not prove stock client CLI private-CA configuration, distinct descriptor upgrades over a native listener, public receipt lookup, remote-server restart retry durability or household deployment. The canonical source suite passed before the narrow follow-ups, but its external API exact replay failed and is not certified by this record.

Tests: `tests/plugins/test_remote_client_workers.py`.

Review: [PR #100](https://github.com/Fillll/whab/pull/100).

### pr103-explicit-child-environments

Verified 2026-10-23; host `2.40.0`. Selected: `example`, `sqlite`, `json`.

The exact source passed six focused cases: the explicit process allowlist and password-alias exclusion, actual report/SQLite/JSON worker startup and cleanup with fictional custom-setting canaries, scoped configuration dispatch, and the installed native HTTPS fixture. Arbitrary apparently safe names were excluded while the reviewed standard locale survived. The native fixture built its declared isolated build backend and retained failure diagnostics privately; it verified the temporary runtime CA, public authorization and an acknowledged fictional account. Earlier complete CI on the equivalent public-SDK foundation had two fixture failures that are corrected here; an interrupted local run is not a complete-pass claim. This record covers the child-environment policy and focused fixture corrections, not the final combined submission gate or household deployment. Trusted executable plugins retain operating-system access.

Tests: `tests/plugins/test_process_environment.py`, `tests/plugins/test_storage_worker_faults.py::test_native_child_receives_scoped_config_without_custom_provider_or_api_environment_credentials`, `tests/plugins/test_remote_client_workers.py::test_installed_host_client_uses_native_https_verified_runtime_ca_and_public_server`.

Review: [PR #103](https://github.com/Fillll/whab/pull/103).

### pr107-installed-storage-primary-smoke

Verified 2026-10-23; host `2.41.0`. Selected: `example-storage`.

Built the exact clean source into genuine host 2.41, toolkit 0.10 and independent storage example 1.0 wheels, then installed outside the checkout. All 71 owning tests passed with zero skips and 100% statement/branch coverage. One native-local-storage-v1 configuration and its exact-artifact replay each verified the candidate as sole required primary, read-only readiness, approved-worker activation, three acknowledged financial writes, two saved-worker reopens, an invalid-selection public fault and complete owned-process cleanup over temporary-CA HTTPS. The independently tested SQLite reference was not co-selected with the primary. This completed smoke exposed and verified the host correction that separates native code readiness from migration/copy eligibility while retaining background-work draining. The earlier installed failure is retained separately. The example does not advertise transfer.local or storage.replica; those controls are explicitly inapplicable. This record claims neither distinct-version upgrades, the complete ten-configuration matrix, a complete contribution gate nor household deployment. The full report and exact artifact closure were retained; no real bank or household data was used.

Tests: `plugins/example/example-storage/tests`, `native-local-storage-v1 configuration and exact-artifact replay`.

Review: [PR #104](https://github.com/Fillll/whab/pull/104), [PR #107](https://github.com/Fillll/whab/pull/107).

### pr102-local-storage-source

Verified 2026-10-23; host `2.37.0`. Selected: `sqlite`, `json`.

Separate fictional SQLite and JSON primary budgets exercised typed storage workers, drained compatible upgrades, rejected candidates, pending native operation evidence, nondecreasing recovery floors, stopped repair, native lease/cleanup ownership and concurrent status reads during both authority directions. Exact frozen source passed 4924 Python cases, all fourteen plugins at 100% statement/branch coverage, 333 browser tests and 164 installed toolkit cases on each genuine minimum/current host. The canonical generated stage stopped at a sanitized API exact-replay failure after its ten native API check configurations had passed; an independent replay of all ten identical retained artifact configurations passed. The original intermittent failure remains unattributed: this record does not claim a complete canonical pass or a timeout/status-race cause. A later narrow source regression covers phase-specific execute rejection versus unknown post-dispatch evidence/receipt publication. Final integrated validation remains required.

Tests: `tests/plugins/test_storage_worker.py`, `tests/plugins/test_storage_worker_faults.py`, `tests/plugins/test_storage_worker_selection.py`, `tests/web/test_storage_migration.py`, `tests/web/test_storage_replicas.py`, `scripts/check.py`.

Review: [PR #102](https://github.com/Fillll/whab/pull/102).

### pr102-local-storage-native-https

Verified 2026-10-23; host `2.37.0`. Selected: `sqlite`, `json`.

Actual installed wheels outside the checkout used native HTTPS and explicit temporary CA trust; an untrusted CA was refused. Separate fictional budgets ran both SQLite-primary/JSON-copy and JSON-primary/SQLite-copy directions, never two authoritative writers in one registry. Distinct approved synthetic versions proved valid upgrade, failed candidate preservation and later upgrade. Four acknowledged accounts per direction, namespace-only revisions, bank-continuation authority deferral, copy catch-up/promotion, reverse migration, two ordinary CLI reopens, saved worker selection, opaque models/files and original containers survived. The bank guard used real encrypted continuation records with fictional capture, not a bundled provider worker or external bank connection. Exact 33-wheel hashes and scripts were retained; homes/processes were removed. This is feature evidence at the stated source, not an installed claim for subsequent source-only outcome fixes.

Tests: `tests/plugins/test_storage_worker.py`, `tests/web/test_storage_migration.py`, `tests/web/test_storage_replicas.py`.

Review: [PR #102](https://github.com/Fillll/whab/pull/102).

### pr102-cross-host-storage-repair

Verified 2026-10-23; host `2.37.0`. Selected: `sqlite`, `json`.

Genuine installed host 2.37 at this source and genuine host 2.38 at a28ab71cf013039e36fbf4842cb96ce7a1644de6 verified both independent backend repair directions. New-host startup refused the old approved code binding before repair without changing native/code/container bytes. Explicit stopped read-only repair retained exact native UUID/epoch/generation/digest, namespace revision, original container, prior reference bytes/history and exact lost-publication retry. Two new-host ordinary CLI reopens per backend retained three acknowledged accounts, models/settings and the saved current approved worker. Each separate named-budget startup legitimately admitted prepare_budget_files(existing=True): its exact native command fingerprint/receipt proved generation G+1 with unchanged financial digest/UUID/epoch/namespace. A new acknowledged write advanced another generation. The original overstrict assertion expecting identical generation across those distinct startup operations is preserved in the evidence; no preparation optimization was made. Both complete artifact closures, hashes, scripts and the original assertion were retained. All homes/providers were fictional; no provider connection or household deployment.

Tests: `tests/plugins/test_storage_worker_selection.py`, `tests/test_budget_cli.py`.

Review: [PR #102](https://github.com/Fillll/whab/pull/102).

### pr101-live-bank-source

Verified 2026-10-23; host `2.38.0`. Selected: `plaid`.

The exact source completed the complete contribution command in GitHub CI on Python 3.14 and 3.15, including genuine minimum/current installed toolkit and generated-project tests, exact-artifact replays and installed distributions. An independent macOS run passed 5143 Python tests, all fourteen runtime plugins at 100% statement/branch coverage and 354 browser checks, then stopped during an old toolkit 0.6 API replay after its ten native checks passed; that separate failure is preserved and was not attributed to a cause. An independent installed rerun passed all ten original API replays with the exact artifact hashes and unchanged 30-second operation deadlines. Bank tests used fictional homes and mocked provider transports for typed worker operations, Item/cursor and annotation preservation, startup/cleanup ownership, active work draining, scoped OAuth flows, encrypted capture and native receipt recovery. Bundled Plaid has complete owning coverage; distinct-version native worker behavior is recorded separately with a reviewed fictional provider. No real provider connection or household deployment was used.

Tests: `tests/test_bank_runtime.py`, `tests/test_bank_native.py`, `tests/test_bank_lifecycle.py`, `tests/plugins/test_provider_worker.py`, `tests/plugins/test_provider_upgrade.py`, `tests/web/test_bank_continuations.py`, `tests/budgets/test_bank_storage_barriers.py`, `plugins/ingestion/plaid/tests`, `scripts/check.py`, `GitHub Actions run 40055955522`.

Review: [PR #101](https://github.com/Fillll/whab/pull/101).

### pr101-native-outcome-phases

Verified 2026-10-23; host `2.38.0`. Selected: `sqlite`, `json`.

A separate follow-up changes only the storage proxy outcome phases and adds fourteen controlled regressions. All sixty focused SQLite/JSON proxy and native worker tests passed. A direct execution rejection permits definite noncommit only after exact unchanged native proof; intent-publication failure, failed proof, malformed evidence/receipt and post-result completion failure retain pending intent and the unknown-write fence. Controlled reproductions preserved original false-rejection evidence and verified the corrected unknown outcome. This is focused follow-up validation, distinct from the earlier source's complete CI and native bank evidence.

Tests: `tests/plugins/test_storage_proxy_outcomes.py`, `tests/plugins/test_storage_worker.py`.

Review: [PR #101](https://github.com/Fillll/whab/pull/101).

### pr101-installed-native-bank

Verified 2026-10-23; host `2.38.0`. Selected: `sqlite`, `json`, `web`.

Built genuine host 2.38, SQLite 1.12, JSON 1.3 and Web 1.27 wheels from the exact source and installed them outside the checkout. Three fictional native HTTPS cases explicitly trusted only their temporary runtime CA: a reviewed offline bank provider 1.0 to failed 1.5 to 2.0 upgrade with ready-flow deferral, disable/re-enable, pending-to-posted annotations and acknowledged-write SIGKILL/stock-CLI reopen; an exchange response lost before encrypted capture with no automatic repeat after reopen; and encrypted captured native application refused by a controlled directory-permission fault, then explicitly finished using the original native command and receipt. A separately reviewed fictional JSON 1.3.1 code-only fixture preserved the native UUID/epoch/generation and captured Item/cursor while recovering the original request. Synthetic provider and JSON patch versions are test fixtures, not bundled Plaid or published backend releases. The helper was extracted candidate source alongside toolkit 0.6, not a released toolkit 0.8 claim. Exact wheel/closure hashes were retained; all owned homes/listeners were removed, and no external provider HTTP or household data was used.

Tests: `installed native HTTPS v1/failed-candidate/v2 bank worker verification`, `installed native HTTPS exchange-uncertain and captured-native-apply recovery`, `ordinary stock CLI reopen after acknowledged-write process termination`.

Review: [PR #101](https://github.com/Fillll/whab/pull/101).

### pr105-bank-toolkit-source

Verified 2026-10-23; host `2.38.0`. Selected: `example-bank`.

The exact source passed 5176 Python cases, all sixteen runtime plugins at 100% statement/branch coverage and 354 browser checks. Genuine installed toolkit tests passed 224 cases each on WHAB 2.22 and 2.38 with zero skips; the current toolkit measured 1507 statements and 474 branches at 100%. The bank module remains lazy on the older SDK and requires actual 2.38 for its native behavior. The complete local command later stopped before generated configurations because two historical source recipes built the same reference-wheel filename. That original artifact-assembly failure is preserved; this record does not claim a complete canonical pass or final generated matrix. Separate prior installed bank records establish their exact tested snapshots.

Tests: `devtools/plugin-devkit/tests`, `plugins/example/example-bank/tests`, `tests/test_plugin_generation_checks.py`, `scripts/check.py`.

Review: [PR #105](https://github.com/Fillll/whab/pull/105).

### pr105-installed-bank-example

Verified 2026-10-23; host `2.38.0`. Selected: `example-bank`, `sqlite`, `json`.

Built and installed the maintained example, toolkit and genuine host/reference wheels outside the checkout. The example passed all twelve owning behavioral tests at 100% of 63 statements and 20 branches, then ten distinct native configurations and ten exact-artifact replays. WHAB 2.38 served both minimum and current roles using one actual compatible artifact. The five variants (standard, ready restart, ready retirement deferral, update restart and completion replay) each ran against SQLite and JSON with distinct reviewed peer sets. Public native HTTPS used only the temporary runtime CA, host financial/continuation APIs and the explicitly approved offline fictional transport. Completed flow, sync, annotation, acknowledged-write/copy/promotion/migration/reopen and disconnect checks created no external connection. Approved same-artifact activation is readiness evidence; distinct-version bank upgrades are recorded in PR101. Exact hashes were retained and owned homes/processes removed.

Tests: `plugins/example/example-bank/tests`, `scripts/check_plugin_generation.py`, `whab_plugin_devkit.bank_conformance`.

Review: [PR #105](https://github.com/Fillll/whab/pull/105).

### pr105-installed-generated-bank

Verified 2026-10-23; host `2.38.0`. Selected: `sqlite`, `json`.

The public generator created an independent generated-bank project outside the checkout. Its installed wheel passed twelve owning tests with complete statement/branch coverage, all ten distinct native-bank-v1 configurations and all ten exact replays using the same five offline flow variants in both reference-backend directions. The actual host 2.38 artifact fulfilled both minimum and current roles; the reviewed immutable recipe retains that genuine SDK floor for later host matrices. Explicit fictional-v1 approval, exact wheel hashes and saved manifests bound every replay. The selected curated packages here are the SQLite/JSON references; the generated bank distribution is a separate reviewed fixture, not the maintained example or bundled Plaid. The behavior used temporary-CA HTTPS and no external provider traffic.

Tests: `scripts/check_plugin_generation.py`, `generated bank project owning tests`, `whab_plugin_devkit.bank_conformance`.

Review: [PR #105](https://github.com/Fillll/whab/pull/105).
