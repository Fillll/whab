# Approved report-worker upgrades

WHAB 2.25 can replace explicitly live, stateless reporting/export code without
restarting its listener. Other families require their own adapters. The parent
retains TLS, authorization, local-access guards, budget identity and financial
validation. This report protocol accepts no financial mutation, backend handle,
router, callable or pickle.

## Artifacts and environments

The operator supplies reviewed local wheels and their complete SHA-256 hashes.
`inspect_wheel(ApprovedWheel(path, sha256))` reads bounded metadata without importing
plugin code. It validates identity, Python/tags, requirements, entry points and
all RECORD hashes. Preparation copies and rechecks those artifacts, validates an
explicit dependency closure containing the current host wheel and one selected
report plugin, and installs offline into a content-addressed environment.
Runtime preparation performs no downloads and never upgrades active files.

The initial installer supports macOS/Linux POSIX locks, the current Python/host,
wheel 1.0 purelib/platlib layouts and dependencies without extras. Unsupported
layouts or closures fail explicitly. A stable lock serializes preparation; a
flushed receipt publishes the environment only after installation verification
and an actual worker readiness probe. Verification detects missing, changed and
unexpected installed files before executing a reused environment. Interrupted
preparations can be discarded under their lock; published environments are never
repaired in place. Old versions remain on disk for inspection and explicit reuse.

Workers use isolated Python startup and disable bytecode writes. Inherited WHAB,
provider/client/server credentials and the configured password-variable alias are
removed. This reduces accidental exposure; plugins remain trusted Python code
with the current user's operating-system access. Processes and versioned venvs
provide failure containment, **not a security sandbox**.

## Protocol and activation

Protocol 1 uses an explicit WHAB header, 8 MiB bounded JSON metadata and at most
50 MiB binary payload, strict primitive types, bounded nesting and exact message
shapes. Snapshots and report outputs also pass the public SDK validation. Startup
negotiates protocol, SDK, host, distribution/version, report capabilities and the
live lifecycle declaration. No arbitrary objects cross the boundary. Startup,
RPC and shutdown are bounded; crashes, malformed frames and hook errors produce
fixed plugin-attributed diagnostics. Owned process groups are cleaned up on
timeout, cancellation and shutdown. There is no automatic restart loop or retry
of a mutation with an unknown outcome.

Enable the stateless report first. Then send an authenticated local
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
generations without deciding authority. Damaged startup files currently require
preserving and repairing the approved environment or disabling that report from
configuration before startup; minimal startup recovery controls are a later adapter.

This adapter covers stateless report code only. Protected storage writes and lost
financial responses require durable typed command receipts; UI resources, bank
flows, parsers and model training need capability-specific workers and barriers.
Tests use only reviewed fictional wheels, temporary homes and mocked providers.
