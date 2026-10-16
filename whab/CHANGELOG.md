# Changelog

WHAB uses Semantic Versioning. Released tags are immutable.

## Unreleased

## 1.7.1 — 2026-10-15

- Move budget selection into Settings → Budgets, with the current budget shown in Settings and beside the selected budget. Remove the desktop and mobile budget dropdowns; preserve page, month and currency when switching budgets.
- Group installed plugins under distinct alphabetical category headings and sort their names alphabetically. Hide example plugins from the Settings list while preserving valid existing example selections.
- Use sentence case and acronym spelling for WHAB web and Import from YNAB ZIP file. Show friendly bundled names for disabled plugins without loading their code.

## 1.7.0 — 2026-10-14

- Show Plaid-supplied merchant logos beside transaction payees as part of the existing Plaid plugin. Import only the supplied artwork reference, with no merchant guessing, external lookup service or additional plugin selection. Keep names visible when artwork is absent or fails.
- Add an optional API v1 merchant-artwork capability for bank providers, guarded local image serving and a bounded in-memory cache. Plaid downloads only PNGs from its public logo CDNs, without banking credentials, redirects or transaction data.
- Preserve merchant artwork through atomic synchronization and pending-to-posted transitions with a compatible nullable SQLite column. Existing rows remain without artwork until a subsequent provider import or update supplies it.

## 1.6.0 — 2026-10-14

- Add Settings → Plugins → Installed with an All categories view, category filtering, selection checkboxes and an Options page for every installed plugin. Keep required storage/interface selections locked and apply optional selection changes after restart.
- Add an optional API v1 settings capability with validated plain-text details and declared actions, served through guarded host endpoints. Disabled plugin settings do not execute code.
- Keep successful Bank logos images for 365 days in a bounded local disk cache that survives restarts. Refresh on the first image request after expiry; keep failed requests cached for five minutes. Add a Flush cache button in Bank logos options to clear saved images and transient misses immediately.

## 1.5.0 — 2026-10-14

- Make the two-level category hierarchy a core contract. Parent groups organize child categories; transactions, category targets and envelope assignments accept children only. Existing child IDs, allocations and annotations remain intact.
- Support accounts and child categories in multiple ISO currencies within one budget. Keep cash, debt, rollover, activity, trends and funding separate by currency, without exchange rates or conversion. Transactions inherit account currency and reject mismatched category assignments.
- Use fixed core formats: dates `YYYY-MM-DD`, USD `$1,234.56`, and other currencies with their ISO code and defined minor-unit precision. The selected interface plugin can replace presentation. The bundled Web interface uses these defaults and provides a currency selector.
- Add compatible SQLite currency columns defaulting existing records to USD. Scope reporting snapshots to one currency and add currency identification to CSV exports; preserve the legacy integer amount field names.

## 1.4.0 — 2026-10-14

- Add the optional YNAB export plugin, with bounded ZIP/TSV/CSV parsing, USD confirmation, a read-only preview, and an explicit atomic import into an empty personal budget. Preserve categories, register rows, memos, clearing status, flags and monthly assignments; repeat imports of the identical archive do not duplicate data or overwrite edits.
- Add an API v1 file-import capability that receives file bytes without credentials or ledger access. The host owns local-only upload routes and storage commits; SQLite implements the optional migration method with transactional receipts.
- Keep exported accounts unclassified and outside budget funding because YNAB dumps omit account types and verified balance snapshots. Explain differences in credit-card envelopes, targets and recomputed activity/availability before import. Existing financial workspaces are never merged or replaced by this importer.

## 1.3.4 — 2026-10-14

- Use WHAB throughout maintained source and documentation, including new SQLite filenames, Plaid Link session keys and the Link household identifier.
- Reopen a single existing SQLite ledger for the selected environment in place, preserving budgets, encrypted connections and journal files. Reject ambiguous or unrecognized saved ledgers instead of starting an empty budget or modifying an unrelated database.
- Finish any in-progress Plaid Link session before upgrading; saved bank connections and their encrypted access tokens remain usable.

## 1.3.3 — 2026-10-14

- Organize contributor plugins under singular category folders: `interface`, `ingestion`, `insight`, `storage`, `automation`, `extension` and `example`. Keep each plugin's implementation, assets, documentation and tests together under `plugins/<category>/<id>/`.
- Preserve existing plugin IDs, Python packages, configuration and runtime routes. Update packaging, architecture checks and automatic 100% plugin coverage discovery for the categorized layout, including the independently installable example.

## 1.3.2 — 2026-10-14

- License WHAB's own code, SDK, bundled plugins, documentation and assets under MIT, permitting commercial forks and proprietary extensions.
- Include MIT license metadata and license files in the application and independently packaged example plugin; bump the example to 1.0.2 and verify both installed distributions carry the license.

## 1.3.1 — 2026-10-14

