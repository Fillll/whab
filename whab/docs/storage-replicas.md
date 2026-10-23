# Local storage copies and explicit promotion

A budget has one authoritative native primary. WHAB can keep a synchronized JSON
copy of a SQLite primary, or a SQLite copy of a JSON primary. Copies reject every
public financial mutation, including provider checkpoints and receipt changes.
Existing `.whab` file destinations remain separate portable copies.

## Management API

These routes use the budget's ordinary HTTPS, Host/origin and authorization guards.
Named budgets prefix them with `/budgets/<budget-id>`. Select the personal workspace
before changing copies; demo cannot attach or promote a primary.

1. `POST /api/storage/copies/preview` with `{"destination":"json"}` (or `"sqlite"`)
   prepares an isolated empty native copy. The response contains `preview_id`.
2. `POST /api/storage/copies/transition` with `{"preview_id":"<preview-id>"}` explicitly
   attaches that preview and returns HTTP 202 with its copy ID.
3. `GET /api/storage/copies` reports the primary identity/generation, copy
   watermarks, attributable failures and promotion progress. `saved` means the
   observed primary and namespace generations match a validated native watermark;
   `pending` or `failed` does not undo a successful primary save.
4. `POST /api/storage/copies/<copy-id>/retry` with `{}` requests reconciliation.
5. `POST /api/storage/copies/<copy-id>/promote` with `{}` explicitly schedules
   catch-up, validation and authority handover. Follow its operation ticket in
   `GET /api/storage/copies`. A queued response is not a completed promotion.
6. `POST /api/storage/copies/<copy-id>/disable` with `{}` stops scheduling that
   attachment while retaining its native data and immutable namespace artifacts.

Up to eight retained copy attachments are supported per budget. Preview creates a
new isolated destination; it cannot overwrite a populated backend. A changed
source instance or authority epoch reports `reattach_required` and requires an
explicit new attachment. Old native
instances and their namespace artifacts remain preserved. Promotion does not turn
the retired primary into a new copy automatically.

## Persistence and failure behavior

`whab.plugins` exports `StorageReplica` and immutable `ReplicaWatermark`. A backend
declares `storage.replica` v1 only when it atomically commits the complete local
financial/protected snapshot, durable command receipts and watermark. The
watermark binds attachment ID, source instance and authority epoch, financial
generation/last operation, state and transfer hashes, and namespace generation/hash.

```python
current = copy_backend.commit_evidence()
watermark = copy_backend.apply_replica(
    replica_id, primary.export_local(), namespace_digest, current,
    namespace_generation=namespace_generation,
)
```

`apply_replica` is a host-private control on role `copy`. It does not bypass native
user-write fences. An exact repeated source generation and namespace generation
returns its durable watermark before checking stale expected evidence. Changed
content for the same generation pair fails. Either generation moving backward
fails. A source/epoch mismatch also fails; timestamps do not select authority.

Financial generation **G** and host namespace generation **N** are independent.
Models, settings and opaque plugin files can change without a financial mutation.
The host captures both under the tracked storage/session lock, writes an immutable
private namespace artifact and durable N index, then applies the native copy. Only
exact generated storage export paths are excluded from the namespace artifact;
disabled and unknown plugin records/files remain opaque. Artifacts can contain
private model data and are not public portable exports. Encryption keys remain in
runtime configuration; provider ciphertext/checkpoints use the protected local
transfer contract, without creating another bank connection.

Namespace capture bounds file count and cumulative encoded size before reading or
expanding the complete tree. It accepts at most 1,000 files within the 50 MiB
artifact bound, with a bounded filesystem traversal. Unsupported larger namespaces
block copying without deleting their source files or failing the primary save.

Post-commit notifications schedule copy work without affecting the primary result.
Work is serialized and coalesced. A notification arriving during capture schedules
another pass. Periodic reconciliation detects non-session namespace changes and
retries failed work. At startup WHAB compares durable primary G and current
namespace content with native copy watermarks, discovering a primary commit or
namespace save that happened before its scheduling notification. Queue contents
are never commit evidence. A stable advisory ownership lock prevents competing
copy coordinators from replacing control metadata concurrently.

## Promotion and recovery

Promotion gates and drains the affected budget, catches the copy up to every
acknowledged primary change, validates its namespace artifact and native content,
and calls `prepare_promotion(transition_id, expected_evidence, watermark)`. That
durable preparation blocks further copy updates. A matching
`abort_promotion(transition_id, expected_evidence)` returns an uncommitted candidate
to copy service without changing its financial generation.

The coordinator then fences the source, durably commits the authority decision,
activates the copy with a fresh epoch, retargets stable service handles and resumes
requests. It retains the ledger service, retry/revision state, bank services,
workers, plugin namespaces, runtime keys and preserved original container. Other
budgets remain available. The retired source remains fenced against user writes.

The authority journal uses explicit format version 2 and `kind` values `migration`
or `promotion`; the exact version 1 reader upgrades migration metadata in memory.
Journal/configuration records contain identities, generations and validation hashes,
without financial payloads, provider ciphertext or encryption keys. Native copy
watermarks are committed with copy data rather than in this journal.

Before the durable decision, recovery resumes the source and aborts any matching
promotion preparation. After it, recovery completes destination activation and
never reverts later destination writes. An uncertain in-process transition stays
gated until `POST /api/storage/recover` with `{}` validates the durable side.
Missing or changed namespace artifacts block unsafe promotion. Invalid copy
configuration is reported separately while the primary remains available.
Status reports unsupported local-authority controls explicitly for remote, legacy
or injected backends. Its primary metadata includes the bound native role, runtime
health and admission state; a gated uncertain or fenced handle is not reported as
an available authoritative primary.

These are trusted local plugin contracts, not a process or filesystem security
sandbox. See [storage authority](storage-authority.md) for complete transfer,
key-reference binding and journal integration details.
