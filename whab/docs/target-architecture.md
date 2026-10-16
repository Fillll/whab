# Budget and plugin model

WHAB combines a plugin host with a hexagonal financial core. The [architecture guide](architecture.md) describes runtime composition and the [SDK guide](plugins.md) defines executable contracts. The [README diagram](../../README.md#architecture) groups plugins by their responsibilities.

## Minimal financial core

The domain core owns four concepts: **accounts, transactions, categories and budgets**. It defines their relationships and financial rules, including integer money, compatible currencies, allocations, rollover and budget calculations. Financial correctness applies regardless of the interface or persistence implementation.

The core depends on no HTTP framework, interface, database, bank provider or plugin runtime. Plugins connect through public contracts. A small host loads selected capabilities, supplies their contexts, manages configuration and lifecycle, and assembles the application outside the domain core.

## Installation and budget ownership

Plugin code is installed once in the WHAB environment. A budget records which of those plugins it uses, along with compatible API requirements, settings and plugin-owned saved state. Opening a budget restores that selection; disabling a plugin preserves its saved state so it can be enabled again.

| Owner | Responsibilities |
| --- | --- |
| WHAB installation | Installed executable plugin packages and available capabilities |
| Budget | Identity, ledger, selected features, plugin settings and saved state |
| Machine or server | Credentials, encryption keys, device paths and connection settings |

A portable `.whab` bundle carries the budget and its plugin manifest. It describes the setup without embedding executable Python or provider credentials. Moving a budget to another installation can require installing compatible plugins and reconnecting credentials. Opening a bundle must validate its format, data and plugin requirements before using it.

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

Contributor folders use `storage`, `interface`, `ingestion`, `insight`, `automation` and `extension`. The additional `example` category holds teaching plugins. Each plugin owns `plugins/<category>/<id>/`, including its implementation, documentation and tests; see [folder ownership and naming](../plugins/README.md).

At least one storage capability must be available. Multiple storage destinations mean copies of the same budget, rather than separate ledgers. The save outcome must distinguish a committed copy from a destination still awaiting synchronization; an unavailable destination must never silently discard a successful save. Interactive use requires a usable interface. A presentation enhancement alone does not provide an entry point.

The public SDK defines the concrete hooks supplied by a plugin. A capability family does not imply that every possible plugin in that family ships with WHAB. Shared domain rules remain in core; plugin-specific behavior and persistence belong to their owner.

## Client/server deployment

Client/server is a deployment option within the same architecture. A server plugin supplies an **API interface capability**; a client plugin supplies **remote storage** and any connection controls it needs. Their primary categories are Interface and Storage respectively, while their descriptors can contribute several capabilities.

- **Client instance:** presents the budget and forwards scoped reads and commands through the client plugin. Local calculations can provide previews; committed server state is authoritative.
- **Server instance:** validates operations through the financial core and persists the budget. Bank ingestion, synchronization cursors and encrypted provider credentials stay here.

The budget describes its features without requiring them all to execute on the client. A server-owned ingestion plugin can expose controls to a client interface while retaining provider access and secrets on the server. Connection configuration belongs to the device; the portable budget contains no bank tokens or encryption keys.

The bundled `server` and `client` plugins use the versioned `/remote/v1` public ledger contract, rather than exposing the privileged `StorageBackend` protocol. The server requires HTTPS and a machine/server bearer credential for budgets explicitly enabled with the server plugin. Commands include an expected revision and request ID; stale revisions fail explicitly, and retries of the same request return the original result while its receipt remains available. A failed connection never silently switches to an unrelated local ledger. See the [SDK guide](plugins.md#remote-interface-and-client) for configuration and retry limits.

## Data and lifecycle boundaries

Ingestion owns parsing and provider communication. Storage validates normalized records and commits imported data with its checkpoint atomically. Replays preserve annotations and cannot duplicate financial records. File import parsers receive bytes rather than a database or bank credentials.

Insights read scoped, immutable snapshots. Specialized state belongs to the extension that understands it; plugins must not write into another plugin's private schema. Automations preserve explicit user edits and submit financial changes through validated operations. Where several capabilities act on the same records, ordering and conflict handling must be explicit.

Installed plugins are trusted Python code. Scoped contracts reduce accidental access and coupling; they do not create an operating-system sandbox. Users retain control over which code and external connections they enable.

Existing ledgers, encrypted connections, environment isolation and atomic synchronization remain compatibility requirements. SDK additions follow the [SemVer policy](../CONTRIBUTING.md#pull-requests-and-releases); the documented public records and operations remain the authority for plugin authors.
