# Per-budget plugin runtime

WHAB 2.24 introduces a stable host-owned coordinator for each open budget.
The HTTPS listener, access guards, budget identity and ledger service remain
stable. Each request pins one immutable generation through its complete response,
including synchronous work dispatched to a thread. Default, addressed and
cookie-selected secondary budgets use the same admission rules.

`BudgetRuntime.handle(name)` resolves a service method inside the current lease;
it never caches a method from a retired generation. A background operation must
acquire `background()` independently. Callers must retain a lease for the entire
operation and must not retain a raw service after that scope ends. Asynchronous
managed calls remain owned by the host when their caller disconnects.

## Controlled handover

Transitions serialize per budget. They prepare and check the candidate before
gating admission, drain admitted operations, commit selection, activate the new
generation, resume requests, and retire unretained resources in reverse order.
An optional `finalize(preparation, candidate)` callback runs after draining and
before durable commit. It returns a `RuntimeCandidate` prepared from the latest
state using detached or read-only contexts. A failure there reopens the unchanged
active generation and closes candidate resources; it is not a committed handover.
Preparation must use detached/read-only contexts. Register acquired resources
with `RuntimePreparation.own()` immediately so failed startup also cleans up.
Blocking callbacks must explicitly use the thread pool.

A drain timeout leaves admitted work running and resumes the original generation;
it never assumes an interrupted financial operation rolled back. A commit error
has an uncertain outcome: the affected budget stays gated until an explicit
`recover()` decision validates active or candidate authority. The coordinator
provides this control contract; durable storage authority journals and user-facing
recovery actions are separate adapters. Slow retirement remains tracked after its
deadline. Status reports safe, attributed errors without raw exception text.

Other budgets have independent coordinators and remain available. A transition
cannot run inside the request lease it needs to drain. Management schedules an
owned transition outside that lease and returns an operation identifier.

## Live optional selection

The first integration supports already installed report/export plugins with an
explicit `PluginContract(lifecycle="live")`. They must have no binder, settings,
saved-state upgrade, or other capability. The maintained `example` 1.4.0 plugin
demonstrates this contract. Legacy plugins and other families retain an accurate
restart requirement until their adapters are implemented.

Explicit stateless branding and file-parser descriptors also support live
selection. Bank-logo settings actions have only their public cache authority;
file parsers receive upload bytes and return a validated `WorkspaceImport`.
General budget binders, saved-state upgrades, financial storage/provider factories
and categorization remain outside these adapters. A zero-argument
`branding_factory` can return independent artwork/settings hooks with the same
identity and effective contract; it receives no budget context or credentials.
The host resolves the selection before invoking factories during preparation.

Send `POST /api/plugins/management/transition` with the complete optional selection,
for example `{"plugins":["example"]}`. Successful scheduling returns HTTP 202.
Poll `GET /api/plugins/runtime` for operations, generation, admission, progress,
health and cleanup errors. Status remains available while admission is gated;
HTTPS, local Host/origin and authorization controls still apply. Demo-scoped
transition requests are rejected. The ordinary `PUT /api/plugins/management`
continues to configure the next startup.

Live changes preserve the existing ledger revision, retry receipts, bank objects,
storage and provider caches. Unknown/disabled optional namespaces remain in the
saved budget. Failed candidate resolution or preparation leaves the active report
selection unchanged. Persisting selection happens only after the affected budget
has drained. Code upgrades and stateful families require their own worker and
resource adapters; this API does not upgrade the running Python environment.

Tests use fictional temporary budgets, held-operation barriers, secondary-budget
dispatch, cancelled callers, failed preparation/commit/cleanup and retry identities.
They do not open external bank connections or update the household installation.

## Live categorization selection

The host supports enable/disable/replacement of an installed, independently
scoped categorizer declaring `PluginContract(lifecycle="live")`. One categorizer
may be active per budget. Its factory and methods are synchronous, receive only
its budget namespace, and may provide `close()` for owned resources. Candidate
factories receive a bounded detached read snapshot after active requests drain;
they cannot publish state or files before the durable selection decision. Their
host callbacks expire on failure or retirement.

Cooperative training continues across settings requests. An unfinished run
defers replacement with `continuation_busy` attributed to that plugin. Finish or
cancel it through the existing settings action before retrying. Unchanged engines
retain their training sessions during unrelated plugin transitions. The latest
completed model is visible to a replacement after draining, and saved models,
demo/personal separation, and disabled suggestion history survive re-enabling
and reopening. Failed candidate readiness preserves the active service and data.

Categorization selection currently executes in the host process. Runtime status
lists it in `host_only_families`; `code_upgrade_families` lists the families with
an available immutable worker upgrade adapter. Selecting a categorizer does not
upgrade or replace imported Python package files. Ordinary saved selections still
apply on the next startup.
