# SQLite storage plugin

This version requires WHAB 2.37 or newer for the public local-authority and
single-primary copy contracts. See [copies and promotion](../../../../docs/storage-replicas.md)
for host-coordinated synchronization and explicit handover.

Distributed independently as `whab_plugin_sqlite`. Its version and dependency compatibility range are defined in [pyproject.toml](pyproject.toml); its dated history belongs in [CHANGELOG.md](CHANGELOG.md). Material changes confined to this plugin update its version and changelog, without an app version or app changelog change.

`src/whab_plugin_sqlite/` owns the bundled SQLite descriptor, ledger transactions, additive schema upgrades and fictional seeding. Keep its implementation and tests in this folder. The host uses the public storage protocol rather than importing this package.

## Reads and atomic mutations

Optional bounded register reads count and select under one snapshot, with Unicode casefold search and stable date/ID ordering; additive indexes support paging and account-history checks. Account overview/settings queries read metadata and counts without exporting history. A batch workspace query reads one ledger snapshot for consecutive months. Transaction annotation edits invoke the existing core command and update only the addressed row inside one transaction, preserving receipts, balances, synchronization fields and rollback on error. No persistent financial-page cache is used; later reads see committed changes.

Batch projections share a request-local core index of dated activity instead of preparing full history for each month. Portable exports read allowlisted ledger columns directly and validate them before rebuilding their fresh sanitized database; they omit the three optional register/history indexes from the portable image. Opening that image restores the usual runtime indexes. The saved financial data, receipts, atomic save behavior and compatible legacy exports remain unchanged.

A private projection reader supplies core with every transaction's financial and reference fields, including deleted, pending and transfer history. Only the viewed months load the additional public display fields. Metadata, history and display reads share one SQLite snapshot; core still performs all validation and budget arithmetic. Complete state, exports and mutations retain their existing record shapes. No projection input or financial result survives the request.

Register counts without an explicit account selection resolve matching currency account IDs once instead of joining each transaction to its account. Explicit selections keep their existing count query. The selected-row query, snapshot, literal search, review counts and pagination remain unchanged; no new index or cache is required.

`receipt_transaction_exists` checks one active transaction by its existing workspace/ID key. WHAB 2.15 can use this optional capability before receipt operations instead of requesting full transaction history. Deleted rows are absent and pending rows remain eligible; each receipt read or write still checks its transaction independently. Images, portable data and persistence rules are unchanged.

Optional `review_transactions` validates one to 100 distinct transactions with core annotation rules and commits their review flags atomically. It preserves all other columns and uses the existing schema. The host tracks this as one mutation and performs one synchronous checkpoint attempt before returning; existing saved/pending recovery behavior remains authoritative. An invalid target or database write failure rolls back the whole batch.

Optional `receipt_ids_for` returns sorted receipt markers only for one to 100 distinct selected transaction IDs. It uses existing receipt and transaction keys without reading image bytes or full history. Deleted and absent rows are omitted; pending and historical rows remain eligible. It matches the full receipt index's workspace semantics, including absent metadata, and changes no schema, data or revision.

Preserve integer currency minor units, separate currency totals, workspace/environment isolation, legacy filenames, saved annotations and atomic synchronization cursors. Accounts and child categories persist their currency; existing rows acquire `USD` through additive columns without changing category IDs or amounts. Parent groups derive from child group labels and cannot receive transactions or allocations. Compatibility fixtures use temporary databases.

## Portable files and restoration

Portable plugin budget containers use `export_budget_files()` and `restore_budget_files(workspace, files)`. SQLite owns a single `budget.sqlite` image (payload format 1, at most 50 MiB). Export rebuilds a fresh database from a coherent validated ledger snapshot, validating that snapshot once; it never copies the live database. The image contains personal ledger history, currencies, allocations and annotations, with no demo ledger, bank connections, tokens, cursors or account connection metadata. The source ledger and its active connections remain unchanged. Older portable schemas may omit any of the additive register indexes; present indexes still require exact supported SQL.

Accounts persist their core `onBudget` scope and optional `subtype`. Existing ledgers and earlier portable images infer on-budget scope for checking/savings (`depository`) and credit cards (`credit`), and off-budget scope for other account types. New account classifications survive reopening, portable roundtrips and provider refreshes. Scope controls envelope budgeting independently of the account's asset or liability type.

Opening an image checks its exact supported schema and bounded rows in memory, rejects triggers, views and additional schema objects, and reads ledger records as data. Restore uses the existing atomic empty-personal-ledger import boundary, leaves the installation's demo ledger intact and marks external account snapshots stale. It never executes SQL scripts or migrations supplied by a budget file. The earlier neutral `export_budget()` and `restore_budget()` methods remain available for version 1 budget files.

The container lifecycle calls `prepare_budget_files(workspace, files, existing=False)` on first open. A retry with the same normalized ledger is harmless; different nonempty data is preserved and rejected. With `existing=True`, an established local ledger remains authoritative and retains its private bank connections; a pristine replacement cache is restored from the image. Every path validates the input image before using the cache.

## Planning, accounts and receipts

WHAB 2.7 and SQLite plugin 1.2 add the optional `future_funding` table with one saved amount per target month and currency. From core 2.9 those records represent explicit set-asides from the previous source month, independently of target category assignments. Core includes the incoming amount in the new monthly carry-in breakdown and validates the source pool and actual cash. The schema and public command remain compatible. Reads, writes, reopening, neutral exports and SQLite portable images preserve the records atomically; earlier ledgers without the table remain readable. Funding counts as saved user data, so a workspace containing it cannot receive a replacement import.

