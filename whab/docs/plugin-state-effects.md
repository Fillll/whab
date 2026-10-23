# Private plugin effects and bank continuations

These host components support stateful worker adapters. The [categorization
worker](plugin-workers.md#categorization-workers) uses complete namespace effects;
bank continuations remain a foundation pending their provider/route integration.
Runtime leases, drain, provider transport, financial validation and authorization
still own admission and activation. No real provider is contacted by their tests.

## Namespace decisions

`whab.plugins.namespace_effects.NamespaceJournal` commits one complete detached
namespace and a result receipt together. `NamespaceSnapshot(state, files)` contains
portable JSON state and every own data file, including unknown files and separate
`demo/` files. Settings remain read-only configuration from `BudgetContext`; an
effect cannot replace `settings.json` or `state.json` through file callbacks.

`NamespaceScope` binds budget, environment and plugin. Each `NamespaceOperation`
has a stable operation ID, name, bounded input bytes, expected namespace generation
and expected content digest. Its fingerprint binds the input and precondition.
`lookup(operation)` must run before dispatching work. A matching retained receipt
returns the original result before checking the current generation. An ID reused
for different input, or an unknown/pruned ID with stale preconditions, refuses.
Successful operations advance the namespace generation even if file bytes did not
change. The last 256 receipts are retained; clients must never create a new ID
automatically to retry an unknown operation.

The authoritative private `namespace-effects.json` contains the full namespace,
generation, before/after digests, input fingerprints and result receipts. A stable
separate writer lock remains valid across replacements. Commits validate a
detached candidate, flush a private temporary file, replace atomically, then flush
the directory. New ancestor entries are also flushed. Existing permissions are
preserved; journal and lock files must be private regular files. Competing writers,
external changes, corruption and unsupported versions refuse without resetting or
overwriting data. A publication error after replacement begins fences the handle
until `recover` validates and flushes the observed decision. Cancellation retains
its exception identity while recording the same uncertainty fence.

The private record binds its stable lock's nonce and native device/inode identity.
An existing journal never recreates a missing lock. Copying old lock bytes onto a
new inode cannot admit a second writer, including against the initial revision.
Retained handles recheck the lock identity and original journal immediately before
replacement and validate the published decision before acknowledgement. Missing
or replaced locks require a separately authorized stopped-host recovery control;
this foundation refuses activation and preserves the files instead of rebinding
or resetting automatically. Do not copy these private journals as namespace data.

The journal contains private model/state payloads. It is distinct from the
payload-free storage-authority journal and must not enter portable containers,
catalogs, diagnostics or worker IPC. It contains no encryption keys. Limits are
1,000 files and 50 MiB for state plus files, 1 MiB JSON state, 8 MiB operation input,
64 KiB per result, 256 receipts and a 100 MiB encoded journal. Unsupported volumes
fail explicitly. This initial foundation uses a complete-file rewrite.

## Materialization and activation

Existing `BudgetContext` callbacks save individual state/files. Those callbacks
cannot make a multi-file effect atomic. The journal therefore remains authoritative
while `materialize(context, read_snapshot)` reconciles every file and complete
state into native callbacks and verifies the final content digest. The original
operation/result receipt remains unchanged. An interrupted materialization resumes
from the same receipt; it never runs training or another plugin command again.

The host must hold its budget mutation lock and admission barrier for commit and
materialization, including copy/namespace observers. It must suppress intermediate
copy publication and publish the copy's complete namespace generation only after
materialization succeeds. On startup or candidate activation, call
`recover(context, read_snapshot)` before exposing any native namespace reader.
`read_snapshot` is a host-owned function returning a complete `NamespaceSnapshot`
through the public context/file snapshot facilities. The journal does not inspect
storage implementation internals. `materialized` also requires verification in
the current process; a persisted marker alone does not open admission.

`NamespaceDraft(base_context, snapshot, writable=False)` supplies revocable public
callbacks for candidate readiness. A command may use `writable=True` to collect a
detached output; none of those callbacks changes native data. Closing the draft
revokes every retained callback. Workers receive only an encoded namespace and
operation identity; host callbacks and journal handles stay in the host.

## Bank continuation ownership

`whab.plugins.bank_continuations.BankContinuations` owns an opaque random ID for
each Link/update flow. `ContinuationScope` binds budget, environment, provider and
runtime generation. Status contains only that opaque ID, generation, flow kind,
stage and deadline. A stale generation cannot finish another generation's flow.
The host must route a previously admitted continuation to its owning generation;
it must never substitute a new connection when an ID is missing or expired.

Every persisted link/public/access/provider secret, including captured results,
uses mandatory host `seal(bytes, aad)` and `open(ciphertext, aad)` callbacks. These
must implement authenticated encryption with the supplied budget/environment/
provider/generation/flow/stage AAD. Keys remain in runtime configuration outside
the journal. Reopening authenticates every retained ciphertext before admitting
external work. No decrypted secret is retained in status or diagnostic records.
Limits are 128 flows, 64 KiB plaintext per field, at most 4 KiB encryption overhead,
one-hour preexchange deadlines and a 40 MiB private encoded journal. Only terminal
records may be pruned; missing old IDs refuse and do not create replacement flows.

`exchange(scope, id, operation_id, request, send)` writes and flushes an encrypted
request plus durable intent **before** invoking the host-owned `send` callback.
After HTTP returns, the encrypted response is durably captured before any native
storage apply. An exception, cancellation or process death after intent but before
capture becomes `exchange_uncertain`, even when the process may have died before
HTTP began. Recovery never exchanges again. Repeating an operation inspects its
existing status; changing its input or identity refuses.

The separate `apply` step requires a stable native command fingerprint and
host-owned lookup/commit callbacks. `NativeApplyEvidence` binds the command ID and
fingerprint. A committed native receipt reconciles a lost acknowledgement without
another native write. Missing receipt alone is `unknown`, never proof of rollback.
`not_committed` requires durable native generation/CAS evidence for the exact
command. Only that proof permits a native apply. Native effects must use an atomic
command-plus-receipt backend; arbitrary legacy save callbacks do not satisfy this
contract. The response stays captured for recovery until native outcome resolves.

`prepare_close()` blocks provider disable/replacement while any generation owns
unfinished or uncertain work. Nonirreversible ready flows may be cancelled or
expire using the controlled clock. Deadlines never clear uncertain exchanges or
saves. `reconcile_exchange` is an explicit host control requiring independent
provider verification of either a captured response or no external effect; a
timeout is insufficient evidence. It performs no HTTP. Host shutdown may close
the durable journal once local calls finish, preserving pending records for the
next open. Shutdown is separate from the lifecycle's `prepare_close` guard.

These are trusted-code containment contracts. They do not restrict a malicious
Python plugin's operating-system access and do not constitute a security sandbox.
