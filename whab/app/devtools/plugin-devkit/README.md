# WHAB plugin developer toolkit

`whab_plugin_devkit` is an independently packaged development dependency. It lives
outside runtime plugin discovery directories and contributes no runtime entry point.
Install it beside the host SDK and the plugin being tested; it does not require
the bundled plugin set, a SQLite implementation, a provider account, or pytest at runtime.

The toolkit provides synchronous storage conformance, nine complete teaching
project generators and constrained configuration replay through public
`whab.plugins` contracts. It requires Python 3.14+ and WHAB 2.22+ within major 2.
Compatibility ranges require testing the minimum supported host artifact and the
current candidate; an API import alone does not establish compatibility.

## Generate and check a teaching project

The installed `whab-plugin` command creates a new directory and refuses to
overwrite an existing project:

```sh
whab-plugin new household-report --capability reporting
```

Add `--ci-ref "$WHAB_CI_COMMIT"` to include a complete reusable GitHub Actions
workflow. Set `WHAB_CI_COMMIT` to a published, reviewed, full lowercase commit
from this repository that contains `plugin-conformance.yml`; mutable branch/tag
names are rejected. Both the workflow reference and its tooling/candidate source
checkout use that exact commit. The generated YAML needs no source path changes
when the project lives at the root of its own repository.

```sh
: "${WHAB_CI_COMMIT:?Set the reviewed published WHAB CI commit first}"
whab-plugin new household-report --capability reporting --ci-ref "$WHAB_CI_COMMIT"
```

Choose `reporting` for a one-currency report and CSV export, `branding` for an
embedded fictional bank logo, `file-import` for a bounded USD account JSON parser,
`categorization` for a complete saved exact-merchant model lifecycle, or `api` for
a declarative API interface requiring WHAB 2.34+. The `ui`, `bank` and
`remote-client` templates require their genuine 2.36, 2.38 and 2.40 floors.
The `storage` template requires the genuine 2.41 public pure-model SDK.
Generated
projects include packaging, public-SDK source, regression tests, a README and an
MIT license. The four original templates declare restart-only lifecycle support;
the API template declares the host-supported live lifecycle. The categorizer learns
only reviewed posted rows, keeps currency-specific matches in its budget namespace
and reports rule matches rather than measured predictive confidence.

Build and review the project wheel, then install it with the built host, toolkit,
SQLite reference and their dependencies in a clean external environment. Run its
ordinary tests before the configuration check:

```sh
python -m pytest tests
whab-plugin check --plugin household-report --combinations 10 --seed 42 --wheelhouse /approved/local/wheels --output /tmp/household-report-check
whab-plugin replay /tmp/household-report-check/configuration-00.json --wheelhouse /approved/local/wheels
```

Add `--runtime` to `check` for the versioned `native-local-v1` profile. On a
supported host (WHAB 2.32+) also install the reviewed JSON reference and the
explicit [runtime peer fixture](fixtures/runtime-peers/README.md) wheels. The kit
itself registers no runtime plugin. This profile starts the installed host CLI
in a fresh temporary Sandbox home, provisions local TLS with the host's
`whab.tls.prepare_local_tls` helper and verifies its CA and hostname. It uses the
documented ledger, plugin-management, storage-migration and copy HTTP APIs.
It never changes OS trust, borrows another runtime home, or opens a provider
connection. The wheelhouse must include every exact installed runtime artifact.

Format-2 manifests retain the seed, ordered peer selections, genuine host and
artifact hashes, primary/copy direction, explicit selection sequence, fault and
replay command. Each supported configuration executes real peer disable/enable,
replacement or reordering, copy attachment/catch-up, explicit promotion, reverse
migration, process restart and acknowledged-data checks. It exercises the
candidate's applicable public hooks before and after; candidate disable/enable
runs only when its declaration and peer dependencies permit it. Restart-only
generated candidates remain selected, with that limitation recorded explicitly.
Candidates with provider factories, legacy API factories, or context binders are explicitly
inapplicable to this bounded native profile; those factories are never started.
Hook probes use the public SDK with fictional inputs. The profile verifies active
candidate selection after reopen; it does not yet train and preserve an opaque
host-owned model through these transitions.

