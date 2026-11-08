# Command line interface

## Simple console

`whab` provides name-addressed local commands. **Production is the default** for
both `whab` and `whab-cli` from CLI 2.0; use `--environment sandbox` for tests, or
keep an explicit sandbox environment in a profile. Existing profiles and explicit
options retain their selections. The host SDK and developer fixtures keep their
existing sandbox defaults.

```sh
whab --help
whab budget --help
whab budget create --help
whab budget create empty-budget --path "~/budgets"
whab budget list
whab plugin --help
whab empty-budget plugin enable ynab-zip-importer
whab empty-budget plugin status
whab empty-budget insights summary
whab empty-budget trnx non-reviewed
whab empty-budget trnx uncategorized --limit 10 --format table
```

Every level has `--help`, including reports and plugin controls. `transactions`
and `trnx` are aliases; `non-reviewed` and `unreviewed` are aliases. Quotes around
`~` are supported: the CLI expands them itself. `--path` accepts a directory or
an explicit `.whab` file. Without it, creation uses the runtime-managed budget
location. Names containing spaces can be quoted. Names are resolved without
changing the default browser budget; use a permanent ID from `budget list` if
legacy names are ambiguous. New duplicate names and reserved command/workspace
names are refused. The CLI never substitutes another ledger after a failed lookup.

Set a local default profile at `~/.config/whab/default.budget` once:

```json
{"home": "/absolute/path/to/private/runtime"}
```

Home precedence is `--home`, `WHAB_HOME`, the profile's `home`, then `~/.whab`.
Use `--profile FILE` to choose another local JSON profile; its relative home/CA
paths resolve beside that profile. Environment precedence is `--environment`, an
explicit profile environment, then production. `--ca` overrides the profile CA;
otherwise the runtime's verified CA is used. Global options work before or after
subcommands. Help and offline plugin discovery do not load the profile or open a
connection. Remote profiles remain available through `whab-cli`.

Creation uses the authenticated local host, starts an empty SQLite budget without
inheriting optional plugin selections or financial data, and registers its saved
path. Default category templates remain. Existing files are never replaced. The
original host, other budgets, credentials and active browser selection stay owned
by their current runtimes. Unknown, ambiguous, busy or failed operations never
silently target another budget or automatically replay a mutation. After an
uncertain result, inspect `whab budget list` and plugin status before trying again.

`insights summary` reads the shared core's monthly projection (USD by default,
`--currency` selects another ISO currency). Money remains in integer minor units;
no unlike currencies are combined. No AI or bank sync is started. `trnx
non-reviewed` reads unreviewed, non-deleted public transactions, optionally limited
by `--month YYYY-MM`; it never changes review status.

CLI 2.4 adds `whab BUDGET trnx uncategorized` (`transactions` and British-spelled
`uncategorised` are aliases). It reads the full available history by default and
selects rows whose category ID is null, independently of review status. Deleted
rows are excluded. Pending transactions, explicit/inferred transfers, inflows,
zero amounts and off-budget accounts remain included unless explicitly filtered.

```sh
# All uncategorized transactions, counts and account names in JSON.
whab "My budget" trnx uncategorized
# Ten examples without jq; newest dates first.
whab "My budget" trnx uncategorized --limit 10 --format table
# Posted transactions excluding both explicit and inferred transfers.
whab "My budget" trnx uncategorized --posted --exclude-transfers --format table
# One calendar month.
whab "My budget" trnx uncategorized --month 2026-11
```

JSON includes `total` (all filtered matches), `returned` (rows actually shown),
`pending`, `transferFlagged`, `postedNonTransfer`, `bySource`, `byCurrency` and
the complete `data` rows with `accountName`. Pending and transfer counts can
overlap. Filters apply before counts and `--limit`; omitting the limit returns
every match. Dates descend, with IDs breaking ties without claiming an intraday
chronology. JSON retains integer currency minor units and full field text; table
output uses each currency's precision, escapes terminal controls and shortens long
text. No amounts of different currencies are summed.

