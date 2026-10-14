# WHAB — We Have A Budget

A local budgeting app built with **Python, FastAPI and Jinja**, with bundled SQLite storage and Plaid bank plugins. It has monthly category allocations and rollover, transaction categorization and review, spending analytics, manual accounts, CSV export, and a server-side Plaid Transactions connection. Pages and budgeting logic run in Python; a small browser script handles forms and Plaid Link. No Node.js build is required.

Current version: **1.2.0**. See [CHANGELOG.md](CHANGELOG.md) for release notes. The limitations below still apply.

## Versioning and contributions

WHAB follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html): patch releases fix compatible behavior, minor releases add compatible features, and major releases introduce breaking changes. The compatibility contract covers documented HTTP/CSV interfaces and the ability to open existing ledgers and encrypted connections through supported migrations. A schema change with a supported automatic migration can be compatible; dropping or changing documented fields, requiring manual data conversion, or invalidating existing tokens requires a major release. The public plugin API in `whab.plugins` is also covered: breaking its documented records or hook behavior requires a major release. Internal modules are not a stable import API. Internal refactoring and visual changes do not by themselves break this contract.

`src/whab/__init__.py` contains the single version value used by packaging, the CLI, FastAPI, and the About dialog. Check it with `.venv/bin/python -m whab --version`. Every material change, including runtime or dependency support changes, must include the appropriate SemVer bump and a dated changelog entry in the same PR. Verify the package and runtime versions match before merging, then tag the merged `main` commit as `vX.Y.Z` and publish matching GitHub release notes. Never move an existing release tag. Small documentation-only changes can remain unreleased until the next app release.

