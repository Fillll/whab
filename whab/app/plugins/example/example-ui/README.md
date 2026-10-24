# example-ui

A complete read-only fictional visual interface for Python 3.14+ and WHAB
**2.36+** within major 2. `UserInterface(view_protocol=1)` supplies bounded
templates/assets and a context helper. WHAB owns public data projections,
financial validation, HTTPS, local access guards and per-budget handover.
Helpers and Jinja run inside an immutable approved visual worker after activation.
Installed Python remains trusted executable code; a worker is not a sandbox.

Install the wheel and explicitly select `WHAB_UI=example-ui`. Keep this
singleton UI outside `WHAB_PLUGINS`, which contains optional peers. Four public
pages share a generation and reference immutable generation-specific assets.
The example has no edit controls: its JavaScript freezes navigation before
acknowledging a pending handover, reloads only after an observable generation
change, and keeps a prepared view frozen if an acknowledgement is lost.
An editable interface must retain unsaved drafts and defer its acknowledgement
until edits are saved or cancelled. Never send draft contents in view status.

Build and install outside the checkout using reviewed local host/toolkit/SQLite
wheels and this project's `test` extra. `python -m pytest tests` runs real
CA-verified HTTPS pages/assets, dirty-view deferral, headless disable/re-enable
and saved-generation reopen in a disposable fictional Sandbox home. It makes
no provider connections and changes no OS trust setting.

`whab-plugin check --plugin example-ui --runtime --combinations 10 --seed 42
--wheelhouse /approved/local/wheels --output /tmp/ui-check` also needs the
reviewed JSON and synthetic runtime-peer wheels. Its `native-ui-v1` profile
activates this exact approved wheel in a worker, exercises ten distinct peer
sets/orderings, browser participation, local copy/promotion/migration directions,
public controlled faults and saved-worker reopen. The actual UI host floor is
2.36; older-host N/A is separate from passes. This profile does not claim
distinct-version upgrades or private durable-boundary injection. Replay each
saved manifest with `whab-plugin replay MANIFEST --wheelhouse /approved/local/wheels`.

`--ci-ref` adds a caller for the reusable workflow pinned to a reviewed full
WHAB source commit. Its genuine minimum artifact is 2.36. SDK, package dependency
resolution and runtime capability selection remain separate. No arbitrary
community package is downloaded during conformance or catalog inspection.
