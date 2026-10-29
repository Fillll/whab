# JSON storage plugin

`whab_plugin_json` is independently packaged local storage. It stores both
personal and fictional demo workspaces in one versioned JSON data file at
`<budget runtime>/data/whab-<environment>.json`. Sandbox and production use
different files. It uses no database driver or other storage implementation.
The host supplies financial commands, currency rules and workspace projections.

Install its wheel with a compatible host (WHAB 2.47+ within major 2), then select
`WHAB_STORAGE=json` for a new original runtime or initial CLI budget creation.
An opened budget chooses its backend through its manifest. Selecting JSON does
not migrate an existing ledger. JSON belongs to the supported bundled set;
installation does not select it. SQLite remains the original-runtime default.

## Commits, locking and recovery

Missing runtime/data directories are created one level at a time, with each new
entry's parent and the new directory flushed before file commits can succeed.
Existing directory permissions and intentional runtime-root symlinks are retained.
One stable sibling `.json.lock` file holds a nonblocking exclusive POSIX advisory
lock for the backend's lifetime. macOS and Linux are supported; platforms without
POSIX locking fail explicitly. The lock inode remains stable while the JSON data
file is atomically replaced. Competing cooperative writers are rejected.
Unexpected data-file or lock replacement, deletion, links and external edits stop
the writer. Preserve conflicting revisions before reopening; there is no automatic
merge or fallback. These cooperative guards are not a security sandbox.

Every mutation runs under the budget backend's lock against a detached candidate.
The complete candidate's types, references and bounds are validated before writing
a private temporary file, flushing it, atomically replacing the data file, flushing
the parent directory, and publishing the candidate in memory. Imports batch all
records into one commit. The native file records an authority epoch, increasing
generation and unique operation identity. `commit_evidence()` returns the public
`CommitEvidence` record: budget/environment and instance identities, authority epoch,
logical generation, last operation, role and complete-state digest. It exposes no
financial records or credentials. The diagnostic `commit_status()` reports the
last committed or uncertain operation/generation.

A failure before replacement leaves the previous committed generation authoritative.
A failure after attempting replacement raises `CommitUncertain`, fences subsequent
reads and writes, and exposes the attempted operation/generation through that error
and `commit_status()`. Close and reopen to inspect the durable identity before
retrying. Do not treat that financial change as rolled back or blindly repeat it.
Interrupted replacement leaves one complete old or new file; temporary files are
not authoritative. Host migration/copy journals and promotion fencing remain a
separate coordination requirement.

## Local authority and complete transfer

Native schema 2 added persistent instance identity, role, transition identity and
durable command receipts. Native schema 3 adds replica watermarks and prepared
promotion identity. Opening schema 1 or 2 upgrades through the same atomic commit
path, preserving its epoch, generation, financial records and existing identities.
Before changing the active file, the upgrader durably saves its exact private
bytes in a sibling `<active filename>.schema1-recovery` or `.schema2-recovery`
artifact with private file
permissions. It never overwrites that artifact: an existing different recovery
file stops the upgrade and preserves both originals. Disk-full or backup-flush
failure retains the original schema as authoritative. The recovery artifact is never selected
as active storage or included in portable exports; protect it like the live ledger.
An older JSON plugin must not open a newer native schema. Restore an
appropriate preserved artifact before downgrading; it represents the state
before upgrading and excludes later changes. Runtime keys remain outside both
files. Portable `budget.json` payloads retain their sanitized format 1.

Version-1 `storage.authority`, `storage.commands` and `transfer.local` capabilities
support host-coordinated same-device migration. A fresh destination must be created
as `staged`; import refuses active primary storage and saved destination data.
Complete transfer preserves both workspaces, deleted history and images, import
identity, exact encrypted tokens, provider associations, masks and cursors. Runtime
keys stay with the host. This protected snapshot is never a portable export.

Role changes compare the complete expected evidence under the writer lock. Staged,
copy, fenced and retired instances reject every ordinary mutation; reopening cannot
reset that role. Activation requires the host's matching transition identity and a
fresh authority epoch. Role-only changes retain logical generation and state digest.

`execute_operation()` commits a fixed data command, result receipt, generation and
financial/provider state together. Exact retries return the original result before
checking the old generation. Conflicting operation identities fail. At most 4,096
receipts are retained; an expired or unknown operation with stale evidence is
rejected instead of executed. The host owns migration decisions, copy promotion and
recovery orchestration through these optional public contracts.

The version-1 `storage.replica` contract updates a `copy` instance from a coherent
primary snapshot. `apply_replica()` commits both workspaces, protected provider
records, durable command receipts and its `ReplicaWatermark` in one replacement.
The copy retains its own instance identity and records the source instance, epoch,
generation and operation identity. The watermark also identifies the host-owned
namespace generation/digest; opaque plugin files remain the host's responsibility.
Exact replay is harmless after a missing response. Older financial or namespace
generations, a changed source/epoch and conflicting same-generation payloads fail
without replacing saved copy data. Reopening validates native copy data against
its persisted watermark. Copy failure does not change the primary's commit.

