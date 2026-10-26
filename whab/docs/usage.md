# Local setup and usage

Detailed operating guidance for WHAB. Start with the [quick start](../app/README.md#run-locally); see the [architecture](architecture.md) and [plugin API](plugins.md) for implementation contracts.

## Contents

- [Runtime setup](#runtime-setup)
- [Local HTTPS](#local-https)
- [Budget addresses](#budget-addresses)
- [Portable budgets](#portable-budgets)
- [Settings and plugin manager](#settings-and-plugin-manager)
- [Connect Plaid Sandbox](#connect-plaid-sandbox)
- [Importing and data handling](#importing-and-data-handling)
- [Budget scope](#budget-scope)
- [Local access and private files](#local-access-and-private-files)
- [Official references](#official-references)

## Runtime setup

Run the commands below from the application directory (`whab/app/`).

Requires **Python 3.14 or newer**. Older Python versions are unsupported and rejected by package installation. For development, use an editable source install as shown below. The source installer installs the host and the [supported plugin set](../app/plugins/README.md#supported-plugins) together. Templates and static assets belong to their owning plugin wheels. When compatible host/plugin wheels are available in the chosen package index or local wheelhouse, `pip install 'whab[bundled]'` selects the same set; a wheel deployment must also provision the private runtime configuration and valid TLS certificate/key described below. Use the [development runbook](development.md) for disposable runtimes, iteration checks and verification of the running installation.

```sh
python3.14 -m venv .venv
.venv/bin/python scripts/install.py --dev
.venv/bin/python scripts/setup.py
.venv/bin/python -m whab
```

When upgrading from an older Python version, stop WHAB and recreate `.venv` with Python 3.14 or newer, then reinstall the app and any additional plugins. Preserve the existing runtime home, including `.env`, `data/`, `budgets/` and `tls/`, with its token encryption key and credentials.

After [trusting the local CA](#local-https), open https://127.0.0.1:5173. **Demo Budget** contains fictional data. Open **My Budget** for your own accounts. A manual account works immediately without Plaid.

The setup script uses `WHAB_HOME` when set, otherwise the application directory (`whab/app/`). It creates a private `.env` and random 32-byte token encryption key when the file is missing, plus local TLS material when not already present. It preserves existing credentials and encryption keys, and migrates the earlier local API port to 5173. After changing `.env`, restart the server. For new source setups, launching from `whab/app/` uses that directory's `.env` and `data/`. Existing checkouts retain their ignored files under the previous `whab/` location; set `WHAB_HOME` to that directory's absolute path before setup or launch, and [recreate the development environment](../README.md#existing-development-checkouts). To reuse runtime files kept elsewhere, including an older checkout root, set `WHAB_HOME` to the absolute directory containing those files before launching; the default is the working directory. Keep an existing runtime home and encryption key together when upgrading. Code and installed assets never determine the data location. `WHAB_ENV` selects `sandbox` or `production`; existing `PLAID_ENV` remains the fallback for upgrades. Use `.venv/bin/python -m whab --reload` while developing, or `--port 5174` to choose another local port.

For the original local workspace and initial `--create` CLI budget, `WHAB_STORAGE=sqlite` is the default storage plugin. An opened portable budget instead uses its manifest's storage selection; changing `WHAB_STORAGE` does not change that budget. Settings’ **Create new budget** action creates SQLite budgets. SQLite is embedded: there is no database server to install, and its plugin opens or creates the database under the runtime home. A single existing `data/*-{environment}.sqlite` file is reopened in place; a noncanonical filename requires base-schema validation. With no matching file, the plugin creates `data/whab-{environment}.sqlite`. Multiple matches fail safely, even if one has the canonical filename. Saved budgets and encrypted Plaid tokens remain supported; keep the same home and encryption key when upgrading. One primary storage backend is required, including when optional bank/report plugins are disabled. Portable budgets can save additional copies without creating separate ledgers. Selecting another installed storage plugin does not copy or migrate existing SQLite data automatically; configure the chosen backend's data location deliberately.

The GitHub repository is [Fillll/whab](https://github.com/Fillll/whab), and the Python package and launch command are only `whab`. Use `python -m whab` in launch commands. Browser OAuth session keys use `whab`, so finish any in-progress Plaid Link flow before upgrading. New Link tokens use `whab-local-household`; saved connected Items and their encrypted tokens remain unchanged. The local checkout path can remain unchanged.

## Local HTTPS

WHAB 2.0 requires HTTPS for every request, including localhost pages, APIs and bank webhooks. The launcher binds `127.0.0.1` with native TLS and refuses missing, invalid, expired or mismatched certificates/keys. There is no HTTP fallback or redirect, and forwarded proxy headers cannot turn a plaintext request into an accepted HTTPS request. Update bookmarks, local API clients and Plaid redirect configuration from earlier HTTP installations.

`scripts/setup.py` creates `tls/ca.crt` and the private `tls/ca.key` under the runtime home, then a localhost certificate at `tls/server.crt` with private key `tls/server.key`. The leaf certificate covers `localhost` and `127.0.0.1`. The local CA lasts ten years; the server certificate lasts about one year. Existing valid material is preserved. Setup prints the CA fingerprint and trust instructions; **it never modifies OS or browser trust**.

Trust the public CA certificate yourself before using the browser. On macOS, import that runtime home's `tls/ca.crt` into your login keychain with Keychain Access, open the imported certificate, expand **Trust**, and set its SSL trust to **Always Trust**. Confirm the fingerprint matches the setup output and restart the browser. On other systems, import that public CA into the OS/browser's trusted certificate-authority store; browsers with a separate store need their own import. Do not import or distribute `ca.key` or `server.key`, and do not bypass certificate errors.

A command-line client can trust only this CA for an individual request without changing OS trust:

```sh
curl --cacert /absolute/path/to/runtime/tls/ca.crt https://127.0.0.1:5173/api/plugins
```

To use an existing certificate, set **both** `WHAB_TLS_CERT_FILE` and `WHAB_TLS_KEY_FILE`. Absolute paths are accepted; relative paths resolve under `WHAB_HOME` or the launch directory. Without overrides the defaults are `tls/server.crt` and `tls/server.key`. The certificate must cover the hostname used by the client and chain to a CA that client trusts. Keep the private key readable only by its owner. A remote client additionally needs a certificate trusted for its configured HTTPS server origin; the generated localhost certificate does not cover a public hostname.

## Budget addresses

Browser addresses identify the saved budget and selected view:

```text
/budgets/<budget-slug>/<page>/<month>/<currency>
/budgets/<budget-slug>/<account-slug>/transactions/<month>/<currency>
```

Every segment is lowercase, including the currency code. Visible budget names and ISO currency labels keep their ordinary capitalization. API payloads and stored currencies continue to use uppercase ISO codes such as `USD`; lowercase is the browser address policy.

| View | Address |
| --- | --- |
| Original budget, current month | `/budgets/my-budget/budget/current-month/usd` |
| Original budget, fixed month | `/budgets/my-budget/transactions/2026-10/eur` |
| Fictional sample budget | `/budgets/demo/accounts/current-month/usd` |
| Named budget | `/budgets/family-budget/budget/current-month/usd` |
| Account register | `/budgets/family-budget/daily-checking/transactions/current-month/usd?all_dates=true` |

Budget and account names produce readable lowercase aliases. Accented Latin names are normalized, Cyrillic names are transliterated, and punctuation becomes hyphens. Duplicate names get short numeric suffixes; IDs provide a fallback when a name cannot produce a slug. The permanent IDs remain financial identities. The device-local `WHAB_HOME/budgets/routes-<environment>.json` registry retains previous aliases so a rename does not break bookmarks or let a duplicate name capture them. That registry is separate from portable `.whab` files. Legacy UUID budget addresses, the original budget's `home` alias, and `?account=<id>` links remain supported. The browser replaces them with the readable address without loading the page again.

`demo` identifies one separate **Demo Budget** backed by the startup runtime's fictional data. There is no `personal` or workspace segment, and choosing demo does not select a second dataset inside each real budget. The page is `budget`, `transactions`, `insights` or `accounts`.

`current-month` resolves to the current calendar month whenever the page loads. A bookmark with it follows the calendar; `2026-10` remains October 2026. On the Budget page the month is the first of the three displayed months. Page and currency changes retain `current-month`; moving the month arrows selects an explicit month. Optional view selections, such as a category, may remain query parameters.

The clean root address `/` opens the initial demo view or the selected saved budget. When launched with `--budget` or `WHAB_BUDGET`, it opens that budget's readable address even without a browser selection cookie. That launch starts the selected portable runtime; it does not also open the original local budget at `home`. Its one Demo Budget uses the startup runtime's sample data. Old `/?page=...&workspace=...&month=...&currency=...` view addresses are no longer supported and are not redirected. Use the canonical address shown in the browser for bookmarks. Plaid's OAuth continuation parameter remains supported for an in-progress connection.

Each open tab resolves its address to one permanent budget ID; API calls, plugin assets and downloads retain that ID scope. Switching a budget in another tab does not retarget its financial operations. Existing unscoped local API integrations remain available with their documented session and workspace behavior.

## Portable budgets

A `.whab` file is a portable container with a required plugin manifest and plugin-owned data. A local SQLite budget includes its database. Remote storage contributes its connection information alongside the manifest and saved plugin state, leaving financial records on the server; opening or saving that connection does not automatically download a financial snapshot. Plugin code is installed in WHAB, outside the container.

Open **Settings → Budgets → Open budget** to see saved budgets and the single **Demo budget** destination together. Select a row and choose the red **Open selected budget** action, entering its password if required. Exactly one row is marked **Current budget**. Each row has a separate **Download** button that leaves the current selection unchanged. A demo download contains only fictional records and has a fresh identity when opened. The shared host remembers created/opened budgets in `WHAB_HOME/budgets/index-<environment>.json`; budgets do not discover one another. Each portable budget has its own storage and plugin context within that host.

Below the list, **Create new budget** contains **New budget name** and a red **Create budget** button. It creates an isolated SQLite budget and opens its dataset. **Add budget** follows another separator with a file chooser, an optional password and **Add as a separate budget**. Its red **Add budget** button adds the file to the list and opens it. All three red actions leave Settings after success; errors keep the dialog open.

The Budgets screen adds `.whab` files under **Add budget**. If a file's remote access credential is password-protected, enter the **Budget password** before adding it. A conflicting revision can be preserved using **Add as a separate budget**: the contents get a new budget identity without overwriting or merging the existing budget. Each row’s **Download** exports that budget with credentials kept on this device. Additional export and saved-copy controls appear under **Saved copies and export options** below creation and adding.

The container remembers its selected plugins, primary storage, settings and saved plugin data. Missing optional packages are reported without blocking opening, and their data stays preserved; required storage and the selected installation UI must be installed and compatible. Disabled or unavailable plugins’ payload files are retained. Version 2 uses namespaced files such as `plugins/sqlite/budget.sqlite` and `plugins/client/connection.json`; existing version 1 manifest/ledger JSON bundles remain supported. Installing missing plugin packages is a separate step.

To transfer a remote access credential, expand **Saved copies and export options**, use the additional export form, select **Include remote access credential**, enter a password, then choose **Export budget**. Use a password of 12–1,024 characters; the recipient enters it when opening or switching to that file. This option protects the remote credential; it does not encrypt all financial/plugin data in the container. Bank-provider tokens and token encryption keys are never carried. Without this option, configure credentials on the destination device and approve the matching remote URL and budget identity.

Open a container from the command line:

```sh
.venv/bin/python -m whab --budget /absolute/path/Household.whab
```

`WHAB_BUDGET` is the equivalent environment setting. Use `--create Household` with a new `--budget` path to create a budget named Household. Add `--copy /absolute/path/Backup.whab` for another saved copy. To unlock a password-protected remote credential, set a private environment variable and pass its name with `--budget-password-env VARIABLE_NAME`; the password itself stays out of command arguments.

Multiple destinations are copies of the same container. Successful saves are retained when another destination is unavailable; the status shows pending copies. In Settings, enter a **Saved copy name** and an absolute **Save location on this device**, then choose **Add saved copy**. Use **Save copies** to retry pending destinations after restoring access. Local financial data remains private even when the container contains no credentials, so store its copies accordingly.

## Settings and plugin manager

Open the gear beside **Local workspace** to find **Budgets**, **Plugins**, and **About this prototype**. **Budgets → Open budget** shows the current budget in a unified list of saved budgets and **Demo budget**. Select a row and choose **Open selected budget**, or download any row without switching. **Create new budget** and **Add budget** follow in dedicated sections. Switching budgets keeps the page, month and currency while clearing the previous category selection. Budget selection lives in Settings on desktop and mobile. The top bar shows the budget name, currency and data mode.

Open **Plugins → Installed** to see installed plugin packages and their running and saved states for the active budget. Categories and plugin names are alphabetical, with separate category headings. Teaching example plugins are hidden from Settings, including Plugin Settings; use the [developer setup](plugins.md#install-the-working-example) to activate or remove them. Saving other choices preserves valid existing example selections. **All categories** is the default view; choose a category to narrow the list. Each row has a selection checkbox and **Settings**, which opens that plugin’s details and available controls. Inactive plugins show enable/restart guidance without executing their settings hooks. Plugins without custom controls still have a details page. Enable or disable optional bank/report/branding plugins, then choose **Save plugin selection**. Changes apply after restarting WHAB. Select the installation's interface through `WHAB_UI` and the original workspace's storage through `WHAB_STORAGE`; existing portable budgets use the storage in their manifest, and Settings creates new SQLite budgets. Storage is required, and an empty `WHAB_UI` runs headlessly.

For a portable budget, saving updates its plugin manifest and preserves disabled plugins’ settings and saved state. Other budgets keep their own selections. The original local workspace retains its private runtime-home `plugins.json`; its validated optional selection takes precedence over `WHAB_PLUGINS` on the next start, with the environment setting used when no saved selection exists. Save loads and validates the selected descriptors without running their factories or hooks, then writes the file atomically. Plugin discovery reads package metadata without loading disabled code. The manager selects already-installed packages; package installation remains a separate local step. Unavailable selected optional plugins can be unchecked before saving. Missing optional packages in a portable budget are reported while preserving their data. Missing packages in the original workspace's selected list, invalid descriptors, required storage/UI failures, and invalid or unreadable saved local settings prevent startup; repair the file, or save a replacement through Settings while WHAB is still running.

Disabling a bank plugin takes effect on restart and retains imported history and saved connections. Saving a selection does not disconnect a bank or activate new connections. Portable budget downloads include the saved feature setup. For the original local workspace, back up `plugins.json` with the runtime configuration; removing it restores the environment default.

If plugin startup fails before Settings is available, run `python -m whab --recover-plugins`
with the affected runtime home and, when applicable, its `--budget` path. The
[minimal plugin recovery page](plugin-recovery.md) inspects metadata without opening
plugin factories, supports explicit optional-plugin disabling and worker-reference
archival, and retries the full application on the same local HTTPS listener.

### YNAB import

Enable **Import from YNAB ZIP file**, save and restart, then open its **Settings**. Choose the ZIP, enter a budget name (the filename supplies an editable suggestion), confirm USD and **Preview import**. **Create budget and import** creates a separate budget with that name, adds it to Settings → Budgets, and opens its Accounts page for classification. Preview creates nothing; retrying a failed commit opens the same new budget. The form is available in the plugin's settings from any page, including Demo. Imported account estimates appear in the sidebar with their source explained on hover and for screen readers; Accounts management still shows **Unavailable** for the unverified balance. Those estimates do not fund the budget or contribute to net-worth totals.

### Optional bank logos

The bundled **bank-logos** plugin is disabled by default. After upgrading a source checkout, refresh local package registrations with `.venv/bin/python scripts/install.py --dev`. In **Settings → Plugins → Installed**, enable **bank-logos**, save, and restart WHAB. When active it is named **Bank logos (Icon Horse)**. Without a saved selection, `WHAB_PLUGINS=plaid,bank-logos` selects it instead. Disable it through the same manager and restart to return to the existing icons and initials.

Logos appear beside accounts in the sidebar and Accounts page. A curated list matches institution names to public website domains, including Chase, Capital One, Bank of America, Wells Fargo, Citi, Fidelity, Robinhood and other common institutions. Unknown institutions, manual accounts and fictional demo/Sandbox banks keep their existing fallback. Logos are website icons and availability or artwork can vary; they do not establish a bank connection or institution support.

Icons come from [Icon Horse](https://icon.horse/), with no API key. In an account register, open **Account settings → Account logo**, enter a domain such as `robinhood.com` (HTTPS is optional in this field), then optionally choose **Retrieve logo**. The preview checks the source before saving metadata. Full URLs are reduced to their public hostname; unusable input or missing artwork never blocks saving an account. Saving without retrieving lets the sidebar image request resolve later.

WHAB sends only the public bank domain to Icon Horse, without account names, IDs, masks, balances, transactions, credentials or tokens. The service sees WHAB's server IP and the requested domain. Browsers load images from WHAB's guarded local endpoint. Fetches use a fixed source, reject redirects and unsupported content, and cap raster images at 128 KiB. Successful images are saved **indefinitely** under `WHAB_HOME/cache/plugins/bank-logos/`, including previously saved images and across app restarts. Saved artwork is not downloaded again because of age or memory eviction. Failed requests remain in memory for five minutes before retrying. Plugin Settings reports the policy and cached count. **Flush cache** deletes saved artwork and failed requests while preserving bank website addresses and account settings. Logos download again automatically when next requested. Page rendering, startup and plugin selection never fetch logos. The plugin works independently of bank ingestion.

## Connect Plaid Sandbox

1. Open the Plaid Dashboard's **Team settings → Keys** and find your Client ID and **Sandbox** secret.
2. Add them to your local `.env` as `PLAID_CLIENT_ID` and `PLAID_SECRET`. Keep `WHAB_ENV=sandbox` and `ALLOW_LIVE_PLAID=false`.
3. Restart WHAB. Open **Accounts → Connect account**.
4. Connect a Plaid Sandbox test institution/account. The standard test credentials are `user_good` / `pass_good` where a credential-based Sandbox institution asks for them. Sandbox OAuth institutions have their own simulated flow.
5. After Link finishes, the server exchanges the public token, saves an encrypted access token, and starts a transaction sync. Use **Sync now** again if the initial import is still loading.

Sandbox uses test data. A successful Sandbox import does not prove that our app has live access to Chase, Capital One, Robinhood, or Fidelity.

### Going live

Enable your Plaid Trial/Production access in the Dashboard and confirm institution access. On a new US/Canada Trial plan, Plaid currently permits 10 created Items; deleting an Item does not restore a slot. An Item usually corresponds to one person's login at an institution, containing one or more authorized accounts. Avoid reconnecting by creating duplicate Items; use the existing connection's **Reconnect** action instead.

When ready, use the production secret and set `WHAB_ENV=production` and `ALLOW_LIVE_PLAID=true`, then restart. The SQLite plugin keeps Sandbox and production in separate databases so test transactions cannot become live transactions through that settings change. All storage backends must preserve environment and demo/personal isolation. `PLAID_ENV` remains a fallback for existing configuration.

Desktop web OAuth can use Plaid's popup flow without a redirect URI. If a redirect is configured, register the exact URI in the Dashboard; redirects must use HTTPS in Sandbox and production. The frontend keeps the short-lived Link token and, for update mode, the existing Item ID in session storage for an OAuth return; it also stores temporary error notices there. Access tokens and the Plaid secret stay on the server. Mobile/app-to-app and a hosted family deployment need further testing and configuration.

This prototype requests **Transactions**. Investments import is the next separate integration; Robinhood and Fidelity holdings are not imported yet. Enable additional products deliberately rather than requiring all products at every institution.

## Importing and data handling

- The selected storage backend persists categories, budgets, accounts, transaction annotations, and per-Item sync cursors; SQLite is the bundled implementation.
- Plaid changes are applied atomically with the cursor after all pages are fetched. A pagination mutation restarts from the original cursor.
- When Plaid matches a pending transaction to a posted transaction, category edits, notes, and review state are preserved. Pending transactions and detected transfers are excluded from income/spending reports. Transfer detection is based on Plaid's categories and may need correction in a later reconciliation workflow.
- User category changes are kept separate from automatic category suggestions. Reviewing a transaction does not create a category override.
- If a bank transfer suggestion is actually a purchase, assigning a category includes it in spending. Clearing that category restores the original transfer exclusion.
- Bank balances are imported snapshots. Removed/disconnected accounts retain their records with a stale label, and stale cash is excluded from assignable money.
- Manual transactions are accepted only for demo/manual accounts. Matching manual purchases to later bank imports is not implemented.
- Initial history requests up to 730 days. Institutions may provide less. Automatic bank checks are generally one to four times daily; a local manual sync fetches changes Plaid already has and does not force a fresh bank fetch.
- Optional `PLAID_WEBHOOK_URL` must be public HTTPS. Registered webhook paths bypass localhost Host/origin checks so Plaid can reach them; Plaid's ES256 signature, timestamp, and hash of the exact body must verify before scheduling a sync. TLS encrypts transport and does not replace that provider authentication. Without a webhook endpoint, use manual sync while working locally.
- Disconnect removes the remote Item before marking the local connection disconnected. Imported transactions remain.
- **Manage** uses Link update mode for existing healthy connections; **Reconnect** repairs expired authorization. The frontend checks institution/account name/mask before exchanging a likely duplicate connection. This is a practical safeguard, not complete joint-account deduplication across different logins.
- Core supports multiple currencies with separate totals and no conversion; the bundled Plaid and YNAB importers currently accept USD only. CSV exports retain `amount_cents` for compatibility, containing integer currency minor units, and append a `currency` column. Text cells are protected against spreadsheet formula injection.

## Budget scope

Choose an individual account in the sidebar to open its transaction register. The account picker shows available bank logos and lets you search names and choose several accounts before Apply. **Select all** selects every available account, including accounts hidden by the search; **Select none** clears the draft for a new selection. The picker shows the selected count and requires at least one account before Apply. The period picker offers **This month**, **Previous month**, and **Custom dates** with inclusive start/end dates; leave a bound blank to keep it open. Legacy All dates bookmarks use Custom dates with both bounds open. The top **R** filter cycles **Needs review → Reviewed → All**. Account, period and review choices stay in the address; switching currency clears the account selection. The main **Accounts** navigation remains the place to manage account classifications and connections.

Accounts have a core `onBudget` flag, an account `type` and an optional `subtype`. On-budget checking, savings, cash and credit accounts participate in envelope funding, income and spending. Off-budget accounts track your wider finances: brokerage investments; retirement accounts such as traditional IRA, Roth IRA and 401(k); property such as a home or vehicle; mortgages, car loans and student loans; and fixed-term deposits such as CDs. You can also track other assets or liabilities and keep a cash or credit account off budget.

Choose **Accounts → Add manual account**, select the budget scope, then the account type and detail. Enter a positive amount owed for loans and credit accounts; assets use their signed value. Record manual value changes as inflows or outflows in Transactions. Off-budget transactions remain visible and editable, but never change envelope activity or budget income/spending trends. Current known balances contribute to the Accounts page's assets, liabilities and net worth within the selected currency. Stale and unavailable balances are excluded; these totals are not a holdings valuation service.

Use **Edit classification** beside any existing account to change scope, type or detail. This applies to its whole transaction history and may leave existing allocations unfunded when money moves off budget; check Ready to assign afterward. Classification does not change balances, transactions or categories, and bank refreshes retain the saved classification. Existing cash and credit accounts keep their on-budget scope; legacy loan, investment and unclassified accounts default off budget. YNAB exports omit account types and balances, so imported accounts remain unclassified off budget with unavailable balances until classified; classification alone does not recover an authoritative balance.

Transfers retain the existing inferred-transfer behavior. When funding an off-budget account from a budget account, categorize the on-budget outflow to spend the appropriate envelope; categorization overrides an inferred transfer exclusion. Automatic transfer matching and investment holdings imports remain outside the current model.

Amounts are stored as integer currency minor units (USD cents). Select a currency in the header to view its accounts, categories, budget and analytics. Each manual account declares its currency; new categories use the selected currency. A transaction inherits its account currency and can use only a matching child category. Cash, credit debt, rollover, trends and ready-to-assign totals are calculated separately for each currency, without exchange rates or conversion.

Categories have exactly two levels: a parent group and a child category. Create a child under a new or existing parent name. Parent headings organize the budget; only children receive targets, transactions and allocations. Existing saved category IDs and group labels are preserved.

Core formats are fixed and locale-independent: dates `YYYY-MM-DD`, monthly periods `YYYY-MM`, and USD `$1,234.56` (negative `-$1,234.56`). Other currencies use a code, such as `EUR 1,234.56`, `JPY 1,234` or `KWD 1,234.567`, with ISO minor-unit precision. The selected UI plugin can override presentation; there are no locale-format settings in core.

The bundled web interface shows three consecutive months. Each compact month card shows its month, year and Carryover choice. The overview has **Income**, **Carried in**, **Assigned for next month**, **Assigned this month** and **Available to assign**, with one amount per month. Outlined fields with a pencil are editable: the next-month set-aside and leaf category assignments. Income, carry-in, monthly totals and parent groups are read-only. Detailed Carryover (when applicable), Assigned, Activity and Available headings sit within Spending categories. Use the month strip to move the window; parent chevrons collapse categories. Settings stays at the bottom left. Earlier planning rows show dashes while historical income remains visible; at `2200-12`, fewer months appear and next-month funding is unavailable.

Expand **Where it comes from** beneath any read-only overview amount to understand that month's number. Income explains received money and opens that month's transactions. Assigned this month lists the child-category assignments. Available to assign shows its four components with addition and subtraction signs. The next-month amount you enter remains editable.

Changing an assignment saves when you leave its field or press Enter. A small status beside the field confirms the save; errors stay beside the value for correction. You can continue entering other amounts while saves finish in order. Totals update in place without reloading the page, moving your focus or closing explanations and collapsed groups. Rollover choices also update in place. Core still validates every save before the displayed totals change.

**Carried in** means previously unassigned money plus the explicit amount set aside for this month and any overall category balance released by the previous month's rollover choice. Its **Where it comes from** disclosure shows the components. With **By category**, positive and negative category balances stay only in their category Carryover columns and do not enter the top row. With **Overall balance**, their signed net enters the top carry-in. Eligible existing opening funds appear in the first budget month's carry-in. The breakdown allocates existing funds; it does not add deposits or move money between accounts.

**Available to assign = Income + Carried in - Assigned for next month - Assigned this month.** Income is the core's received income for that month; Assigned this month is the sum of new category assignments. **Assigned for next month** is an independent amount taken out of this month's pool and carried into the following month. It does not have to cover that month's category plans, and editing it never changes those plans. For example, $5,000 income plus $1,000 carry-in, less a $500 next-month set-aside and $4,500 category assignments, leaves $1,000 available. Next month receives the $500 set-aside and the $1,000 left unassigned, plus any overall rollover. Reducing the set-aside changes that breakdown; money left unassigned still carries forward. All amounts stay in their own currency. Existing saved funding amounts are retained as explicit set-asides.

Category available money equals carried-over money plus assigned money minus cleared categorized activity; categorized refunds add back to the category. Analytics retain imported historical spending.

Choose **Carryover → By category** or **Overall balance** on each month card. The displayed source month and currency control the following month's transition. By category is the default and preserves individual positive and negative category balances. Overall balance starts next month's categories at zero and adds their signed net to that month's assignable carry-in. For example, +$100 Utilities and -$50 Groceries contribute +$50 only under Overall balance; under By category they stay in their categories. This applies to savings categories such as Travel too. Changing an earlier choice recalculates later months without changing account balances, transactions or saved category assignments. The choice still saves if a later month develops a funding shortfall. A red Funding shortfall note marks each affected month; signed balances remain visible, and Available to assign’s Where it comes from disclosure explains spending and account reserves when they constrain funding further. Earlier budgets without saved choices keep category rollover. Older remote servers leave unsupported controls read-only.

The personal budget starts in its initialization month. Pre-budget imports remain available for analytics and do not create old envelope deficits. Earlier months cannot receive assignments. The demo begins earlier to demonstrate rollover.

Core reconstructs monthly opening cash and credit reserves from current account snapshots and dated transactions. Imported history is not added to snapshots again, and pending purchases do not enter category activity. The monthly overview follows the allocation formula above; later category plans do not silently reduce an earlier month's displayed pool. Separate save validation also checks actual eligible cash, positive category commitments and credit reserves, and protects affected monthly budgets. Category overspending, uncategorized outflows or unavailable balances can make the safely assignable amount lower than the displayed monthly pool; the overview explains that situation and rejected saves preserve the ledger. Historical figures are reconstructed rather than frozen records of when budget edits happened. Past edits must fit the applicable monthly and cash headroom.

This is an initial planning model, not the complete YNAB credit-card payment system. Separate payment categories, debt payoff targets, splits, manual/import reconciliation, editable transfer matching, merchant rules, recurring bills, goals editing, and shared family authentication are not implemented yet.

## Local access and private files

The default Python server binds to `127.0.0.1` with TLS and accepts HTTPS only. Ordinary pages and API routes check localhost Host and browser origin. Two narrow exceptions bypass those local checks: explicitly enabled `/remote/v1` routes authenticate a bearer credential for permitted budgets, and registered bank-webhook routes authenticate provider signatures. All routes still require HTTPS. Neither TLS nor a machine bearer credential provides a family login system for the visual app. Keep visual/private banking routes on loopback when configuring remote protocol or webhook access. See the [remote plugin contract](plugins.md#remote-interface-and-client).

`.env`, `plugins.json`, `data/`, `tls/`, `cache/`, `work/`, and `.venv/` are ignored by Git. The runtime `budgets/` directories at the repository root, under `whab/` and under `whab/app/` are also ignored, along with saved `.whab` files and temporary `.whab-*` files or directories anywhere in the checkout. Runtime budget files include financial data, budget names and saved copy paths. These rules preserve the tracked application code under `whab/app/src/whab/budgets/` and its tests.

Database files and portable budgets contain private financial data even though Plaid tokens are encrypted. Back up the runtime home securely, including the active database, `budgets/` runtimes and recovery/copy records, `plugins.json`, provider credentials and token encryption key from `.env`, and TLS certificate/private-key files. Store an additional protected copy of keys separately from the data backup. Preserve custom certificate paths outside the home too. Stop WHAB before copying a runtime/database, or use SQLite's backup API for a live database. Do not replace the token encryption key after linking accounts without a migration: existing tokens will no longer decrypt.

A portable `.whab` export is not a complete runtime backup: it omits bank Items, provider access tokens, sync cursors, token encryption keys and TLS private keys. A fresh restore keeps imported bank history but needs device/server credentials and the original runtime for continuing those saved bank connections. A remote connection export leaves financial data on the server; its server still needs a separate data/credential backup. The optional password-encrypted remote credential export protects only that credential.

Default SQLite files:

- `data/whab-sandbox.sqlite` — demo and Sandbox personal data
- `data/whab-production.sqlite` — demo and production personal data
- `.env` — private local credentials and token encryption key
- `budgets/<id>/<environment>/data/` — isolated databases for opened portable SQLite budgets
- `budgets/<id>/<environment>/` — private container copies, plugin state and recovery/copy records
- `tls/ca.crt` and `tls/server.crt` — local CA and server certificates
- `tls/ca.key` and `tls/server.key` — private signing and server keys; never export or share

An existing compatible ledger may retain its earlier filename; back up the file WHAB reopens in the same runtime home's `data/` directory.

## Official references

- [Plaid Trial plan](https://support.plaid.com/hc/en-us/articles/39994173227159-What-is-the-Plaid-Trial-plan)
- [Transactions integration](https://plaid.com/docs/transactions/)
- [Transaction states](https://plaid.com/docs/transactions/transactions-data/)
- [Link Web SDK](https://plaid.com/docs/link/web/)
- [OAuth guide](https://plaid.com/docs/link/oauth/)
- [Webhook verification](https://plaid.com/docs/api/webhooks/webhook-verification/)
- [Institution coverage](https://plaid.com/docs/institutions/)

### Plugin Marketplace

Open Settings → Plugins for installed plugins, including disabled ones, grouped into Interfaces, Ingestion, Insights, Storage, Automation and Extensions. Empty categories remain visible. Each plugin's Settings shows its dependencies; selecting a plugin requires its dependencies to be installed and enabled for that budget.

Choose **Plugin Marketplace** to browse maintained plugins and their installation status. **Review installation** prepares the required plugin and Python package list; **Install reviewed packages** installs it on this device. Refresh the installed list, then enable optional plugins for the intended budget. Installing does not enable a plugin or change its saved data. Required plugins cannot be disabled while their dependents remain selected. See [the installation contract](plugins.md#plugin-dependency-manifests) for compatibility, failed installs and supported environments.
