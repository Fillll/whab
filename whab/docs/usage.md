# Local setup and usage

Detailed operating guidance for WHAB. Start with the [quick start](../README.md#run-locally); see the [architecture](architecture.md) and [plugin API](plugins.md) for implementation contracts.

## Contents

- [Runtime setup](#runtime-setup)
- [Settings and plugin manager](#settings-and-plugin-manager)
- [Connect Plaid Sandbox](#connect-plaid-sandbox)
- [Importing and data handling](#importing-and-data-handling)
- [Budget scope](#budget-scope)
- [Local access and private files](#local-access-and-private-files)
- [Official references](#official-references)

## Runtime setup

Run the commands below from the application directory (`whab/`).

Requires **Python 3.14 or newer**. Older Python versions are unsupported and rejected by package installation. For development, use an editable source install as shown below. Templates and static assets are packaged, so installed wheels also work.

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python scripts/setup.py
.venv/bin/python -m whab
```

When upgrading from an older Python version, stop WHAB and recreate `.venv` with Python 3.14 or newer, then reinstall the app and any additional plugins. Keep your existing `.env` and `data/` in place.

Open http://127.0.0.1:5173. The demo workspace contains fictional data. Switch to **My budget** for your own accounts. A manual account works immediately without Plaid.

The setup script creates a private `.env` in the application directory (`whab/`) and a random 32-byte token encryption key when the file is missing. It preserves existing credentials and encryption keys, and migrates the earlier local API port to 5173. After changing `.env`, restart the server. Launch from `whab/` to reuse its `.env` and `data/`. To reuse runtime files kept elsewhere, including an older checkout root, set `WHAB_HOME` to the absolute directory containing those files before launching; the default is the working directory. Keep an existing runtime home and encryption key together when upgrading. Code and installed assets never determine the data location. `WHAB_ENV` selects `sandbox` or `production`; existing `PLAID_ENV` remains the fallback for upgrades. Use `.venv/bin/python -m whab --reload` while developing, or `--port 5174` to choose another local port.

The default storage plugin is `WHAB_STORAGE=sqlite`. SQLite is embedded: there is no database server to install, and its plugin opens or creates the database under the runtime home. A single existing `data/*-{environment}.sqlite` file is reopened in place; a noncanonical filename requires base-schema validation. With no matching file, the plugin creates `data/whab-{environment}.sqlite`. Multiple matches fail safely, even if one has the canonical filename. Saved budgets and encrypted Plaid tokens remain supported; keep the same home and encryption key when upgrading. Exactly one storage backend is required, including when optional bank/report plugins are disabled. Selecting another installed storage plugin does not copy or migrate existing SQLite data automatically; configure the chosen backend's data location deliberately.

The GitHub repository is [Fillll/whab](https://github.com/Fillll/whab), and the Python package and launch command are only `whab`. Use `python -m whab` in launch commands. Browser OAuth session keys use `whab`, so finish any in-progress Plaid Link flow before upgrading. New Link tokens use `whab-local-household`; saved connected Items and their encrypted tokens remain unchanged. The local checkout path can remain unchanged.

## Settings and plugin manager

Open the gear beside **Local workspace** to find **Plugin manager** and **About this prototype**. The compass logo is a square mark; the top bar contains only workspace and data-mode controls.

The plugin manager lists installed plugin packages and their running and saved states. Enable or disable optional bank/report/branding plugins, then choose **Save plugin selection**. Changes apply after restarting WHAB. Select storage and the interface through `WHAB_STORAGE` and `WHAB_UI`; storage is required, and an empty `WHAB_UI` runs headlessly.

Saving creates a private `plugins.json` in the runtime home. Its validated optional selection takes precedence over `WHAB_PLUGINS` on the next start; without this file, the environment setting remains the default. Save loads and validates the selected descriptors without running their factories or hooks, then writes the file atomically. Plugin discovery reads package metadata without loading disabled code. The manager selects already-installed packages; package installation remains a separate local step. Unavailable selected optional plugins can be unchecked before saving. Invalid or unreadable saved settings prevent startup; repair the file, or save a replacement through Settings while WHAB is still running.

Disabling a bank plugin takes effect on restart and retains imported history and saved connections. Saving a selection does not disconnect a bank or activate new connections. Back up `plugins.json` with the runtime configuration; removing it restores the environment default.

### Optional bank logos

The bundled **bank-logos** plugin is disabled by default. After upgrading a source checkout, refresh its registration with `.venv/bin/python -m pip install --no-deps -e .`. In **Settings → Plugin manager**, enable **bank-logos**, save, and restart WHAB. When active it is named **Bank logos (Icon Horse)**. Without a saved selection, `WHAB_PLUGINS=plaid,bank-logos` selects it instead. Disable it through the same manager and restart to return to the existing icons and initials.

Logos appear beside accounts in the sidebar and Accounts page. A curated list matches institution names to public website domains, including Chase, Capital One, Bank of America, Wells Fargo, Citi, Fidelity, Robinhood and other common institutions. Unknown institutions, manual accounts and fictional demo/Sandbox banks keep their existing fallback. Logos are website icons and availability or artwork can vary; they do not establish a bank connection or institution support.

Icons come from [Icon Horse](https://icon.horse/), with no API key. Its basic free tier currently allows 1,000 icons per month; see the provider for current terms. WHAB requests only the matched public website domain from Icon Horse; it never sends account names, IDs, masks, balances, transactions, credentials or tokens. The service sees WHAB's server IP and requested bank domain. Browsers load images from WHAB's guarded local endpoint. Fetches use a fixed source, reject redirects and unsupported content, cap raster images at 128 KiB, and cache up to 128 results in memory (24 hours for successes, five minutes for failures). No logos are fetched at startup, while saving selections, or while rendering pages. Fetching an image is deferred until the browser requests it; offline or unavailable icons keep the fallback. A restart clears the cache. The plugin needs no Plaid credentials and works independently of bank ingestion.

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

Desktop web OAuth can use Plaid's popup flow without a redirect URI. If a redirect is configured, register the exact URI in the Dashboard; production redirects must use HTTPS. The frontend persists only the short-lived Link token in session storage for an OAuth return. Access tokens and the Plaid secret stay on the server. Mobile/app-to-app and a hosted family deployment need further testing and configuration.

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
- Optional `PLAID_WEBHOOK_URL` must be public HTTPS. Webhooks verify Plaid's ES256 signature, timestamp, and hash of the exact body before scheduling a sync. Without a webhook endpoint, use manual sync while working locally.
- Disconnect removes the remote Item before marking the local connection disconnected. Imported transactions remain.
- **Manage** uses Link update mode for existing healthy connections; **Reconnect** repairs expired authorization. The frontend checks institution/account name/mask before exchanging a likely duplicate connection. This is a practical safeguard, not complete joint-account deduplication across different logins.
- Core supports multiple currencies with separate totals and no conversion; the bundled Plaid and YNAB importers currently accept USD only. CSV exports retain `amount_cents` for compatibility, containing integer currency minor units, and append a `currency` column. Text cells are protected against spreadsheet formula injection.

## Budget scope

Amounts are stored as integer currency minor units (USD cents). Select a currency in the header to view its accounts, categories, budget and analytics. Each manual account declares its currency; new categories use the selected currency. A transaction inherits its account currency and can use only a matching child category. Cash, credit debt, rollover, trends and ready-to-assign totals are calculated separately for each currency, without exchange rates or conversion.

Categories have exactly two levels: a parent group and a child category. Create a child under a new or existing parent name. Parent headings organize the budget; only children receive targets, transactions and allocations. Existing saved category IDs and group labels are preserved.

Core formats are fixed and locale-independent: dates `YYYY-MM-DD`, monthly periods `YYYY-MM`, and USD `$1,234.56` (negative `-$1,234.56`). Other currencies use a code, such as `EUR 1,234.56`, `JPY 1,234` or `KWD 1,234.567`, with ISO minor-unit precision. The selected UI plugin can override presentation; there are no locale-format settings in core.

Category available money equals carried-over money plus assigned money minus cleared categorized activity; categorized refunds add back to the category. Analytics retain imported historical spending.

The personal budget starts in its initialization month. Pre-budget imports remain available for analytics and do not create old envelope deficits. Earlier months cannot receive assignments. The demo begins earlier to demonstrate rollover.

**To assign** uses cash account balance snapshots, subtracts remaining positive category balances, and conservatively reserves all positive credit-card balances. Imported income is not added again to balances. Past-month balance reconstruction is approximate because it depends on available transaction history. Current pending purchases are not included in category activity.

This is an initial planning model, not the complete YNAB credit-card payment system. Separate payment categories, debt payoff targets, splits, manual/import reconciliation, editable transfer matching, merchant rules, recurring bills, goals editing, and shared family authentication are not implemented yet.

## Local access and private files

The Python server binds to `127.0.0.1`. API access checks the Host and browser origin; the app has no family login/authentication layer yet. Keep it local. A hosted version requires authentication, authorization, secure deployment, backup operations, and an explicit account-sharing model.

`.env`, `plugins.json`, `data/`, `work/`, and `.venv/` are ignored by Git. Database files contain private financial data even though Plaid tokens are encrypted. Back up the database and the encryption key separately before relying on the app. Stop WHAB before copying the database, or use SQLite's backup API while it is running. Do not replace the encryption key after linking accounts without a migration: existing tokens will no longer decrypt.

Default SQLite files:

- `data/whab-sandbox.sqlite` — demo and Sandbox personal data
- `data/whab-production.sqlite` — demo and production personal data
- `.env` — private local credentials and encryption key

An existing compatible ledger may retain its earlier filename; back up the file WHAB reopens in the same runtime home's `data/` directory.

## Official references

- [Plaid Trial plan](https://support.plaid.com/hc/en-us/articles/39994173227159-What-is-the-Plaid-Trial-plan)
- [Transactions integration](https://plaid.com/docs/transactions/)
- [Transaction states](https://plaid.com/docs/transactions/transactions-data/)
- [Link Web SDK](https://plaid.com/docs/link/web/)
- [OAuth guide](https://plaid.com/docs/link/oauth/)
- [Webhook verification](https://plaid.com/docs/api/webhooks/webhook-verification/)
- [Institution coverage](https://plaid.com/docs/institutions/)