The separate `native-api-v1` recipe requires WHAB 2.34 and a validated data-only
`RemoteLedgerInterface` enabling both queries and commands. It retains the format-2
reader while naming a new versioned profile, so `native-local-v1` manifests keep
their exact meaning. In every API configuration the kit generates an ephemeral
host token, activates the exact approved candidate wheel in an immutable worker,
then tests native HTTPS authorization, host/origin and budget guards, financial
validation and identical request retries. The worker closure includes only the
host, candidate and their runtime dependencies; SQLite, JSON, toolkit and peer
registrations stay outside that child. The same candidate participates in the
recorded live selections and local migration/copy steps, and authenticated queries
and the exact saved worker are checked after restart. Token values and financial
records never enter replay artifacts. This tests worker execution of one exact
artifact; it does not claim a distinct-version code upgrade or retry durability
across a host restart. Query-only/command-only declarations can use the direct
public API probe but remain inapplicable to this complete financial profile.

Precise faults are invalid selection preflight, consumed-preview reuse, stale
copy authority requiring reattachment, and SIGKILL after an acknowledged primary
write and settled transition. These are not injected disk/journal commit
boundaries. The host and its owned process group are stopped and the disposable
runtime is removed, including after failure. Results contain identifiers and
case outcomes, never snapshots, certificates, private paths or raw errors.

The genuine 2.22 minimum-host run still executes resolver/hooks and supported
reference contracts. Its newer runtime profile is explicitly `not-applicable`
and contributes **zero** runtime passes. Broken advertised behavior on a supported
host fails. Version-1 resolver manifests remain readable; replay automatically
selects their schema. Runtime profiles do not yet claim worker code upgrades,
private commit-boundary injection or provider network behavior.

The wheelhouse contains the exact approved installed artifacts. Matching compares
installed package bytes with local wheel members before importing the candidate
and SQLite entry points; editable installations cannot establish this evidence.
The host, toolkit, candidate and reference runtime dependency closure must be
present with matching versions and hashes. Requirement markers are evaluated for
this interpreter; runtime URL requirements and extras are unsupported. Additional
matching test-tool wheels may be recorded without becoming selected runtime code.
The commands do not install, download or approve plugin code.

Each check samples ten distinct selected peer sets from six reviewed synthetic
report, branding and import descriptors. It uses the actual public capability
resolver, varies discovery/configuration order, exercises the candidate and peer
hooks, and runs the SQLite reference's advertised conformance suites. The saved
manifests include the seed, Python version, exact wheel identities/hashes, selected
order, a selection preflight sequence and an explicit fault choice. Deterministic
negative cases require missing/version-incompatible capabilities, cycles,
conflicting writers and unsupported state versions to reject. Injected report
failures must be attributed; invalid imports must reject atomically. Results
contain case outcomes and identifiers, without ledger snapshots or private
exception text. Preserve a failed manifest for replay.

The default version-1 selection sequences resolve peer removal/restoration, replacement or reordering.
They do not activate live processes, migrate schemas, copy budgets or promote
financial authority. Those unimplemented slots are explicit in every manifest.
The synthetic snapshot and account-file probes are deliberately small teaching
formats; extend the project's own regression tests for other file formats or
candidate-specific rules.

## Run from an external plugin project

Install built host, toolkit and candidate plugin wheels into an isolated environment:

```sh
python -m pip install 'pytest>=9,<10' /wheelhouse/whab-*.whl /wheelhouse/whab_plugin_devkit-*.whl /wheelhouse/your_plugin-*.whl
python -m pytest tests/test_whab_contracts.py
```

The external test can select its installed descriptor through package metadata:

```python
from importlib.metadata import distribution

from whab_plugin_devkit import BackendAdapter, Capability, run_conformance


def test_storage_contracts():
    registration = next(
        entry for entry in distribution("your-plugin-distribution").entry_points
        if entry.group == "whab.plugins" and entry.name == "your-plugin-id"
    )
    descriptor = registration.load()
    adapter = BackendAdapter(
        factory=descriptor.storage,
        capabilities=frozenset({Capability.RECEIPTS, Capability.NEUTRAL_TRANSFER}),
    )
    run_conformance(adapter).assert_success()
```

