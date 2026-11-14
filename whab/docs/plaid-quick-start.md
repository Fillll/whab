# Automatic bank imports with Plaid

Plaid connects bank accounts to WHAB. You choose which accounts and history to import, then choose manual or regular updates. Start in Sandbox with test data before connecting real accounts.

## 1. Start a Sandbox app

[Install locally](agentic-work-101.md#1-install-locally), then run from `whab/app/`:

```sh
export WHAB_HOME="$HOME/.local/share/whab-plaid-sandbox"
.venv/bin/python scripts/setup.py
```

In this runtime home's private `.env`, set `PLAID_CLIENT_ID` and `PLAID_SECRET` to your Plaid Sandbox credentials. Keep `ALLOW_LIVE_PLAID=false`. Keep credentials local; do not commit them or paste them into an agent chat.

```sh
WHAB_ENV=sandbox WHAB_UI=web .venv/bin/python -m whab
```

[Trust this runtime's public CA certificate](usage.md#local-https), then open [https://localhost:5173](https://localhost:5173). Stop another local server first, or use `--port 5174` and open that port. Sandbox is a separate environment, and this guide uses a separate home from your real budget.

## 2. Connect and choose what to import

1. Open your budget, then **Settings → Plugins**, enable **Plaid** and open its **Settings**.
2. Choose **Connect account** and complete the interactive Sandbox consent flow. An agent can help configure WHAB, but bank consent stays with you.
3. Choose **Choose destinations**. For each bank account, select an existing compatible WHAB account, create one, or choose **Do not import**. Select an inclusive start date or **All available history** explicitly.
4. Choose **Save and sync now**, then inspect **Accounts** and **Transactions**. Importing does not replace your classification and review decisions.

See [Sandbox connection details](usage.md#connect-plaid-sandbox) and [account destinations](../app/plugins/ingestion/plaid/README.md#account-destinations-and-transaction-history) if you need help. Overlapping manual or YNAB history is not automatically reconciled.

## 3. Turn on regular updates

In Plaid settings, set **Regular updates** to every hour, every 3 hours or every 6 hours, and save. Checks run while WHAB is running. **Manual only** disables automatic and webhook-triggered imports; **Sync now** remains available. Institution availability determines when new bank data arrives.

On macOS, **Enable background checks** can register hourly wakeups when the app is closed. On other platforms, see [background scheduling](../app/plugins/ingestion/plaid/README.md#manual-and-regular-updates).

Agents can inspect and set the same policy through the CLI, using the same home:

```sh
whab --environment sandbox "My budget" bank connections plaid
whab --environment sandbox "My budget" bank schedule plaid --every-hours 6
whab --environment sandbox "My budget" bank --help
```

Replace "My budget" with the name returned by `whab --environment sandbox budget list`.

## When you are ready for real accounts

Follow [Going live](usage.md#going-live) for Plaid access, production credentials and the explicit `ALLOW_LIVE_PLAID=true` opt-in. Use your real runtime home and production environment; Sandbox success does not establish live institution access. Reconnect an existing bank connection instead of creating a duplicate. Keep the runtime home and encryption key together when backing up or upgrading.
