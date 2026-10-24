# Same-device storage authority contracts

WHAB supports explicit, previewed same-device SQLite/JSON migration through the
budget's storage management API. The SDK and coordinator contracts below also
allow other local adapters to implement complete transfer. [Native copies and
explicit promotion](storage-replicas.md) use the same authority decision. Existing
saved-file copies remain portable file destinations.

## Application controls

All controls use the existing HTTPS, Host/origin and authorization guards. Named
budgets use the same paths beneath `/budgets/<budget-id>`; the default budget uses
the paths directly. Select the personal workspace before requesting a transition.

1. `POST /api/storage/preview` with `{"destination":"json"}` (or `"sqlite"`)
   creates an isolated staged destination and validates complete transfer support.
   It returns a `preview_id`, source/destination identities and summary counts.
2. `POST /api/storage/transition` with `{"preview_id":"<preview-id>"}` explicitly
   starts that preview. The HTTP 202 response contains an operation ticket; the
   transition runs independently so this request can release its generation lease.
3. `GET /api/storage/status` reports the current backend, admission/transition
   progress, durable authority evidence and operation outcome. A queued ticket is
   not evidence that handover completed. Check its terminal state and authority.
4. If an uncertain native or journal write leaves `recovery_required`,
   `POST /api/storage/recover` with `{}` validates the durable decision and resumes
   the authoritative side. Status and recovery remain available while ordinary
   budget operations are gated.

Only an empty staged destination may be populated. Preview and execution reject
unsupported storage contracts and enabled legacy bank plugins that cannot declare
independent background leases. Bundled Plaid declares `lifecycle.background` v1
and holds a separate budget lease for queued synchronization, including work that
outlives its scheduling request. No migration creates a bank connection.

The application retains the same ledger service, revision/retry state, shared
lock and bank service objects. Their tracked storage handle resolves each call
against the selected backend after gate/drain. Other budgets remain available.
Restart resolves the authority journal before comparing the incoming saved
container's storage selection. Missing host plugin recovery files stop the affected
budget; reopening an older preserved container cannot erase newer plugin state.

## Public adapter surface

`whab.plugins` exports the optional `StorageAuthority`, `LocalTransferStorage`,
and `StorageCommands` protocols and their immutable records. These are additive
to `StorageBackend`. A backend advertises support only when every write path
implements the corresponding fence and atomicity requirements.

```python
from whab.plugins import StorageOperation
from whab.plugins.storage_transfer import canonical_storage_json

evidence = backend.commit_evidence()
request = StorageOperation(
    operation_id="stable-host-request-identity",
    authority_epoch=evidence.authority_epoch,
    expected_generation=evidence.generation,
    workspace="personal",
    name="assign",
    payload=canonical_storage_json({
        "categoryId": "groceries", "month": "2026-01", "assigned": 12500,
    }),
)
result = backend.execute_operation(request)
```

`StorageOperation` accepts a fixed method whitelist and exact top-level payload
fields. There are no method lookups supplied by executable code, callables,
pickles, or native database handles. `dispatch_storage_operation` converts the
versioned data envelope to existing public methods; those methods still apply
the financial core's validation. The adapter invokes dispatch inside its one
native state/evidence/result-receipt commit.

The backend checks a matching durable receipt before checking the request's old
expected generation. An exact retry returns the original result, including any
generated identifiers. Reusing an operation identity with a different fingerprint
fails. An absent or expired receipt is not proof of rollback: if the expected
epoch/generation is stale, the backend rejects the command without repeating it.
`operation_receipt(operation_id)` and `commit_evidence()` allow an owning worker
adapter to resolve lost responses after reopening. A host must retain the same
operation identity and canonical payload while resolving a lost response.

Receipts contain backend result data. HTTP revisions and transport response
envelopes remain host concerns; these contracts do not turn the existing
in-memory `LedgerService` response cache into durable retry storage automatically.
Legacy public methods remain compatible, but must enforce native role fences.

