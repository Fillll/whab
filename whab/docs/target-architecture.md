# Proposed plugin architecture

This is a design proposal. The [current implementation](architecture.md) and [public SDK v1](plugins.md) remain the implemented contracts. See the [Mermaid overview](../../README.md#architecture) for the proposed capability map.

## Minimal financial core

The domain core owns four concepts: **accounts, transactions, categories and budgets**. It defines their relationships, validated commands and queries, and financial rules such as integer money, category allocation, rollover and budget calculations. Those rules remain authoritative across every interface and plugin.

The core has no dependency on HTTP, a particular interface, database, bank, report, machine-learning model or plugin runtime. Plugins depend on its contracts. Ledger changes always pass through core validation; moving capabilities out of core must not make financial correctness optional.

A small host sits outside the domain core. It loads plugins, resolves required capabilities and declared dependencies, supplies scoped contexts, manages configuration and lifecycle, and assembles the selected application. Transport-specific concerns belong to interface adapters. Shared access policy must apply consistently across those adapters.

## Capability families

These are plugin capability families, not mutually exclusive package types. A plugin may provide several: Plaid can supply ingestion and connection controls; a mortgage extension can supply a model, reports and interface panels.

| Family | Examples | Selection rule |
| --- | --- | --- |
| Storage | SQLite or another backend | At least one storage plugin must be active; multiple storage plugins may be selected |
| Interface | Web, API, TUI, CLI; bank-logo enhancement | Multiple interfaces may coexist; interactive use requires at least one usable interface |
| Ingestion | Plaid, bank statements, manual entry, YNAB import | Zero or more; at least one is needed to enter or import new financial records |
| Insight | Sankey diagrams, reports, exports | Zero or more; read scoped snapshots |
| Extension | Mortgages, loans, investments, stocks | Zero or more; add specialized models and workflows |
| Automation | Rules and smart categorization, including random forest | Zero or more; propose or apply configured actions through core commands |

Requirements name capabilities rather than specific packages. Missing required capabilities or incompatible dependencies must fail at startup with an actionable error. When multiple storage plugins are active, their data ownership, routing and consistency guarantees must be explicit in the storage contract. A background-only profile may run without a user interface.

Contributor folders use the matching singular categories `storage`, `interface`, `ingestion`, `insight`, `extension` and `automation`. The additional `example` category holds teaching plugins and does not define a runtime capability. Each plugin has one primary category even when it provides several capabilities; see [folder ownership and naming](../plugins/README.md).

## Interface

Web, API, TUI and CLI are peers. HTTP transport belongs to the API interface rather than the financial core. A Web interface may call core services locally or depend on an API capability; it does not make the API mandatory for every other interface.

Bank logos and similar presentation enhancements also belong under Interface. They augment another interface and do not satisfy the requirement for a usable interactive entry point by themselves. Optional interface contributions need fallbacks when an enhancement is disabled.

## Client/server deployment proposal

As an alternative to one local instance, two WHAB instances could divide interface and persistence responsibilities. The [README diagram](../../README.md#alternative-proposal-client-and-server) shows the client selecting a remote storage plugin and the server exposing only an API, with no visual interface.

- **Client instance:** runs an interface and the financial core, with a remote storage adapter as its selected storage plugin. The adapter translates ledger reads and changes into authenticated API requests to the server. Interface calculations can provide previews, but the server's validation and committed state remain authoritative.
- **Server instance:** runs the API, financial core and a persistent storage plugin such as SQLite. It owns the canonical ledger, migrations, bank ingestion, synchronization cursors and encrypted provider credentials. Provider access tokens and encryption keys stay on this instance.

The remote contract needs scoped public queries and validated domain commands. It must not expose a database or the full privileged `StorageBackend` protocol over HTTP: internal bank-store operations and token records are not client capabilities. Ingestion remains on the server so normalized records and synchronization cursors can commit atomically under the existing storage guarantees.

Before implementation, define:

- Authentication, household/workspace authorization and encrypted transport, applied to every remote operation. Headless mode alone does not make a network deployment safe.
- Atomic mutations, coherent reads, conflict detection and retry/idempotency rules, so concurrent clients or a timed-out request cannot silently lose or duplicate changes.
- API version negotiation and compatible plugin capabilities, including how server-owned ingestion workflows are invoked and presented by the client.
- Explicit connection failures and recovery. The client must not silently switch to a local ledger; offline writes, caching, replication and automatic data migration are outside this proposal.

The current app already supports `WHAB_UI=` for headless APIs with storage, but it has no remote storage plugin or authenticated network deployment contract. This future topology requires new remote contracts beyond SDK v1. Until those contracts exist, keep the current application bound to localhost.

## Ingestion

Each ingestion plugin translates its source into normalized core records or commands. Plaid owns provider communication and synchronization; statement readers own parsing; manual entry owns input workflows; YNAB import owns source mapping. Interface controls for those workflows are optional contributions from the same package.

The core validates the resulting changes, identities and references before persistence. Imports need atomic batches and checkpoints, replay protection and preservation of user annotations. A full YNAB migration also needs explicit mapping for categories and budget history; it is broader than transaction import alone.

## Insight, Extension and Automation

Insight reads scoped snapshots and returns visualizations, reports or exports without changing the ledger. A Sankey plugin supplies the data and visualization contribution; each interface decides how to display supported outputs.

Extension owns specialized models such as amortization schedules, loans, investment positions and stock holdings. These reference core accounts and transactions without adding mortgage or market-specific rules to the minimal core. Extension plugins need explicit persistence and migration contracts, including atomicity when specialized state and ledger changes must commit together. They must not write into another plugin's private database schema.

Automation is a clearer family name than “Logic,” because financial logic remains in core. Categorization rules and a random-forest model are interchangeable implementations. They consume scoped inputs and produce suggestions, confidence and proposed actions. Accepted suggestions or configured automatic actions use core commands and preserve explicit user edits. Ordering and conflict resolution must be explicit when several automations handle the same transaction.

## Implementation boundary

The current application already has selected SQLite and Web plugins, optional Plaid and YNAB file ingestion, reporting/export hooks and optional bank-logo branding. Its descriptors currently expose `storage`, `user_interface`, `data_ingestion`, `reporting` and `branding` kinds.

The proposed families are a design vocabulary, not new SDK names. API-as-interface, multiple interface runtimes, general statement/manual ingestion contracts, rich visual insights, extension persistence and Automation require further design and implementation. The current API remains host-owned and manual entry remains in the existing ledger routes. SDK v1 provides bank synchronization and bounded file-import contracts; the YNAB adapter currently migrates history and assignments into an empty personal budget, with source metadata and reconciliation limits explained in its preview.

Moving those capabilities must preserve saved ledgers, encrypted connections, isolation and atomic imports. Existing documented interfaces and SDK contracts remain subject to the [compatibility policy](../CONTRIBUTING.md#pull-requests-and-releases); this proposal does not itself change them.
