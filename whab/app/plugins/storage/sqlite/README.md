# SQLite storage plugin

This version requires WHAB 2.70 or newer for the public local-authority and
single-primary copy contracts. See [copies and promotion](../../../../docs/storage-replicas.md)
for host-coordinated synchronization and explicit handover.

Distributed independently as `whab_plugin_sqlite`. Its version and dependency compatibility range are defined in [pyproject.toml](pyproject.toml); its dated history belongs in [CHANGELOG.md](CHANGELOG.md). Material changes confined to this plugin update its version and changelog, without an app version or app changelog change.

`src/whab_plugin_sqlite/` owns the bundled SQLite descriptor, ledger transactions, additive schema upgrades and fictional seeding. Keep its implementation and tests in this folder. The host uses the public storage protocol rather than importing this package.

## Reads and atomic mutations

Version 1.21 with WHAB 2.69 implements optional `CategoryDeletion`: core rejects
all current transaction references (including tombstones), nonzero assignment rows,
and groups with children. After all guards pass, the exact workspace/child and its
zero-only assignment rows are deleted inside the same native transaction; its
empty parent is kept. Any blocker preserves all zero rows. History retains every
exact removed zero row, restoring it with the category/parent metadata on guarded
undo and reapplying on redo. Monthly monetary results and rollover rules stay intact.
Empty parent deletion changes only parent metadata. Financial state, audit history
and native receipts commit atomically; write failures roll back the deletion. No
schema migration, financial cascade, bank-mapping change or sync is performed.

Bank connection summaries and single-connection lookups read only the native
`items` table and workspace metadata under a read snapshot. They do not reconstruct
transactions, assignments or audit history. Summary results still omit encrypted
tokens and cursors; the privileged host-only lookup preserves the complete saved
connection record, including optional sync settings. Disconnected connections stay
out of summaries. No schema, mutation, synchronization or persistence behavior changes.

Optional bounded register reads count and select under one snapshot, with Unicode casefold search and stable date/ID ordering; additive indexes support paging and account-history checks. Account overview/settings queries read metadata and counts without exporting history. A batch workspace query reads one ledger snapshot for consecutive months. Transaction annotation edits invoke the existing core command and update only the addressed row inside one transaction, preserving receipts, balances, synchronization fields and rollback on error. No persistent financial-page cache is used; later reads see committed changes.

Batch projections share a request-local core index of dated activity instead of preparing full history for each month. Portable exports read allowlisted ledger columns directly and validate them before rebuilding their fresh sanitized database; they omit the optional runtime register/history and uncategorized-count indexes from the portable image. Opening that image restores the usual runtime indexes. The saved financial data, receipts, atomic save behavior and compatible legacy exports remain unchanged.

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

The optional `ReceiptStorage` capability keeps validated JPEG/PNG/PDF attachments with WHAB 2.45 or newer (existing WebP remains readable) in an additive table, outside financial projections. Files are bounded to 5 MiB, and raster images to 25 megapixels, scoped by workspace and transaction, and migrate with matched pending transactions. Sanitized SQLite portable images include receipts; old schemas remain readable. Image writes roll back if the resulting portable image would exceed the existing 50 MiB container bound. No private image data is stored in web assets or sent to external logo providers.

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

## Transaction detail corrections

The optional `edit_transaction_details(workspace, transaction_id, changes)` capability delegates date, payee, amount, same-currency account and combined annotation validation to core in one atomic mutation. Manual entries adjust the old and new account balances together. Imported corrections retain provider snapshots and original edited fields in an additive nullable `original_details` column; later syncs preserve explicit overrides, including pending-to-posted replacement. Portable exports and reopening retain those annotations, and earlier SQLite schemas remain readable without running untrusted migrations. This schema addition needs a stopped-host update before candidate read-only readiness can succeed on an older native schema.

## Operation documentation

The packaged [operations.json](src/whab_plugin_sqlite/operations.json) documents this plugin's public operations, input/output schemas, fictional examples, effects, access, scope, errors and retries. Read it offline with `whab-cli describe --plugin sqlite`, add `--json` for structured metadata, or use operation-specific `--help` for public CLI commands. Local-only settings and SDK callbacks are explicitly labeled and are not made remotely callable by documentation. Delegated ledger operations are described by `whab-cli describe --plugin core`.


## Explicit bank account setup

WHAB 2.59 adds the optional durable `configure_bank_sync` native operation. It validates provider ownership, unique destinations, compatible currencies and explicit inclusive date/all-history choices, then atomically saves the connection’s account mapping. Linking an auto-created bank account to an existing account moves references without changing transaction IDs, receipts or annotations. Already imported history is retained; cross-source overlap is not automatically reconciled. Future imports filter new records by the saved date while still processing modifications and pending-to-posted matches for known bank transactions. Lowering the cutoff resets the cursor for idempotent provider replay. Protected local transfers retain the settings; portable exports continue to omit provider credentials and protected connection state.


SQLite 1.14.1 accepts exact older portable image schemas that predate the native-only bank sync settings column, including all previously supported planning-table and index combinations. Validation remains read-only and rejects unrecognized schema changes. Opening an existing budget does not replace its committed live ledger with the portable snapshot.


## Category agent attribution

