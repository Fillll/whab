# Command line interface

The `cli` Interface plugin installs `whab-cli`, a noninteractive JSON terminal interface for people and external agents. It needs no Web interface or AI plugins. Run it against a budget explicitly enabled with the [server interface](../server/README.md). The terminal executable is available after installing this package; enabling its optional server-side registration adds package information only. The existing `python -m whab` bootstrap is unchanged.

Supply an HTTPS origin, stable budget ID, and access token through an environment variable. Use `--ca` for an explicitly selected CA certificate; certificate verification is always enabled. The default token variable is `WHAB_CLI_TOKEN`; `--token-env` selects another. This is ordinary server access, separate from AI credentials.

```sh
whab-cli --url https://localhost:5173 --budget BUDGET_ID --ca /absolute/path/ca.crt query transactions
whab-cli --url https://localhost:5173 --budget BUDGET_ID --ca /absolute/path/ca.crt query categories
whab-cli --url https://localhost:5173 --budget BUDGET_ID --ca /absolute/path/ca.crt query features
whab-cli --url https://localhost:5173 --budget BUDGET_ID --ca /absolute/path/ca.crt feature-query subscriptions report --parameters '{"month":"2026-11","currency":"USD"}'
```

All responses are JSON envelopes containing `budgetId`, `revision` and `data`. Queries accept `--workspace demo` on the root command (personal is the default). Use `query transactions --month YYYY-MM` to limit the read. `query features` lists the enabled feature query and command names. `feature-query PLUGIN ACTION --parameters JSON` calls their public reads.

Writes require the exact revision used to make the decision and a caller-generated unique request ID:

```sh
whab-cli --url https://localhost:5173 --budget BUDGET_ID command edit_transaction --parameters '{"transactionId":"PAYMENT_ID","categoryId":"CATEGORY_ID"}' --revision OBSERVED_REVISION --request-id UNIQUE_INTENT_ID
whab-cli --url https://localhost:5173 --budget BUDGET_ID feature-command subscriptions set_label --parameters '{"transactionId":"PAYMENT_ID","fingerprint":"OBSERVED_FINGERPRINT","isSubscription":true,"cadence":"monthly"}' --revision OBSERVED_REVISION --request-id ANOTHER_UNIQUE_INTENT_ID
```

Subscription labels apply to matching payments in the same account; see [Subscriptions](../../automation/subscriptions/README.md). Setting a transaction category does not mark it reviewed unless `reviewed: true` is explicitly included. `command OPERATION --parameters JSON` supports the documented public ledger command vocabulary. `feature-command` supports the commands returned by discovery. Use `--parameters -` to read up to 1 MiB from standard input.

The CLI makes one request, never retries a write or refreshes its revision automatically, rejects redirects/proxies and validates response budget identity. Success prints JSON to stdout and exits 0; failure prints a sanitized JSON error to stderr and exits 1. After an uncertain write, retain the original target, parameters, revision and request ID. Inspect the budget and retry only that exact intent; a restart or stale revision requires reconciliation rather than a new write. Request receipts have the host's documented lifetime; the CLI does not claim durable receipt storage.

Install only the host, this plugin, and needed storage/server/feature packages for a model-free environment. The `bundled` extra also installs optional AI packages but does not enable them. Python 3.14+ and WHAB 2.52+ are required.