Only authenticated local public queries are used. The budget name resolves once;
transaction and account-name reads must match its permanent ID and ledger revision
or fail without partial output. No categories, review flags or sync state change.
The response bound is 16 MiB; `--limit` limits output, not the initial history
read, so choose `--month` if a larger budget exceeds that bound.

The existing detailed JSON vocabulary remains discoverable through:

```sh
whab api --help
whab api command edit_transaction --help
whab api describe --plugin ynab-zip-importer --help
```

## Simple category edits

CLI 2.6 adds category editing directly under `whab BUDGET trnx` (`transactions`
and British-spelled `categorise` are aliases). No JSON, advanced command syntax,
revision or request ID is needed for an ordinary edit:

```sh
whab "My budget" trnx categories
whab "My budget" trnx categorize TRANSACTION_ID --category "Food / Groceries" --agent Codex --needs-review
whab "My budget" trnx categorize TRANSACTION_ID --category CATEGORY_ID
whab "My budget" trnx categorize TRANSACTION_ID --clear
```

`--category` accepts an exact stable ID, a unique category name or `Group / Name`.
Name matching ignores case and outer whitespace. Ambiguous or missing names fail
before writing. Use the categories list to disambiguate; IDs take precedence.
Core still validates the transaction, currency and category on the addressed budget.

Each invocation resolves the budget once, reads categories and their revision,
and submits one guarded category edit. Success returns `requestId` and
`expectedRevision` with the public result. Agents that already observed a decision
revision may pass `--revision`; `--request-id` retains an explicit intent identity.
There is no automatic write retry or revision refresh. After an uncertain result,
inspect the transaction and keep the original arguments and reported revision and
request ID; only an exact intent can be retried while its receipt remains available.
The existing 16 MiB response bound and verified local HTTPS connection apply.

Review status stays unchanged unless `--needs-review` is supplied. `--agent LABEL`
is explicit opt-in; normal manual category edits/clears remove a previous agent
marker. No bank synchronization or model categorization is started.

## Agent category labels

CLI 2.5 with WHAB 2.61 adds an explicit `--agent LABEL` option to
`command edit_transaction`, available through both `whab api` and `whab-cli`.
Normal CLI category changes do not claim an agent. Read the transactions and
categories first, then use their observed revision and a unique request ID:

```sh
whab api --budget BUDGET_ID query transactions
whab api --budget BUDGET_ID query categories
whab api --budget BUDGET_ID command edit_transaction --agent Codex --parameters '{"transactionId":"PAYMENT_ID","categoryId":"CATEGORY_ID","reviewed":false}' --revision OBSERVED_REVISION --request-id UNIQUE_INTENT_ID
# Equivalent detailed interface:
whab-cli --budget BUDGET_ID command edit_transaction --agent Codex --parameters '{"transactionId":"PAYMENT_ID","categoryId":"CATEGORY_ID"}' --revision OBSERVED_REVISION --request-id UNIQUE_INTENT_ID
```

`--agent` requires `categoryId` in the same edit. It saves the optional public
`categoryAgent` field atomically with that category. The same field can be
provided directly in JSON; conflicting labels are rejected. Labels contain
1–80 printable characters without surrounding whitespace. They are self-declared
provenance, not authenticated identities. The transaction's import `source`
(Plaid, YNAB or manual) remains separate.

The Web register shows **Agent · Codex** beside the category. Review, memo and
financial-detail-only edits retain the label; a category change without an agent
label clears it, including selecting the same category manually. Existing review
status is preserved unless explicitly set; include `"reviewed":false` to put an
already-reviewed transaction back in the review queue. Refreshes, posted matches,
reopening and portable saves retain the marker. Older transactions stay unlabelled.
A storage adapter must explicitly advertise durable support; an unsupported
adapter rejects the labelled write instead of silently discarding the marker.

## Account classification help