Loading the explicitly selected candidate executes trusted installed Python code.
The toolkit does not discover, download or execute community packages automatically.
Supply only fictional configuration. Factories receive newly created temporary
homes with Sandbox selected; the environment-isolation case also opens an empty
production namespace in that same temporary home. It does not connect to a bank.
Adapters must avoid external provider traffic and use mocked transports if needed.

`BackendAdapter.reopen_factory` optionally supplies a distinct public reopen path.
`BackendHarness` owns temporary homes, closes successful opens in reverse order,
attempts remaining cleanup after a close failure and surfaces cleanup errors.
Factories must clean resources they allocate before raising during startup.
`SyntheticClock(month="2026-10")` supplies fixed fictional dates where a candidate
factory can inject the same calendar. Otherwise the default follows the current
UTC month; the kit does not patch a backend's clock or the process environment.

## Applicability and observations

The ledger suite always runs. It covers integer minor units, USD/EUR projections,
cross-currency rejection, atomic invalid operations, two budgets with colliding
transaction IDs, demo/personal and Sandbox/production isolation, detached public
reads, annotations and close/reopen persistence.

Advertise optional suite names through `Capability`:

| Capability | Checks in this increment |
| --- | --- |
| `provider-sync` | Colliding connection/account IDs, pending/posting annotations, cursor compare-and-set, failed-sync atomicity, checkpoint reopen |
| `receipts` | Synthetic PNG persistence, invalid image/identity rejection, byte preservation, deletion |
| `workspace-import` | Atomic invalid archive rejection, preview, durable import identity and replay preserving later edits |
| `next-month-funding` | Saved projection, invalid amount rejection and reopen |
| `monthly-rollover` | Saved policy, invalid policy rejection and reopen |
| `neutral-transfer` | Financial round trip, empty-destination guard, private-record exclusion; deleted history when sync is also advertised |
| `native-replica` | Complete local copy, native write fence, independent financial/namespace watermarks, stale-job rejection, durable replay/reopen and explicit prepared promotion |
| `durable-commands` | Discarded public operation result, exact receipt after reopen, stale-generation replay, conflicting identity and unknown stale rejection, atomic invalid payload and receipt isolation |

An unadvertised optional suite returns `not-applicable` with an explicit reason.
An advertised capability with missing or broken methods returns `failed`; it is
never skipped. Unknown capability names are rejected. Call `Report.assert_success()`
in CI; merely creating a report is observational and does not raise for failed cases.
Reports retain case names and exception types, excluding exception text, paths,
record values and snapshots. Public snapshots remain detached in memory for
comparisons. Use synthetic data exclusively.

`native-replica` exercises the public `storage.replica` v1 methods. It forwards
`StorageContext.initial_role="copy"` only for this advertised suite. Ordinary
primary factories keep the older SDK constructor path. Reference configuration
runs select the replica suite only when the installed descriptor advertises it;
an older reference reports it as not applicable. Advertising replica support on
an SDK or backend without its required controls fails the suite.

These tests check observable financial records and projections, not implementation
classes, database handles or private state methods. Neutral restores may mark
external account snapshots stale according to the existing SDK behavior. Receipts
are checked separately because the legacy neutral ledger transfer omits image bytes.

## Scope and remaining work

This is a bounded conformance suite, not certification of a backend. The explicit
native profile covers the public lifecycle, migration/copy and restart operations
listed above. It does not yet provide worker-upgrade barriers, provider transports, injected disk faults,
arbitrary commit-boundary termination recovery, schema upgrade fixtures, protected same-device transfer,
or receipt transfer on pending/posting. The durable command suite discards a
completed result and validates receipt resolution; it does not inject an uncertain
native commit. Backend-specific commit-boundary termination tests remain with
their owning backend and host coordinator.
The current tests observe cleanup calls; they cannot prove a backend released every
OS resource. SQLite-specific SQL/schema/failure tests remain with that plugin.

The package's own tests use a reviewed fictional ledger double to detect broken
rejection, persistence, applicability and cleanup behavior. An optional integration
test loads the installed SQLite descriptor through its public entry point and runs
all currently provided suites. That test explicitly reports a pytest skip if the
reference distribution is absent; candidate suite applicability never uses skips.

