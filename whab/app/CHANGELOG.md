# Changelog

WHAB uses Semantic Versioning. Released tags are immutable.

## Unreleased

## 2.14.3 — 2026-10-18

PR: [#61](https://github.com/Fillll/whab/pull/61)

- Vary transaction responses by the optional sidebar-fragment header, and require SQLite 1.6.3 and web 1.19.3 in the supported bundle for cheaper register counts and smaller navigation responses.

## 2.14.2 — 2026-10-18

PR: [#60](https://github.com/Fillll/whab/pull/60)

- Require SQLite 1.6.2 and web 1.19.2 in the supported bundle for lower projection CPU/memory costs and lighter initial account-dialog markup.

## 2.14.1 — 2026-10-18

PR: [#59](https://github.com/Fillll/whab/pull/59)

- Index dated activity once per request and reuse validated currency scopes across consecutive budget months, avoiding full-history record copies while preserving all projection and funding rules.
- Read recovery and source files in bounded chunks instead of allocating the maximum container size for small files. Reuse immutable private-field rules during public projection sanitization.
- Support compact transaction-summary responses so the web interface can refresh review counts without rebuilding the register.
- Require SQLite 1.6.1, web 1.19.1 and Plaid 1.1.3 in the supported bundle for the corresponding memory and responsiveness fixes.

## 2.14.0 — 2026-10-18

PR: [#58](https://github.com/Fillll/whab/pull/58)

- Add optional bounded register, account metadata/settings and batch workspace reads, with compatible legacy storage fallbacks and UI opt-ins. Preserve shared revision locking, currency boundaries, full-history search/counts and deleted-history currency protection.
- Validate category metadata once per projection and build public transactions only for the requested month, retaining identical budget calculations and error contracts.
- Reduce strict calendar-date validation and reuse credential-free archives during synchronous portable checkpoints, preserving recovery journals and credential separation.
- Require SQLite 1.6.0 and web 1.19.0 in the supported bundle.

## 2.13.1 — 2026-10-18

PR: [#57](https://github.com/Fillll/whab/pull/57)

- Aggregate category spending, prior assignments and historical account adjustments once per projection instead of repeatedly scanning transaction history. Preserve all budget calculations, currency boundaries and rollover behavior.
- Require web 1.18.0 in the supported bundle to keep large transaction registers bounded and searchable.

## 2.13.0 — 2026-10-18

PR: [#55](https://github.com/Fillll/whab/pull/55)

- Add inclusive transaction date ranges, current/previous-month presets and multiple account selection while preserving legacy register addresses. Expose filter state to UI plugins and retain budget/currency isolation.
- Offer an optional filtered-register storage capability with compatible public-read fallbacks. Require web 1.16.0 and SQLite 1.5.0 in the supported bundle.

## 2.12.1 — 2026-10-18

PR: [#54](https://github.com/Fillll/whab/pull/54)

- Save monthly rollover choices even when they create or worsen a following funding deficit. Preserve signed projections, account balances, saved assignments and supported numeric ranges; retain funding validation for new assignments and next-month set-asides.
- Require web 1.15.2 in the supported bundle for visible shortfall warnings and contained rollover save feedback.

## 2.12.0 — 2026-10-18

PR: [#52](https://github.com/Fillll/whab/pull/52)

- Normalize optional bank website input without blocking account edits, and add a guarded provider-attributed logo preview endpoint that does not change account metadata.
- Offer an optional atomic scoped register projection and UI opt-in that skip envelope calculations while preserving existing plugin contracts. Require the updated web, SQLite and Bank logos packages in the supported bundle.

## 2.11.0 — 2026-10-18

PR: [#51](https://github.com/Fillll/whab/pull/51)

- Add optional storage-owned private raster receipts with guarded local endpoints and portable SQLite images. Preserve memo edits and account name overrides through bank synchronization.
- Add account metadata settings, allow currency changes only before transaction history, and expose explicit bank website attribution through optional branding hooks.
- Use readable budget/account aliases while keeping internal IDs, API scoping, legacy URLs and renamed bookmarks stable. Require the updated web, SQLite, client and Bank logos packages in the supported bundle.

## 2.10.0 — 2026-10-18

PR: [#50](https://github.com/Fillll/whab/pull/50)

- Add read-only file import previews and retry-safe commits that create isolated budgets, leaving the source budget intact. Keep the existing empty-budget import API available.
- Support account-specific transaction views across all dates. Require web 1.13.0 and YNAB 1.2.0 in the supported bundle for the revised settings and import workflow.

## 2.9.2 — 2026-10-18

PR: [#49](https://github.com/Fillll/whab/pull/49)

- Require web 1.12.1 in the supported bundle for responsive in-place budget edits, preserving core financial calculations and validation.

## 2.9.1 — 2026-10-18

PR: [#48](https://github.com/Fillll/whab/pull/48)

- Require web 1.12 in the supported bundle for expandable explanations of every read-only monthly budget total. Preserve core financial calculations.

## 2.9.0 — 2026-10-18

PR: [#47](https://github.com/Fillll/whab/pull/47)

- Calculate Available to assign as received income plus assignable carry-in, less the explicit next-month set-aside and current category assignments. Expose previously unassigned, incoming set-aside and overall category carryover components; keep by-category balances outside the general pool.
- Treat saved next-month funding as an independent transfer rather than a floor for the target month's category assignments. Preserve existing records and remove the requirement to cover those assignments. Do not implicitly reserve later category plans in earlier monthly displays.
- Validate monthly funding and actual eligible cash independently, preserving credit reserves, historical boundaries and currency isolation. Let assignments cover existing category debt without inventing funds for other categories.
- Advertise monthly funding semantics through a public capability and require web 1.11 in the supported bundle.

## 2.8.2 — 2026-10-17

PR: [#45](https://github.com/Fillll/whab/pull/45)

- Require web 1.10 in bundled installations for compact month cards and a five-row overview ending in Available to assign. Preserve core financial projections and funding rules.

## 2.8.1 — 2026-10-17

PR: [#44](https://github.com/Fillll/whab/pull/44)

- Require web 1.9 in bundled installations for the consolidated Carried in overview and Available to spend summary. Keep core rollover, funding and account balance calculations unchanged.

## 2.8.0 — 2026-10-17

PR: [#43](https://github.com/Fillll/whab/pull/43)

- Expose the previous month's incoming rollover mode in each currency's budget projection, including when the source month is outside the displayed window. Keep category balances and cash calculations unchanged.
- Require web 1.8 in bundled installations for the visible incoming category carryover column.

## 2.7.0 — 2026-10-17

PR: [#41](https://github.com/Fillll/whab/pull/41)

- Add currency-scoped monthly summary fields for outstanding category overspending carried into the month and funding for exactly the following month.
- Save next-month funding independently from child category assignments through an optional storage capability and revision-checked public command. Reserve the greater of each future month's saved amount and category allocations once, and release the commitment in its target month. Keep funding limits, debt safeguards and currency isolation in core.
- Let each month and currency carry positive and negative category balances into the same categories or return their net balance to general funds. Preserve total cash and apply each choice to the following month through an optional storage capability and revision-checked command.
- Correct historical To assign amounts so later category plans do not appear as unfunded commitments before their income existed. Validate edits and rollover changes against funding in every affected month.
- Preserve saved funding and rollover modes in neutral portable snapshots while continuing to read older snapshots. Require web 1.7, SQLite 1.2 and remote client 1.2 in the bundled installation.

## 2.6.1 — 2026-10-16

PR: [#40](https://github.com/Fillll/whab/pull/40)

- Standardize every supported and example plugin's Python package and distribution as `whab_plugin_<name>`, and update bundled dependency requirements and plugin management metadata. Saved plugin IDs, settings, routes and portable budgets keep their existing identities.
- Upgrade source installations by installing replacement packages before removing obsolete official distribution registrations, preserving existing plugin selections and preventing duplicate discovery.

## 2.6.0 — 2026-10-16

PR: [#35](https://github.com/Fillll/whab/pull/35)

- Download any known budget without changing the browser's selected budget, including password-protected saved connections after restart.
- Export the demo ledger as its own portable budget, with a separate identity and no personal ledger, plugin state or connection credentials.

## 2.5.0 — 2026-10-16

PR: [#36](https://github.com/Fillll/whab/pull/36)

- Give browser views stable, lowercase addresses: `/budgets/<budget-key>/<page>/<month>/<currency>`. Use `home` for the original budget, each saved budget's permanent ID for named budgets, and `demo` for the single fictional sample budget. Budget names can change without changing their addresses.
- Resolve budget-scoped pages, APIs, assets and downloads from the address independently of the browser's selected-budget cookie, keeping simultaneously open budgets isolated. Preserve existing unscoped API integrations and uppercase ISO currency values in API payloads and storage.
- Support dynamic `current-month` and explicit `yyyy-mm` view periods. The root address opens the initial demo or selected saved budget; old query-style page addresses are no longer supported.

## 2.3.0 — 2026-10-16

PR: [#33](https://github.com/Fillll/whab/pull/33)

- Organize the product under `whab/app`, `whab/launchers` and `whab/docs`, keeping installed code and private runtime homes separate.
- Add a local HTTPS readiness endpoint with application, version, build and managed-instance identity for the macOS launcher. Readiness does not read or expose financial records or banking configuration.

## 2.2.0 — 2026-10-16

PR: [#32](https://github.com/Fillll/whab/pull/32)

- Let visual interface plugins request up to twelve consecutive core-calculated budget periods through the optional `UserInterface.budget_months` setting. Keep existing single-month interfaces and storage contracts compatible.
- Read period projections through the shared ledger service, preserving existing currency isolation, rollover, credit reserves, historical restrictions and assignment validation. Budgeting rules remain in core; presentation remains owned by the selected interface plugin.

## 2.1.0 — 2026-10-16

PR: [#31](https://github.com/Fillll/whab/pull/31)

- Add core on-budget and off-budget accounts. Only on-budget cash and credit accounts fund envelopes and contribute to budget activity, income and spending trends. Track investments, retirement accounts, property, loans and fixed-term deposits separately in currency-specific assets, liabilities and net worth.
- Add validated account types and subtypes, including brokerage, IRA, Roth IRA, 401(k), home, vehicle, mortgage, car loan, student loan and CD. Reclassify existing accounts through the shared ledger service and local API without changing balances or transaction history.
- Preserve account scope through compatible saved-ledger migrations, portable imports/exports and bank refreshes. Expose scope in immutable plugin snapshots and keep account classification changes optional for existing storage plugins.

## 2.0.0 — 2026-10-15

PR: [#30](https://github.com/Fillll/whab/pull/30)

- Require HTTPS for the local interface, APIs, remote commands and provider webhooks. Remove plaintext HTTP launch and OAuth redirect support. Set up device-local certificates with explicit trust instructions, allow configured certificates, and fail instead of falling back to HTTP. Existing HTTP URLs and deployments must migrate before upgrading.
- Package the application core separately from independently versioned plugin distributions. Preserve plugin IDs and the public API v1 contracts; install the default plugin set with the source installer or the bundled extra. Plugin-only changes now update their owning version and dated changelog, independently of application releases.
- Align the operating, architecture, plugin-author and contributor guides with portable storage selection, unavailable optional plugins, container payload ownership, currency minor units and the supported plugin inventory. Keep teaching examples outside ordinary Settings and document developer activation.

## 1.9.0 — 2026-10-15

PR: [#27](https://github.com/Fillll/whab/pull/27)

- Make `.whab` an extensible container with a required manifest and plugin-owned files. Local SQLite budgets carry a sanitized database; remote budgets carry connection information without downloading a ledger snapshot. Continue opening the previous JSON ledger format.
- Let plugins read, write and preserve their own budget files, including files for disabled or unavailable plugins. Retain isolated runtime caches, conflict checks and recovery journals.
- Support explicitly including remote API credentials in a password-encrypted section, with passwords supplied through Settings or a named environment variable. Bind credentials to their remote target and exclude bank-provider tokens from portable files.

## 1.8.0 — 2026-10-15

PR: [#26](https://github.com/Fillll/whab/pull/26)

- Add portable `.whab` budgets with a data-only ledger and plugin manifest. Create, open, download and switch budgets in Settings; restore plugin selections, public settings and saved state independently for each budget. Keep installed plugin code and connection credentials on the owning machine or server.
- Save an opened budget to additional file destinations, report pending copies, and retain a local recovery journal across interrupted saves. Preserve ledger history, currencies, annotations and allocations while excluding banking credentials from portable files.
- Add a shared validated ledger command/query service and a transport interface contract. Bundle optional HTTPS server and remote client plugins with scoped bearer access, revision conflicts and bounded retry protection; keep ingestion and banking tokens on the server.
- Preserve the existing runtime home, SQLite ledgers, encrypted connections and default local launch. Refresh the main README with centered branding, Python and extensibility emphasis, deployment options and user-controlled privacy.

## 1.7.1 — 2026-10-15

PR: [#25](https://github.com/Fillll/whab/pull/25)

- Move budget selection into Settings → Budgets, with the current budget shown in Settings and beside the selected budget. Remove the desktop and mobile budget dropdowns; preserve page, month and currency when switching budgets.
- Group installed plugins under distinct alphabetical category headings and sort their names alphabetically. Hide example plugins from the Settings list while preserving valid existing example selections.
- Use sentence case and acronym spelling for WHAB web and Import from YNAB ZIP file. Show friendly bundled names for disabled plugins without loading their code.

## 1.7.0 — 2026-10-14

PR: [#23](https://github.com/Fillll/whab/pull/23)

- Show Plaid-supplied merchant logos beside transaction payees as part of the existing Plaid plugin. Import only the supplied artwork reference, with no merchant guessing, external lookup service or additional plugin selection. Keep names visible when artwork is absent or fails.
- Add an optional API v1 merchant-artwork capability for bank providers, guarded local image serving and a bounded in-memory cache. Plaid downloads only PNGs from its public logo CDNs, without banking credentials, redirects or transaction data.
- Preserve merchant artwork through atomic synchronization and pending-to-posted transitions with a compatible nullable SQLite column. Existing rows remain without artwork until a subsequent provider import or update supplies it.

## 1.6.0 — 2026-10-14

PR: [#21](https://github.com/Fillll/whab/pull/21)

- Add Settings → Plugins → Installed with an All categories view, category filtering, selection checkboxes and an Options page for every installed plugin. Keep required storage/interface selections locked and apply optional selection changes after restart.
- Add an optional API v1 settings capability with validated plain-text details and declared actions, served through guarded host endpoints. Disabled plugin settings do not execute code.
- Keep successful Bank logos images for 365 days in a bounded local disk cache that survives restarts. Refresh on the first image request after expiry; keep failed requests cached for five minutes. Add a Flush cache button in Bank logos options to clear saved images and transient misses immediately.

## 1.5.0 — 2026-10-14

PR: [#20](https://github.com/Fillll/whab/pull/20)

- Make the two-level category hierarchy a core contract. Parent groups organize child categories; transactions, category targets and envelope assignments accept children only. Existing child IDs, allocations and annotations remain intact.
- Support accounts and child categories in multiple ISO currencies within one budget. Keep cash, debt, rollover, activity, trends and funding separate by currency, without exchange rates or conversion. Transactions inherit account currency and reject mismatched category assignments.
- Use fixed core formats: dates `YYYY-MM-DD`, USD `$1,234.56`, and other currencies with their ISO code and defined minor-unit precision. The selected interface plugin can replace presentation. The bundled Web interface uses these defaults and provides a currency selector.
- Add compatible SQLite currency columns defaulting existing records to USD. Scope reporting snapshots to one currency and add currency identification to CSV exports; preserve the legacy integer amount field names.

## 1.4.0 — 2026-10-14

PR: [#19](https://github.com/Fillll/whab/pull/19)

- Add the optional YNAB export plugin, with bounded ZIP/TSV/CSV parsing, USD confirmation, a read-only preview, and an explicit atomic import into an empty personal budget. Preserve categories, register rows, memos, clearing status, flags and monthly assignments; repeat imports of the identical archive do not duplicate data or overwrite edits.
- Add an API v1 file-import capability that receives file bytes without credentials or ledger access. The host owns local-only upload routes and storage commits; SQLite implements the optional migration method with transactional receipts.
- Keep exported accounts unclassified and outside budget funding because YNAB dumps omit account types and verified balance snapshots. Explain differences in credit-card envelopes, targets and recomputed activity/availability before import. Existing financial workspaces are never merged or replaced by this importer.

## 1.3.4 — 2026-10-14

PR: [#18](https://github.com/Fillll/whab/pull/18)

- Use WHAB throughout maintained source and documentation, including new SQLite filenames, Plaid Link session keys and the Link household identifier.
- Reopen a single existing SQLite ledger for the selected environment in place, preserving budgets, encrypted connections and journal files. Reject ambiguous or unrecognized saved ledgers instead of starting an empty budget or modifying an unrelated database.
- Finish any in-progress Plaid Link session before upgrading; saved bank connections and their encrypted access tokens remain usable.

## 1.3.3 — 2026-10-14

PR: [#17](https://github.com/Fillll/whab/pull/17)

- Organize contributor plugins under singular category folders: `interface`, `ingestion`, `insight`, `storage`, `automation`, `extension` and `example`. Keep each plugin's implementation, assets, documentation and tests together under `plugins/<category>/<id>/`.
- Preserve existing plugin IDs, Python packages, configuration and runtime routes. Update packaging, architecture checks and automatic 100% plugin coverage discovery for the categorized layout, including the independently installable example.

## 1.3.2 — 2026-10-14

PR: [#13](https://github.com/Fillll/whab/pull/13)

- License WHAB's own code, SDK, bundled plugins, documentation and assets under MIT, permitting commercial forks and proprietary extensions.
- Include MIT license metadata and license files in the application and independently packaged example plugin; bump the example to 1.0.2 and verify both installed distributions carry the license.

## 1.3.1 — 2026-10-14

PR: [#12](https://github.com/Fillll/whab/pull/12)

- Group each bundled plugin's implementation, assets, documentation and tests in its own `plugins/<id>/` contributor folder. Keep the public `whab.plugins` SDK and host contracts in core.
- Run plugin-local and central tests together, and include every bundled plugin's source and assets in the application wheel. Keep the example report plugin independently installable under `plugins/example/`.
- Require 100% Python statement and branch coverage for every plugin in CI, including unimported source files and the example. Add mocked failure, retry, rollback and presentation regressions; keep type-only imports outside the runtime coverage scope.

## 1.3.0 — 2026-10-13

PR: [#12](https://github.com/Fillll/whab/pull/12)

- Add an optional Bank logos plugin using Icon Horse's public institution icons, disabled by default and selectable through Settings → Plugin manager.
- Show bank logos in the sidebar and Accounts page, with existing icons or initials when a bank is unknown or its logo is unavailable.
- Add an API v1 branding capability with local institution resolution, host-served bounded raster images, and a bounded success/failure cache. Only curated public bank domains reach the icon source; bank credentials and financial records remain local.

## 1.2.0 — 2026-10-13

PR: [#9](https://github.com/Fillll/whab/pull/9)

- Add Settings beside Local workspace and move About this prototype into it; remove the top-right About control.
- Add a plugin manager for installed optional plugins. Save a validated selection for the next app restart while keeping the active storage and interface required.
- Keep saved plugin choices in an atomic local plugins.json file, with separate running and pending states. Existing environment defaults apply when no saved selection exists.
- Make the yellow-compass-on-red logo square and center its compass.

## 1.1.2 — 2026-10-13

PR: [#8](https://github.com/Fillll/whab/pull/8)

- Adopt a flat yellow eight-point compass on a red background for the bundled Web UI, sidebar branding, and favicon.
- Refresh the Web UI with warm neutral surfaces, red controls and navigation, yellow accents, and distinct financial status colors.
- Version stylesheet and logo URLs and verify the new logo asset in installed-wheel checks.

## 1.1.1 — 2026-10-12

PR: [#7](https://github.com/Fillll/whab/pull/7)

- Raise the minimum Python version to 3.14 for WHAB and its example plugin. Align package metadata, Ruff, setup instructions, CI, and pending branch-protection checks; retain CI coverage for Python 3.14 and 3.15.
- Bump the independently versioned example plugin to 1.0.1 for its runtime support change.

## 1.1.0 — 2026-10-12

PR: [#5](https://github.com/Fillll/whab/pull/5)

- Adopt a `src/whab` layout with separate domain rules, storage adapters, bank integrations, web presentation, and application composition.
- Add Python package plugins through the versioned `whab.plugins` API: selected storage backends, optional bank providers, immutable workspace/month reporting snapshots, Insights reports, and safe CSV exports.
- Make SQLite the bundled default storage plugin selected by `WHAB_STORAGE=sqlite`. Load exactly one required backend alongside optional plugins; disabling banks/reports retains manual budgeting and storage. Core app/web code uses storage protocols, while SQLite owns legacy filenames and additive schema migrations. Backend switches do not automatically migrate existing data.
- Make the visual Web interface a bundled plugin selected by `WHAB_UI=web`, owning presentation, templates and browser assets. `WHAB_UI=` keeps APIs/storage in headless mode. Classify descriptors by user-interface, storage, data-ingestion and reporting capabilities; API v1's UI contract is server-rendered web presentation.
- Make Plaid a bundled plugin, enabled by default; disabling it unloads its service, routes and UI. Normalize bank imports through a provider-scoped ledger boundary while preserving existing Plaid identities and ciphertext.
- Include a separately installable example plugin, contributor onboarding, and architecture/extension documentation.
- Package templates and static assets in wheels, separate runtime configuration/data from installed code, and test wheel/plugin installation in CI.
- Preserve existing HTTP/CSV behavior, legacy SQLite filenames, encrypted connections, monetary rules, and atomic synchronization. Reinstall editable checkouts after upgrading: `python -m pip install -e '.[dev]'`.

## 1.0.2 — 2026-10-12

PR: [#4](https://github.com/Fillll/whab/pull/4)

- Rename the app to WHAB — We Have A Budget, and the private GitHub repository to Fillll/whab.
- Update the Python package, UI, favicon, Plaid display name, exports, documentation, and CI paths.
- Preserve existing databases, encrypted connections, and browser OAuth state. Use only `python -m whab` for launching the renamed app.

## 1.0.1 — 2026-10-12

PRs: [#2](https://github.com/Fillll/whab/pull/2), [#3](https://github.com/Fillll/whab/pull/3)

- Raise the minimum supported Python version to 3.13; older versions are no longer supported. Align packaging, Ruff, and setup instructions, and test Python 3.13, 3.14, and 3.15 in CI. ([#2](https://github.com/Fillll/whab/pull/2))
- Show the release version in About using the same version source as packaging, the CLI, and FastAPI. ([#3](https://github.com/Fillll/whab/pull/3))
- Require a version bump and changelog entry for each material change, including runtime support changes. ([#3](https://github.com/Fillll/whab/pull/3))

## 1.0.0 — 2026-10-12

PR: [#1](https://github.com/Fillll/whab/pull/1)

First versioned release of the local Python and SQLite app.

- Monthly category budgeting with rollover, manual accounts, transaction categorization and review, spending analytics, and CSV export.
- Plaid Transactions integration with encrypted tokens, atomic synchronization, update mode, and signed webhook verification.
- Separate demo/personal workspaces and Sandbox/production databases.
- Preserve the original SQLite ledger and encrypted Sandbox connection.
- Establish a PR workflow and automated pytest/Ruff checks on Python 3.11 and 3.13.

Live institution coverage, investment imports, family authentication, and the full YNAB credit-card payment model remain outside this release.
