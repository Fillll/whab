# WHAB architecture

**Current implementation.** WHAB is a local Python application with plugins for visual presentation, persistence, bank providers and reporting. Web and SQLite are its selected default UI/storage plugins; Plaid is an optional bank plugin enabled by default. The core app assembles descriptors through SDK protocols. Headless mode keeps APIs and storage without rendering pages. Reports use scoped read-only snapshots; bank imports use their normalized atomic ingestion contract. Portable containers carry a plugin manifest and plugin-owned payloads, while trusted interface adapters expose validated ledger operations. Local storage can carry its database; remote connection containers leave financial data on the server. The [budget and plugin model](target-architecture.md) describes feature ownership and capability families; the [Mermaid diagram](../../README.md#architecture) shows those boundaries around the financial core.

## Package map

| Package | Owns | Depends on |
| --- | --- | --- |
| `whab.domain` | Integer money, calendar validation, normalized import records, public transaction projections and envelope calculations | Python standard library and other domain modules |
| `whab.storage.bank` | Generic provider identities and scoped Item/import operations | Domain records and public storage protocols |
| `whab_sqlite` | Bundled storage descriptor, SQLite upgrades, fictional seeding, coherent reads and transactional mutations | Domain rules; plugin SDK for its descriptor/context |
| `whab_plaid` | Bundled plugin descriptor, provider mapping, token encryption, Link, synchronization, webhooks and provider UI | Domain records and public plugin contracts |
| `whab_web` | Default visual descriptor, view models, icons, templates, styles and browser controls | Domain presentation inputs and public plugin SDK |
| `whab_ynab` | Local YNAB archive parsing, import preview and controls | Domain import records and public plugin SDK |
| `whab_bank_logos` | Curated institution lookup, public icons and cache | Public branding/settings contracts |
| `whab_server` | HTTPS bearer-authorized API interface for public ledger operations | Public interface/service contracts |
| `whab_client` | Remote storage adapter and portable connection payload | Public storage/remote protocol contracts |
| `whab_example_plugin` | Separately installed developer teaching report/export | Public immutable report snapshots |
| `whab.web` | Host ledger/page/plugin/bank routes, schemas, middleware, safe errors, CSV and scoped projections | Domain records, generic bank facade and public plugin SDK |
| `whab.plugins` | Public API v1, kinds, selectors, UI/storage/bank protocols and reporting hooks | Python standard library and domain records/errors |
| `whab.services` | Budget-scoped validated ledger commands, coherent queries, revisions and retry receipts | Domain records and storage protocols |
| `whab.budgets` | Data-only bundles, budget manifests, saved copies and active-budget lifecycle | Domain portable records and public plugin contracts |
| `whab.app` | Component construction, dependency injection, router assembly, assets and lifespan cleanup | Domain rules, host transport and public plugin contracts |
| `whab.config` / `whab.__main__` | Runtime configuration and local launch command | Environment/configuration and application composition |

Core packages live under `src/whab` and ship only in the host/core `whab` wheel. Each concrete plugin lives in `plugins/<category>/<id>/src/<package>/`, with its own `pyproject.toml`, `CHANGELOG.md`, `tests/` and `README.md` alongside. Web assets are package data in the independently distributed `whab_web` plugin, bank-specific resources in `whab_plaid`, and local import controls in `whab_ynab`. The host's `bundled` extra and `scripts/install.py` install the seven supported plugin distributions; they do not embed them in the host wheel. The teaching example is separately installed and hidden from ordinary Settings. There are no concrete Automation, Extension or production Insight plugins yet. The public `whab.plugins` package remains the SDK and host runtime, distinct from the contributor folders. File parsers receive bytes only; guarded host routes validate results and delegate atomic migrations to the optional storage import capability. See [CONTRIBUTING.md](../CONTRIBUTING.md) for development and test ownership.

`domain.budget.calculate_workspace` projects a plain ledger state into a monthly view without querying a database or provider. `ensure_assignment_allowed` takes an explicit as-of month. Domain modules must stay independent of web, storage, configuration, providers and plugin runtimes.

`domain.commands` applies manual account, transaction, review and allocation rules to plain ledger state. Storage supplies identifiers and explicit time context, then commits the validated changes atomically; the financial behavior stays in the shared core.

`domain.imports` defines the provider-neutral account, transaction and change records accepted by the ledger. Bank adapters normalize payloads; storage backends validate normalized records and merge current annotations inside their transaction. Domain rules import no plugin or adapter runtimes. App, host web, the generic bank facade and plugin runtime import no concrete storage, bank or visual implementation. Generic bank routes call `BankService`, public ledger operations pass through `LedgerService`, and page transport calls the selected UI's context builder. `LedgerService` validates the command vocabulary and serializes mutations over the selected `StorageBackend`; backends retain atomic persistence, referential validation and ingestion guarantees.

## Composition and runtime files

`app.create_app` accepts injected settings, backend, plugin registry and bank services, or constructs the selected defaults. For the original workspace and initial CLI budget creation, `WHAB_STORAGE` selects the required primary storage factory, defaulting to `sqlite`. An opened portable budget takes storage and optional selections from its manifest; Settings creates new SQLite budgets. The host loads required storage alongside the budget's available optional plugins. There is no silent fallback when a chosen backend is missing or fails. The storage factory receives `StorageContext(home, environment, config)` and returns a `StorageBackend`. Bank factories receive provider-scoped facades over that backend. Shutdown and startup-failure cleanup close created bank services before the owned backend; tests retain ownership of injected stores.

`WHAB_UI` selects one server-rendered visual descriptor, defaulting to `web`. `WHAB_UI=` selects headless mode: the root page and core static mount are absent, while guarded APIs remain. `UserInterface` supplies template/static directories and a `build_context` callable. The host owns Jinja transport and injects enabled plugin panels/outputs; the chosen interface owns the resulting visual layout. Trusted `Interface` factories can contribute transport routes using a scoped `InterfaceContext`; shared host access policy and cleanup still apply. The bundled `server` plugin uses this capability for the public remote command protocol. Server-rendered `UserInterface` remains independently selectable, and `WHAB_UI=` omits visual pages.

Configuration reads `.env` from `WHAB_HOME`, or from the process's launch directory when no home is set. Environment values override `.env` values. `WHAB_ENV` selects Sandbox or production, with the legacy `PLAID_ENV` setting as its fallback. SQLite files follow that environment:

```text
<runtime home>/
  .env
  plugins.json
  tls/
    ca.crt
    ca.key
    server.crt
    server.key
  data/
    whab-sandbox.sqlite
    whab-production.sqlite
  budgets/
    index-<environment>.json
    <budget-id>/<environment>/
      manifest.json
      plugin-data.whab
      source.json
      data/whab-<environment>.sqlite
```

The SQLite factory owns ledger selection and additive schema migrations. It reopens a single `data/*-{environment}.sqlite` file in place, validating the base schema for a noncanonical filename. It creates the canonical filename shown above only when no matching file exists. Multiple matches fail safely, even if one has the canonical filename. This preserves saved budgets and encrypted connections. Core configuration supplies a home/environment rather than choosing a concrete database file. Other storage plugins own their connection settings and migrations. Selecting a different backend does not automatically transfer the previous ledger. Runtime paths do not derive from the checkout or `site-packages`. Keep the existing home and encryption key when upgrading SQLite. The package and launch command are only `whab`.

`src/whab/__init__.py::__version__` is the host/core version source for its distribution metadata, CLI, FastAPI and About. Each concrete plugin's `pyproject.toml::project.version` and local dated `CHANGELOG.md` own its independent version history. A plugin-only material change updates that plugin alone; host/core/SDK changes update the app, and spanning changes update each affected owner. `scripts/check_distribution.py` tests the host, all seven supported plugin wheels and teaching example from an unrelated temporary directory; source-checkout tests alone do not establish that the distribution includes its assets.

The launcher uses native TLS even for localhost. `WHAB_TLS_CERT_FILE` and `WHAB_TLS_KEY_FILE` are paired overrides, with relative paths resolved against the runtime home; defaults are `tls/server.crt` and `tls/server.key`. Setup provisions a private local CA and localhost certificate but never changes OS trust. Missing, invalid, expired or mismatched material fails launch. All incoming requests require HTTPS, and proxy headers are not trusted to bypass that requirement. See [local HTTPS and manual trust](usage.md#local-https).

## Portable budgets and copies

`whab.budgets` owns a versioned `.whab` container with a required `manifest.json`. Version 2 stores plugin-owned payloads under `plugins/<id>/`, preserving files belonging to disabled or unavailable plugins. Local SQLite storage contributes a database image; the remote client contributes connection JSON and does not automatically fetch the remote ledger. A mandatory `ledger.json` is not part of version 2. Version 1 manifest/ledger JSON bundles remain readable, using `domain.portable` to validate their exact records, references, dates, money and currencies before restoration.

Containers carry data rather than executable plugin packages. Default exports omit credentials. Explicit opt-in can carry a password-encrypted remote access credential bound to its public target; bank-provider tokens and encryption keys remain local/server-only. Machine credentials are used only for an approved matching remote target. Container loading validates paths, sizes, manifest compatibility and plugin payloads before activation.

An opened budget has an isolated runtime and plugin registry. The browser's HttpOnly, SameSite-strict budget cookie selects that runtime; the original local runtime remains accessible. Each budget retains separate demo and personal workspaces. The installation supplies executable packages and its visual entry point, while the selected budget supplies its optional feature configuration. Missing optional packages in a portable budget are reported without blocking opening, and their manifest entries and data remain preserved. Required storage and selected UI must be installed and compatible; missing original-workspace selections, duplicate registrations and invalid descriptors fail explicitly. Packages are never installed automatically.

Additional storage destinations save copies of the same portable budget. A failed destination is marked pending without undoing successful copies. Paths and credentials are local configuration rather than portable financial data. Ordinary SQLite runtime files remain supported without requiring existing users to convert their budgets first.

## Ledger and money contracts

Every backend must keep `demo` and `personal` workspaces separate and preserve environment isolation. Demo data is fictional; personal data begins with categories and no accounts. The bundled SQLite schema initialization applies additive compatibility changes and seeds only missing initial state, preserving saved annotations, cursors and the original budget start month. Storage plugins must provide those behaviors through the public protocol, without requiring core web code to know their schema or database library.

Money is integer currency minor units, with checked-in ISO currency precision in `domain.money`; existing ledgers default to USD cents. Accounts and child categories declare currency, transactions inherit their account currency, and cross-currency category references are rejected. Core computes cash, debt, envelopes, summaries and trends separately by currency without conversion. `calculate_workspace` exposes `summariesByCurrency` and `trendsByCurrency`; the legacy `summary`/`trend` describe USD. `select_currency` creates a homogeneous view for interfaces and reports. Fixed defaults are `YYYY-MM-DD` dates, `$1,234.56` USD and code-prefixed amounts for other currencies. Presentation plugins may replace formatting without changing stored values or core validation.

`domain.categories` owns exactly two levels: deterministic parent group identities and assignable child categories. Stored child IDs and legacy `group` labels remain compatible; public children add `parentId` and `currency`, and `categoryGroups` exposes parent nodes with child leaves. Parents cannot receive transactions, targets or allocations. Parent identities derive from group names; the current API has no parent rename operation.

Transactions use positive outflows and negative inflows. Pending and transfer transactions do not contribute to cleared spending or envelope activity. Categorized inflows reduce category spending as refunds; uncategorized depository inflows contribute to income analytics.

An envelope's available amount is prior assignments minus prior cleared category activity, plus the selected month's assignment minus that month's activity. History before `budgetStartMonth` remains visible in analytics but does not consume a newly started budget. Assignment reductions remain possible when an account balance falls and creates a deficit.

Ready to assign uses depository balance snapshots, subtracts positive credit-card debt and positive envelope availability, and excludes stale cash. Unknown credit balances cannot create positive funding. Imported income is never added again to cash snapshots. This conservatively reserves card debt; it is not a full YNAB-style credit-card payment category model. Historical cash estimates reverse later cleared account flows and are approximate, rather than audited month-end reconciliations.

Manual transactions update manual-account balances; entries into connected Plaid accounts are rejected to avoid unsupported reconciliation and duplicates. Ordinary public transaction views omit internal mutation metadata. Raw store state and Item records are internal and may contain encrypted credentials; do not pass them directly to browser responses or plugins.

## Plaid synchronization and secrets

The browser obtains a short-lived Link token from the server and returns a public token after linking. The server exchanges that token, encrypts the access token and stores the Item before synchronizing. Reconnecting an existing Item uses Link update mode. Disconnecting removes its token and marks its account snapshots stale while retaining ledger history.

Access tokens use AES-GCM with the Item ID as authenticated associated data. The encoded nonce/tag/ciphertext layout remains compatible with synthetic fixtures from the original implementation. Renaming UI or package identifiers must preserve this token contract and the encryption key. New Plaid Link tokens use `whab-local-household`; existing connected Items remain unchanged. Browser OAuth continuation keys use `whab`, so users must finish in-progress Link flows before upgrading.

Synchronization starts from the stored cursor, accumulates every page and refreshes account snapshots before committing the batch. A pagination mutation restarts accumulation from the original cursor. SQLite applies accounts, added/modified/removed transactions and the new cursor in one transaction, checking the expected cursor and reading current user annotations inside that transaction. Validation or SQL failures leave the ledger and cursor unchanged. Matched pending-to-posted records preserve category edits, review state and notes; replay remains idempotent.

Webhook verification checks the ES256 signature, the exact request-body digest and timestamp freshness before queueing a sync. The queue coalesces repeated notifications and schedules another pass for a notification received during an active sync. Registered webhook paths bypass local Host/origin checks so the provider can reach them; they still require HTTPS and successful provider signature verification. TLS encrypts transport, while signature verification authenticates the provider. These controls do not add a family login system.

Sandbox is the default. Production requires explicit live enablement as well as valid credentials and provider access. A successful Sandbox test or Link flow is not proof of production institution coverage. HTTP errors and status responses use authored messages that omit provider secrets and private remote error details.

## Web boundary

Host transport validates requests and enforces local access. The selected visual plugin owns Python presentation, templates and small browser interactions, while domain rules own budgeting decisions. The bundled Web plugin's `static/client.js` supplies shared HTTP/toast helpers. Plaid's controls, template and disconnect dialog belong to its integration assets. FastAPI routers validate schemas before calling protocols. Middleware restricts Host/Origin, requires JSON for mutations and caps streamed bodies. HTML/API responses are non-cacheable; Jinja escapes user and reporting-plugin text. CSV serialization protects headings and text cells from spreadsheet formulas.

The default application binds to loopback with native TLS and applies local Host/origin checks to ordinary routes. Only explicitly enabled `/remote/v1` protocol routes and registered bank webhooks bypass those local checks. Remote protocol routes require bearer authorization for available budgets; webhooks require provider signature verification. Every route still requires HTTPS, with no plaintext fallback or redirect and no trusted forwarded-header bypass. TLS encryption is separate from those authentication checks and supplies no family login system. Keep visual/private banking routes loopback-only and provider credentials on the server.

## Plugin boundary

The reporting side of the public [`whab.plugins` API v1](plugins.md) provides frozen, typed snapshots scoped to one workspace and one month. `web.plugin_snapshot` constructs them from an explicit public-field allowlist. They include balances in integer currency minor units (legacy field names retain `_cents`), category figures and selected-month transactions; they omit Item records, tokens, settings, notes and arbitrary history. Each insight or export hook receives fresh immutable records.

Installed packages register descriptors in `whab.plugins`. Capabilities determine kinds: `user_interface`, `storage`, `data_ingestion`, `reporting` and `branding`; one descriptor may advertise more than one capability. The selected storage and UI descriptors load alongside optional names. `WHAB_STORAGE` defaults to `sqlite`, `WHAB_UI` to `web`, and absent `WHAB_PLUGINS` to `plaid`. A blank optional list retains selected UI/storage; a blank UI setting runs headlessly. Missing required storage/UI, missing original-workspace selections, duplicate registrations or incompatible descriptors cause an actionable startup error. An opened portable budget reports unavailable optional packages and preserves their data instead of refusing to open. Insights are bounded text, exports are validated tables, and ordinary reporting failures use sanitized messages.

Storage factories receive the resolved runtime home, environment and backend-prefixed configuration. They own all ledger persistence, schema upgrades and transactional behavior; reporting hooks never receive that context or backend. The public `StoreError` and `UNSET` sentinel keep error handling and omitted-field semantics consistent across implementations. A backend switch is a configuration change, not a migration service. Authors must implement the full storage protocol and test rollback, coherent reads, compatible reopen and resource cleanup.

A bank descriptor provides a `BankService` factory and optional `BankUI` directories. Its separate `BankContext` contains configuration, the encryption key, environment and a provider-scoped store. Those are privileged capabilities, never reporting-hook inputs. The store scopes Item operations and normalized synchronization batches to that provider, reserving existing Plaid identifiers for saved-data compatibility. A provider owns connection tokens, exchange, synchronization, disconnection, signed-webhook verification and cleanup. The host exposes `/api/banks/{id}` endpoints, validates protocol results, and preserves `/api/plaid` aliases while Plaid is enabled. Disabling a provider removes its routes and UI without deleting its saved ledger history.

Optional branding uses a `BankLogos` descriptor. Local resolution receives institution text only and produces a public logo key; the host supplies an `account_logos` map to the selected interface. A separate guarded image request invokes fetching, revalidates bounded raster bytes, and revalidates the returned bytes. Providers with the optional cache setup hook receive only their plugin-owned cache directory; they own retention and fetch coalescing. Other providers retain the bounded in-memory registry cache. The bundled `bank-logos` plugin is disabled by default, maps curated bank names to fixed public domains, and fetches from Icon Horse without bank credentials. Unknown institutions and network/content failures retain UI fallbacks. Its bounded disk cache retains successful images for 365 days across restarts, refreshes lazily after expiry, and exposes a flush action through the generic plugin settings capability. Settings hooks receive no ledger, banking credentials or global configuration. It does not mutate the ledger or change bank ingestion. See the SDK guide for the full capability contract.

Python plugins run with the server's privileges and are trusted code, not sandboxed code. Reports receive immutable snapshots, bank factories receive scoped persistence, storage plugins own the full ledger, and visual templates/scripts are trusted presentation code. None of these interfaces prevents malicious Python from independently accessing files or the network. API v1 has no arbitrary report/bank mutation hooks or general background-job interface. Trusted transport plugins use `InterfaceContext` with a public ledger service rather than a privileged bank store. The remote server interface owns its bearer authorization and versioned routes; it cannot read bank tokens through that context.

## Extending the application

Change domain rules with direct domain regressions, storage behavior with isolated SQLite fixtures, provider flows with mocked HTTP, and public behavior with web tests. Keep plugin compatibility tests against the exported SDK rather than private internals. Preserve installed-wheel checks whenever packaging, assets or entry points change.

Adding a bank provider requires normalization into the existing import contract and careful handling of source identity, money conversion, credentials, deduplication and atomic checkpoints. A one-time migration additionally needs a design for account matching, split transactions, transfers and budget history; it is not an insights/export hook. See the [README](../README.md#limits-and-data-safety) for current limitations and [contribution guide](../CONTRIBUTING.md#pull-requests-and-releases) for the release compatibility policy.
