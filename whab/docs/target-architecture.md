# Budget and plugin model

WHAB combines a plugin host with a hexagonal financial core. The [architecture guide](architecture.md) describes runtime composition and the [SDK guide](plugins.md) defines executable contracts. The [README diagram](../../README.md#architecture) groups plugins by their responsibilities.

## Minimal financial core

The domain core owns four concepts: **accounts, transactions, categories and budgets**. It defines their relationships and financial rules, including integer money, compatible currencies, allocations, rollover and budget calculations. Financial correctness applies regardless of the interface or persistence implementation.

The core depends on no HTTP framework, interface, database, bank provider or plugin runtime. Plugins connect through public contracts. A small host loads selected capabilities, supplies their contexts, manages configuration and lifecycle, and assembles the application outside the domain core.

## Installation and budget ownership

Plugin code is installed once in the WHAB environment. The host/core and [supported plugin distributions](../app/plugins/README.md#supported-plugins) have independent versions and changelogs; the host's `bundled` extra and source installer install that set together. The teaching example is separate and hidden from ordinary Settings. Random forest categorization ships as an optional Automation plugin; no concrete Extension or production Insight plugin ships yet. A budget records which plugins it uses, along with compatible API requirements, settings and plugin-owned saved state. Opening a budget restores that selection; disabling a plugin preserves its saved state so it can be enabled again. The [budget-isolation invariant and verification recipe](architecture.md#budget-isolation-verification) apply to all of those capabilities.

| Owner | Responsibilities |
| --- | --- |
| WHAB installation | Installed executable plugin packages and available capabilities |
| Budget | Identity, selected features, settings and plugin-owned payloads |
| Machine or server | Credentials by default, encryption keys, approved remote targets and device paths |

A portable `.whab` container always includes `manifest.json`. Version 2 lets each plugin own files under `plugins/<id>/`, such as `plugins/sqlite/budget.sqlite` or `plugins/client/connection.json`. The host preserves unknown and disabled plugins’ files without interpreting them. There is no mandatory financial snapshot: local storage carries its data, while a remote connection container can leave all financial records on the server. Version 1 bundles containing a manifest and ledger JSON remain readable.

Plugin code stays installed in WHAB. Containers carry data, never executable plugin packages. Credentials remain device-local by default. An explicit password-protected export can include the remote access credential, while bank-provider tokens and token encryption keys stay on the machine/server. Public connection information identifies the remote URL and budget; using a machine credential requires an approved match to that target. Opening validates container paths, sizes, plugin requirements and the owning plugin’s payload before activation.

An opened container's manifest selects its primary storage and optional plugins; `WHAB_STORAGE` applies to the original workspace and initial CLI creation, and Settings creates new SQLite budgets. Missing optional packages are reported while preserving their entries/data; required storage and selected UI failures remain explicit.

The installation also supplies services needed before a budget is open. A selected interface presents budget selection; a server listener starts once for the installation and exposes only budgets available to it. Budget-owned selection does not imply that every plugin runs in every process.

## Capability families

The categories describe a plugin's primary role. Capabilities determine what it can contribute; one package can provide several.

| Family | Responsibility |
| --- | --- |
| Storage | Persist the budget and provide coherent reads and transactional changes |
| Interface | Present a budget or expose operations to another application |
| Ingestion | Normalize imported records and user input into validated financial data |
| Insight | Read scoped snapshots to produce reports, visualizations or exports |
| Automation | Suggest or apply configured actions while preserving user decisions |
| Extension | Add specialized models and workflows around the financial core |

Contributor folders use `storage`, `interface`, `ingestion`, `insight`, `automation` and `extension`. The additional `example` category holds teaching plugins. Each plugin owns `whab/app/plugins/<category>/<id>/`, including its implementation, documentation and tests; see [folder ownership and naming](../app/plugins/README.md).

At least one storage capability must be available. Multiple storage destinations mean copies of the same budget, rather than separate ledgers. The save outcome must distinguish a committed copy from a destination still awaiting synchronization; an unavailable destination must never silently discard a successful save. Interactive use requires a usable interface. A presentation enhancement alone does not provide an entry point.

The public SDK defines the concrete hooks supplied by a plugin. A capability family does not imply that every possible plugin in that family ships with WHAB. Shared domain rules remain in core; plugin-specific behavior and persistence belong to their owner.

## Client/server deployment

Client/server is a deployment option within the same architecture. A server plugin supplies an **API interface capability**; a client plugin supplies **remote storage** and any connection controls it needs. Their primary categories are Interface and Storage respectively, while their descriptors can contribute several capabilities.

- **Client instance:** presents the budget and forwards scoped reads and commands through the client plugin. Local calculations can provide previews; committed server state is authoritative.
- **Server instance:** validates operations through the financial core and persists the budget. Bank ingestion, synchronization cursors and encrypted provider credentials stay here.

The budget describes its features without requiring them all to execute on the client. Bank ingestion and credentials stay on the server; clients use scoped ledger operations. A client container can carry its public remote URL and budget identity, plus an optional password-encrypted remote access credential. It does not automatically carry a copy of the remote financial ledger, bank-provider tokens or encryption keys.

The bundled `server` and `client` plugins use the versioned `/remote/v1` public ledger contract, rather than exposing the privileged `StorageBackend` protocol. Every WHAB route, including localhost and webhooks, requires HTTPS. Ordinary routes retain loopback Host/origin checks. Only enabled remote protocol routes and registered signed bank webhooks bypass those local checks; the server requires a machine/server bearer credential for budgets explicitly enabled with the server plugin. Commands include an expected revision and request ID; stale revisions fail explicitly, and retries of the same request return the original result while its receipt remains available. Webhook signature verification authenticates provider deliveries independently of TLS encryption. Local setup provisions a CA/certificate without changing OS trust; the user must [trust the CA explicitly](usage.md#local-https). A failed connection never silently switches to an unrelated local ledger. See the [SDK guide](plugins.md#remote-interface-and-client) for configuration and retry limits.

## Data and lifecycle boundaries

Ingestion owns parsing and provider communication. Storage validates normalized records and commits imported data with its checkpoint atomically. Replays preserve annotations and cannot duplicate financial records. File import parsers receive bytes rather than a database or bank credentials.

Insights read scoped, immutable snapshots. Specialized state belongs to the extension that understands it; plugins must not write into another plugin's private schema. Automations preserve explicit user edits and submit financial changes through validated operations. Where several capabilities act on the same records, ordering and conflict handling must be explicit.

Installed plugins are trusted Python code. Scoped contracts reduce accidental access and coupling; they do not create an operating-system sandbox. Users retain control over which code and external connections they enable.

Existing ledgers, encrypted connections, environment isolation and atomic synchronization remain compatibility requirements. SDK additions follow the [SemVer policy](../app/CONTRIBUTING.md#pull-requests-and-releases); the documented public records and operations remain the authority for plugin authors.