With WHAB 2.61, `supports_category_agent()` returns `True`. The optional
`categoryAgent` label is persisted atomically with explicit category edits through
`edit_transaction_details`. Public reads, review-only edits, provider refreshes,
posting replacements, checkpoints, reopen and portable exports retain it. Manual
category writes without a label clear it; existing history remains unlabelled.
This self-declared label is separate from import `source` and review state.
See the [SDK contract](../../../../docs/plugins.md#explicit-categorizing-agents).

An additive nullable `category_agent` column retains labels; legacy native and portable files remain readable, and ordinary writable opens add the column without inferring past attribution. Update the stopped host before opening a candidate against an older schema.

## Financial history

See the [budget history workflow](../../../../docs/usage.md#budget-history).
This version requires WHAB 2.62+. History is saved atomically with financial
changes and retained across reopen and exports. Earlier changes are not backfilled.

## Durable archived accounts

With WHAB 2.64+, `supports_account_lifecycle()` advertises persistent account
`closed` state for every type, including off-budget `deposit` / `cd`. Existing
accounts default open without changing their financial history hashes or recovery
digests. Close/reopen retain identity, amounts, uncertainty, currency, annotations
and mappings. New manual entries are rejected for archives; bank merges skip their
account snapshots/transactions while open peers continue. Private per-account
cursor checkpoints survive native reopen, copy/transfer and recovery; reopening
replays from that checkpoint on the next normal sync. No synchronization is started
by close/reopen. Portable exports retain lifecycle but omit protected bank state.
See [the core semantics](../../../../docs/architecture.md#account-lifecycle-and-cds).

## Category management

This version requires WHAB 2.69+ for optional SDK `CategoryManagement`.
`category_tree(workspace)` includes real empty parents; `add_category_group(workspace,
name)` creates one; `move_category(workspace, category_id, parent_id)` preserves the
child ID, target, currency, transaction references and all monthly assignments.
The financial core validates the two-level hierarchy and duplicate destinations.
Native evidence/receipts/history commit with the metadata. SQL uses an additive
nullable `meta.category_groups` column; JSON stores the optional neutral
`categoryGroups` collection. Omitted metadata preserves legacy digests. Portable
and protected native copies retain explicit parents, including emptied sources.
There is no manual reorder operation; moves retain the existing ledger order.
Use the [simple CLI workflow](../../interface/cli/README.md#category-management).

Read-only `CategoryDeletionInspection.category_deletion_status` returns only the
addressed child's live/deleted reference counts and every assignment month, including
zero rows, plus nonzeroCount/nonzeroMonths. Zero-only rows permit deletion and are
removed atomically with the child while retained exactly in history. Nonzero rows
or transaction references block the entire operation. The read itself never erases
records or history. See the CLI `category show` workflow.

## CLI read and save performance

Optional `search_transactions(workspace, filters)` selects complete date/merchant
matches, or uncategorized counts and a requested sample, under one read snapshot.
It preserves Unicode casefold literal matching, descending date/ID order and
integer currency amounts. `account_latest_dates(workspace)` reads active-history
MAX dates grouped by account for provider previews.

Merchant selections use a disposable in-memory index of Python-casefolded text.
SQLite's [FTS5 trigram index](https://www.sqlite.org/fts5.html#the_trigram_tokenizer)
finds candidates for substrings of three or more code points; exact literal
matching verifies candidates. Shorter searches and SQLite builds without FTS5
use the same folded-text table without a Python callback per stored row. The
index is outside the authoritative database and portable image. Its generation
stamp is updated only after known bounded commits; external writes, unbounded
changes and triggers require rebuilding it under the next read snapshot. Failed
index maintenance discards the cache and cannot fail an already-committed edit.

Native evidence is reused only while SQLite total-change and data-version stamps
agree. Bounded edits replace detached snapshot records and reuse canonical bytes
of immutable rows and journal baselines. External SQL, triggers and failed writes
invalidate these optimizations. Native evidence and history hashes retain their
existing canonical representation and remain independently reproducible.
Bounded writes retain sorted transaction positions and reuse complete canonical
native record views, avoiding whole-ledger dictionaries and repeated sorting.
Only their changed metadata and records are replaced; complete canonical hashes
are still computed with the original algorithm. Unknown scopes and identity
changes retain the full reconstruction path.

Classifications that affect account metadata update one row using core rules.
Currency and lifecycle changes retain their complete-history validation. Portable
exports reuse a previously sanitized image for supported account/transaction
edits, apply exact audited changes and update its journal. Unsupported changes or
unverified source stamps rebuild a fresh validated image. The live database,
connections and credentials are never copied into a portable image. Checkpoints
still finish synchronously before the CLI returns.

Audit records remain compatible JSON TEXT. A small whitespace reserve lets
SQLite append entries transactionally through incremental blob writes. Larger
entries, unverified records and triggers retain full JSON replacement. The
portable storage capability may return SHA-256-bound file patches so unchanged
image pages stay in the host. WHAB still publishes a complete `.whab` container.
For lower checkpoint latency, sessions reuse independently compressed 64 KiB
blocks for unchanged payload ranges. Files remain ordinary DEFLATE ZIP entries;
standard ZIP readers and earlier hosts can decompress them. Dictionary resets
may increase archive size slightly compared with one continuous compression
stream. The cache is bounded by the current container's expanded size. Checkpoints
reuse their encoder's validated in-memory container; opening files and checking
external revisions continue through full container parsing and validation.
