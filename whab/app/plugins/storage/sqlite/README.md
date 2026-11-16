# SQLite storage plugin

This version requires WHAB 2.72 or newer for the public local-authority and
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

Portable plugin budget containers use `export_budget_files()` and `restore_budget_files(workspace, files)`. SQLite owns a single `budget.sqlite` payload (at most 50 MiB). From SQLite 1.29 / WHAB 2.81, payload format 2 wraps a sanitized SQLite snapshot and checksummed financial change records. Earlier format-1 SQLite images remain readable. New snapshots rebuild a fresh database from a coherent validated ledger; export never copies the live database. The image contains personal ledger history, currencies, allocations and annotations, with no demo ledger, bank connections, tokens, cursors or account connection metadata. The source ledger and its active connections remain unchanged. Older portable schemas may omit any of the additive register indexes; present indexes still require exact supported SQL.

Accounts persist their core `onBudget` scope and optional `subtype`. Existing ledgers and earlier portable images infer on-budget scope for checking/savings (`depository`) and credit cards (`credit`), and off-budget scope for other account types. New account classifications survive reopening, portable roundtrips and provider refreshes. Scope controls envelope budgeting independently of the account's asset or liability type.

Opening an image checks its exact supported schema and bounded rows in memory, rejects triggers, views and additional schema objects, and reads ledger records as data. Restore uses the existing atomic empty-personal-ledger import boundary, leaves the installation's demo ledger intact and marks external account snapshots stale. It never executes SQL scripts or migrations supplied by a budget file. The earlier neutral `export_budget()` and `restore_budget()` methods remain available for version 1 budget files.

The container lifecycle calls `prepare_budget_files(workspace, files, existing=False)` on first open. A retry with the same normalized ledger is harmless; different nonempty data is preserved and rejected. With `existing=True`, an established local ledger remains authoritative and retains its private bank connections; a pristine replacement cache is restored from the image. Every path validates the input image before using the cache.

Cold restoration reuses the image reader's fully validated detached snapshot inside the atomic import, avoiding a second ledger/history validation. An existing cache with a personal account is recognized by an indexed presence query without loading its transaction history. Independent file verification, public JSON restore validation and all empty-workspace guards remain in place.

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


## Identity-preserving category rename

With WHAB 2.71 this plugin implements optional `CategoryRenaming`, explicitly
advertising support and delegating a name-only child edit to shared core validation.
Duplicate siblings are refused atomically. The category ID, parent, currency, target,
transactions, assignments, protected state and previous history stay intact. The
native mutation, reversible audit entry and durable receipt commit together using
the existing format; no migration is needed. The host's append-memo command reuses
this plugin's existing note-only edit, preserving merchant whitespace and every
unrelated transaction field. Exact public intent replays do not append twice.

## CLI read and save performance