From `whab/app/`, during toolkit development:

```sh
.venv/bin/python scripts/install.py --dev --example --runtime-fixtures
.venv/bin/python -m pytest devtools/plugin-devkit/tests
```

`--runtime-fixtures` explicitly adds the six reviewed fixture registrations needed
by current-host native tests and implies `--dev`. Normal/default installation and
plain `--dev` do not add these runtime registrations. The canonical contribution
gate collects source tests separately and runs toolkit tests in its own installed
environment, building the exact fixture artifact there automatically. The flag is
needed only for a direct source toolkit test invocation.

For external validation, build wheels, install into a clean directory outside the
checkout, clear source import paths, and verify the imported host/toolkit/candidate
files resolve inside that installation. Do not infer installed-artifact success
from the editable development invocation.

## Installed-wheel compatibility gate

The repository's full `scripts/check.py` runs this dedicated gate once:

```sh
.venv/bin/python scripts/check_plugin_devkit.py --report /tmp/whab-devkit-artifacts.json
```

It builds the genuine minimum host **2.22.0** from pinned repository commit
`bac249ca0e62638a40a6effc44af741c283e83d7`, the current candidate host, the toolkit,
and the SQLite reference. Candidate hosts supporting the native profile also
install exact JSON and reviewed runtime-peer fixture wheels. CI fetches complete Git history for that controlled
baseline. Each host receives a fresh virtual environment outside the checkout,
with only these WHAB distributions and their runtime/test dependency closures.
Dependencies are downloaded to a local wheelhouse; installation uses `--no-index`.
The gate clears source import paths, verifies installed module paths and versions,
and requires exactly the explicit reference/fixture runtime registrations for
that host; the minimum artifact retains SQLite as its only registration.

Both environments run the complete toolkit tests, including the advertised SQLite
capability suites. Any skipped, missing or failed reference test fails the gate.
The candidate run also requires **100% statement and branch coverage** of every
installed toolkit Python file; it does not rerun the toolkit tests for coverage.
The minimum-host run checks compatibility independently. Source-level central
and runtime-plugin tests remain in their existing single coverage run.

The optional JSON report records the pinned/candidate source commits, candidate
source modification status, exact wheel versions and SHA-256 hashes, test counts
and coverage result. It contains no financial records, snapshots, credentials or
temporary runtime paths. Dependencies/build tools may need network access while
preparing wheels; no provider account or bank connection is used. This initial
gate validates maintained artifacts. The separate generated-project gate builds
all nine projects using an installed toolkit and validates every project wheel
outside the checkout:

```sh
.venv/bin/python scripts/check_plugin_generation.py --report /tmp/whab-generation-artifacts.json
```

Each family must pass its installed regression tests with 100% statement and
branch coverage, ten distinct configurations and replay of all ten manifests on
the each project’s genuine minimum and candidate host artifacts. The four original
families retain the 2.22.0 recipe; API uses genuine 2.34.0 source pinned at
`ebbff0d464dd67c0269428a6d4a339778e21ec57`. When a project’s minimum and candidate
versions coincide, one actual artifact is recorded with both roles. API is never
installed on 2.22 or counted as an old-host runtime pass. The optional report preserves the manifests and
exact wheel evidence; the versioned native-profile results distinguish actual lifecycle/migration/copy
execution from unsupported-host N/A results.

The full repository gate runs both toolkit and generated-project checks. Use
`scripts/check.py --evidence-dir /absolute/new/evidence` to retain the reviewed
generated wheelhouse, reports and per-host replay files. The dedicated generator
gate also accepts `--artifacts-directory /absolute/new/generated`; it preserves
wheels and completed replay manifests on failure while omitting runtime homes.
Existing evidence directories are never overwritten. CI archives this synthetic
evidence for each Python version for 14 days, including failed runs that reached
generation. Install the exact host/toolkit/reference/candidate artifacts listed in
a chosen manifest, then replay it with that saved wheelhouse. Different artifacts
or installed files fail verification before plugin hooks run.

## Reusable external-author CI