CLI 2.2.1 with WHAB 2.59.1 or newer makes both `whab --help` and `whab-cli --help` explain account classification offline from the core’s packaged operation descriptions. For the full account fields and explicit edit example:

```sh
whab api query accounts --help
whab api command update_account --help
whab-cli query accounts --help
whab-cli command update_account --help
whab-cli describe --offline --plugin core --operation update_account --json
```

`type` and `onBudget` are separate. `credit` means a credit card or line of credit, treated as debt even when on budget. `other` is a generic unclassified account. Only `depository` and `credit` may be on budget; tracking types remain off budget. New cash/card accounts default on budget, but changing an existing off-budget account to `credit` preserves its scope unless you also pass `"onBudget": true`. Classification alone does not verify an imported balance, connect a bank account, match duplicates or change transaction provenance.

Read the addressed budget with `whab-cli CONFIG query accounts` or `whab --home /absolute/runtime api --budget BUDGET_ID query accounts` to obtain the current type, scope, stable account ID and revision. Then use the existing explicit `update_account` command with the observed revision and a unique request ID; review help before submitting. No classification decision is inferred or saved by help/discovery.

## Plugin controls

CLI is an installed management tool, available to all local budgets without
per-budget activation. Plugin status and documentation identify it as a management
tool with `enabled: null`; enabling or disabling it for a budget is rejected.
Primary storage is required: attempts to disable it fail with a nonzero exit code
and a clear explanation, without changing the selection. Use storage settings to
change the primary backend. Web is optional; enabling/disabling it uses the durable
visual-interface transition and preserves ledger access and saved data. An
already-applied optional choice succeeds without starting another transition.

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

`plugins status` and successful plugin changes return `host_url`, including the
chosen temporary port, so enabling Web also tells you where to open it. Browser
access still requires explicitly trusting that runtime's CA; CLI trust does not
change OS/browser trust.

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

Plugin enable/disable applies supported changes live. If preflight reports that a
plugin requires restart, the host validates and saves the requested selection
without replacing the running plugins; the response has `restart_required: true`.
Restart WHAB to apply it. Busy, invalid and failed live transitions remain errors
and never trigger a fallback write.

Local management is personal-workspace only. Commands return JSON and errors are
sanitized. Plugin changes use existing live transitions, including browser edit
draining for visual changes; unsupported live changes report their limitation.
Neither the CLI nor the scheduler reads or writes a database directly.

## File imports

CLI 2.1 adds preview and commit commands for enabled file importers. Start with
`whab BUDGET import --help` and `whab BUDGET import ynab-zip-importer preview --help`.
For a new empty budget:

```sh
whab budget create empty-budget
whab empty-budget plugin enable ynab-zip-importer
whab empty-budget import ynab-zip-importer preview '~/Downloads/export.zip' --currency USD
whab empty-budget import ynab-zip-importer commit '~/Downloads/export.zip' --currency USD --fingerprint SHA256_FROM_PREVIEW
```

Review the preview's counts, warnings, `canImport`, `reason` and SHA-256
`fingerprint` before committing. Currency confirmation is required; the bundled
YNAB parser accepts USD. ZIP files must be nonempty and at most 10 MiB. Quoted
`~` expands. Use the fingerprint returned by the reviewed preview verbatim.

Commit checks that fingerprint before connecting, obtains a fresh preview and
echoes its parser identity and fingerprint with the identical archive bytes in
the same host lease. A parser change between that preview and commit is refused.
This works even if a temporary headless host exited after the earlier preview.
Names resolve once to a permanent budget identity; other budgets are unchanged.
The CLI uses the existing host import routes and never writes a database directly.

This imports into the addressed pristine personal budget, replacing only untouched
seed categories. Nonempty budgets are refused; financial data is never merged or
overwritten. Preview saves no financial records. Commit atomically saves the
import and its receipt. Neither step creates a second budget or switches the
browser's selected budget. The importer must already be enabled in the target.