Monthly carryover rules are saved per source month and currency in the additive `rollover_modes` table. `set_rollover_mode()` delegates validation to core under the ledger transaction. Neutral `rolloverModes` records and SQLite images preserve choices, including default-category choices, through reopen and export/restore. A saved choice counts as user data for import safety. Portable validation accepts every earlier supported combination of account scope and planning tables without executing stored schema SQL. Workspace queries supply the current month explicitly so past-month availability excludes plans funded later, while command validation retains historical and current funding protections.

Account metadata preserves a user-defined name override and a validated public bank website through synchronization, reopening and portable export. Currency changes are accepted only before the account has any transaction history. Transaction memo edits preserve explicit empty notes across provider refreshes.

The optional `ReceiptStorage` capability keeps validated PNG/JPEG/WebP attachments in an additive table, outside financial projections. Images are bounded to 5 MiB and 25 megapixels, scoped by workspace and transaction, and migrate with matched pending transactions. Sanitized SQLite portable images include receipts; old schemas remain readable. Image writes roll back if the resulting portable image would exceed the existing 50 MiB container bound. No private image data is stored in web assets or sent to external logo providers.

WHAB 2.12 can request an atomic transaction-register projection without rebuilding envelope history. SQLite selects accounts, categories and the requested account/month/currency transactions within one snapshot. WHAB 2.13 can additionally request `transaction_register_filtered` with inclusive start/end dates and several account IDs. SQLite applies the date bounds and account membership in SQL within the same snapshot. Open bounds cover all earlier or later dates; an empty explicit account tuple returns no transactions. The original workspace and transaction APIs remain available; earlier SQLite portable images remain readable.

## Development checks

The optional version-1 `storage.authority`, `storage.commands` and `transfer.local`
contracts persist budget/environment and instance identities, authority epoch,
logical generation, roles, transition identities and operation receipts in additive
runtime tables. SQLite uses `synchronous=FULL`; each ordinary mutation updates its
generation in the same native transaction. The authority schema and its initial
identity also publish in one transaction: interruption before that commit retains
the original usable legacy database. Pre-existing incomplete authority metadata is
preserved and refused. Optimized annotation/review writes retain
their scoped financial queries and add one authority-row update. Every ordinary
mutation rejects staged, copy, fenced or retired storage, including through an older
open handle. Reopening retains the persisted role.

Durable commands apply core validation and atomically commit financial/provider
changes with their result receipt. Exact retries return the original result before
checking stale generation evidence. At most 4,096 receipts are retained; an unknown
or expired command with stale evidence fails without executing. A lost response
after native commit fences that handle as uncertain until reopening and comparing
its durable evidence. A failed transaction retains its previous generation.

Complete local transfer requires an empty staged destination and preserves both
workspaces, deleted history and images, import identity, exact encrypted tokens,
associations, masks and cursors. It leaves runtime keys with the host. The host must
validate the complete digest, fence the source and durably choose authority before
activation with a fresh epoch. Matching transfer retries are harmless; populated or
different destinations are refused. States exceeding the public local-transfer
bounds are refused without restricting existing SQLite ledger writes. Runtime
authority tables and protected records never enter the sanitized portable database.

Authority metadata format 2 adds the version-1 `storage.replica` contract and a
prepared promotion identity. Opening format 1 upgrades that metadata in one native
transaction without changing the instance, epoch, generation or financial records.
The replica watermark, both workspaces, protected records and durable command
receipts commit together. A copy keeps its own instance identity while recording
its source instance/epoch/generation/operation and the host-owned namespace
generation/digest. Opaque plugin files remain the host's responsibility.

`apply_replica()` accepts only a copy. Exact replay is harmless after a lost
response; older financial or namespace generations, a different source/epoch and
conflicting same-generation data cannot overwrite a newer copy. Native copy data
must match its saved watermark on reopen. Copy failure cannot undo the primary's
committed save.

`prepare_promotion()` compares complete copy evidence and the exact watermark,
persists the transition and blocks further replica updates. Its matching
`abort_promotion()` clears that preparation. The host must first catch up, validate
namespaces, fence the primary and durably choose authority. Only then may
`set_role()` activate the prepared copy with a fresh epoch. Promotion and abort
leave financial generation and data unchanged; new primary commands advance it.

From `whab/app/`, use `.venv/bin/python -m pytest plugins/storage/sqlite/tests` or `.venv/bin/python scripts/check.py --quick` during iteration, then `.venv/bin/python scripts/check.py` before submitting. Documentation-only edits can use `--docs`. See [plugin contribution rules](../../README.md#test-ownership) for coverage/packaging requirements and [CHANGELOG.md](CHANGELOG.md) for the release history of these capabilities.


## Read-only readiness and live code

The explicit version-1 `storage.worker` contract supports compatible live code
replacement through the host's approved environment coordinator. The host drains
financial/background work before releasing the old writer, durably chooses the
new code before its first writable constructor, and retains native instance,
authority epoch and financial generation. It uses native operation receipts to
reconcile lost mutation replies; uncertainty gates this budget until explicit
recovery. This does not change storage authority or select another backend.

`StorageContext(read_only=True)` validates an existing current native schema
without creating a ledger or upgrading older metadata, and rejects all mutations.
Only compatible readers may pass candidate readiness; schema changes need an
explicit reader/upgrader strategy. See [storage workers and stopped-host repair](../../../../docs/storage-workers.md)
for the exact-host fence, protocol limits, durable request replay and recovery.
The client backend requires a separate host-managed remote adapter.
