# Fictional account import

This parser accepts at most 16 KiB of UTF-8 JSON with exactly `name` and integer `balance` fields. It produces a validated USD-only `WorkspaceImport` with one fictional manual account. Duplicate fields, malformed input, monetary booleans and invalid snapshots are rejected. The host owns preview, authorization, financial validation and atomic import; the parser never writes to a ledger.

For example, `{"name": "Fictional checking", "balance": 12500}` describes a balance of USD 125.00. No bank provider or external service is involved.

This independently packaged teaching plugin requires Python 3.14+ and WHAB 2.22+ within major 2. It advertises restart lifecycle support. WHAB 2.26+ hides it from ordinary Settings; enable `example-file-import` explicitly. See the [teaching guide](../README.md).

Build and test from a copy of this folder outside the WHAB checkout, in a clean environment containing the approved host and toolkit artifacts:

```sh
python -m pip wheel --no-deps . --wheel-dir /tmp/teaching-wheels
python -m pip install --no-index --find-links /approved/local/wheels '.[test]'
python -m pytest tests
whab-plugin check --plugin example-file-import --combinations 10 --seed 42 --wheelhouse /approved/local/wheels --output /tmp/example-file-import-check
```

The configuration check also needs the installed SQLite reference distribution and the exact wheels for all installed runtime dependencies in the approved wheelhouse. Project tests use public SDK contracts; no other concrete plugin is imported. Installed plugins are trusted executable Python. This example is a starting point, with bounded fictional behavior, rather than production provider certification.
