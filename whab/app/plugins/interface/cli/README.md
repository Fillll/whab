# Command line interface

## Local operation

A fresh runtime needs only the host, CLI and storage packages. No AI account, Web
UI, remote API plugin or manually configured bearer token is required. Use a JSON
`default.budget` profile:

```json
{
  "home": "/absolute/path/to/private/runtime",
  "budget": "default",
  "environment": "sandbox"
}
```

```sh
whab-cli default.budget query accounts
whab-cli default.budget plugins list
whab-cli default.budget plugins enable web
whab-cli default.budget plugins disable web
whab-cli default.budget plugins enable plaid
whab-cli default.budget sync plaid
whab-cli default.budget jobs status
whab-cli default.budget jobs enable
whab-cli default.budget jobs run
whab-cli default.budget jobs disable
```

Relative `home` and `ca` paths resolve against the profile directory. Choose
`home` for local operation or `url` for a remote connection. `--home DIRECTORY`
and `--environment sandbox|production` provide the same explicit configuration.
For a saved portable budget, use its permanent UUID from the runtime catalog as
`budget`. Production requires an explicit environment and existing authorized
provider configuration; plugin enablement never creates a bank connection.

The CLI acquires a private authenticated HTTPS lease on the existing host or
starts a temporary one. Startup is serialized per runtime home/environment;
commands retain their original revision/request ID and are never replayed after
transport failure. The host exits shortly after the last lease ends, unless an
enabled visual/API interface or an explicit server launch (`python -m whab`, optionally `--serve`) needs it.
Closing browser tabs does not disable Web. Stateful engines and active operations
retain their owners; pure rendering/import/report/artwork hooks and installed
bank provider clients release their child after each operation. Existing budget
selections and encrypted connections remain intact.

Plaid declares a 3,600-second incremental sync interval. `jobs enable` installs a
private macOS LaunchAgent that runs due jobs hourly, including without Web UI.
The running host also checks due work; saved due times prevent duplicate runs and
coalesce missed intervals after sleep. `jobs run` runs only due jobs; `sync plaid`
is an explicit immediate sync. Job status records the last attempt/success and a
sanitized failure code. `jobs disable` removes OS wakeups; a running host still
honors enabled providers' intervals. Other operating systems can schedule
`python -m whab --home DIRECTORY --environment sandbox --run-jobs` with their OS
scheduler; automatic registration currently supports macOS. No public webhook
receiver is needed for polling.

Local management is personal-workspace only. Commands return JSON and errors are
sanitized. Plugin changes use existing live transitions, including browser edit
draining for visual changes; unsupported live changes report their limitation.
Neither the CLI nor the scheduler reads or writes a database directly.

## Remote operation

The `cli` Interface plugin installs `whab-cli`, a noninteractive JSON terminal interface for people and external agents. It needs no Web interface or AI plugins. Run it against a budget explicitly enabled with the [server interface](../server/README.md). The terminal executable is available after installing this package; enabling its optional server-side registration adds package information only. `python -m whab --serve` explicitly starts a persistent headless service.

Supply an HTTPS origin, stable budget ID, and access token through an environment variable. Use `--ca` for an explicitly selected CA certificate; certificate verification is always enabled. The default token variable is `WHAB_CLI_TOKEN`; `--token-env` selects another. This is ordinary server access, separate from AI credentials.

Save repeated connection options as a UTF-8 JSON object in a file named, for example, `default.budget`:

```json
{
  "url": "https://localhost:5173",
  "budget": "default",
  "ca": "/absolute/path/ca.crt"
}
```

Pass that file before options and the command:

```sh
whab-cli default.budget --help
whab-cli default.budget command edit_transaction --help
whab-cli default.budget describe --offline --json
whab-cli default.budget query transactions
whab-cli default.budget --budget ANOTHER_BUDGET query accounts
```

A remote profile accepts `url`, `budget`, `ca`, `workspace` and `token_env`; a local profile also supports `home` and `environment`. Values are all nonempty strings with the same values as their command-line options. For example, `"workspace": "demo"` selects the demo workspace and `"token_env": "MY_WHAB_TOKEN"` selects the environment variable containing the access token. Unknown or duplicate keys, invalid JSON and incorrect value types are rejected before a request. Explicit command-line options override file defaults. Relative CA paths use the config file's directory; `~` expands to the user's home for the config and CA paths. Files are limited to 64 KiB and are read as data: shell commands and environment substitutions are never executed. Keep the file with private runtime configuration, outside Git. Store tokens in the named environment variable. Configured help and offline discovery need no server or token.

Existing flag-based connection files remain readable for compatibility. New files and help examples use JSON; the `.budget` filename extension is unchanged.

The equivalent explicit connection options remain supported:

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

Install only the host, this plugin, and needed storage/server/feature packages for a model-free environment. The `bundled` extra also installs optional AI packages but does not enable them. Python 3.14+ and WHAB 2.56+ are required.

## Operation documentation

Help and packaged documentation work without a server, token, AI account or source checkout:

```sh
whab-cli --help
whab-cli command edit_transaction --help
whab-cli feature-command subscriptions set_label --help
whab-cli describe --plugin subscriptions
whab-cli describe --plugin ai-credentials --kind local --operation save-key --help
whab-cli describe --json
```

Operation help lists input fields, required values, defaults/choices, output fields, examples, scope, access, side effects, errors and retry rules. `describe --json` returns complete JSON Schemas; filter with `--plugin core`, `--kind command` or `--operation edit_transaction`. Offline listings identify installed plugins and explicitly leave their enablement unknown.

To inspect the running authority instead, supply the usual connection arguments:

```sh
whab-cli --url https://localhost:5173 --budget BUDGET_ID --ca /absolute/path/ca.crt describe --json
```

Authenticated online discovery adds current plugin enablement and the running HTTP OpenAPI paths, request parameters/bodies and response schemas. Human-readable `describe` lists HTTP methods and paths. Access rules still apply: local settings and SDK callbacks are described with their actual boundaries. Discovering a local credential action does not make it remotely executable. Add `--offline` to use installed package metadata even with a configured URL. `query describe` returns the same online metadata in the normal revision envelope; `describe` prints only the catalog.

The packaged [operations.json](src/whab_plugin_cli/operations.json) documents this plugin's public operations, input/output schemas, fictional examples, effects, access, scope, errors and retries. Read it offline with `whab-cli describe --plugin cli`, add `--json` for structured metadata, or use operation-specific `--help` for public CLI commands. Local-only settings and SDK callbacks are explicitly labeled and are not made remotely callable by documentation. Delegated ledger operations are described by `whab-cli describe --plugin core`.
