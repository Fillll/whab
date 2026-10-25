# __PLUGIN_ID__ local storage

This independent POSIX storage example requires the genuine WHAB 2.41 public
`whab.plugins.storage_model` SDK and toolkit 0.10 for its author tests. It imports
no bundled backend. Monetary rules come from that shared public pure model; one
FULL-synchronous SQLite transaction publishes a detached complete native state,
commit evidence and the caller's durable operation receipt together.

Install the built host, toolkit and candidate wheels in a clean environment, then
run `python -m pytest tests`. Build the project wheel and approve its complete
local dependency closure before running:

```sh
whab-plugin check --plugin __PLUGIN_ID__ --combinations 10 --seed 42 --runtime --wheelhouse /approved/local/wheels --output /tmp/__PLUGIN_ID__-check
whab-plugin replay /tmp/__PLUGIN_ID__-check/configuration-00.json --wheelhouse /approved/local/wheels
```

The storage candidate is the sole required primary. The SQLite reference is
executed independently for conformance and never co-selected as a second writer.
Format-4 `native-local-storage-v1` manifests bind exact installed artifacts,
ordered peer selections and public faults. Real CA-verified fictional HTTPS
checks activate the exact approved storage wheel, retain the required primary
through peer transitions and reject a bad candidate hash. They acknowledge writes
before activation, through the active worker and after reopening, then verify all
three writes and the exact worker after another restart. One sampled fault uses
SIGKILL after an acknowledged worker write. Direct
public readiness tests separately prove unchanged stopped read-only bytes and
exact native receipt replay. An ordinary worker activation uses the same approved
artifact; this is not a distinct-version upgrade claim.

This adapter advertises mandatory ledger/provider behavior, receipts, neutral
payload export/preparation, native authority, durable commands and storage worker
readiness. It deliberately omits same-device transfer and replicas; those profile
steps report not applicable. No provider connection, network fetch, private
runtime, OS trust change or encryption key is used by the tests. Provider records
arrive already encrypted from the host; portable exports omit those records.

Native schema version 1 stores both workspaces, private encrypted records,
generation/authority evidence and at most 4,096 operation receipts. Its file and
writer lease names include the validated plugin ID. All writes require primary
role; exact receipt retry precedes an old expected-generation comparison. Native
identity, active logical schema, unexpected row changes or replaced paths fence
the handle. Unknown outcomes require deliberate close/reopen and exact receipt
reconciliation, never a blind retry or a new empty ledger.

The own-file payload writer uses version 2 and retains non-secret import identity
and counts, deleted transaction history and receipt bytes. The exact version-1
reader remains available with its original absent-import-identity semantics;
unknown fields/versions refuse. Fresh-cache restore and preparation preserve
saved import retries, while an existing authoritative cache retains later
acknowledged edits. Active receipt APIs hide deleted attachments. Neutral restore
marks external accounts stale and does not recreate provider connections.

This instructional adapter supports POSIX only: it synchronizes every freshly
created directory ancestor before acknowledging state and refuses unsupported
platforms before creating files. The package declares that restriction; toolkit
platform applicability must report unsupported runs as not applicable rather than
counting native passes. A process killed after creating the initial native file
but before schema/state commit has acknowledged no financial state. Reopen
preserves and refuses that incomplete file; it never resets or silently adopts it.

The host owns TLS/authentication, budget identity, financial validation, request
revision/retry, encryption keys, admission and authority. Worker code remains
trusted code; the process boundary is containment, not a security sandbox.
