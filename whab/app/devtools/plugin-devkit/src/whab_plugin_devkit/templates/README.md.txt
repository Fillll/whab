# __PLUGIN_ID__

An independently packaged teaching plugin built with the public WHAB SDK.
This project needs Python 3.14+ and WHAB 2.22+ within major 2. It is trusted
executable Python, with restart-only lifecycle support. Installing it does not
enable it. It creates no bank connection and uses no external service.

Build a wheel and install it with the host and test dependencies in a clean
environment outside the WHAB checkout. Run `python -m pytest tests` from this
project. Then run `whab-plugin check --plugin __PLUGIN_ID__ --combinations 10
--seed 42 --wheelhouse /approved/local/wheels --output /tmp/plugin-check`.
The configuration check also needs the installed SQLite reference distribution.

Generate this project with `--ci-ref` and a published, reviewed full WHAB commit
to include `.github/workflows/whab.yml`. It calls WHAB's reusable workflow pinned
to that commit, building the genuine minimum and candidate host artifacts. The
workflow builds this project, verifies installed wheel bytes and discovery, runs
these behavioral tests with full statement/branch coverage, then runs ten public
teaching-hook configurations and all SQLite reference suites with exact replay.
No provider connection is created. Caller project/build/test code remains trusted
executable code; the workflow supplies no write credentials or inherited secrets.

Additional build/runtime/test dependencies need reviewed local wheels. Point the
workflow's optional `extra-wheelhouse-directory` input at their directory in the
caller checkout, or use `extra-wheelhouse-artifact` for wheels uploaded by an
earlier caller job. Choose one input, and keep generated wheels out of Git.
Missing wheels fail installation; candidate dependencies are not
downloaded automatically. A candidate requiring a newer host must change its
compatibility floor and the corresponding minimum-artifact recipe together.

The report/export example consumes one-currency immutable snapshots. Branding
uses a fixed fictional institution and embedded synthetic PNG, without networking.
File import accepts a bounded USD-only JSON account with `name` and integer
`balance` fields; the host owns import validation and ledger changes.
The categorization example stores one exact-merchant model per budget namespace,
learned only from reviewed posted rows and keyed by currency. Its `1.0` suggestion
score means an exact teaching-rule match, not measured predictive confidence.
It never changes the ledger. Delete its previous model before saving another.

These deliberately small examples are starting points, not production provider
certification. Add capability-specific regression tests when extending them.
