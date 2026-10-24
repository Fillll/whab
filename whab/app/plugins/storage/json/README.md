# JSON storage plugin

`whab_plugin_json` is independently packaged local storage. It stores both
personal and fictional demo workspaces in one versioned JSON data file at
`<budget runtime>/data/whab-<environment>.json`. Sandbox and production use
different files. It uses no database driver or other storage implementation.
The host supplies financial commands, currency rules and workspace projections.

Install its wheel with a compatible host (WHAB 2.23+ within major 2), then select
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
generation and unique operation identity. `commit_evidence()` exposes only those
identities and commit status, never financial records or credentials.

A failure before replacement leaves the previous committed generation authoritative.
A failure after attempting replacement raises `CommitUncertain`, fences subsequent
reads and writes, and exposes the attempted operation/generation through that error
and `commit_evidence()`. Close and reopen to inspect the durable identity before
retrying. Do not treat that financial change as rolled back or blindly repeat it.
Interrupted replacement leaves one complete old or new file; temporary files are
not authoritative. Host migration/copy journals and promotion fencing remain a
separate coordination requirement.

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
base64 expansion. Individual receipts retain the shared 5 MiB/25-megapixel limits.
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
No schema upgrade is currently needed or advertised beyond native/payload version 1.

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
