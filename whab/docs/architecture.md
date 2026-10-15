# WHAB architecture

**Current implementation.** WHAB is a local Python application with plugins for visual presentation, persistence, bank providers and reporting. Web and SQLite are its selected default UI/storage plugins; Plaid is an optional bank plugin enabled by default. The core app assembles descriptors through SDK protocols. Headless mode keeps APIs and storage without rendering pages. There is no arbitrary report/bank ledger-write or one-time migration plugin interface. The [target architecture](target-architecture.md) proposes a smaller financial core and broader plugin capabilities; its [Mermaid diagram](../../README.md#architecture) describes that proposal, rather than the runtime documented here.

## Package map

| Package | Owns | Depends on |
| --- | --- | --- |
| `whab.domain` | Integer money, calendar validation, normalized import records, public transaction projections and envelope calculations | Python standard library and other domain modules |
| `whab.storage.bank` | Generic provider identities and scoped Item/import operations | Domain records and public storage protocols |
| `whab_sqlite` | Bundled storage descriptor, SQLite upgrades, fictional seeding, coherent reads and transactional mutations | Domain rules; plugin SDK for its descriptor/context |
| `whab_plaid` | Bundled plugin descriptor, provider mapping, token encryption, Link, synchronization, webhooks and provider UI | Domain records and public plugin contracts |
| `whab_web` | Bundled visual descriptor, view models, icons, templates, styles and browser controls | Domain presentation inputs and public plugin SDK |
| `whab.web` | Host ledger/page/plugin/bank routes, schemas, middleware, safe errors, CSV and scoped projections | Domain records, generic bank facade and public plugin SDK |
| `whab.plugins` | Public API v1, kinds, selectors, UI/storage/bank protocols and reporting hooks | Python standard library and domain records/errors |
| `whab.app` | Component construction, dependency injection, router assembly, assets and lifespan cleanup | Domain rules, host transport and public plugin contracts |
| `whab.config` / `whab.__main__` | Runtime configuration and local launch command | Environment/configuration and application composition |

Core packages live under `src/whab`. Each concrete plugin lives in `plugins/<category>/<id>/src/<package>/`, with its own `tests/` and `README.md` alongside. Web assets are package data under `whab_web`, bank-specific resources under `whab_plaid`, and local import controls under `whab_ynab`; all bundled plugins travel in the application wheel. The example has its own separately installable distribution. The public `whab.plugins` package remains the SDK and host runtime, distinct from the contributor folders. File parsers receive bytes only; guarded host routes validate results and delegate atomic migrations to the optional storage import capability. See [CONTRIBUTING.md](../CONTRIBUTING.md) for development and test ownership.

`domain.budget.calculate_workspace` projects a plain ledger state into a monthly view without querying a database or provider. `ensure_assignment_allowed` takes an explicit as-of month. Domain modules must stay independent of web, storage, configuration, providers and plugin runtimes.

`domain.imports` defines the provider-neutral account, transaction and change records accepted by the ledger. Bank adapters normalize payloads; storage backends validate normalized records and merge current annotations inside their transaction. Domain rules import no plugin or adapter runtimes. App, host web, the generic bank facade and plugin runtime import no concrete storage, bank or visual implementation. Generic bank routes call `BankService`, ledger operations use `StorageBackend`, and page transport calls the selected UI's context builder.

## Composition and runtime files

`app.create_app` accepts injected settings, backend, plugin registry and bank services, or constructs the selected defaults. `WHAB_STORAGE` selects exactly one required storage factory, defaulting to `sqlite`; the host loads its descriptor in addition to optional `WHAB_PLUGINS`. There is no silent fallback when a chosen backend is missing or fails. The storage factory receives `StorageContext(home, environment, config)` and returns a `StorageBackend`. Bank factories receive provider-scoped facades over that backend. Shutdown and startup-failure cleanup close created bank services before the owned backend; tests retain ownership of injected stores.

`WHAB_UI` selects one server-rendered visual descriptor, defaulting to `web`. `WHAB_UI=` selects headless mode: the root page and core static mount are absent, while guarded APIs remain. `UserInterface` supplies template/static directories and a `build_context` callable. The host owns Jinja transport and injects enabled plugin panels/outputs; the chosen interface owns the resulting visual layout. API v1 does not create native applications or arbitrary renderer/router factories.

Configuration reads `.env` from `WHAB_HOME`, or from the process's launch directory when no home is set. Environment values override `.env` values. `WHAB_ENV` selects Sandbox or production, with the legacy `PLAID_ENV` setting as its fallback. SQLite files follow that environment:

```text
<runtime home>/
  .env
  data/
    whab-sandbox.sqlite
    whab-production.sqlite
```

The SQLite factory owns ledger selection and additive schema migrations. It reopens a single `data/*-{environment}.sqlite` file in place, validating the base schema for a noncanonical filename. It creates the canonical filename shown above only when no matching file exists. Multiple matches fail safely, even if one has the canonical filename. This preserves saved budgets and encrypted connections. Core configuration supplies a home/environment rather than choosing a concrete database file. Other storage plugins own their connection settings and migrations. Selecting a different backend does not automatically transfer the previous ledger. Runtime paths do not derive from the checkout or `site-packages`. Keep the existing home and encryption key when upgrading SQLite. The package and launch command are only `whab`.

`src/whab/__init__.py::__version__` is the single version source for distribution metadata, CLI, FastAPI and About. `scripts/check_distribution.py` tests a wheel and example plugin from an unrelated temporary directory; source-checkout tests alone do not establish that the distribution includes its assets.

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

Webhook verification checks the ES256 signature, the exact request-body digest and timestamp freshness before queueing a sync. The queue coalesces repeated notifications and schedules another pass for a notification received during an active sync. These controls preserve correctness for the local prototype; they do not make a local-only HTTP listener a hosted family service.

Sandbox is the default. Production requires explicit live enablement as well as valid credentials and provider access. A successful Sandbox test or Link flow is not proof of production institution coverage. HTTP errors and status responses use authored messages that omit provider secrets and private remote error details.

## Web boundary

Host transport validates requests and enforces local access. The selected visual plugin owns Python presentation, templates and small browser interactions, while domain rules own budgeting decisions. The bundled Web plugin's `static/client.js` supplies shared HTTP/toast helpers. Plaid's controls, template and disconnect dialog belong to its integration assets. FastAPI routers validate schemas before calling protocols. Middleware restricts Host/Origin, requires JSON for mutations and caps streamed bodies. HTML/API responses are non-cacheable; Jinja escapes user and reporting-plugin text. CSV serialization protects headings and text cells from spreadsheet formulas.

The application binds to loopback and has no multiuser authentication or authorization. Household sharing over a network needs an explicit authentication, authorization and deployment design before removing local access restrictions.

## Plugin boundary

The reporting side of the public [`whab.plugins` API v1](plugins.md) provides frozen, typed snapshots scoped to one workspace and one month. `web.plugin_snapshot` constructs them from an explicit public-field allowlist. They include integer-cent balances, category figures and selected-month transactions; they omit Item records, tokens, settings, notes and arbitrary history. Each insight or export hook receives fresh immutable records.

Installed packages register descriptors in `whab.plugins`. Capabilities determine kinds: `user_interface`, `storage`, `data_ingestion`, `reporting` and `branding`; one descriptor may advertise more than one capability. The selected storage and UI descriptors load alongside optional names. `WHAB_STORAGE` defaults to `sqlite`, `WHAB_UI` to `web`, and absent `WHAB_PLUGINS` to `plaid`. A blank optional list retains selected UI/storage; a blank UI setting runs headlessly. Missing, duplicate or incompatible selections cause an actionable startup error. Insights are bounded text, exports are validated tables, and ordinary reporting failures use sanitized messages.

Storage factories receive the resolved runtime home, environment and backend-prefixed configuration. They own all ledger persistence, schema upgrades and transactional behavior; reporting hooks never receive that context or backend. The public `StoreError` and `UNSET` sentinel keep error handling and omitted-field semantics consistent across implementations. A backend switch is a configuration change, not a migration service. Authors must implement the full storage protocol and test rollback, coherent reads, compatible reopen and resource cleanup.

A bank descriptor provides a `BankService` factory and optional `BankUI` directories. Its separate `BankContext` contains configuration, the encryption key, environment and a provider-scoped store. Those are privileged capabilities, never reporting-hook inputs. The store scopes Item operations and normalized synchronization batches to that provider, reserving existing Plaid identifiers for saved-data compatibility. A provider owns connection tokens, exchange, synchronization, disconnection, signed-webhook verification and cleanup. The host exposes `/api/banks/{id}` endpoints, validates protocol results, and preserves `/api/plaid` aliases while Plaid is enabled. Disabling a provider removes its routes and UI without deleting its saved ledger history.

Optional branding uses a `BankLogos` descriptor. Local resolution receives institution text only and produces a public logo key; the host supplies an `account_logos` map to the selected interface. A separate guarded image request invokes fetching, revalidates bounded raster bytes, and revalidates the returned bytes. Providers with the optional cache setup hook receive only their plugin-owned cache directory; they own retention and fetch coalescing. Other providers retain the bounded in-memory registry cache. The bundled `bank-logos` plugin is disabled by default, maps curated bank names to fixed public domains, and fetches from Icon Horse without bank credentials. Unknown institutions and network/content failures retain UI fallbacks. Its bounded disk cache retains successful images for 365 days across restarts, refreshes lazily after expiry, and exposes a flush action through the generic plugin settings capability. Settings hooks receive no ledger, banking credentials or global configuration. It does not mutate the ledger or change bank ingestion. See the SDK guide for the full capability contract.

Python plugins run with the server's privileges and are trusted code, not sandboxed code. Reports receive immutable snapshots, bank factories receive scoped persistence, storage plugins own the full ledger, and visual templates/scripts are trusted presentation code. None of these interfaces prevents malicious Python from independently accessing files or the network. API v1 has no arbitrary report/bank mutation hooks, custom routes, authentication or general background-job interface.

## Extending the application

Change domain rules with direct domain regressions, storage behavior with isolated SQLite fixtures, provider flows with mocked HTTP, and public behavior with web tests. Keep plugin compatibility tests against the exported SDK rather than private internals. Preserve installed-wheel checks whenever packaging, assets or entry points change.

Adding a bank provider requires normalization into the existing import contract and careful handling of source identity, money conversion, credentials, deduplication and atomic checkpoints. A one-time migration additionally needs a design for account matching, split transactions, transfers and budget history; it is not an insights/export hook. See the [README](../README.md#limits-and-data-safety) for current limitations and [contribution guide](../CONTRIBUTING.md#pull-requests-and-releases) for the release compatibility policy.