Promotion is explicit. `prepare_promotion()` compares complete copy evidence and
the exact watermark, then durably records the transition and blocks further copy
updates. A matching `abort_promotion()` removes that preparation without changing
data. The host must catch up, validate namespaces, fence the primary and durably
choose authority before `set_role()` activates the prepared copy with a fresh
epoch. Neither a copy nor a stale queued update can activate itself.

Serialization and durability are synchronous I/O. The host's existing blocking-I/O
execution path must call storage methods; do not call a full-file mutation directly
on an asynchronous UI event loop. Large ledgers require proportionally more memory
and disk work per mutation. This backend favors inspectable local persistence over
SQLite's indexed query and incremental-write performance.

## Data and portable files

The native file includes ledger history, allocations, funding, rollover, import
identities, validated receipt bytes encoded as base64, and protected provider records
with account associations/checkpoints. Providers supply encrypted token strings
through the existing privileged storage contract. Encryption keys remain in the
owning runtime configuration outside the JSON file; the backend neither creates
bank connections nor decrypts tokens. Other plugins retain host-managed namespaced
settings, models and opaque files; this backend does not take ownership of them.

The live runtime JSON file is private and **is not a portable export**. Use
`export_budget_files()` to produce the sanitized `budget.json` payload, which omits
encrypted tokens, connection IDs, masks and synchronization cursors. It preserves
financial tombstones, annotations, funding/rollover, import identity and receipts.
The exact payload schema is distinct from the native runtime schema; supplying a
copied live runtime file as a portable payload is rejected. Restoring a sanitized
payload marks external account snapshots stale and requires an empty personal
workspace. Existing-cache preparation validates the supplied file while retaining
committed live data and private connections. Legacy neutral `export_budget()` and
`restore_budget()` preserve financial records but do not carry receipt bytes or
provider state.

Native files are bounded to 64 MiB; portable payloads to 50 MiB. Limits include
base64 expansion. Individual receipts retain the shared 5 MiB limit and raster images the 25-megapixel limit. The public host/SDK validates JPEG, PNG and PDF receipts; existing WebP attachments remain readable and portable. PDF-bearing budgets require WHAB 2.45 or newer.
Native validation also uses the neutral snapshot limits: 1,000 accounts, 10,000
categories, 250,000 transactions and 250,000 records per planning collection.
For each currency, the cumulative absolute values across balances, targets,
transaction history, allocations and funding may total at most
9,007,199,254,740,991 minor units. This is a historical-volume bound even when
positive and negative transactions cancel. For example, 9,008 transactions of
1,000,000,000,000 minor units each exceed it despite alternating signs. SQLite
can retain some histories outside this snapshot bound; JSON refuses them before
committing. Migration must preview and block such unsupported data rather than
silently truncate it or change authority.
The parser rejects duplicate fields, excessive nesting, unsupported schemas,
nonfinite values, invalid monetary types, dangling/duplicate references and malformed
receipt encoding. Corrupted/truncated files fail explicitly and are preserved.
Portable payloads remain format 1 and never contain replica or authority metadata.

## Validation and measured limits

Owning tests use temporary homes and fictional records. They cover public conformance,
reopen, isolation, provider annotations/checkpoints, portable receipts, strict parsing,
competing writers, external changes, disk faults and interrupted commit boundaries.
The initial developer toolkit supplies additional public-contract suites; it does
not replace implementation-specific durability tests.

Run checks from `whab/app/`:

```sh
.venv/bin/python -m pytest plugins/storage/json/tests
.venv/bin/python scripts/check.py --quick
```

Run the complete repository check before submission. The synthetic benchmark at
`plugins/storage/json/scripts/benchmark.py` measures batch initialization and
full-file annotation commits without reading household records. One local macOS arm64
run on Python 3.14.8 (2026-10-23, five annotation edits per size) measured:

| Fictional transactions | Native bytes | Batch import | Median edit | Maximum edit | Reopen |
| --- | --- | --- | --- | --- | --- |
| 100 | 39,740 | 5.37 ms | 4.53 ms | 4.88 ms | 3.18 ms |
| 1,000 | 376,377 | 35.01 ms | 31.33 ms | 32.37 ms | 22.09 ms |
| 10,000 | 3,758,604 | 325.20 ms | 294.79 ms | 424.24 ms | 202.44 ms |

Every batch committed once. These observations depend on hardware, filesystem
and receipt sizes; they are not a latency guarantee. A 10,000-row annotation edit
already rewrites about 3.8 MB. Prefer SQLite for larger or frequently edited ledgers,
and benchmark the intended storage before choosing full-file JSON for that workload.
Reproduce the measurement with:

```sh
.venv/bin/python plugins/storage/json/scripts/benchmark.py --counts 100 1000 10000 --repeats 5
```


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

The optional `edit_transaction_details(workspace, transaction_id, changes)` capability commits core-validated date, payee, amount, same-currency account and annotation edits atomically. Manual entries adjust balances together; imported corrections retain their provider balances and original edited fields. Provider refreshes preserve those overrides through posting replacements. Optional `original` records survive native reopen, portable export and copies. Existing records without corrections retain their earlier format.