[The reusable workflow](../../../../.github/workflows/plugin-conformance.yml)
uses GitHub's [`workflow_call` interface](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows).
The generated `--ci-ref` workflow is a complete caller. Existing projects can use
the same job with `plugin-id`, the full reviewed `whab-ref`, and optional
`project-directory` relative to the caller checkout. The caller selects trusted
source/build/test code to execute. The workflow has read-only repository access,
does not inherit secrets, and does not provide security sandboxing.

The selected WHAB commit supplies the current candidate host, toolkit and SQLite
reference. The project’s `[tool.whab.plugin-devkit] minimum-host` selects a known immutable
recipe matching its declared WHAB floor: `bac249ca0e62638a40a6effc44af741c283e83d7`
for 2.22.0, or `ebbff0d464dd67c0269428a6d4a339778e21ec57` for the API family’s
2.34.0. Additional genuine recipes cover 2.36/visual, 2.38/bank, and
`e8fa8585c16d2f8a93b1966315c0fee34eb11d35` for 2.40/remote client.
A compatible actual SQLite reference accompanies each host. Different
minimum/current versions produce distinct real artifacts; equal versions share
one artifact with both roles. Version metadata is never rewritten for compatibility. The installed toolkit retains its declared minimum host floor.

The shared runner builds the caller project once, then creates independent
environments outside both source checkouts. Only candidate/reference plugin
registrations are permitted. Before importing either descriptor it verifies exact
installed wheel members, dependency closure and entry-point ownership. Each host
runs the project's behavioral tests once, with every installed candidate Python
file at 100% statement/branch coverage, ten distinct configurations, all advertised
SQLite reference suites, and all ten exact-artifact replays. Missing/empty/skipped
project tests and missing/broken advertised suites fail. Unsupported optional
reference capabilities remain explicit `not-applicable` results; they are never
used to hide failures. The installed profile covers the nine generated teaching families and their
explicit public probes. The API native recipe also tests its scoped HTTP/worker
behavior; this is not arbitrary provider or all-family lifecycle certification. Add project-specific public-contract tests when extending them.

Dependency preparation is separate from runtime capability selection. Only host,
toolkit, SQLite reference, pinned test tools and pinned Setuptools build tools are
downloaded during preparation. Candidate build/runtime/test dependencies must
already exist in that closure or in `extra-wheelhouse-directory`, an optional
caller-reviewed wheel directory. Prefer `extra-wheelhouse-artifact` when an
earlier caller job prepares reviewed wheels: make the calling job depend on that
preparation job, and upload only the wheel files at the artifact root. The reusable
workflow downloads that one explicitly named artifact; it never fetches a plugin
from an index. Choose either directory or artifact, and keep generated wheels out
of Git. URL and extra dependency requirements are
unsupported. Every project build and test-environment install uses `--no-index`;
build isolation cannot silently download additional tools. Missing required wheels
produce an installation failure and a clear wheelhouse diagnostic. No community
plugin is discovered, downloaded or enabled automatically.

For a local equivalent, from `whab/app/` in a reviewed tooling checkout and with
`PLUGIN_PROJECT` set to the external project's absolute directory:

```sh
: "${PLUGIN_PROJECT:?Set the external project directory first}"
python -m pip install packaging==26.3
python scripts/check_plugin_generation.py \
  --project "$PLUGIN_PROJECT" --plugin household-report \
  --candidate-ref "$(git rev-parse HEAD)" \
  --artifacts-directory /tmp/household-report-ci-evidence
```

The toolkit tests and their coverage remain in `check_plugin_devkit.py`; the
external runner reuses the generation gate rather than duplicating those tests.
Temporary build/test environments and runtime homes close and disappear on both
success and failure. Archived evidence contains approved wheels, versions/hashes,
source commit identities, test counts and fictional replay files, without runtime
homes or ledger payloads. Preserve the wheelhouse for offline replay. The repository
CI calls this same reusable workflow for its maintained fictional branding example.

## Visual interface projects

