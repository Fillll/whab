# SQLite storage plugin

`src/whab_sqlite/` owns the bundled SQLite descriptor, ledger transactions, additive schema upgrades and fictional seeding. Keep its implementation and tests in this folder. The host uses the public storage protocol rather than importing this package.

Preserve integer currency minor units, separate currency totals, workspace/environment isolation, legacy filenames, saved annotations and atomic synchronization cursors. Accounts and child categories persist their currency; existing rows acquire `USD` through additive columns without changing category IDs or amounts. Parent groups derive from child group labels and cannot receive transactions or allocations. Compatibility fixtures use temporary databases.

From the application directory, run `.venv/bin/python -m pytest plugins/storage/sqlite/tests`. See [plugin contribution rules](../../README.md) for all checks and packaging.
