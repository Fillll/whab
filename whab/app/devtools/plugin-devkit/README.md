# WHAB plugin developer toolkit

`whab_plugin_devkit` is an independently packaged development dependency. It lives
outside runtime plugin discovery directories and contributes no runtime entry point.
Install it beside the host SDK and the plugin being tested; it does not require
the bundled plugin set, a SQLite implementation, a provider account, or pytest at runtime.

The toolkit provides synchronous storage conformance, four complete teaching
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

Choose `reporting` for a one-currency report and CSV export, `branding` for an
embedded fictional bank logo, `file-import` for a bounded USD account JSON parser,
or `categorization` for a complete saved exact-merchant model lifecycle. Generated
projects include packaging, public-SDK source, regression tests, a README and an
MIT license. They declare restart-only lifecycle support. The categorizer learns
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

Selection sequences resolve peer removal/restoration, replacement or reordering.
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

An unadvertised optional suite returns `not-applicable` with an explicit reason.
An advertised capability with missing or broken methods returns `failed`; it is
never skipped. Unknown capability names are rejected. Call `Report.assert_success()`
in CI; merely creating a report is observational and does not raise for failed cases.
Reports retain case names and exception types, excluding exception text, paths,
record values and snapshots. Public snapshots remain detached in memory for
comparisons. Use synthetic data exclusively.

These tests check observable financial records and projections, not implementation
classes, database handles or private state methods. Neutral restores may mark
external account snapshots stale according to the existing SDK behavior. Receipts
are checked separately because the legacy neutral ledger transfer omits image bytes.

## Scope and remaining work

This is a starting conformance suite, not certification of a backend. It does not
yet provide worker/lifecycle barriers, controlled transports, injected disk faults,
termination recovery, schema upgrade fixtures, protected same-device transfer,
receipt transfer on pending/posting, copy authority/promotion, retry evidence for
unknown mutation outcomes, live configuration coordination or migration/copy replay.
Those need the corresponding host contracts.
The current tests observe cleanup calls; they cannot prove a backend released every
OS resource. SQLite-specific SQL/schema/failure tests remain with that plugin.

The package's own tests use a reviewed fictional ledger double to detect broken
rejection, persistence, applicability and cleanup behavior. An optional integration
test loads the installed SQLite descriptor through its public entry point and runs
all currently provided suites. That test explicitly reports a pytest skip if the
reference distribution is absent; candidate suite applicability never uses skips.

From `whab/app/`, during toolkit development:

```sh
.venv/bin/python -m pip install --no-deps -e devtools/plugin-devkit
.venv/bin/python -m pytest devtools/plugin-devkit/tests
```

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
and the SQLite reference. CI fetches complete Git history for that controlled
baseline. Each host receives a fresh virtual environment outside the checkout,
with only these WHAB distributions and their runtime/test dependency closures.
Dependencies are downloaded to a local wheelhouse; installation uses `--no-index`.
The gate clears source import paths, verifies installed module paths and versions,
and requires SQLite to be the only runtime plugin entry point.

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
all four projects using an installed toolkit and validates every project wheel
outside the checkout:

```sh
.venv/bin/python scripts/check_plugin_generation.py --report /tmp/whab-generation-artifacts.json
```

Each family must pass its installed regression tests with 100% statement and
branch coverage, ten distinct configurations and replay of all ten manifests on
the minimum and candidate host artifacts. When those versions coincide, one
artifact covers both roles. The optional report preserves the manifests and
exact wheel evidence; it does not claim lifecycle, schema migration or budget
copy coverage.

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
