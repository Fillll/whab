# Changelog

WHAB uses Semantic Versioning. Released tags are immutable.

## Unreleased

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