No command is automatically retried. After an uncertain commit, preview the same
file in the same budget and inspect `alreadyImported`; an exact same-file commit
retry returns the receipt without duplicating records or overwriting later edits.
A different subsequent archive is refused. See the [YNAB migration limits](../../ingestion/ynab/README.md#migration-limits):
imported accounts need classification and exported balances remain unverified
estimates.

Import validation failures retain the host's bounded authored explanation for
HTTP 400, 409 and 413. Invalid JSON, unexpected response shapes, server crashes
and transport errors remain sanitized. No failed request is automatically replayed.

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

Install only the host, this plugin, and needed storage/server/feature packages for a model-free environment. The `bundled` extra also installs optional AI packages but does not enable them. Python 3.14+ and WHAB 2.62+ are required.

## Operation documentation

Top-level help lists each plugin ID with its packaged purpose and documented operations.
The YNAB importer uses the CLI identifier **`ynab-zip-importer`** and display name
**YNAB ZIP importer**. For example:

```sh
whab-cli --home /absolute/runtime --budget BUDGET_ID plugins enable ynab-zip-importer
whab-cli describe --plugin ynab-zip-importer --help
```

Help, JSON discovery and plugin listings use this explicit CLI identifier. The CLI
translates it to the existing host identifier `ynab`, preserving saved budgets and
compatibility with existing hosts. The old spelling is still accepted in scripts.
A conflicting installed plugin with the reserved CLI identifier is rejected before
submitting an operation.

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


## Bank account setup and update policies

CLI 2.3 with WHAB 2.60 also supports `bank investments PROVIDER --item ID`
through both `whab BUDGET` and `whab-cli PROFILE`. Optional `--bank-account`
selects a provider account ID from preview/the report, not a WHAB destination ID.
`--since YYYY-MM-DD` and `--until YYYY-MM-DD` filter inclusive activity history;
defaults are the last two years through today. Positions remain current snapshots.
JSON includes each position's quantity, price/date, value, reported cost basis,
unrealized gain and percentage, and earliest recorded addition when supported.
Money is integer currency minor units, quantities/prices/percentages are decimal
strings, and unknown dates/basis/realized returns are null. Currency totals stay
separate and describe basis coverage. This reads an already active/consented
Investments product without importing trades or changing transaction sync.
`describe --plugin plaid --json` documents `bank.investments` and its limits.

CLI 2.2 with WHAB 2.59 supports `whab BUDGET bank` and `whab-cli PROFILE bank`. Use `connections PROVIDER` to read connection IDs and saved destinations, `preview PROVIDER --item ID` to review bank accounts without importing, and `configure PROVIDER --item ID --accounts JSON` to save account destinations and explicit history choices. The JSON array contains `bankAccountId`, `accountId` (existing same-currency account or explicit null for a new account), and `since` (inclusive YYYY-MM-DD or explicit null for all available history). No history start is inferred. Omitted accounts are not imported. Configuration and synchronization are separate operations: run `bank sync PROVIDER [--item ID]` after reviewing/saving.

Use `bank schedule plaid --every-hours 1`, `--every-hours 3`, `--every-hours 6`, or `--manual`. Omit those flags to read the saved policy and due status. Manual mode disables periodic and webhook-triggered imports while retaining explicit sync. `bank background plaid --enable` / `--disable` controls macOS device/environment wakeups, which check the per-budget policies. Existing `jobs enable`, `jobs disable`, `jobs run` and `sync plaid` remain supported. An hourly cron entry invoking `python -m whab --home /absolute/runtime --environment production --run-jobs` provides the same due-job filtering on other platforms. Bank controls require the local personal budget and preserve HTTPS/authentication; no remote credential or built-in AI is required. See [Plaid setup and limitations](../../ingestion/plaid/README.md#account-destinations-and-transaction-history).

## Financial history

See the [budget history workflow](../../../../docs/usage.md#budget-history).
This version requires WHAB 2.62+. History is saved atomically with financial
changes and retained across reopen and exports. Earlier changes are not backfilled.
