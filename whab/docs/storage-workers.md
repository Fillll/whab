# Local storage workers and compatible code replacement

WHAB 2.37, SQLite 1.12 and JSON 1.3 provide the explicit version-1 `storage.worker` contract, alongside
`storage.authority` and `storage.commands`. Their compatible code can run in a
replaceable child while the host retains the HTTPS listener, authorization,
budget identity, financial validation, LedgerService revision/retry state and
stable storage handle. Workers remain trusted Python code with the user's OS
access; this is failure containment, not a security sandbox.

The client storage plugin is not supported by this adapter. Its remote authority,
HTTP authentication and durable remote request binding need a host-managed
transport. Unsupported storage reports an explicit lifecycle limitation. Disabling
a required storage provider is refused; changing backend still uses the explicit
[migration](storage-authority.md) or [copy/promotion](storage-replicas.md) controls.

## Approved code activation

Supply the complete reviewed local wheel closure, including the exact current
host wheel and dependencies, to the existing authenticated local
`POST /api/plugins/management/upgrade` control. Use the selected storage plugin ID
and one absolute path/full SHA-256 per wheel, as in the
[optional worker instructions](plugin-workers.md#artifacts-and-environments).
HTTP 202 schedules an owned operation. Poll `GET /api/plugins/runtime` for its
completion; `GET /api/storage/status` includes the `code` selection and runtime
health. Scheduling is not a successful handover.

Preparation installs offline into a new immutable environment, negotiates the
storage protocol/SDK/current host/contract, and opens only a read-only candidate.
It validates complete public ledger/history and protected records against native
evidence. After gating admission and draining requests/background work, readiness
runs again on the latest data. Only then does a flushed code-selection journal
choose the approved environment. The host releases the drained writer and opens
the selected candidate, verifies unchanged native evidence, retargets the stable
handle, resumes admission and retires old resources. JSON retains its single
writer lock until that controlled release.

A code-only replacement preserves native instance ID, authority epoch, logical
financial generation, both workspaces, receipts, encrypted provider records and
host namespaces. It does not change backend selection or copy authority. Failed
preparation leaves active code/data unchanged. Any uncertain activation stays
gated until the durable code decision is reconciled; after candidate selection,
recovery cannot silently choose earlier host code. Other budgets remain available.

Startup validates and restores the exact approved environment before invoking a
native factory. A missing/replaced native instance, incompatible schema, changed
environment or host-version mismatch stops this budget and preserves its files.
There is no legacy fallback. Native schema upgrades require a compatible explicit
reader/upgrader; a read-only candidate never adds native metadata, creates a new
ledger or adopts an old schema. JSON read-only candidates acquire no writer lock.
SQLite reads an active writer's existing WAL; a clean stopped snapshot uses an
immutable read and refuses observed changes, without creating WAL/shared-memory
sidecars. [SQLite's immutable URI contract](https://www.sqlite.org/uri.html#uriimmutable)
disables native locking/change detection, so this adapter checks the original
file identity and absence of a new WAL around each snapshot. Readiness is not
permission for an external concurrent writer.

## Financial uncertainty and exact retries

The private storage channel has its own protocol version 1 and `WHST` framing.
It supports a fixed method/argument allowlist and validated public durable records;
it carries no callable, router, Python backend handle or pickle. Frames allow at
most 64 MiB canonical metadata and 256 MiB raw binary data, with bounded nesting,
node count and exact binary offset/size/hash checks. Startup and each RPC are
bounded; complete readiness has a 60-second budget. A ledger exceeding a worker
transport bound is refused before code selection. Native JSON, portable, receipt
and public transfer limits remain independently enforced.

All ordinary financial/provider/receipt/import writes become validated native
`StorageOperation` commands. The host durably records operation identity,
fingerprint, precondition evidence and optional caller revision binding **before**
dispatch. The native backend atomically saves the data and result receipt. The
host acknowledges only a matching durable receipt and evidence. Exact caller
replay precedes a stale revision check and returns that original result/revision;
it does not repeat financial work or rewind the current LedgerService revision.

The intent journal contains no financial payload, credential or result. Native
receipts retain results under their native privacy rules. Host request bindings
retain at most 1,024 completed requests; native receipts retain at most 4,096.
An expired/missing receipt never authorizes replay with stale native evidence.

A timeout, child crash, malformed mutation reply or failed host publication fences
the affected budget. It does not imply rollback or automatically terminate and
repeat the operation. Explicit `POST /api/plugins/management/recover` with
`{"authority":"active"}` drains admitted work, reaps the owned child, reopens
its exact approved code and reconciles native evidence. A matching receipt proves
commit. Only the exact unchanged pre-dispatch evidence, after the old child is
fully quiescent and the sole writer is reopened, proves noncommit. Anything else
remains unknown. The control never resends the original operation. Code-decision
uncertainty uses the same route with the authority consistent with that durable
selection. A storage recovery cannot resume another capability's uncertainty.
Migration owns a lost source-fence response separately: while its exact transition
remains gated, it quiesces the original child and reopens the same approved code
with the recorded native UUID. Only a role difference from the recorded complete
source evidence is accepted before the authority journal resolves the decision.
Pending financial intents prevent this control recovery. Migration preview also
binds subsequent destination opens to the UUID that the user reviewed; a missing
native destination is refused before a writable constructor can recreate it.

A native read-transport failure during an authority commit can independently fence
the financial worker while the authority decision remains uncertain. This combined
case deliberately requires a host restart: storage status reports
`recovery.mode=restart_required` and `combined_storage_recovery`, and the in-process
authority recovery control refuses to clear either owner's gate. Stop the owning
host, preserve both native instances and restart it. Startup validates the retained
authority journal before choosing a writable primary; acknowledged source and
destination writes remain subject to their original native evidence. Normal
compatible code handovers and isolated lost fence replies retain their live
recovery controls.

Each writable storage child also retains a stable host-private writer journal in
its explicit native home. A second writable child refuses before opening either
backend while that owner remains alive, including after its host parent crashes.
This closes the gap between SQLite's per-transaction locks and proof that an older
dispatch can no longer commit. Owner integrity is checked around RPCs. Read-only
probes do not acquire this writer journal. Stopped-host repair retains an existing
writer lease without changing its bytes, and refuses a live writable child.

## Stopped-host repair after an application upgrade

Prepared environments are bound to the exact host version. A newer host refuses
the saved old interpreter before execution, even if the plugin requirement range
allows both. The following explicit control replaces only its code-selection
metadata. Stop the owning host, or hold the complete budget gate/drain and release
its controller owner first. No financial/background/native work may remain. This
is a precondition of the selected repair action; the helper does not stop another
host or close its live owner. Retained code and native writer locks refuse
concurrent use. Startup rechecks the exact captured code decision while retaining
its owner across the read-only proof and writable open. Once the controller owns
that decision, later factories borrow its retained owner; a retired factory cannot
reopen the journal to create another writer.

From the current host's Python environment, read the exact selection revision:

```python
from pathlib import Path
from whab.budgets.storage_upgrade import storage_worker_revision
revision = storage_worker_revision(Path('/absolute/runtime-home'),
                                   'default', 'sandbox', 'json')
```

Approve a complete closure of current-host wheels and construct the explicit
native context. For secondary budgets supply its exact `BudgetContext` identity;
when authority metadata selects an existing instance, also pass that canonical
UUID as `expected_instance_id`. The native home can differ from the runtime home
after migration; use the home selected by the existing authority metadata. Do not
infer another native home, change keys, or choose another backend to bypass a
failure.

```python
from whab.budgets.storage_upgrade import replace_saved_storage_worker
from whab.plugins import StorageContext
from whab.plugins.environments import ApprovedWheel
approvals = tuple(ApprovedWheel(Path(row['path']), row['sha256'])
                  for row in reviewed_closure)
context = StorageContext(Path('/absolute/selected-native-home'), 'sandbox', {})
prepared = replace_saved_storage_worker(
    Path('/absolute/runtime-home'), 'json', approvals, context,
    expected_revision=revision,
)
```

The helper never executes the old environment or opens a writable native backend.
It binds the new read-only reader to the saved native budget/environment, instance,
authority epoch and minimum generation, requires exact financial evidence at the
same generation, and checks complete supported reads before publication. It can
read any retained native role, preserving that role. A different native instance
or epoch requires the host's validated authority journal and matching registered
native home; an arbitrary requested UUID or path is insufficient. The explicit
source/destination evidence supplies the required minimum generation and digest.
Code repair does not promote or un-fence either side or change that authority
journal. Newer unreadable schemas are preserved and refused without reset or
fallback.

For an interrupted migration/promotion spanning an application upgrade, repair
each retained old-host storage code selection needed to read its prescribed
source/destination. Startup then resolves the existing authority journal first.
Only its resolved primary decision may update the code journal's native binding
after the prescribed source-unfence or destination-promotion step. Native intent
recovery remains separate; code-pending exact-evidence checks are not relaxed.

The exact previous payload-free selection envelope is durably archived under
`plugin-workers/storage/<plugin>/history/<revision>.json`. Selection body format 2
adds a durable repair receipt. The exact format-1 reader remains supported and
upgrades only during an authorized new metadata decision; unknown newer formats
are rejected. The journal retains its stable lock identity across both formats.
The replacement uses compare-and-swap against the inspected revision. If its reply
is lost, repeat the **same closure and original revision**: the retained repair
receipt plus exact archived envelope proves the same decision. A different
candidate or changed decision is a conflict.

All pending native intents, native operation receipts, native files and prior
immutable environments remain unchanged. A missing financial receipt remains
unknown; successful code repair does not clear that financial gate. Restart only
after repair succeeds, then use explicit native-evidence recovery as needed. Keep
runtime homes, histories and encryption keys private and outside version control.

Native child allocation is registered before process startup, including environment probes, read-only readiness checks and recovery replacements. A failed hello or cleanup retains the exact child and private journal owners until bounded explicit cleanup confirms exit. Code selection and native request locks are never released while a financial actor may still own work. Stopped-host repair retains its read-only probe and ownership leases across a failed reap; an explicit repair retry first finishes that cleanup.

Migration and copy promotion check participating banks after request/background drain and before their first authority decision. A captured unfinished continuation refuses the authority change with `continuation_busy`; the existing source and journal remain unchanged. A compatible code-only storage upgrade preserves native UUID, epoch and generation and retains those bank services.
