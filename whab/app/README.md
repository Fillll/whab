# WHAB

**We Have A Budget**

A highly extensible budgeting app built with Python, with monthly envelopes and rollover, transaction review, spending analytics and manual accounts. Plugins supply replaceable interfaces, storage, imports, reports and additional workflows.

See the [architecture and Mermaid diagrams](../../README.md#architecture), [runtime architecture](../docs/architecture.md) and [budget and plugin model](../docs/target-architecture.md). Current host version: **2.10.0** ([changelog](CHANGELOG.md)).

The [client/server deployment option](../../README.md#clientserver-deployment) uses the same architecture: a client plugin connects an interface to a server plugin exposing an API interface capability.

## Run locally

Use the [macOS launcher](../launchers/macos/README.md) for native installation and process controls. Source development requires **Python 3.14 or newer**. [Existing checkouts must preserve their runtime home](../README.md#existing-development-checkouts) before running setup from the new location. Run these commands from this application directory (`whab/app/`):

```sh
python3.14 -m venv .venv
.venv/bin/python scripts/install.py --dev
.venv/bin/python scripts/setup.py
.venv/bin/python -m whab
```

Setup generates a local CA and localhost certificate without changing OS trust. [Trust that CA explicitly](../docs/usage.md#local-https), then open [127.0.0.1:5173](https://127.0.0.1:5173). Explore fictional data in **Demo Budget**, or choose **Settings → Budgets → My Budget** and add a manual account. Settings shows the current budget. No Plaid credentials are needed for manual budgeting. Use `--reload` while developing or `--port 5174` for another local port.

Setup creates private runtime configuration, a token encryption key and TLS material if missing. Every request requires HTTPS; there is no HTTP fallback. Runtime files live in the launch directory, or the absolute directory set by `WHAB_HOME`. Keep the existing runtime home, data and encryption key together when upgrading, and restart after configuration changes. See [runtime setup](../docs/usage.md#runtime-setup) for upgrades and saved-data compatibility.

Browser views use `/budgets/<budget-slug>/<page>/<month>/<currency>`, with every path segment lowercase. Account registers add an account-name alias before `transactions`, such as `/budgets/my-budget/daily-checking/transactions/current-month/usd`. Names are transliterated into readable aliases; duplicate names get numeric suffixes. Permanent IDs still own API/data scope, and old names, UUID addresses, `home` and account-ID query links keep working. The sample budget remains `demo`. See [budget addresses](../docs/usage.md#budget-addresses).

The web budget shows three consecutive months in one category table. Compact month cards show the month, year and Carryover control. The overview has one amount per month for **Income**, **Carried in**, **Assigned for next month**, **Assigned this month** and **Available to assign**. Available to assign equals income plus carry-in minus the explicit next-month set-aside and this month's category assignments. Outlined fields mark editable next-month set-asides and individual category assignments. The set-aside transfers funding to the following month independently of that month's category plans. Core validates assignment and next-month set-aside increases against monthly funding and available account cash. Detailed **Carryover** (when applicable), **Assigned**, **Activity** and **Available** columns sit under Spending categories. Use the month strip to move the window and collapse category groups. Settings stays at the bottom left; the web plugin owns presentation and core owns budget rules.

Each month has a **Carryover** control for the selected currency: **By category** keeps each category's positive or negative balance in that category next month; **Overall balance** resets category carryovers and returns their signed net to the general budget. Category rollover is the default. Rollover choices save even when they produce a funding shortfall; the affected month shows a red warning and signed negative balances remain visible. The choice does not change saved assignments or account balances. **Carried in** contains previously unassigned money, the previous month's explicit set-aside for this month, and any overall category balance released at that boundary. Its expandable breakdown shows each component. By-category balances appear only in the detailed Carryover columns and stay outside the top carry-in row. The first budget month includes eligible opening funds; imported income is not added to account snapshots again. These calculations allocate existing money without moving it between accounts.

## Configuration and plugins

The core owns budgeting rules, HTTPS access checks and plugin contracts. The `whab` wheel contains the host/core and public SDK; its seven supported plugins are independently packaged and versioned. When compatible distributions are available in the chosen package index or local wheelhouse, `pip install 'whab[bundled]'` selects the supported set. The source installer installs the checkout's local packages together without requiring unpublished plugin releases from an index. Installing a plugin does not enable it. Concrete implementations live under `plugins/<category>/<id>/`, each with its own source, assets, tests and documentation. The singular categories are `interface`, `ingestion`, `insight`, `storage`, `automation`, `extension` and `example`; see [plugin folders and naming](plugins/README.md).

| Setting | Default | Purpose |
| --- | --- | --- |
| `WHAB_HOME` | Launch directory | Location of private device configuration and runtime data |
| `WHAB_BUDGET` | Unset | Portable `.whab` file to open; also available as `--budget` |
| `WHAB_ENV` | `sandbox` | Sandbox or production data; `PLAID_ENV` is the legacy fallback |
| `WHAB_STORAGE` | `sqlite` | Required primary storage for the original workspace and CLI budget creation; existing portable budgets use their manifest |
| `WHAB_UI` | `web` | Visual interface; an empty value runs APIs headlessly |
| `WHAB_PLUGINS` | `plaid` | Original workspace's optional extensions when no saved `plugins.json` exists; portable budgets use their manifest |
| `WHAB_TLS_CERT_FILE` / `WHAB_TLS_KEY_FILE` | `tls/server.crt` / `tls/server.key` | HTTPS certificate and private key; override both together, using absolute paths or paths relative to the runtime home |

Open **Settings → Budgets** to see every budget remembered by this runtime and **Demo budget** in one list. Each row has a **Download** action, and one row is marked **Current budget**. Select a row, enter its password if required, and choose **Open selected budget**. Below the list, **Create new budget** starts a separate budget from the entered name, while **Open existing budget** opens a portable `.whab` file, optionally as a separate budget. These red actions open the chosen budget and leave Settings after success; errors stay in the dialog. Downloads keep the current selection, and demo downloads contain only fictional data. Saved-copy and remote-credential controls remain under **Saved copies and export options**. The core owns budget creation, isolation and exports; the web plugin presents the controls. Local containers include the sanitized database, while remote containers hold connection information. Credentials remain on this device by default; an optional password-protected export can include a remote access credential. Bank-provider tokens are never included. See [portable budgets](../docs/usage.md#portable-budgets) for command-line opening and saved copies.

Use **Settings → Plugins → Installed** to enable or disable installed optional plugins for the active budget, save, and restart. Portable budgets save their own selections. The original local workspace keeps its saved `plugins.json` selection, which takes precedence over `WHAB_PLUGINS`. Disabling a bank plugin retains imported history and saved connections. The **Bank logos** plugin is in the supported set but disabled by default. The separately installed [teaching example](plugins/example/example/README.md) is hidden from ordinary Settings, including Plugin Settings; use its [developer setup](../docs/plugins.md#install-the-working-example) to activate it. Valid existing example selections are preserved when saving other plugins; unavailable example registrations are dropped so they cannot block saving. Categories and plugin names are alphabetized, with distinct category headings. Choose **All categories** or a category to browse installed plugins, then open **Settings** for plugin details and controls. Bank logos Settings reports indefinite cache duration and provides **Flush cache**. Flushing removes saved artwork and failed requests while keeping bank websites and account settings; images download again when next requested. Plugins run as trusted server code.

For bank imports, follow the [Plaid Sandbox guide](../docs/usage.md#connect-plaid-sandbox). Keep `WHAB_ENV=sandbox` and `ALLOW_LIVE_PLAID=false` for test data. Production access requires deliberate configuration and institution access; a successful Sandbox flow does not establish live access.

Plaid merchant logos are included with the existing **Plaid** plugin. Transactions display the artwork supplied by Plaid when available; missing artwork leaves the payee text visible. Only public logo files are downloaded, through guarded local image routes with a bounded memory cache (24 hours for successes, five minutes for failures). No merchant lookup service, LLM key or separate merchant-logo plugin is needed. Existing imported transactions receive artwork only when Plaid supplies them again in an import or update; enabling this feature does not reconnect a bank or automatically backfill history. See the [Plaid plugin guide](plugins/ingestion/plaid/README.md).

For a YNAB ZIP dump, enable **ynab** in the plugin manager, save, and restart. Open **Settings → Plugins → Import from YNAB ZIP file → Settings**, choose the ZIP, confirm USD, and preview the counts and limitations. **Create budget and import** creates and opens a new isolated budget; your existing budget stays intact. Preview creates no budget or financial records. See the [YNAB plugin guide](plugins/ingestion/ynab/README.md) for supported formats and migration limits.

The compact transaction register saves category, memo and review edits in place; multi-select is independent of review status. Filter using multiple accounts with optional bank logos, This month, Previous month or inclusive custom dates, and an R button that cycles Needs review, Reviewed and All. Open a sidebar account to switch the main register without reloading the shell. **Account settings** edits names and classification, allows currency changes before transaction history, and offers a plugin-labelled bank website field when Bank logos is active. Enter a bare domain or full website URL; **Retrieve logo** previews it immediately without saving the account. A missing logo never blocks account edits. The website domain is sent to Icon Horse for public artwork. Optional receipt images stay inside SQLite budgets and portable exports, with no external upload.

## Limits and data safety

- **Budget and tracking accounts:** on-budget checking, savings, cash and credit accounts participate in envelopes. Off-budget investments, retirement accounts (IRA, Roth IRA, 401(k) and others), property, loans and fixed-term deposits (including CDs) contribute to assets, liabilities and net worth without funding or consuming envelopes. Choose the scope, type and detail when adding an account, or use **Accounts → Edit classification** for an existing account. Account totals stay separate by currency and exclude stale or unavailable balances. Loan and credit balances use a positive amount owed. Investment holdings, prices and automatic property valuations are not imported.
- **HTTPS and local access:** WHAB binds to `127.0.0.1` with TLS and checks Host/origin for ordinary routes. Only enabled remote protocol routes and registered bank webhooks bypass those local checks: remote routes require bearer authorization and webhooks require provider signature verification. HTTPS encrypts transport for all routes; it does not supply a family login system.
- **Multiple currencies:** accounts and child categories declare an ISO currency; each currency has separate balances, activity, rollover and allocations. There is no automatic conversion. Money uses integer currency minor units. Plaid and the bundled YNAB importer currently accept USD only.
- **Two category levels:** parent groups organize child categories. Only children receive transactions, targets and allocations. Core dates use `YYYY-MM-DD`; USD money uses `$1,234.56` (other currencies use their ISO code and fixed minor-unit precision). A selected interface plugin can override presentation.
- **Initial budgeting model:** credit-card handling conservatively reserves debt. Payment categories, splits, manual/import reconciliation, editable transfer matching, merchant rules, recurring bills and goals editing are not implemented.
- **Transactions only:** investment holdings are not imported. Bank history and refresh timing depend on the institution and Plaid.
- **Private local files:** keep `.env`, `plugins.json`, `data/`, runtime `budgets/`, `tls/`, saved `.whab` files, `cache/`, `work/` and `.venv/` out of Git. Back up the runtime home, including portable-budget runtime data, provider credentials, token encryption key and TLS private keys, securely before upgrading. Portable exports omit bank connections, provider credentials, token encryption keys and TLS keys. Replacing the token encryption key makes saved bank tokens unreadable; see [backup guidance](../docs/usage.md#local-access-and-private-files).

See [budget behavior](../docs/usage.md#budget-scope), [import handling](../docs/usage.md#importing-and-data-handling) and [backup guidance](../docs/usage.md#local-access-and-private-files) for details.

## Development

Run checks from this directory:

```sh
.venv/bin/python -m pytest
.venv/bin/python scripts/check_plugin_coverage.py
.venv/bin/python -m ruff check src scripts tests plugins conftest.py
.venv/bin/python scripts/check_distribution.py
```

Tests use fictional data and mocked services. CI checks Python 3.14 and 3.15 and requires 100% Python statement and branch coverage for every plugin.

- [Local setup and usage](../docs/usage.md) — runtime files, plugin manager, Plaid, budgets and backups
- [Budget and plugin model](../docs/target-architecture.md) and [runtime architecture](../docs/architecture.md)
- [Plugin author guide](../docs/plugins.md) and [folder ownership](plugins/README.md)
- [Contributing](CONTRIBUTING.md) — checks, PR workflow, SemVer compatibility and branch protection

## License

WHAB's code, public plugin SDK, bundled plugins, example plugin, documentation and assets use the [MIT License](LICENSE). Commercial use, proprietary plugins and commercial forks are permitted with the required notices. Third-party dependencies, services and institution logos retain their own licenses or terms.