`whab-plugin new fictional-ui --capability ui` generates a complete read-only visual project for WHAB 2.36+ and toolkit 0.7+. Select its singleton with `WHAB_UI=fictional-ui`, outside the optional peer list. The `native-ui-v1` format-2 recipe keeps earlier local/API profile meanings and tests this exact approved candidate in an immutable worker, four coherent HTTPS pages, public assets supplied in its resource record, dirty-view deferral, exact preview/commit correlation, disable/re-enable and exact saved-worker/generation reopen. It also retains the constrained peer/order/sequence, primary/copy directions and public faults of the local profile. It does not claim distinct-version upgrades or private durable-boundary faults. Authors can call `assert_visual_interface(runtime, plugin, asset_name="app.js")` to name an asset in their own behavioral tests.

The genuine visual minimum recipe is source `a866eda580c6e363a20636785bc4b4003db0652f`, WHAB 2.36.0. Toolkit imports and the original generated families retain their actual 2.22 floor; unsupported older-host visual runtime profiles are N/A, never passes. The generated JavaScript has no editable fields; an editable UI must preserve its own drafts and withhold its browser acknowledgement until saving/cancelling is complete.

`NativeRuntime` keeps its startup/HTTP deadline separate from the bounded management deadline: `transition_timeout=180` allows the host's 120-second environment preparation and subsequent handover to finish. It accepts at most 300 seconds. A terminal failed operation fails the suite immediately; the longer preparation allowance does not convert failures to passes. Each genuine host also receives a compatible JSON reference wheel from its source recipe when the current backend requires a newer host.

The full CI job has a 180-minute ceiling per Python version; the reusable job for
one external project has a 60-minute ceiling. The observed source coverage phase
alone took about 34 minutes, before the expanded genuine-host artifact builds,
native configuration matrix and exact replays. These overall CI ceilings provide
bounded room for that work and dependency installation; they are not runtime
performance guarantees. Production readiness, RPC, startup, HTTP and transition
deadlines are unchanged.


Historical hosts may share an unchanged reference package version. The checker
builds each source separately and reuses the first approved wheel only when every
member's contents, modes and flags match; ZIP timestamps may differ. A conflicting
same-identity artifact or a failed build stops validation without replacing the
approved wheel. Exact saved artifact hashes remain the replay authority.

## Offline bank projects

`whab-plugin new fictional-bank --capability bank` creates an independent typed
`BankAdapter` fixture requiring genuine WHAB 2.38+ and toolkit 0.8+. Its default
transport has no URLs or HTTP client. Owning tests cover mocked provider failures
and actual CA-verified native HTTPS begin/complete/sync/update/disconnect on both
SQLite and JSON. The toolkit remains importable on its genuine 2.22 SDK floor;
the newer bank profile is explicitly inapplicable there.

After reviewing and installing the exact fictional provider and its wheel closure:

```sh
whab-plugin check --plugin fictional-bank --runtime --bank-fixture fictional-v1 --combinations 10 --wheelhouse /approved/local/wheels --output /tmp/fictional-bank-check
whab-plugin replay /tmp/fictional-bank-check/configuration-00.json --wheelhouse /approved/local/wheels
```

`--bank-fixture fictional-v1` is explicit authorization for the versioned offline
fixture, not permission to use a real provider. Without it a bank candidate's
runtime suite is N/A and starts no provider. An approved fixture's broken behavior
fails. The generated metadata carries the same explicit opt-in for reusable CI.
Ten `native-bank-v1` manifests vary five connection sequences across both local
backends and ten distinct peer sets: ordinary completion, ready-flow restart,
ready-flow retirement deferral/cancel, update restart and exact completion retry.
Each then executes its recorded peer/candidate selections, native copy catch-up,
promotion, reverse migration, reopen and cursor checks before disconnecting.
Pending-to-posted annotations and demo isolation are checked using public HTTPS
responses. Worker readiness uses the approved exact candidate artifact; this is
not a distinct-version upgrade claim. The profile makes no external provider
HTTP calls or private commit-boundary injection claim.

The public `bank_conformance` module also exposes `BankRuntime`, `BankScenario`,
`fictional_scenario`, `exercise_bank_runtime` and the pure `bank_declaration`
predicate. These helpers never import a concrete provider or storage module.
The native harness gives the fixture a fresh host-owned key, keeps it across its
own process restarts, excludes caller credentials and removes its private home.
Trusted Python remains executable code; the harness is not a network sandbox.

## Remote client projects