`CommitEvidence` binds budget, environment, persistent backend instance UUID,
authority epoch UUID, monotonic logical generation, last operation identity,
role, and logical content digest. UUID epochs compare for equality; their
lexical order has no authority meaning. Role-only control commits preserve the
logical generation and last operation identity. A new primary retains the
transferred generation and uses a fresh authority epoch before its next mutation.

`StorageContext.initial_role` defaults to `primary`; `staged` and `copy` are valid
for a newly created isolated native instance. Reopening reads the persisted role
and must not change it from factory configuration. Context budget identity and
environment are explicit; path names do not establish budget authority.

## Local payloads and ownership

`export_local()` returns `LocalTransferSnapshot`. Its financial body contains both
workspaces' complete neutral ledger history, image receipts including deleted
transaction images, and workspace import retry identities. Its protected body
contains encrypted provider records, connection identities, account associations,
masks, statuses and synchronization checkpoints. Receipt metadata travels with
the snapshot so a migration does not erase durable command retry evidence.

`encode_local_state` and `decode_local_state` perform canonical packing and strict
bounded validation. They preserve external account freshness during local
transfer. The protected body is exclusively same-device runtime material. It
does not belong in `.whab` portable exports, catalog metadata, diagnostics, or
management responses. Both body fields and receipt results are hidden from
record representations. No encryption key enters these payloads.

The protected association collection covers every account, including manual and
demo accounts with no connection. `itemId` and `mask` are independently nullable;
unattached masks are still retained. Missing account metadata, duplicate entries,
or non-null provider links in demo are rejected. This complete metadata also
participates in native commit evidence, so a missing mask cannot hide behind an
unchanged public financial digest.

The content digest hashes length-prefixed canonical financial bytes followed by
canonical protected bytes. It excludes authority and operation-receipt metadata
to avoid circular receipt evidence. The transfer digest additionally binds the
source evidence and canonical receipt metadata. Record order does not change
the canonical financial digest. Adapters may use `encode_local_state(...,
validate=False)` only to hash already-trusted native state that exceeds portable
transfer limits; constructing a transfer snapshot always validates fully.

`preview_local(snapshot)` is read-only and reports whether an isolated staged
destination is untouched and supports the complete snapshot. Untouched default
demo/templates may be replaced; saved custom data may not. `import_local` requires
a staged destination, preserves its own instance UUID, and adopts the source
epoch/generation/last operation identity. Retrying the identical transfer identity
and digest is idempotent. A different transfer cannot overwrite a populated
destination. Import commits both workspaces, protected data, images and receipts
atomically. It does not create provider connections or decrypt/re-encrypt tokens.

Host-managed plugin settings, models, state and files remain owned by their
existing budget namespaces. Disabled and unknown namespaces are retained opaquely.
They do not become storage-plugin-owned fields. Original source backend files and
the original saved container remain preserved. Runtime key files remain where
they are. The journal records a host-generated opaque UUID key reference. A separate
private host metadata record binds that reference to the configured runtime key
using a domain-separated hash; neither the key nor this private binding enters
the journal, portable transfer or management responses. A changed or missing key
blocks activation rather than replacing encrypted connection state.

A storage descriptor declares `storage_payload_paths`, the exact relative file
names emitted by its `export_budget_files` contract (`budget.sqlite` or
`budget.json` for the bundled adapters). The host overlays only those generated
paths when checkpointing or restoring a container. Other files beneath the same
storage plugin prefix remain opaque and participate in namespace validation.
Live migration requires this declaration; excluding an entire storage namespace
would hide loss of disabled or unknown plugin data.

## Host coordinator integration

`StorageAuthorityCoordinator` uses `BudgetRuntime.transition` for preparation,
gating, draining, activation and resource retirement. Its journal writer lock and
coordinator task cover the complete handover, including later journal cleanup.
Cancelling the initiating caller leaves owned work running. A drain deadline
does not cancel admitted financial work.