All changes use a feature branch and a pull request targeting `main`. CI runs pytest and Ruff on Python 3.14 and 3.15 without Plaid credentials or private data. Review the final diff, fix failures, and squash merge after the checks pass. The sole maintainer has authorized routine PR review and merging without another confirmation. There is no second-person approval requirement because [GitHub cannot approve a PR through its author's account](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/approving-a-pull-request-with-required-reviews).

**Branch protection is pending.** GitHub rejected protection for this private repository on the current account plan; the maintainer chose to leave enforcement pending. [Private repository protection requires Pro or an eligible organization plan](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches). The PR workflow is a project convention until Pro is enabled. `.github/main-protection.json` records the intended rule: require PRs and passing CI, apply it to administrators, prevent force pushes/deletion, and require resolved conversations and linear history. After enabling Pro, apply it with:

```sh
gh api --method PUT repos/Fillll/whab/branches/main/protection \
  --input .github/main-protection.json
```

## Run locally

Requires **Python 3.14 or newer**. Older Python versions are unsupported and rejected by package installation. For development, use an editable source install as shown below. Templates and static assets are packaged, so installed wheels also work.

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python scripts/setup.py
.venv/bin/python -m whab
```

When upgrading from an older Python version, stop WHAB and recreate `.venv` with Python 3.14 or newer, then reinstall the app and any additional plugins. Keep your existing `.env` and `data/` in place.

Open http://127.0.0.1:5173. The demo workspace contains fictional data. Switch to **My budget** for your own accounts. A manual account works immediately without Plaid.

The setup script creates a private `.env` and a random 32-byte token encryption key when the file is missing. It preserves existing credentials and encryption keys, and migrates the earlier local API port to 5173. After changing `.env`, restart the server. Run from the checkout root to reuse its `.env` and `data/`. To launch elsewhere, set `WHAB_HOME` to the absolute directory containing those runtime files before launching; the default is the working directory. Code and installed assets never determine the data location. `WHAB_ENV` selects `sandbox` or `production`; existing `PLAID_ENV` remains the fallback for upgrades. Use `.venv/bin/python -m whab --reload` while developing, or `--port 5174` to choose another local port.

The default storage plugin is `WHAB_STORAGE=sqlite`. SQLite is embedded: there is no database server to install, and its plugin opens or creates the database under the runtime home. Existing IHAB SQLite data and encrypted Plaid tokens are supported; keep the same home and encryption key when upgrading. Exactly one storage backend is required, including when optional bank/report plugins are disabled. Selecting another installed storage plugin does not copy or migrate existing SQLite data automatically; configure the chosen backend's data location deliberately.

WHAB was formerly IHAB. The GitHub repository is now [Fillll/whab](https://github.com/Fillll/whab), and the Python package and launch command are only `whab`. Update existing launch commands to `python -m whab`. Database filenames, the opaque Plaid household identity, and browser OAuth storage keys retain their legacy names to preserve existing data and connections. The local checkout path can remain unchanged.

## Develop and extend WHAB

The bundled Web UI uses a flat yellow eight-point compass on a red background, warm neutral surfaces, and red controls. Its templates, styles, and SVG brand assets live in `src/whab/interfaces/web/`; financial status colors remain distinct from branding.

Start with [CONTRIBUTING.md](CONTRIBUTING.md), the [architecture guide](docs/architecture.md), and the [plugin author guide](docs/plugins.md).

```text
src/whab/
  domain/                # Pure money, date, transaction and budgeting rules
  storage/bank.py         # Provider-scoped facade over the storage protocol
  storage/sqlite/         # Bundled storage plugin, schema migration and demo seeds
  integrations/plaid/    # Bundled bank plugin: protocol, mapping, secrets and UI
  interfaces/web/        # Bundled visual plugin: view models, templates and browser assets
  plugins/               # Public versioned SDK, plugin kinds and entry-point selection
  web/                   # Host HTTP routes, validation, middleware and safe projections
  app.py                 # Composition root wiring these pieces together
  config.py              # Local runtime configuration
  __main__.py            # python -m whab
examples/whab-example-plugin/  # Separately installable report/export plugin
scripts/                 # Setup and installed-wheel verification
tests/                   # Domain, storage, web, integration and plugin tests
docs/                    # Architecture and extension contracts
```

Plugins are separate Python packages registered through the `whab.plugins` entry-point group. Descriptors advertise `storage`, `user_interface`, `data_ingestion` and `reporting` capabilities. The host loads the selected storage and UI descriptors alongside optional extensions:

| Setting | Default | Purpose |
| --- | --- | --- |
| `WHAB_STORAGE` | `sqlite` | Exactly one required storage backend |
| `WHAB_UI` | `web` | Selected server-rendered visual interface; an empty value runs APIs headlessly |
| `WHAB_PLUGINS` | `plaid` | Default optional bank/report selection when no saved `plugins.json` exists; an empty value disables these |

Without a saved `plugins.json`, `WHAB_PLUGINS=` retains storage and the selected visual interface for manual budgeting. A selection saved in **Settings → Plugin manager** takes precedence over this environment setting. `WHAB_UI=` removes the page at `/` and core visual assets while keeping APIs and storage available. API v1's data-ingestion capability is the bank connection/sync contract; arbitrary one-time imports and native UI runtimes are not implemented. Installation alone does not enable an additional plugin. Try the included example; the launch setting below applies when the runtime home has no saved selection:

```sh
.venv/bin/python -m pip install -e examples/whab-example-plugin
WHAB_PLUGINS=plaid,example .venv/bin/python -m whab
```

If you already have a saved selection, enable **example** in **Settings → Plugin manager**, save, and restart. Open **Insights** to see the extra report and its CSV download. Disable the example in the same manager, save, and restart; `WHAB_PLUGINS=plaid` is an alternative only when no saved selection exists. Plugins run as trusted Python code in the server process; this is an extension contract, not a sandbox. Reporting hooks receive no settings, tokens, or database handles. Bank factories receive a separate privileged context with provider-scoped configuration, an encryption key, and a scoped atomic import interface. Storage factories receive the runtime home, environment and backend configuration, and own ledger persistence and migrations for their backend. The host renders escaped text, guards endpoints, and contains ordinary reporting-hook failures.

Plaid owns its Link UI, encryption, remote requests, synchronization, and signed webhooks. The generic bank facade owns provider-scoped identities; the selected storage backend implements ledger validation and atomic cursor updates using core domain rules. SQLite and the visual Web interface are registered through the same plugin mechanism. Core app/web code imports their protocols rather than concrete implementations. The Web plugin owns presentation logic, templates, styles and browser controls; the host owns HTTP transport and access checks. Disabling Plaid preserves imported history. Arbitrary report/bank ledger-write hooks, one-time migration hooks and custom routes are not public extension points; storage implementations own their own compatible schema migrations. The repository remains private and no open-source license is granted by this structural change.

## Settings and plugin manager

Open the gear beside **Local workspace** to find **Plugin manager** and **About this prototype**. The compass logo is a square mark; the top bar contains only workspace and data-mode controls.

The plugin manager lists installed plugin packages and their running and saved states. Enable or disable optional bank/report plugins, then choose **Save plugin selection**. Changes apply after restarting WHAB. The selected storage and interface remain required; choose a different backend or interface through their existing configuration settings.

Saving creates a private `plugins.json` in the runtime home. Its validated optional selection takes precedence over `WHAB_PLUGINS` on the next start; without this file, the environment setting remains the default. Save loads and validates the selected descriptors without running their factories or hooks, then writes the file atomically. Plugin discovery reads package metadata without loading disabled code. The manager selects already-installed packages; package installation remains a separate local step. Unavailable selected optional plugins can be unchecked before saving. Invalid or unreadable saved settings prevent startup; repair the file, or save a replacement through Settings while WHAB is still running.

Disabling a bank plugin takes effect on restart and retains imported history and saved connections. Saving a selection does not disconnect a bank or activate new connections. Back up `plugins.json` with the runtime configuration; removing it restores the environment default.

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
- Only USD is supported. CSV exports use integer cents and guard against spreadsheet formula injection.

## Budget scope

Amounts are stored as integer cents. Category available money equals carried-over money plus assigned money minus cleared categorized activity; categorized refunds add back to the category. Analytics retain imported historical spending.

The personal budget starts in its initialization month. Pre-budget imports remain available for analytics and do not create old envelope deficits. Earlier months cannot receive assignments. The demo begins earlier to demonstrate rollover.

**To assign** uses cash account balance snapshots, subtracts remaining positive category balances, and conservatively reserves all positive credit-card balances. Imported income is not added again to balances. Past-month balance reconstruction is approximate because it depends on available transaction history. Current pending purchases are not included in category activity.

This is an initial planning model, not the complete YNAB credit-card payment system. Separate payment categories, debt payoff targets, splits, manual/import reconciliation, editable transfer matching, merchant rules, recurring bills, goals editing, and shared family authentication are not implemented yet.

## Local access and private files

The Python server binds to `127.0.0.1`. API access checks the Host and browser origin; the app has no family login/authentication layer yet. Keep it local. A hosted version requires authentication, authorization, secure deployment, backup operations, and an explicit account-sharing model.

`.env`, `plugins.json`, `data/`, `work/`, and `.venv/` are ignored by Git. Database files contain private financial data even though Plaid tokens are encrypted. Back up the database and the encryption key separately before relying on the app. Stop WHAB before copying the database, or use SQLite's backup API while it is running. Do not replace the encryption key after linking accounts without a migration: existing tokens will no longer decrypt.

Default SQLite files:

- `data/ihab-sandbox.sqlite` — demo and Sandbox personal data
- `data/ihab-production.sqlite` — demo and production personal data
- `.env` — private local credentials and encryption key

## Verify

```sh
.venv/bin/python -m pytest
.venv/bin/python -m ruff check src scripts tests examples
.venv/bin/python scripts/check_distribution.py
```

The tests cover workspace isolation, legacy database compatibility, valid integer money and dates, API origin/Host and request-size checks, transaction review, CSV safety, Plaid token encryption, webhook authenticity, pending-to-posted annotations, pagination restart, atomic failed sync, category rollover/refunds, and cash/debt reservations. They use temporary databases and do not require Plaid credentials.

CI checks Python 3.14 and 3.15. Tests use temporary databases and mocked services. Packaging checks build the app and example plugin wheels, install them in a temporary target, and render every page from outside the checkout with fictional data. Plugin checks cover required storage selection, visual/headless selection, optional enablement, API versions, workspace/month isolation, immutable snapshots, escaping, failure handling and cleanup. Dependency guards keep domain rules independent and prevent core code from importing concrete SQLite, Plaid or visual implementations.

Previously verified integration state: a Plaid Sandbox connection with 7 test accounts and 362 simulated transactions, with no duplicates after repeated sync. The Link dialog launches; its full consent/OAuth completion has not been exercised. Real institution access is not verified yet.

## Official references

- [Plaid Trial plan](https://support.plaid.com/hc/en-us/articles/39994173227159-What-is-the-Plaid-Trial-plan)
- [Transactions integration](https://plaid.com/docs/transactions/)
- [Transaction states](https://plaid.com/docs/transactions/transactions-data/)
- [Link Web SDK](https://plaid.com/docs/link/web/)
- [OAuth guide](https://plaid.com/docs/link/oauth/)
- [Webhook verification](https://plaid.com/docs/api/webhooks/webhook-verification/)
- [Institution coverage](https://plaid.com/docs/institutions/)
