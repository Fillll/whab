# SQLite storage plugin

Distributed independently as `whab-sqlite-plugin`. Its version and dependency compatibility range are defined in [pyproject.toml](pyproject.toml); its dated history belongs in [CHANGELOG.md](CHANGELOG.md). Material changes confined to this plugin update its version and changelog, without an app version or app changelog change.

`src/whab_sqlite/` owns the bundled SQLite descriptor, ledger transactions, additive schema upgrades and fictional seeding. Keep its implementation and tests in this folder. The host uses the public storage protocol rather than importing this package.

Preserve integer currency minor units, separate currency totals, workspace/environment isolation, legacy filenames, saved annotations and atomic synchronization cursors. Accounts and child categories persist their currency; existing rows acquire `USD` through additive columns without changing category IDs or amounts. Parent groups derive from child group labels and cannot receive transactions or allocations. Compatibility fixtures use temporary databases.

Portable plugin budget containers use `export_budget_files()` and `restore_budget_files(workspace, files)`. SQLite owns a single `budget.sqlite` image (payload format 1, at most 50 MiB). Export rebuilds a fresh database from a coherent validated ledger snapshot; it never copies the live database. The image contains personal ledger history, currencies, allocations and annotations, with no demo ledger, bank connections, tokens, cursors or account connection metadata. The source ledger and its active connections remain unchanged.

Accounts persist their core `onBudget` scope and optional `subtype`. Existing ledgers and earlier portable images infer on-budget scope for checking/savings (`depository`) and credit cards (`credit`), and off-budget scope for other account types. New account classifications survive reopening, portable roundtrips and provider refreshes. Scope controls envelope budgeting independently of the account's asset or liability type.

Opening an image checks its exact supported schema and bounded rows in memory, rejects triggers, views and additional schema objects, and reads ledger records as data. Restore uses the existing atomic empty-personal-ledger import boundary, leaves the installation's demo ledger intact and marks external account snapshots stale. It never executes SQL scripts or migrations supplied by a budget file. The earlier neutral `export_budget()` and `restore_budget()` methods remain available for version 1 budget files.

The container lifecycle calls `prepare_budget_files(workspace, files, existing=False)` on first open. A retry with the same normalized ledger is harmless; different nonempty data is preserved and rejected. With `existing=True`, an established local ledger remains authoritative and retains its private bank connections; a pristine replacement cache is restored from the image. Every path validates the input image before using the cache.

From the application directory, run `.venv/bin/python -m pytest plugins/storage/sqlite/tests`. See [plugin contribution rules](../../README.md) for all checks and packaging.