`whab-plugin new fictional-client --capability remote-client` creates a pure
`RemoteLedgerClient` declaration, independent packaging and native behavioral
tests. Its genuine host floor is 2.40: the ABI begins in 2.39, while 2.40 excludes
custom caller settings from offline provisioning. The toolkit itself keeps its
lazy 2.22 floor; older hosts cannot count this profile as a runtime pass.

The explicit `[tool.whab.plugin-devkit] offline-remote-fixture = "fictional-v1"`
recipe permits only two disposable fictional hosts. Install the reviewed SQLite,
API server and runtime-peer fixtures beside the candidate. JSON is not needed
for this profile. The server uses the ordinary official CLI. The reviewed static
`remote_launcher` supplies a CA-verified public `RemoteStore` transport, with
environment trust disabled, then runs the official CLI. This verifies that
transport and official services; it makes no product private-CA setting claim.
No OS trust, household runtime or external provider is involved.

Format-3 `native-remote-client-v1` manifests explicitly separate the server's local
reference backend from the client writer. Ten distinct peer sets exercise the
candidate's exact approved immutable worker, same-binding pause/enable, public
financial services, peer transitions and saved-worker reopen. Controlled faults
are rejected selection, rejected wheel hash, client SIGKILL after an acknowledged
write, and loss of a real acknowledged public command result. Lost results remain
unknown through client restart and block another write. Only an explicit Finish
of the exact original authorized body, retained ID and CAS may complete it against
the same live server. This is not receipt lookup, automatic reconciliation, or
durable retry proof across a server restart. A single candidate wheel makes no
distinct-version upgrade claim. Local migration and copies are explicitly N/A;
all existing version-1/version-2 local profiles remain readable.

The worker closure includes only the approved host, candidate and their runtime
dependencies. Server, reference backend, toolkit and peer registrations remain
outside the descriptor child. Endpoint, credential, budget and runtime key values
stay in the fictional parent host and are absent from replay evidence. Both hosts,
their child processes and temporary homes are cleaned even after failure. The
maintained [remote example](../../plugins/example/example-remote-client/README.md)
uses these same public probes.


## Independent local storage primary

`whab-plugin new fictional-storage --capability storage` creates a complete
independent POSIX snapshot adapter, multi-file owning regression suite and package
metadata. It imports the genuine WHAB 2.41 public `storage_model` API instead of
wrapping a bundled backend or copying monetary policy. The toolkit retains its
2.22 floor and loads newer storage SDK records only when advertised native
readiness is exercised; unsupported old SDK advertisement fails explicitly.
The actual public-model minimum source is
`c10bf14a96cfd3707124fc2916d794da1b69f41f`, with no rewritten host version.

`storage_conformance(plugin)` runs mandatory ledger/provider/import/funding/rollover
behavior plus every advertised optional suite. `assert_storage_readiness(plugin)`
proves complete read-only observations and exact durable receipt retry after
reopen. Native opens use the public plugin factory and records only. An explicit
POSIX-only installed package classifier makes unsupported-platform profiles not
applicable; such a result never contributes a native pass.

Format-4 `native-local-storage-v1` manifests make the reviewed storage candidate
the sole required primary. The separately executed SQLite reference is never
co-selected as another writer. Ten distinct seeded peer sets activate the exact
approved candidate worker over native CA-verified HTTPS, exercise validation and
peer transitions, retain the required storage/code binding, reject bad approved
hashes, and verify acknowledged data after graceful restart or SIGKILL.
Writes are acknowledged before activation, through the active worker and after
reopening, then all three are verified after a second restart. The profile does
not infer HTTP mutation retry support from native command receipt support. Optional
local transfer/copy controls run only when advertised; the generated adapter
omits these and records explicit not-applicable outcomes. Distinct-version upgrades
and private disk faults are outside this profile; its owning tests cover native
commit/interruption boundaries separately. Existing manifest versions 1, 2 and 3
retain their original readers and meanings.

The maintained [independent local storage example](../../plugins/example/example-storage/README.md)
uses the same native source/test contract as generated projects. New native and
own-file formats require explicit versioned readers; opaque provider records stay
host-encrypted and outside portable payloads. An incomplete initial native file or
unknown native schema is preserved and refused rather than reset. No test uses an
external provider connection, real budget, household key or OS trust change.
