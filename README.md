# IHAB — I Have A Budget

A local budgeting app built with **Python, FastAPI, Jinja, and SQLite**. It has monthly category allocations and rollover, transaction categorization and review, spending analytics, manual accounts, CSV export, and a server-side Plaid Transactions connection. Pages and budgeting logic run in Python; a small browser script handles forms and Plaid Link. No Node.js build is required.

Current version: **1.0.0**. See [CHANGELOG.md](CHANGELOG.md) for release notes. This version establishes the compatibility baseline for the local app; the limitations below still apply.

## Versioning and contributions

IHAB follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html): patch releases fix compatible behavior, minor releases add compatible features, and major releases introduce breaking changes. The compatibility contract covers documented HTTP/CSV interfaces and the ability to open existing ledgers and encrypted connections through supported migrations. A schema change with a supported automatic migration can be compatible; dropping or changing documented fields, requiring manual data conversion, or invalidating existing tokens requires a major release. Internal refactoring and visual changes do not by themselves break this contract.

`ihab/__init__.py` contains the single version value used by packaging, the CLI, and FastAPI. Check it with `.venv/bin/python -m ihab --version`. For a release, update that value and the changelog in a PR, then tag the merged `main` commit as `vX.Y.Z` and publish matching GitHub release notes. Never move an existing release tag. Documentation and tooling PRs can remain unreleased until the next app release.

