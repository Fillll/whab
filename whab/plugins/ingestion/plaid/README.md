# Plaid bank plugin

`src/whab_plaid/` owns the descriptor, payload normalization, encrypted tokens, Link, synchronization, signed webhooks, templates and connection script. Keep supporting modules, assets and mocked provider tests in this folder.

Tests use fictional credentials, in-memory storage, temporary homes and HTTP mock transports. Preserve encrypted-token compatibility, pending-to-posted annotations and atomic imports/cursors.

From the application directory, run `.venv/bin/python -m pytest plugins/ingestion/plaid/tests`. See [plugin contribution rules](../../README.md) for all checks and packaging.