```python
from whab.budgets.storage_authority import (
    AuthorityJournal, NamespaceEvidence, StorageAuthorityCoordinator,
)

journal = AuthorityJournal(authority_path, budget_id, environment)
coordinator = StorageAuthorityCoordinator(runtime, journal, namespace_evidence)

# Both factories address the same isolated staged native instance. StorageContext
# binds the same budget/environment and initial_role="staged" on its first create.
task = runtime.schedule(coordinator.migrate(
    source_plugin_id, destination_plugin_id,
    prepare_destination, reopen_destination, build_candidate,
    flush=flush_budget_state,
    activate=activate_tracked_storage,
))
```

The callbacks have these responsibilities:

- `prepare_destination()` creates/opens an isolated staged backend. It must not
  rewrite the active backend or place several SQLite instances under a factory
  directory that selects an ambiguous native filename.
- `reopen_destination()` reopens that same instance after its import has closed.
  Reopen must enforce native schema compatibility and read persisted evidence.
- `build_candidate(active_generation, prepared_storage)` returns a service mapping
  or `RuntimeCandidate`; its `storage` entry must be the supplied stable handle.
  Every new storage consumer uses that handle. Unrelated service/lock/model state
  stays retained. The builder must not cache its underlying native backend.
- `namespace_evidence()` returns `NamespaceEvidence`: a digest covering all opaque
  plugin settings/state/files, the preserved original container digest, and the
  runtime key-reference UUID. It reads the current host-owned namespace without
  changing it. The coordinator runs this reader off the event loop.
- `flush_budget_state()` is an optional host hook called after drain. An async
  hook must offload blocking work. Its flush must not overwrite or relocate the
  preserved original container during migration.
- `activate_tracked_storage(prepared_storage, journal_record)` is an optional
  idempotent hook run while admission remains gated after destination authority
  commits. It retargets stable consumers, detaches checkpoint tracking from the
  preserved original container and updates required plugin selection. If it
  fails after the decision, recovery invokes it again for the same destination.

The host's barrier must cover requests, bank syncs, webhooks, imports,
categorization/training/model saves, checkpoints, exports and copies. A background
task holds `runtime.background()` for its whole operation. The migration request
returns an owned transition ticket; it must not wait for migration while retaining
the request lease that migration needs to drain.

The host must also detach the original source container from active checkpoint
write tracking and resolve the authority journal **before** enforcing incoming
container versus local-manifest storage-name equality. Otherwise a preserved
original SQLite container could override a committed JSON handover on restart.
The coordinator itself does not edit `BudgetSession`, routes, key configuration or
saved containers. Source cleanup ownership must avoid closing a backend twice
through both application shutdown and runtime-resource retirement.

## Durable decision and recovery

The stages are prepare, import, reopen/validate, fence source, commit journal
authority, activate destination, switch runtime handles, resume and retire source.
The durable journal decision is the linearization point. The journal stores only
identities, epochs/generations, stage, key reference and validation hashes. It
uses a stable writer lock, flushed temporary file, atomic replacement and directory
flushes. Its exact versioned schema rejects unknown or corrupt records.

Before the decision, source remains authoritative. `resolve_authority` verifies
the source and staged destination before undoing the matching source fence and
recording an aborted transition. After the decision, destination remains
authoritative even if the caller missed success. Recovery completes activation;
it never automatically reverts to source. Later destination generations are
retained. Original container and key-reference evidence remain fixed, while later
legitimate active plugin-state writes may change the namespace digest.

`coordinator.recover()` handles a still-running process after an uncertain commit.
At process startup, open only the journal's explicit source/destination instances,
keep admission gated, and call `resolve_authority(journal, source, destination,
namespace_evidence())`. It returns `source` or `destination` after validation.
Invalid metadata or evidence stops only the affected budget; no backend-name
fallback or automatic stale-copy promotion is permitted.

All mutation methods must reject staged, copy, fenced and retired instances,
including optimized transaction edits, bulk review, receipt operations and provider
checkpoint saves. Closing a connection or changing file permissions alone is not
a durable fence. These guarantees concern supported native adapters; trusted
Python plugins and legacy binaries are not a security sandbox.

Native parallel copies add monotonic financial and namespace watermarks, immutable
private namespace artifacts and explicit promotion through the same authority
decision. See [copy contracts and controls](storage-replicas.md). Hashing namespaces
only at migration time does not provide continuous replicas.
