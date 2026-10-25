# WHAB

**We Have A Budget**

A Python budgeting app with monthly envelopes and rollover, transaction review, spending analytics and manual accounts. Independently packaged plugins supply interfaces, storage, imports, reports and automation. See the [product overview](../../README.md), [architecture](../docs/architecture.md) and [changelog](CHANGELOG.md). The host version comes from [`src/whab/__init__.py`](src/whab/__init__.py); installed plugin versions come from their distribution metadata.

## Run locally

Use the [macOS launcher](../launchers/macos/README.md) for native installation and process controls. Source development requires **Python 3.14 or newer**. [Existing checkouts must preserve their runtime home](../README.md#existing-development-checkouts) before running setup from the new location. Run application commands from `whab/app/`:

```sh
python3.14 -m venv .venv
.venv/bin/python scripts/install.py --dev --example
.venv/bin/python scripts/preview.py
```

The preview uses a disposable fictional runtime on port 5174. See [preview and verification](../docs/development.md#isolated-preview), including the bounded `--check` mode. For a persistent local runtime, choose a separate absolute `WHAB_HOME`, run `scripts/setup.py`, then `python -m whab` using this environment. Setup creates missing private configuration, a token encryption key and localhost HTTPS certificates without changing OS trust. [Trust the chosen runtime's CA explicitly](../docs/usage.md#local-https). The ordinary listener defaults to [127.0.0.1:5173](https://127.0.0.1:5173); use `--port` to change it and `--reload` for source development.

Keep each existing runtime home, its data and keys together across upgrades. Restart after configuration changes. Manual budgeting needs no Plaid credentials; browser checks use fictional or Sandbox data.

## Configuration and plugins

The `whab` wheel contains the host/core and public SDK. The [supported plugin inventory](plugins/README.md#supported-plugins) defines the independently packaged plugin set, versions' ownership and default selections. `pip install 'whab[bundled]'` selects that set when compatible distributions are available in the chosen package index or wheelhouse. The source installer installs local packages together without requiring unpublished releases from an index; `--example` also installs the teaching plugin.

Installing a plugin does not enable it. Settings selects installed optional plugins for the active budget; portable containers keep their own selections and data. Plugin code runs as trusted server-process Python. See [plugin management](../docs/usage.md#settings-and-plugin-manager), [folder ownership](plugins/README.md) and the [public SDK](../docs/plugins.md).

| Setting | Default | Purpose |
| --- | --- | --- |
| `WHAB_HOME` | Launch directory | Private device configuration and runtime data; choose a separate directory for source development |
| `WHAB_BUDGET` | Unset | Portable `.whab` file to open; also available as `--budget` |
| `WHAB_ENV` | `sandbox` | Sandbox or production data; `PLAID_ENV` is the legacy fallback |
| `WHAB_STORAGE` | `sqlite` | Primary storage for the original workspace and CLI budget creation; existing portable budgets use their manifest |
| `WHAB_UI` | `web` | Visual interface; an empty value runs APIs headlessly |
| `WHAB_PLUGINS` | `plaid` | Original workspace's optional extensions when no saved `plugins.json` exists; portable budgets use their manifest |
| `WHAB_TLS_CERT_FILE` / `WHAB_TLS_KEY_FILE` | `tls/server.crt` / `tls/server.key` | HTTPS certificate and key; override both together, with absolute paths or paths relative to the runtime home |

<a id="limits-and-data-safety"></a>

## Behavior and limits

- **Budgeting:** core owns integer-money calculations, currency isolation, assignments and rollover; Web owns presentation. See [budget scope](../docs/usage.md#budget-scope) for category carryover, overall carryover, opening funds and next-month set-asides.
- **Accounts:** on-budget accounts participate in envelopes; off-budget assets and debts contribute to tracking. Totals stay separate by currency and exclude stale/unavailable snapshots. Investment holdings, live prices and property valuations are not imported.
- **Transactions:** edits save in place, filters apply across the selected range, and Web paginates large registers. See the [current Web contract](plugins/interface/web/README.md) for navigation, review controls, receipts, account settings and balance presentation.
- **Imports:** Plaid history/refresh timing depends on the institution. Plaid and the YNAB importer currently accept USD only. Follow [Sandbox setup](../docs/usage.md#connect-plaid-sandbox), [YNAB migration limits](plugins/ingestion/ynab/README.md) and [import handling](../docs/usage.md#importing-and-data-handling). Production access needs deliberate configuration; Sandbox success does not establish live access.
- **Category suggestions:** the optional [Random Forest plugin](plugins/automation/random-forest-categorization/README.md) trains on reviewed, categorized records from its own budget. Suggestions remain provisional until manual review. Models and training state remain budget-local.
- **Compatibility:** category groups have two levels; only children receive transactions, targets and assignments. There is no currency conversion. Payment categories, editable splits, manual/import reconciliation, editable transfer matching, merchant rules, recurring bills and goals editing are not implemented.
- **Performance:** paged register reads and atomic transaction edits bound routine work, while portable saves still rebuild the complete sanitized ledger and grow with budget size. See [performance verification](../docs/development.md#performance-changes).
- **Access:** every route requires HTTPS. Ordinary routes retain loopback Host/origin checks; explicitly enabled remote operations require bearer authorization and bank webhooks require signatures. HTTPS does not provide a household login system.
- **Private files:** keep configuration, databases, saved budgets, caches, work directories and keys out of Git. Back up the whole runtime securely; portable exports omit bank connections and encryption/TLS keys. An optional password-protected remote-credential export does not encrypt every financial record. See [portable budgets](../docs/usage.md#portable-budgets) and [backup guidance](../docs/usage.md#local-access-and-private-files).

## Development

Install Node.js 22 or newer for the browser tests; the application has no Node build or React runtime. During iteration run focused regression tests and:

```sh
.venv/bin/python scripts/check_pr.py local --base origin/main
```

Required PR CI runs the complete source suite in parallel on Python 3.14 and 3.15, verifies 100% plugin statement/branch coverage and checks installed wheels. Run exhaustive certification manually when needed:

```sh
.venv/bin/python scripts/check.py
```

The exhaustive command also validates toolkit wheels, genuine historical hosts and generated-plugin configuration/replay matrices. It runs after merge and nightly; local focused checks are sufficient before pushing, with required PR CI providing comprehensive source validation. Use `scripts/check.py --docs` for documentation links and inventory only. See [contributing](CONTRIBUTING.md#tests-and-checks) for the timing target and validation policy, [development verification](../docs/development.md) for previews and troubleshooting, and [usage](../docs/usage.md) for user workflows.

## License

WHAB's code, SDK, bundled plugins, example, documentation and assets use the [MIT License](LICENSE). Commercial use, proprietary plugins and forks are permitted with the required notices. Third-party dependencies, services and institution logos retain their own licenses or terms.