- Group each bundled plugin's implementation, assets, documentation and tests in its own `plugins/<id>/` contributor folder. Keep the public `whab.plugins` SDK and host contracts in core.
- Run plugin-local and central tests together, and include every bundled plugin's source and assets in the application wheel. Keep the example report plugin independently installable under `plugins/example/`.
- Require 100% Python statement and branch coverage for every plugin in CI, including unimported source files and the example. Add mocked failure, retry, rollback and presentation regressions; keep type-only imports outside the runtime coverage scope.

## 1.3.0 — 2026-10-13

- Add an optional Bank logos plugin using Icon Horse's public institution icons, disabled by default and selectable through Settings → Plugin manager.
- Show bank logos in the sidebar and Accounts page, with existing icons or initials when a bank is unknown or its logo is unavailable.
- Add an API v1 branding capability with local institution resolution, host-served bounded raster images, and a bounded success/failure cache. Only curated public bank domains reach the icon source; bank credentials and financial records remain local.

## 1.2.0 — 2026-10-13

- Add Settings beside Local workspace and move About this prototype into it; remove the top-right About control.
- Add a plugin manager for installed optional plugins. Save a validated selection for the next app restart while keeping the active storage and interface required.
- Keep saved plugin choices in an atomic local plugins.json file, with separate running and pending states. Existing environment defaults apply when no saved selection exists.
- Make the yellow-compass-on-red logo square and center its compass.

## 1.1.2 — 2026-10-13

- Adopt a flat yellow eight-point compass on a red background for the bundled Web UI, sidebar branding, and favicon.
- Refresh the Web UI with warm neutral surfaces, red controls and navigation, yellow accents, and distinct financial status colors.
- Version stylesheet and logo URLs and verify the new logo asset in installed-wheel checks.

## 1.1.1 — 2026-10-12

- Raise the minimum Python version to 3.14 for WHAB and its example plugin. Align package metadata, Ruff, setup instructions, CI, and pending branch-protection checks; retain CI coverage for Python 3.14 and 3.15.
- Bump the independently versioned example plugin to 1.0.1 for its runtime support change.

## 1.1.0 — 2026-10-12

- Adopt a `src/whab` layout with separate domain rules, storage adapters, bank integrations, web presentation, and application composition.
- Add Python package plugins through the versioned `whab.plugins` API: selected storage backends, optional bank providers, immutable workspace/month reporting snapshots, Insights reports, and safe CSV exports.
- Make SQLite the bundled default storage plugin selected by `WHAB_STORAGE=sqlite`. Load exactly one required backend alongside optional plugins; disabling banks/reports retains manual budgeting and storage. Core app/web code uses storage protocols, while SQLite owns legacy filenames and additive schema migrations. Backend switches do not automatically migrate existing data.
- Make the visual Web interface a bundled plugin selected by `WHAB_UI=web`, owning presentation, templates and browser assets. `WHAB_UI=` keeps APIs/storage in headless mode. Classify descriptors by user-interface, storage, data-ingestion and reporting capabilities; API v1's UI contract is server-rendered web presentation.
- Make Plaid a bundled plugin, enabled by default; disabling it unloads its service, routes and UI. Normalize bank imports through a provider-scoped ledger boundary while preserving existing Plaid identities and ciphertext.
- Include a separately installable example plugin, contributor onboarding, and architecture/extension documentation.
- Package templates and static assets in wheels, separate runtime configuration/data from installed code, and test wheel/plugin installation in CI.
- Preserve existing HTTP/CSV behavior, legacy SQLite filenames, encrypted connections, monetary rules, and atomic synchronization. Reinstall editable checkouts after upgrading: `python -m pip install -e '.[dev]'`.

## 1.0.2 — 2026-10-12

- Rename the app to WHAB — We Have A Budget, and the private GitHub repository to Fillll/whab.
- Update the Python package, UI, favicon, Plaid display name, exports, documentation, and CI paths.
- Preserve existing databases, encrypted connections, and browser OAuth state. Use only `python -m whab` for launching the renamed app.

## 1.0.1 — 2026-10-12

- Raise the minimum supported Python version to 3.13; older versions are no longer supported. Align packaging, Ruff, and setup instructions, and test Python 3.13, 3.14, and 3.15 in CI.
- Show the release version in About using the same version source as packaging, the CLI, and FastAPI.
- Require a version bump and changelog entry for each material change, including runtime support changes.

## 1.0.0 — 2026-10-12

First versioned release of the local Python and SQLite app.

- Monthly category budgeting with rollover, manual accounts, transaction categorization and review, spending analytics, and CSV export.
- Plaid Transactions integration with encrypted tokens, atomic synchronization, update mode, and signed webhook verification.
- Separate demo/personal workspaces and Sandbox/production databases.
- Preserve the original SQLite ledger and encrypted Sandbox connection.
- Establish a PR workflow and automated pytest/Ruff checks on Python 3.11 and 3.13.

Live institution coverage, investment imports, family authentication, and the full YNAB credit-card payment model remain outside this release.