All changes use a feature branch and a pull request targeting `main`. CI runs pytest and Ruff on Python 3.13, 3.14, and 3.15 without Plaid credentials or private data. Review the final diff, fix failures, and squash merge after the checks pass. The sole maintainer has authorized routine PR review and merging without another confirmation. There is no second-person approval requirement because [GitHub cannot approve a PR through its author's account](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/approving-a-pull-request-with-required-reviews).

**Branch protection is pending.** GitHub rejected protection for this private repository on the current account plan; the maintainer chose to leave enforcement pending. [Private repository protection requires Pro or an eligible organization plan](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches). The PR workflow is a project convention until Pro is enabled. `.github/main-protection.json` records the intended rule: require PRs and passing CI, apply it to administrators, prevent force pushes/deletion, and require resolved conversations and linear history. After enabling Pro, apply it with:

```sh
gh api --method PUT repos/Fillll/ihab/branches/main/protection \
  --input .github/main-protection.json
```

## Run locally

Requires **Python 3.13 or newer**. Older Python versions are unsupported and rejected by package installation. Run from a source checkout; templates and static assets live alongside the Python package.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python scripts/setup.py
.venv/bin/python -m ihab
```

Open http://127.0.0.1:5173. The demo workspace contains fictional data. Switch to **My budget** for your own accounts. A manual account works immediately without Plaid.

The setup script creates a private `.env` and a random 32-byte token encryption key when the file is missing. It preserves existing credentials and encryption keys, and migrates the earlier local API port to 5173. After changing `.env`, restart the server. Use `.venv/bin/python -m ihab --reload` while developing, or `--port 5174` to choose another local port.

SQLite is embedded: there is no database server to install. The app creates its database on first launch. Existing IHAB SQLite data and encrypted Plaid tokens are supported; keep the same encryption key when upgrading.

### Source layout

- `ihab/store.py` — SQLite schema, budget calculations, and atomic transaction imports
- `ihab/plaid_service.py` — Plaid API calls, encrypted tokens, synchronization, and webhook verification
- `ihab/app.py` — HTTP routes, validated requests, and local access checks
- `ihab/views.py` — presentation data and analytics
- `templates/` — server-rendered Jinja pages
- `static/` — styles, icons, and browser interactions
- `tests/` — isolated SQLite and mocked Plaid tests

## Connect Plaid Sandbox

1. Open the Plaid Dashboard's **Team settings → Keys** and find your Client ID and **Sandbox** secret.
2. Add them to your local `.env` as `PLAID_CLIENT_ID` and `PLAID_SECRET`. Keep `PLAID_ENV=sandbox` and `ALLOW_LIVE_PLAID=false`.
3. Restart IHAB. Open **Accounts → Connect account**.
4. Connect a Plaid Sandbox test institution/account. The standard test credentials are `user_good` / `pass_good` where a credential-based Sandbox institution asks for them. Sandbox OAuth institutions have their own simulated flow.
5. After Link finishes, the server exchanges the public token, saves an encrypted access token, and starts a transaction sync. Use **Sync now** again if the initial import is still loading.

Sandbox uses test data. A successful Sandbox import does not prove that our app has live access to Chase, Capital One, Robinhood, or Fidelity.

### Going live

Enable your Plaid Trial/Production access in the Dashboard and confirm institution access. On a new US/Canada Trial plan, Plaid currently permits 10 created Items; deleting an Item does not restore a slot. An Item usually corresponds to one person's login at an institution, containing one or more authorized accounts. Avoid reconnecting by creating duplicate Items; use the existing connection's **Reconnect** action instead.

When ready, use the production secret and set `PLAID_ENV=production` and `ALLOW_LIVE_PLAID=true`, then restart. Sandbox and production use separate databases so test transactions can never become live transactions through a settings change. The demo and personal workspaces are also isolated.

Desktop web OAuth can use Plaid's popup flow without a redirect URI. If a redirect is configured, register the exact URI in the Dashboard; production redirects must use HTTPS. The frontend persists only the short-lived Link token in session storage for an OAuth return. Access tokens and the Plaid secret stay on the server. Mobile/app-to-app and a hosted family deployment need further testing and configuration.

This prototype requests **Transactions**. Investments import is the next separate integration; Robinhood and Fidelity holdings are not imported yet. Enable additional products deliberately rather than requiring all products at every institution.

## Importing and data handling

- SQLite persists categories, budgets, accounts, transaction annotations, and per-Item sync cursors.
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

`.env`, `data/`, `work/`, and `.venv/` are ignored by Git. Database files contain private financial data even though Plaid tokens are encrypted. Back up the database and the encryption key separately before relying on the app. Stop IHAB before copying the database, or use SQLite's backup API while it is running. Do not replace the encryption key after linking accounts without a migration: existing tokens will no longer decrypt.

Files:

- `data/ihab-sandbox.sqlite` — demo and Sandbox personal data
- `data/ihab-production.sqlite` — demo and production personal data
- `.env` — private local credentials and encryption key

## Verify

```sh
.venv/bin/python -m pytest
.venv/bin/python -m ruff check ihab scripts tests
```

The tests cover workspace isolation, legacy database compatibility, valid integer money and dates, API origin/Host and request-size checks, transaction review, CSV safety, Plaid token encryption, webhook authenticity, pending-to-posted annotations, pagination restart, atomic failed sync, category rollover/refunds, and cash/debt reservations. They use temporary databases and do not require Plaid credentials.

Current validation: 25 passing tests, Ruff checks, browser checks for persisted allocations, categorization, and review status, and an actual Plaid Sandbox API connection with 7 test accounts and 362 simulated transactions. A repeated Python sync kept the count unchanged. The Link dialog launches; its full consent/OAuth completion has not been exercised. Real institution access is not verified yet.

## Official references

- [Plaid Trial plan](https://support.plaid.com/hc/en-us/articles/39994173227159-What-is-the-Plaid-Trial-plan)
- [Transactions integration](https://plaid.com/docs/transactions/)
- [Transaction states](https://plaid.com/docs/transactions/transactions-data/)
- [Link Web SDK](https://plaid.com/docs/link/web/)
- [OAuth guide](https://plaid.com/docs/link/oauth/)
- [Webhook verification](https://plaid.com/docs/api/webhooks/webhook-verification/)
- [Institution coverage](https://plaid.com/docs/institutions/)
