# Approved optional-worker upgrades

WHAB 2.25 introduced explicitly live stateless reporting/export upgrades. The
current adapter also replaces branding, file-parser, declarative API interface,
explicitly scoped categorization and typed bank-provider code without restarting its listener.
WHAB 2.36 adds the separate visual adapter with coherent assets and browser participation.
WHAB 2.37 adds the separate [local storage adapter](storage-workers.md) for SQLite and JSON, with native durable receipts and same-authority code handover.
WHAB 2.38 adds the typed bank-provider adapter and host-owned durable continuation/native broker.
Other families require their own adapters. The parent
retains TLS, authorization, local-access guards, budget identity and financial
validation. These data protocols accept no financial mutation, backend handle,
router, callable or pickle.

API workers advertise `adapters.interface` as a strict `RemoteLedgerInterface`
record and answer only `interface.describe` for that capability. The parent
compares the readiness response with the startup declaration and requires an
exact current-host version. Visual workers advertise a separate strict
`adapters.ui` projection record; other workers keep it `null`. Private interface configuration and financial commands remain in
the host; the worker does not implement an HTTP router. See the
[API interface lifecycle](plugins.md#remote-interface-and-client).

## Artifacts and environments

The operator supplies reviewed local wheels and their complete SHA-256 hashes.
`inspect_wheel(ApprovedWheel(path, sha256))` reads bounded metadata without importing
plugin code. It validates identity, Python/tags, requirements, entry points and
all RECORD hashes. Preparation copies and rechecks those artifacts, validates an
explicit dependency closure containing the current host wheel and one selected
optional plugin, and installs offline into a content-addressed environment.
Runtime preparation performs no downloads and never upgrades active files.

From WHAB 2.40, the offline installer, installed-artifact verification,
readiness probes and active optional/native-storage workers share one explicit
platform, executable-path, temporary-directory and locale environment policy.
They receive no caller plugin settings, proxy settings, credential aliases or
Python/pip configuration, including custom setting names. The configured
password-variable alias is excluded even if it names a standard variable.
This limits accidental inheritance; it does not restrict trusted code's access
to the current user's files or network.

The initial installer supports macOS/Linux POSIX locks, the current Python/host,
wheel 1.0 purelib/platlib layouts and dependencies without extras. Unsupported
layouts or closures fail explicitly. A stable lock serializes preparation; a
flushed receipt publishes the environment only after installation verification
and an actual worker readiness probe. Verification detects missing, changed and
unexpected installed files before executing a reused environment. Interrupted
preparations can be discarded under their lock; published environments are never
repaired in place. Old versions remain on disk for inspection and explicit reuse.

Workers use isolated Python startup and disable bytecode writes. Plugin runtime
configuration reaches only its explicitly owned typed host adapter. Plugins remain trusted Python code
with the current user's operating-system access. Processes and versioned venvs
provide failure containment, **not a security sandbox**.

## Protocol and activation

Protocol 2 uses an explicit WHAB header, 8 MiB bounded JSON metadata and at most
50 MiB binary payload, strict primitive types, bounded nesting and exact message
shapes. Snapshots and report outputs also pass the public SDK validation. Startup
negotiates protocol, SDK, host, distribution/version, report capabilities and the
live lifecycle declaration. No arbitrary objects cross the boundary. Startup,
RPC and shutdown are bounded; crashes, malformed frames and hook errors produce
fixed plugin-attributed diagnostics. Owned process groups are cleaned up on
timeout, cancellation and shutdown. There is no automatic restart loop or retry
of a mutation with an unknown outcome.

Enable the supported optional plugin first. Then send an authenticated local
`POST /api/plugins/management/upgrade` request:

```json
{"pluginId":"example","wheels":[{"path":"/absolute/reviewed/plugin.whl","sha256":"<complete SHA-256>"}]}
```

Include every wheel in the approved host/plugin dependency closure, each with its
own hash. The abbreviated illustrative JSON is not a complete runnable closure.
The response is HTTP 202 with an operation ID. Poll `GET /api/plugins/runtime`;
do not infer completion from scheduling. The active `workers` list gives the
actual worker distribution, version, environment ID and health; installed host
package metadata may still identify the earlier version.

Preparation runs while the old generation remains available. Handover drains the
affected budget, durably replaces its device-local active-version reference,
activates the candidate and retires the old worker. A broken candidate preserves
the active version. Reopening validates and restores the saved version rather
than silently executing the host's older installed code. Disable/re-enable retains
the approved version and preserves optional plugin namespaces. Another host/SDK
version requires preparing a compatible environment for that application upgrade.

## Recovery and limits

An error during the activation decision leaves admission gated. Status and
`POST /api/plugins/management/recover` remain accessible through the existing
security guards. Supply `{"authority":"active"}` or `{"authority":"candidate"}`.
The host validates the saved selection and content-addressed version reference
before resuming; it refuses a decision inconsistent with the durable files.
When a response is lost after the candidate decision, recovery selects that
candidate and never silently reverts it. Closing the host retires both uncertain
generations without deciding authority.

Prepared environments have an exact host-version fence. This includes saved
protocol 1 report environments created by WHAB 2.25: a newer host refuses the old
environment before executing its interpreter, even when the plugin's declared
host range still includes the new version. It preserves the old reference and
environment and does not silently choose the host's installed plugin. Protocol 2
does not reinterpret protocol 1 messages.

To recover, **stop the host that owns this budget** and use its current Python
environment. `worker_reference_revision(home, plugin_id)` returns the 64-character
SHA-256 revision without loading the saved worker. Inspect that revision, approve
a complete current-host wheel closure, and pass the observed revision to
`replace_saved_worker`. The plugin wheel can be the same version when its host
requirement permits the new host. The stopped-host control is available without
a recovery HTTP endpoint; never invoke it against an admitted, running budget.

For example, put the reviewed closure in a local JSON array, with one
`{"path":"/absolute/reviewed/distribution.whl","sha256":"<complete lowercase hash>"}`
record per wheel. This file authorizes the complete installation, including the
current WHAB wheel and all runtime dependencies. Read the saved revision using:

```sh
/path/to/current/venv/bin/python - /absolute/runtime-home fictional-reports <<'PY'
import sys
from pathlib import Path
from whab.plugins.runtime import PluginError
from whab.plugins.worker_selection import worker_reference_revision
try:
    print(worker_reference_revision(Path(sys.argv[1]), sys.argv[2]))
except PluginError as error:
    print(error.code)
    raise SystemExit(1)
PY
```

Then run the replacement, supplying the exact revision that was inspected:

```sh
/path/to/current/venv/bin/python - /absolute/runtime-home fictional-reports /absolute/reviewed/closure.json OBSERVED_64_CHARACTER_SHA256 <<'PY'
import json
import sys
from pathlib import Path
from whab.plugins.environments import ApprovedWheel, EnvironmentPreparationError
from whab.plugins.runtime import PluginError
from whab.plugins.worker_selection import replace_saved_worker
try:
    with Path(sys.argv[3]).open("rb") as source:
        raw = source.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError()
    rows = json.loads(raw)
    if type(rows) is not list or not 1 <= len(rows) <= 128:
        raise ValueError()
    if any(type(row) is not dict or set(row) != {"path", "sha256"} for row in rows):
        raise ValueError()
    approvals = tuple(ApprovedWheel(Path(row["path"]), row["sha256"]) for row in rows)
    result = replace_saved_worker(Path(sys.argv[1]), sys.argv[2], approvals,
                                  expected_reference_sha256=sys.argv[4])
    print(json.dumps({"plugin": result.plugin_id, "version": result.version,
                      "environment": result.identifier}, sort_keys=True))
except (PluginError, EnvironmentPreparationError) as error:
    print(json.dumps({"code": error.code}))
    raise SystemExit(1)
except Exception:
    print('{"code":"invalid_replacement_request"}')
    raise SystemExit(1)
PY
```

The control prepares and probes the approved environment before changing the
selection, then compares the saved reference under a writer lock. It preserves
the exact previous reference bytes in
`plugin-workers/history/<plugin>/<revision>.json` before publishing the new one.
Those history files and all old immutable environments remain intact. A changed
reference refuses replacement. After an uncertain or lost publication response,
repeat the same closure and original expected revision: matching durable history
and the already-published environment return the same decision. Do not substitute
a new revision to bypass a conflict. Restart the host only after the control
succeeds; startup validates and restores the newly approved worker.

Branding workers receive bounded public institution/hostname text and opaque
artwork keys. They return validated raster images and plain settings/action
results. Cache directories belong to one budget and worker environment; candidate
preparation never flushes the active version's cache.

File-parser workers receive at most 10 MiB of upload bytes. They return only
bounded, validated `WorkspaceImport` data; parent validation and financial writes
remain unchanged. Preview responses include `parserIdentity`, which commits must
echo in `X-Import-Parser`. Retired or missing identities fail before reparsing;
raw file fingerprints and durable import operation IDs retain their meaning.
Requests already admitted finish on the old generation before activation.

`PluginResources` captures safe relative `templates/` and `static/` files as
immutable bytes: at most 256 files, 2 MiB per file and 4 MiB total, with UTF-8
templates. File import requires `templates/accounts.html` and `static/import.js`.
The wire bundle carries exact sizes and SHA-256 hashes, without archive extraction
or child filesystem paths. Versioned URLs keep each page attached to its matching
assets. Up to eight parser resource versions and 8 MiB per plugin are retained in
memory; older URLs, or previous-run URLs after restart, return 404 rather than
serving a newer file. Unrelated report/branding transitions keep parser identities.

## Categorization workers

An explicit live descriptor providing only `Categorization(factory)` can run as
a separate installed pure-engine child. Budget binders, settings hooks, saved-state
upgrades and combined capabilities remain outside this adapter. Approved upgrades
use the same immutable closure, readiness, drain, durable reference and recovery
controls described above. Readiness opens and inspects both workspace engines
without permitting namespace writes; final readiness uses the latest drained
saved namespace. An unfinished training run returns `continuation_busy` until it
finishes or is canceled. Unrelated plugin transitions retain that run.

The child receives detached reviewed training rows, inference rows, read-only
settings and a complete bounded own namespace. Engine objects, model objects and
training iterators stay in the child; cooperative requests carry opaque engine
and candidate-model IDs. No training happens between settings requests. Each
namespace frame contains at most 1,000 files and 48 MiB of state plus files,
including unknown files and isolated `demo/` data. Metadata is bounded to 3 MiB,
JSON state to 1 MiB and training input to 20,000 rows / 6 MiB. Wire nesting and
metadata bounds still apply; unsupported namespaces refuse without truncation.

A save/select/rename/delete computes a detached full namespace. The host commits
that namespace and original result receipt atomically through the private
[namespace effect journal](plugin-state-effects.md), then materializes every
state/file callback and verifies the complete digest. Portable checkpoints and
copy notifications are suppressed until materialization completes. Startup
recovers all retained journals, including disabled categorizer namespaces,
before workers or copy observers can read them. The generic portable plugin-data
state editor joins the same journal, including for disabled owners, and preserves
their settings and unknown files. Interrupted materialization
replays the original committed namespace without executing the plugin again.
An uncertain publication blocks further effects pending explicit reconciliation
or restart. A child crash before a complete reply publishes no namespace; the
active generation reports a fixed error and has no automatic mutation retry or
restart loop. Restart restores the last validated approved environment and
recovers its authoritative namespace before exposing it.

The host continues to own suggestion IDs, eligibility, currency/review validation,
ledger commands, request receipts and session expiry. Private effect journals
and immutable environments stay outside portable budgets; model/state files
remain portable. Version upgrades preserve data rather than migrate unsupported
model formats. The installed-version upgrade test uses distinct fictional v1/v2
wheels, both workspaces and a failed candidate, then reopens from the saved v2
reference. Random forest host tests also run the actual installed engine child.

Local storage requires its own separate adapter. Tests use reviewed
fictional wheels, temporary homes and mocked providers; worker processes are not
a security sandbox.

## Visual interface workers

An explicitly live pure `UserInterface` descriptor opts into `view_protocol=1`.
Protocol zero remains supported for legacy startup rendering and requires a
restart for visual changes. A visual worker owns only presentation code. Its
`ui.resources` reply captures bounded immutable UTF-8 templates and static bytes;
`ui.render` accepts bounded public page projections and returns validated HTML
bytes. Python/Jinja helpers execute inside the child. Cookies, bearer headers,
application/backend objects, routers and callables never cross these messages.
Startup declarations negotiate the exact current host and projection flags.

The host retains complete resource generations under its private runtime home;
their public URLs include the content/code generation. Old URLs always serve
their exact retained bytes, including after disabling or reopening. Missing old
resources fail without falling through to current assets. All four page kinds
render fictional readiness projections before activation. The same generation
owns projection flags, templates, helpers and browser module imports throughout
one request lease. Bank/import extension templates remain bounded public data.

Local controls are `GET /api/ui/status`, `POST /api/ui/preview` with `pluginId`
(or `null` to disable), optional approved `wheels` and `useInstalled`, then
`POST /api/ui/commit` with the returned `previewId`. Duplicate commits return
the same operation. Inspect the operation's terminal status rather than treating
HTTP 202 as completion. Demo controls refuse mutations. HTTPS, Host/origin checks
and local access remain required, including while runtime admission is gated.

Preparation keeps financial admission open while `/api/ui/views` participants
flush edits and acknowledge an opaque preparation. Dirty, failed or busy views
defer handover. After drain the host rechecks participation, durably selects
code/resources in one private `visual-selection.json` decision and publishes the
generation. A lost decision reply keeps admission gated and views paused until
`POST /api/ui/recover` validates the exact active or candidate decision. Optional
plugin recovery cannot decide a visual transition. Only the retired visual
worker/resources retire; ledger revision/retry state and unrelated services stay
owned by the stable budget runtime.

The choice is device-local and overrides initial `WHAB_UI` on reopen. Disabled
and unavailable interface records remain preserved. An existing saved worker
must reopen its exact approved environment; incompatibility or missing files
never selects native code automatically. Host SDK upgrades remain application
upgrades. The resources and worker processes provide coherence and failure
containment, not protection from malicious installed Python.

After stopping the affected budget, `visual_worker_revision(home, budget_id,
environment)` in `whab.plugins.ui_repair` returns an opaque compare-and-set token.
`replace_saved_visual_worker(..., plugin_id, approvals,
expected_revision=token)` prepares only the supplied approved current-host wheels,
checks all four page projections, and closes its candidate before publishing one
new visual decision. It never executes the saved environment or opens a financial
backend. Pending child cleanup blocks another candidate. The exact old decision
bytes are retained privately under `visual-ui/history/`; resource and environment
generations remain intact. An exact completed request returns its original receipt
without reading wheels or executing code, including after a later explicit disable.
It does not select that older decision again.

`disable_saved_visual(..., expected_revision=token)` explicitly selects headless
startup and retains saved worker references. The minimal recovery listener offers
the same **Select headless startup** action with its own current recovery revision.
A running owner, changed decision, invalid scope or unsupported journal version
refuses repair; preserve those files instead of removing a journal or choosing a
different interface automatically. A host upgrade requires approved wheels built
for that actual host before a worker can resume.
## Bank provider workers

A live bank declares `bank=BankAdapter(factory)`, `PluginContract(lifecycle="live",
authority="local", provides=(Capability("ingestion.bank"),
Capability("lifecycle.background")))`. The typed provider adapter is separate from
legacy `BankContext` factories, whose restart ABI remains supported. Only the
explicit pure provider shape can enter an approved immutable worker environment.
The host probes construction/public configuration and captured UI resources during
readiness; it sends no Link, exchange, synchronization or provider HTTP request.

The synchronous factory receives `BankProviderContext(budget_id, environment,
config)`. Its config is copied, bounded and hidden from repr. Host encryption keys,
backend handles, `BudgetContext`, file callbacks and background factories are
excluded. The async engine exposes `connection_token`, `exchange_token`,
`read_sync`, `remove_token`, `webhook_event` and `close`. Transport outputs are strict
bounded JSON DTOs. A sync returns normalized `SyncChanges`/`ImportedAccount` records,
a cursor and historical-complete flag; the host validates again and performs
provider namespacing and native atomic apply. Webhook signature verification runs
against the original bytes in the child; only verified typed actions reach the
host's owned Item lookup and background sync queue.

Provider RPC payloads are at most 16 MiB; synchronization accepts up to 100,000
changes and 500 accounts per complete batch. Webhooks are bounded by the existing
128 KiB HTTP body limit (the internal codec also caps raw bytes at 1 MiB), and
captured bank UI bundles use the shared 4 MiB/256-file resource bounds. Unsupported
volumes fail before native mutation. The bank's public config contains only
`environment`, `configured`, `liveEnabled` and bounded missing-config key names.
Expected errors use fixed `ProviderFailure` codes (`not_configured`, `reconnect`,
`transport`); provider response/error text never enters diagnostics. Unknown hook
failures, process death, malformed frames and irreversible transport failures
close the child; there is no automatic restart or repeated external exchange.

`BankCoordinator` owns the four-method continuation facade described in
[the bank SDK](plugins.md#privileged-bank-capability). A begin retry returns its original
encrypted ready Link token. Exchange intent is flushed before provider HTTP, then
the normalized response and exact native request are encrypted and flushed before
`StorageCommands.execute_operation`. The native receipt is the sole commit
proof. Status can reconcile an existing receipt without HTTP or a new native
write. Explicit Finish saving can resume the captured exact request after receipt
or unchanged-generation evidence permits it. Receipt absence alone is unknown;
a pending native-worker intent cannot be bypassed. Unknown external exchange
requires independent provider verification through the private continuation
reconciliation control, not a new Link or token exchange.

Host-owned request tasks retain generation leases after browser disconnection.
Queued webhook sync uses an independent lease and retains a coalesced follow-up
notification. Changed provider selection/upgrade defers unfinished or uncertain
flows and queued work; unchanged providers retain their original scope across
unrelated runtime changes. Missing host keys leave a new provider unconfigured;
existing private journals require their original key. Private journals never
enter portable containers, catalogs, worker IPC or copy namespaces. This remains
a trusted-Python containment contract, without an OS sandbox.
