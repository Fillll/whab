# SQLite storage plugin

`src/whab_sqlite/` owns the bundled SQLite descriptor, ledger transactions, additive schema upgrades and fictional seeding. Keep its implementation and tests in this folder. The host uses the public storage protocol rather than importing this package.

Preserve integer cents, workspace/environment isolation, legacy filenames, saved annotations and atomic synchronization cursors. Compatibility fixtures use temporary databases.

From the application directory, run `.venv/bin/python -m pytest plugins/sqlite/tests`. See [plugin contribution rules](../README.md) for all checks and packaging.