Optional `search_transactions(workspace, filters)` selects complete date/merchant
matches, or uncategorized counts and a requested sample, under one read snapshot.
It preserves Unicode casefold literal matching, descending date/ID order and
integer currency amounts. `account_latest_dates(workspace)` reads active-history
MAX dates grouped by account for provider previews. A partial date/ID index
keeps limited uncategorized samples from walking categorized history; readers of
older native stores retain the ordinary query fallback. Portable schema validation
checks each optional runtime index's exact definition once, without enumerating
every combination of omitted indexes.

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
agree. On the first financial write, native metadata advances to format 5 and
selects version-2 logical Merkle hashes. Known bounded edits update changed record
leaves and their ancestors; retained history baselines and audit entries are
immutable. Unknown scopes, outside SQL, triggers and rollbacks discard cached
state. Older metadata, original entry hashes and old retry receipts remain readable.
Opening alone does not change financial revisions. Earlier writers refuse the
new native metadata instead of modifying a state they cannot verify. See the
[versioned integrity contract](../../../../docs/plugins.md#incremental-integrity).

Classifications that affect account metadata update selected rows using core
rules. An explicit budget-scope value does not force a full ledger rewrite.
The transfer guard reads at most one confirmed-transfer witness per addressed
account through a partial native index; explicit and implicit scope changes still
require unmatching confirmed transfers first. Currency and lifecycle changes
retain complete-history validation. Atomic
transaction and account batches share one native transaction, revision, receipt,
audit entry and checkpoint; `--batch` remains only a history grouping label.

Portable format 2 begins with `WHAB-SQLITE-2`, a bounded SQLite snapshot length and
SHA-256 checksum, followed by that sanitized snapshot. Each appended frame has
its own length, SHA-256 checksum and JSON audit entries. Supported edits replace
existing account or transaction rows. Independent opening validates the snapshot
schema and every frame, checks exact prior records and the complete audit chain,
then validates the final ledger and receipt references. Nested envelopes, damaged
frames, unknown fields and unsupported changes are rejected without executing
stored SQL. Earlier image-only readers refuse the new header; use SQLite 1.29+
to open these saved files.

Provider accounts in a saved image remain detached and stale. Replay checks the
image against the independently verified audit state, allowing only that exact
stale-flag projection; account names, balances, manual-account flags and every
other financial field must match. The original audit records remain unchanged.
A legacy provider ledger without audit history needs one complete snapshot on
its first audited edit to establish that baseline; later supported edits append.

The journal compacts to a fresh sanitized snapshot after 512 frames, 4 MiB of
changes, a change that cannot be represented by supported replacements, or when
needed to retain the 50 MiB payload bound. Compaction, the first integrity build,
full exports and recovery remain size-dependent operations. No receipt or audit
entry is removed by compaction. Bank credentials and private native records never
enter either the snapshot or change records.

The host still saves one complete ordinary ZIP `.whab` file atomically and
synchronously. That file contains every acknowledged edit, including the current
journal; it needs no sidecar or future compaction to restore. Unchanged compressed
64 KiB blocks are reused. Append patches retain ordinary SHA-256 checksums: both
storage and host copy their independently computed SHA state and hash only the
new suffix after checking the exact previous bytes. A changed prefix requires
full verification. Native commit failure cannot promote a prepared portable
image; checksum or encoding failure cannot publish the candidate archive.

Writable native file connections recommend a 64 MiB SQLite page cache, allocated
on demand, without changing journal or sync guarantees. In-memory portable
builders and read-only connections retain their prior setting. Audit records
remain JSON TEXT; a bounded whitespace reserve permits transactional blob appends.

## Atomic bulk edits

With WHAB 2.72 this backend implements optional `AtomicBulkEdits`.
`preview_bulk_edits(workspace, operation, edits)` is read-only; `edit_transactions`
and `classify_accounts` apply 1–100 explicit distinct targets through core validation.
Every candidate validates before writes. SQLite performs bounded updates inside one
native transaction; JSON validates a detached candidate and replaces its file once.
The operation, exact before/after financial history and durable receipt commit together,
with one generation advancement. No omitted field is normalized or overwritten.
Existing provider mappings, receipts, financial fields, imported notes and merchant
whitespace remain. There is no bank call or sync. Unsupported backends must refuse,
never emulate this contract by looping public single-record methods. Exact retries
return the original receipt before stale checks and never save twice, including after
restart. Uncertain outcomes retain their existing authority fence and recovery identity.
Undo/redo treats the entire coherent audit entry as one guarded operation.
See [CLI examples, JSON fields and dry-run](../../interface/cli/README.md#atomic-bulk-edits).

## Reviewed transfer matching

WHAB 2.74 introduces optional public `TransferMatching` SDK capability. The financial
core owns equal nonzero opposite amounts, same currency and budget scope, distinct
active accounts, posted/nondeleted/uncategorized eligibility and the seven-day date
window. Deterministic proposals are read-only and preserve every ambiguous alternative;
merchant names and provider hints never prove a pair. Date/account filters select an
anchor; its counterpart may lie outside those filters. Pagination returns total/next;
over 1,000 candidates requires narrower filters. No initial-page background scan runs.

The interface plugins own presentation: Web's Transactions **Suggest transfers** panel
shows both sides and requires **Review this pair**, then **Confirm transfer match**.
CLI `trnx transfers`, `match-transfer --dry-run`, `match-transfer` and `unmatch-transfer`
use the same public operations. Neither matching nor unmatching reviews a transaction:
both become unreviewed for the normal review workflow. No bank synchronization or
persistent future rule is created. Refunds may look identical and require judgment.
Cross-currency, fees/splits and cross-budget-scope transfers need manual handling.

Confirmation uses an observed revision and fingerprint, one native operation, one
reversible before/after audit entry, one durable receipt and one synchronous checkpoint.
Exact successful retries keep operation, IDs/order, action, fingerprint, revision,
request ID and history attribution, including after restart. Unknown outcomes must
retain that intent; no fresh-intent automatic replay. Unsupported backends/authorities
fail before mutation. SQLite 1.24 and JSON 1.16 implement the capability through their
existing native transactions; remote transports delegate to the authority.

Optional `transferPeerId` is a reciprocal stable identity; absence preserves legacy
records and historical digests. Match changes only this link, `isTransfer=true` and
`reviewed=false`. Unmatch removes both links, restores each existing `inferredTransfer`
hint and sets both unreviewed. IDs, amounts, dates, accounts, currencies, merchants
(including whitespace), notes, category attribution, original metadata and receipts
are retained. Unmatch before editing a paired amount/date/account/category or changing
an account's budget scope. Ordinary notes/merchant/review edits preserve the pair.
Provider corrections/removals that invalidate a pair remove both links and require
fresh review; this never automatically creates another pair.

The shared contract is necessary because no previous public operation could atomically
link two transactions with reciprocal identity, validated preview and reversible audit.
Storage internals stay behind SDK/native operations; interfaces never inspect databases.

## Explicit Income category treatment

WHAB 2.76 and this adapter implement optional public `CategoryClassification`:
`supports_category_classification`, read-only `preview_category_classification`,
and guarded `classify_category`. `add_category` accepts optional `kind=income` or `kind=transfer`;
missing kind remains Expense. Core owns eligibility, funding, target/assignment
validation and preview; the adapter only persists the exact records. Kind changes
commit with one native revision/receipt and reversible audit entry. Transactions,
review status, balances, provider mappings and monthly assignments remain verbatim.
No sync or automatic historical classification occurs. Preview shows historical
summary consequences; income conversion refuses nonzero targets and **any** monthly
assignment row. Public worker and HTTPS transports carry the same contract.

Portable/history category records retain optional kind. Old records are not expanded
or relabeled, preserving their history digests and financial totals. Legacy
uncategorized inflows retain funding compatibility and are reported separately from
explicit Income. Exact retries/undo/redo and restore use the existing durability
contracts. Older adapters must not reinterpret explicit Income as an envelope.
SQLite adds a nullable `categories.kind` column without rewriting old records.
On the first committed Income category, native authority format advances to at least 3 in the
same transaction; older SQLite adapters reject it before reads/writes. The fence
survives undo. Read-only readiness accepts formats 2/3/4 only with the current column.
Sanitized portable SQLite schema validation retains historical schema support;
older readers reject the new payload instead of dropping its financial type.

## Classification before review

WHAB 2.77 supports Expense, Income and Transfer child types (`kind` in public
records, `--type` in CLI). Missing kind remains Expense. Income and Transfer have
no expense target or assignment rows. Transfer is explicit account movement,
excluded from ordinary income/spending without changing account balances. It may
be classified before pairing; matching still requires equal opposite posted
amounts, different active accounts, same currency/budget scope and dates within
seven days. Matching remains separate and never reviews automatically.

A posted, nondeleted transaction needs a valid child of any type, or a previously
confirmed transfer pair, before explicit review. A bank hint alone is insufficient.
Assigning classification does not manufacture review. Clearing it makes unreviewed
in the same native operation. An explicit conflicting clear+review rejects the
whole operation. Mixed batches validate their final proposed state atomically.
Pending records cannot be reviewed. Undo/redo cannot restore a newly invalid review.

Old saved reviewed/unclassified flags and historical audit hashes remain readable
and unchanged on upgrade/read. `trnx review-audit` reports them; cleanup is a
separate, explicitly authorized public review operation with dry-run and audit.
New imports do not derive review from an unclassified source clearing flag; YNAB
clearing/flag information remains in its original memo metadata. Ordinary bank
updates retain untouched legacy invalid flags for that explicit cleanup, while
classification/pending transitions enforce the rule. No merchant inference, bank
synchronization or automatic production cleanup is introduced.

```sh
whab "My budget" category group create Finance
whab "My budget" category create Salary --parent Finance --type income --agent Codex
whab "My budget" category create Transfers --parent Finance --type transfer --agent Codex
whab "My budget" trnx categorize TX1 --category "Finance / Transfers" --agent Codex
whab "My budget" trnx review TX1 --reviewed --dry-run --agent Codex
whab "My budget" trnx review TX1 --reviewed --agent Codex
whab "My budget" trnx review-audit --format table
whab "My budget" trnx review TX2 TX3 --needs-review --dry-run --agent Codex
whab "My budget" trnx review --help
whab "My budget" trnx review-audit --help
```

Review supports 1–100 explicit IDs, one observed revision/request ID/native commit
and durable save; no splitting or silent skipping. One invalid target rejects all.
`--batch` only groups history. Mixed classification+review uses `trnx edit-many`
JSON/files/stdin. Omitted review remains unchanged for an already valid classified
record; assigning a category to a legacy invalid reviewed row makes it unreviewed
unless review is explicitly requested in the same valid edit. Exact retries retain
the entire original intent, actor/batch, revision and request ID, returning the
original receipt without another change/history/save. Stale/uncertain results
never trigger an automatic fresh intent. JSON is complete; tables are readable.

Audit is read-only with inclusive `--since`/`--until` bounds, complete results or
an explicit existing 16 MiB size error. Narrow dates rather than truncating.
Native SQLite/JSON advertise optional `ClassifiedReview`; the public `review_policy`
query probes the authority. Unsupported storage/remote authorities fail before
classification/review writes. Financial rules remain in core; adapters retain
atomic persistence, annotations, original/import records and native receipts.

## Explicit Opening Balance classification

WHAB 2.79 adds optional `OpeningBalanceClassification` with read-only
`supports_opening_balances()`. The public `opening_balance_policy` query probes the
actual authority; unsupported backends fail before mutation. `kind=opening_balance`
is a technical category type, independent of its name or parent. Creating or
converting one requires a zero target and no monthly assignment rows, including
zero rows. Use the existing read-only category classification preview to inspect
monthly before/after consequences, then the existing guarded public operation.

Core reports `openingFunding` separately from ordinary income and spending. Only
posted opening entries on eligible on-budget cash accounts contribute funding;
credit/loan debt and off-budget accounts never create spendable cash. Existing
amount signs, currencies, balances, transaction identities, notes, source metadata,
reviews and provider mappings are preserved. An Opening Balance is never an
automatic transfer candidate. No bank synchronization or historical inference runs.

Existing zero-valued transactions can be classified and explicitly reviewed when
posted and otherwise eligible. General financial creation/edit validation still
rejects zero amounts. Classification never automatically reviews; clearing it
requires fresh review. Native single/bulk operations retain one guarded revision,
one durable receipt and exact before/after audit state. Exact retries after restart
return the original receipt without another change; guarded undo/redo and actual
saved-file restoration retain the classification and prior history hashes.

SQLite advances native authority format to **4** when Opening Balance appears
in current records or retained audit history. The fence never decreases after
later writes, undo, restore or local transfer; older native readers reject it
before opening the ledger with unsupported financial rules. No existing record
is rewritten and no new SQL category column is needed.

```sh
whab "My budget" category create "Starting Balances" --parent Finance --type opening_balance --agent Codex
whab "My budget" category classify "Finance / Starting Balances" --type opening_balance --dry-run --agent Codex
whab "My budget" trnx categorize TX1 --category "Finance / Starting Balances" --agent Codex
whab "My budget" trnx review TX1 --reviewed --agent Codex
```

The historical-reader regression uses genuine WHAB 2.78.0 / SQLite 1.27.0 / JSON
1.19.0 source from pinned commit `ec9d8d9338ed3e239eb093d9b07d97d219f24af6`.
It creates isolated fictional current-kind and history-only files, verifies old
writable/read-only readers refuse all eight cases, checks bytes and native evidence
stay unchanged, then reopens them successfully with current code. Run from
`whab/app` with full local Git history and the development environment:

```sh
.venv/bin/python plugins/storage/sqlite/scripts/check_opening_balance_compatibility.py
```

## Atomic transaction edits and creations

WHAB 2.78 adds optional `AtomicTransactionChanges` with read-only
`preview_transaction_changes(workspace, edits, creates, preserve_balances=False)`
and native `change_transactions` using the same arguments. This adapter advertises
`supports_atomic_transaction_changes()`. A combined batch contains 1–100 explicit
existing edits and new manual records, validated by core before any write. Creation
uses a preselected canonical UUID; reusing an existing or deleted identity is refused.
The public ledger supplies the observed revision, request ID and agent/batch audit
metadata. New records are manual/unreviewed; actual amount/date/account/merchant
corrections require fresh review. Omitted values, including merchant whitespace,
notes, classification and provider identity, remain exact. Matched transfer
financial/category corrections are refused until an explicit unmatch.

The complete preview/receipt includes every before/after record (a creation has
`before: null`), new stable IDs and every touched account's before/after balance.
One native transaction stores all records, exact reversible audit history and its
durable receipt; the host performs one synchronous durable checkpoint. An invalid
item or persistence failure preserves the whole prior state. Exact retries after
restart return the same receipt without a second creation or history entry. Guarded
undo removes all records created by the batch and restores every edited original;
redo restores the entire same change with the same IDs. Portable exports/restores
retain all transaction records, correction provenance and permanent history.

For splitting an existing receipt into ordinary transactions, explicitly request
`preserve_balances=True` (public JSON `preserveBalances: true`). Core requires zero
signed transaction amount delta for each account before retaining the saved account
balance snapshots exactly. This is especially necessary for imported records:
ordinary imported corrections retain a provider/import balance snapshot, while an
ordinary manual creation changes the account balance. Without this explicit option,
those established behaviors remain unchanged. A split stores no parent/child or
synthetic transfer links and creates no extra balancing transaction.

Imported corrections retain the original fields in the existing `original` metadata
and keep the original transaction ID/source. Later provider refreshes retain the
user's corrected fields while updating the underlying original provider evidence;
new manual rows are independent and are never matched or reconciled automatically.
Provider removal/pending replacement continues the existing import contract, so
inspect subsequent imported changes before another correction. No bank sync runs
as part of an edit, creation, preview, retry or audit reversal. Existing annotation
`edit_transactions` batches and their output shape remain compatible.
