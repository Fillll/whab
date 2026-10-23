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
General budget binders, saved-state upgrades, financial storage and legacy provider factories
remain outside these stateless adapters. Explicit `BankAdapter` providers use the
separate [bank worker lifecycle](plugin-workers.md#bank-provider-workers): active
requests/background synchronization drain, pending/uncertain connection flows
defer replacement, and native writes retain public atomic command receipts. A zero-argument
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

Failed resource closes retain their private owner. Runtime status counts both
ongoing cleanup and retained failures in `cleanup_pending`, and reports completed
failed attempts in `cleanup_failed`; diagnostic messages contain no plugin data.
An explicit local `POST /api/plugins/runtime/cleanup` with the strict empty JSON
body `{}` makes one bounded retry of each retained failed owner. Re-read status:
HTTP 200 confirms the retry finished, while remaining failures stay observable.
Slow closes continue without cancellation. Demo requests are refused and the
normal HTTPS, Host, origin and authorization guards apply even while admission is
gated. Cleanup never repeats a financial mutation, decides transition authority,
or clears a native financial recovery gate. Host shutdown retries retained owners;
historical diagnostics may remain after cleanup succeeds.

Unrelated live changes preserve the existing ledger revision, retry receipts, bank objects,
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

Installed pure categorizers run through a bounded child engine adapter and the
host-owned complete namespace effect journal. `code_upgrade_families` includes
`categorization`; approved immutable code upgrades use the [worker upgrade
control](plugin-workers.md#categorization-workers). Model objects and training
iterators remain child-local, while the host owns suggestion/financial rules and
atomic model namespace decisions. Startup materializes and verifies retained
namespace decisions before exposing workers or copy observers. Ordinary saved
selections still apply on the next startup. Legacy descriptors without an explicit
compatible live contract retain restart behavior.
