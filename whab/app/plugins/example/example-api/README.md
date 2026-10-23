# example-api

This independently packaged fictional API interface requires Python 3.14+ and
WHAB **2.34+** within major 2. It declares `Interface(adapter=RemoteLedgerInterface(...))`
and a live lifecycle. It supplies no router, ledger handle, callback or credential
value to a worker. The host owns native HTTPS, constant-time bearer authorization,
local access guards, strict financial validation, revisions and retry handling.
Installed Python remains trusted executable code; workers are not a sandbox.

Install this wheel explicitly and select `example-api` as the budget's optional
API interface. Configure `EXAMPLE_API_TOKEN` privately on the host with a random
32–4096-character token. Do not put that value in plugin state, source, a wheel,
a browser asset, a replay manifest or a report. Select one declarative API route
owner for `/remote/v1`. Installing the package alone does not enable it.

Build and test outside the WHAB checkout with approved local host/toolkit/SQLite
reference wheels. Install the project's `test` extra and run `python -m pytest
tests`. Its behavioral test launches an installed host in a disposable fictional
Sandbox home, verifies the runtime CA explicitly, and checks real authenticated
queries/commands, conflict handling, unchanged request retries and cleanup. The
test generates its own ephemeral host token and creates no provider connection.

Run `whab-plugin check --plugin example-api --runtime --combinations 10 --seed
42 --wheelhouse /approved/local/wheels --output /tmp/api-check`. The runtime check also needs approved JSON and reviewed runtime-peer fixture
wheels in its isolated test environment. Ten distinct
constrained peer configurations exercise this candidate through public native
HTTPS operations, selection changes and the reviewed local migration/copy profile.
Replay one saved manifest with `whab-plugin replay MANIFEST --wheelhouse
/approved/local/wheels`. The API profile has an actual host floor of 2.34;
older-host runtime N/A results are never counted as API passes. Each API configuration activates this exact candidate wheel in an immutable
worker and verifies the saved worker on reopen. The initial
profile does not claim distinct-version worker upgrades or retry durability
across a host restart.

`--ci-ref` adds a complete caller for WHAB's reusable workflow, pinned to your
reviewed full source commit. `[tool.whab.plugin-devkit] minimum-host` selects the
genuine 2.34 artifact recipe. Other generated families keep their own older
host floors; the toolkit itself still supports 2.22. Build/runtime/test dependency
resolution uses a controlled wheelhouse and is separate from capability selection.
Missing reviewed wheels fail the check. Caller-authorized project code executes
without inherited workflow secrets or an OS sandbox.
