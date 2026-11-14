# Start without an agent: Web

WHAB works as a local budgeting app without an agent or bank connection.

## 1. Install

Follow [Install locally](agentic-work-101.md#1-install-locally), then stay in `whab/app/`. You can use the Web interface without using an agent.

## 2. Start your local app

Choose a private runtime directory outside the checkout:

```sh
export WHAB_HOME="$HOME/.local/share/whab"
.venv/bin/python scripts/setup.py
WHAB_ENV=production WHAB_UI=web .venv/bin/python -m whab
```

Setup creates private configuration and localhost HTTPS certificates. Manually [trust this runtime's public CA certificate](usage.md#local-https), following the fingerprint and instructions printed by setup. Setup does not change browser or OS trust. Open [https://localhost:5173](https://localhost:5173); keep the terminal running and use Ctrl+C to stop the app. Restart with the same home and launch command to reopen your data. If port 5173 is already in use, add `--port 5174` and open that port instead.

For fictional experiments, choose a separate practice home and launch with `WHAB_ENV=sandbox`. Match that environment explicitly in any CLI commands.

## 3. Make your first budget

1. Explore **Demo Budget** for fictional examples, or open **My Budget** and create a budget through **Settings → Budgets**.
2. In **Accounts**, add a manual account with its currency, type, budget scope and balance.
3. Plan category amounts in **Budget**, add and classify transactions in **Transactions**, then inspect **Insights**. Choose an Expense, Income or Transfer category before marking a posted transaction reviewed.

Edits save in place. [Plaid](plaid-quick-start.md) adds bank imports when you want them. See [usage and backups](usage.md) for portable budgets, rollover and recovery, or [configuration](../app/README.md#configuration-and-plugins) for other setups. Existing installations must retain their runtime home and keys across upgrades.
