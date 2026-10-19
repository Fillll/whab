# Import from YNAB ZIP file

Distributed independently as `whab_plugin_ynab`. Its version and dependency compatibility range are defined in [pyproject.toml](pyproject.toml); its dated history belongs in [CHANGELOG.md](CHANGELOG.md). Material changes confined to this plugin update its version and changelog, without an app version or app changelog change.

Optional plugin ID: `ynab`; Python package: `whab_plugin_ynab`. It is installed by WHAB's supported-set installer/extra and is disabled by default. It reads a local YNAB ZIP containing one Budget and one Register table, recognized by their English column headers. Both TSV and CSV, UTF-8 and BOM-marked UTF-16 are supported. No YNAB account, API token or network request is needed.

Import panels bind once per element on startup and `whab:page-ready`. During preview or commit, the module cancels `whab:before-navigate` to keep its file, operation identity and response attached to the active view. Successful creation still opens the newly imported budget with a document navigation.

Enable **Import from YNAB ZIP file** through Settings → Plugins, save, and restart. Open the plugin's **Settings** from any page, including Demo, choose the ZIP (at most 10 MiB), enter a budget name (up to 100 characters; an editable suggestion comes from the filename), confirm that its monetary values use USD, and preview. Review the counts and warnings before clicking **Create budget and import**. Each import creates a separate budget with your chosen name in Settings → Budgets and opens its Accounts page for classification. With no saved plugin selection, `WHAB_PLUGINS=ynab` enables it on startup.

The destination is a new empty SQLite budget, so the current budget can already contain accounts or imported data. Existing financial data is never replaced or merged. Preview only reads and validates the ZIP; it creates no budget or financial records. Commit reparses and validates the same SHA-256 fingerprint, and SQLite saves the complete import and receipt in one transaction. Once creation starts, the name stays locked for retries. A failed request can be retried with the same import operation to open the same budget without duplicates or overwriting subsequent edits. Starting a new preview and import creates another separate budget, even for the same archive. Source account records, bank connections, credentials and plugin settings are not copied. The older API for importing directly into a pristine personal budget remains available to integrations.

## Preserved information

- Accounts and category groups/categories, including categories found only in the register.
- Every register row, including identical duplicates and exported split lines, with dates, payees, inflows/outflows, category, memo and clearing status. Payees longer than the 200-character display limit remain in full in the note. Positive WHAB cents mean outflow; negative cents mean inflow. USD confirmation is required because `$` alone is ambiguous.
- Uncategorized `Transfer : ` rows remain transfers. Categorized transfers retain spending/refund behavior. Ready to Assign / To be Budgeted inflows have no expense category.
- Flags and clearing status remain in transaction notes. Cleared/reconciled rows are reviewed. Uncleared rows are unreviewed posted transactions, since YNAB clearing is different from a bank authorization hold.
- Payees longer than 200 characters use a shortened display label; their full original text remains in the transaction note.
- Monthly assigned amounts, including negative reallocations, and the earliest imported month as budget start. WHAB recomputes activity and availability from its ledger.

## Migration limits

The dump omits account types, on-budget/off-budget scope, verified balance snapshots, currency metadata, closed status, targets and scheduled transactions. Register-derived account balances are retained internally but displayed as unavailable; all imported accounts begin as unclassified off-budget accounts and supply no spendable budget funding. Use WHAB's account classification controls after import to choose the account type and budget scope; a balance that remains unavailable still supplies no funding. The importer cannot infer tracking accounts safely from account names or transaction categories. This importer is useful for history and assignments; it is not a complete replacement for a live YNAB budget.

YNAB's exported Activity and Available values are validated but are not stored as envelope balances: WHAB calculates those values under its own rules. Credit-card payment envelopes and rollover totals can differ. The export lacks original category and transaction IDs; identifiers are deterministic hashes of source fields, with an occurrence number preserving identical rows. Renamed categories and overlapping bank transactions cannot be reconciled automatically. Split rows stay separate; no editable parent/split model is reconstructed. Notes that cannot retain the original memo, status and any long payee within WHAB's 1,000-character limit are rejected instead of truncated.

The parser never extracts archive members. It bounds compressed and expanded sizes, member counts and row counts; rejects unsafe paths, symlinks, encrypted members, malformed tables, ambiguous table duplicates and unsupported monetary formats. Errors do not include private record contents.

Run from `whab/app/`:

```sh
.venv/bin/python -m pytest plugins/ingestion/ynab/tests
.venv/bin/python scripts/check_plugin_coverage.py --plugin ynab
```
