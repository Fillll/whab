# Plaid bank plugin

Distributed independently as `whab_plugin_plaid`. Its version and dependency compatibility range are defined in [pyproject.toml](pyproject.toml); its dated history belongs in [CHANGELOG.md](CHANGELOG.md). Material changes confined to this plugin update its version and changelog, without an app version or app changelog change.

The live typed provider adapter in version 1.3 requires WHAB 2.38 or newer. Older hosts must retain a compatible earlier plugin version.

`src/whab_plugin_plaid/` owns the descriptor, provider transport, payload normalization, signed webhook verification, templates and connection script. The host owns Item-token encryption, durable continuation capture and native command application. Legacy direct service helpers preserve their existing ciphertext format. Keep supporting modules, assets and mocked provider tests in this folder.

WHAB requires HTTPS for local pages, bank endpoints, OAuth redirects in every environment and webhook delivery. Registered webhook paths bypass localhost Host/origin checks, but must pass Plaid signature/timestamp/body-hash verification; TLS encryption does not replace provider authentication. See [TLS setup](../../../../docs/usage.md#local-https).

Tests use fictional credentials, in-memory storage, temporary homes and HTTP mock transports. Preserve encrypted-token compatibility, pending-to-posted annotations and atomic imports/cursors.

Browser controls bind once per element on startup and on `whab:page-ready`, so inserted Accounts views remain usable. A cancelable `whab:before-navigate` event vetoes partial replacement while Link, synchronization or disconnection is active, including asynchronous exchange/sync after Link succeeds. The module remains independent of the selected visual plugin and preserves its original budget API scope.

The browser also accepts the host's continuation-aware bank facade. A new Link
session stores its opaque flow ID, original budget address, provider, environment,
generation, deadline and short-lived Link token in this tab's session storage.
Public tokens, credentials and raw exchange responses are never stored there. OAuth
returns to that original Accounts address, including a saved readable budget
alias, and checks the existing flow before reopening Link. Unversioned browser
records cannot resume an exchange; finish old Link sessions before upgrading.

For a tracked session, Link exit requests cancellation and keeps the session if
cancellation cannot be confirmed. Manage completes the update continuation without
exchanging a public token or creating another Item. A successful connection reads
Items separately before returning to Accounts. Duplicate Link callbacks submit
completion once. A lost completion response checks the same flow's status; it
never starts another Link session or resubmits an exchange. The Accounts panel's
Check status and Cancel session controls retain uncertain work for reconciliation.
After status confirms a captured response or uncertain native save, Finish saving
can resume only that retained native command. It sends the flow ID without a
public token, rechecks status before submission, and keeps the intent if its reply
is lost. It cannot retry an external exchange. A rejected OAuth callback also
preserves known durable or uncertain work for the original budget’s Plaid plugin settings.
A ready session whose public token submission was interrupted can be cancelled
explicitly; it is not automatically reopened. Closing the tab does not cancel a
server operation.

The typed worker adapter uses the host provider coordinator to bind every flow to its
budget, environment, provider and captured generation, persist intent before
external exchange, encrypt the response before native application, and reconcile
lost native replies from durable receipts. The browser does not supply commit
evidence or authorize replay. An explicit retry after a lost initial Link-token
response must recover the coordinator's original ready flow for the same Item;
an incompatible pending flow defers it. The descriptor now uses `BankAdapter`
with an explicit live lifecycle. Services exposing only the
original token/exchange methods retain the restart compatibility path, without a
durable continuation guarantee.

Synchronous storage operations and synchronization normalization run in the event loop's bounded executor so file writes and ledger locks do not block unrelated network requests. A cancelled request waits for already-started storage work to finish before releasing its per-connection lock; saves and cursor updates retain their synchronous, atomic storage contract. Request context propagates into storage workers.

The host's queued webhook synchronization acquires an independent runtime lease
and coalesces follow-up notifications without dropping an update received during
a sync. Replacement/disable defers unfinished Link, update, disconnect or queued
sync work. Unrelated optional-plugin changes retain the bank engine and original
flow generation. Shutdown waits for admitted native writes and preserves pending
private records. The retained `create_bank(BankContext)` helper remains a legacy
direct service API; only the typed descriptor participates in worker upgrades.

The worker receives a bounded `BankProviderContext` with its reviewed provider
configuration. The host encryption key, native store, namespace callbacks and
background factory stay outside IPC. Provider errors use fixed `ProviderFailure`
codes; unknown hook/transport failures retire the child. Native apply uncertainty
is decided only by the selected backend's atomic command receipt and does not
clear an independent storage-worker recovery gate. A missing host key leaves the
provider explicitly unconfigured and creates no plaintext continuation journal.

UI templates and scripts are captured as bounded immutable `PluginResources`.
Pages use `/banks/plaid/{generation-token}/static/...`; old pages keep their exact
bytes through bounded retention, and the original `/plugins/plaid/static/...`
address remains a compatibility alias. Existing encrypted Items and cursors are
preserved across approved code replacement and reopening. The installed fictional
provider upgrade test exercises different v1/v2 wheels, failed readiness, pending
flow deferral, child death during exchange and encrypted-journal reopen. All Plaid
transport tests use synthetic records and HTTP mocks; these checks do not establish
real institution access.

Imported checking, savings and credit accounts default on-budget. Investment, retirement, loan and CD accounts default off-budget, so their balances and transactions do not fund envelopes. Supported provider subtypes identify brokerage, IRA, Roth IRA, 401(k), 403(b), 457(b), pension, mortgage, auto loan, student loan and CD accounts. Unrecognized subtypes remain unspecified; unrecognized account types become unclassified off-budget accounts. Saved user account classifications survive subsequent bank refreshes.

The existing Plaid plugin also displays merchant artwork when a transaction supplies a supported top-level `logo_url`. Normalization retains only an opaque artwork key for canonical HTTPS PNGs on `plaid-merchant-logos.plaid.com` or `plaid-counterparty-logos.plaid.com`; absent or unsupported artwork keeps the interface fallback. It does not infer a merchant, search counterparties, or request a separate enrichment product.

Artwork downloads happen only through a local image request, separately from synchronization and page rendering. They use public Plaid CDN URLs without banking credentials, accept at most 128 KiB of PNG data, and reject redirects. The host caches successful artwork for 24 hours and unavailable artwork for 5 minutes in memory. No new token or secret is required. Previously saved transactions receive artwork only when Plaid supplies them again in an added or modified sync record.

From the application directory, use `.venv/bin/python -m pytest plugins/ingestion/plaid/tests` or `.venv/bin/python scripts/check.py --quick` during iteration. Run `.venv/bin/python scripts/check.py` before submitting; use `--docs` for documentation-only edits. See [plugin contribution rules](../../README.md#test-ownership) for the complete check contract.


### Connection settings

Open **Settings → Plugins → Plaid → Settings** to connect, manage, reconnect, sync or disconnect banks. Accounts lists financial accounts without connection setup controls. Disconnect confirmation stays inside the same settings page, and imported records remain in the budget. OAuth continuation keeps its existing addressed Accounts return path and original budget/environment checks, then returns to Plaid settings. The provider emits the public `whab:plugin-settings` document event while opening/exiting Link so a visual adapter can suspend its native settings modal without stacking dialogs. Completed operations reopen `#plugin-settings-plaid`.

## Investment positions and returns

Plaid 1.7 with WHAB 2.60 adds **Investments** to each connection in Plaid settings.
Open it to select an investment account, inspect current positions and expand
investment activity. **Connections** returns to the same settings page. The
account and inclusive history filters also work through both CLI entry points:

```sh
whab BUDGET bank connections plaid
whab BUDGET bank investments plaid --item CONNECTION_ID
whab-cli PROFILE bank investments plaid --item CONNECTION_ID --bank-account PROVIDER_ACCOUNT_ID --since 2025-01-01 --until 2026-11-07
```

Use the provider account ID returned by preview or the investment report, rather
than its linked WHAB destination ID. Omitting the account selects all investment
accounts. History defaults to the last two years through today; institutions may
provide less. These dates filter activity, while holdings always represent the
current provider snapshot. They do not change transaction-import cutoffs.

Reports include quantity, price, price date, value, reported total cost basis,
position status and unrealized gain/loss in currency minor units and percent.
Unrealized return compares value with positive reported basis for a nonshort
position; it excludes dividends and realized returns. Unknown/unsupported basis
stays unavailable, and totals identify positions with known basis and keep
currencies separate. **First recorded addition** is the earliest uncancelled buy
or explicit inbound transfer in the selected available history. It is not a
tax-lot acquisition date. No acquisition date or realized return is invented
when history is incomplete.

Investments must already be active and consented on this existing Plaid Item.
The report never activates another product, creates a connection or forces a
fresh institution fetch. Existing provider billing applies. Reading positions
does not import trades into the budget, move accounts, advance a transaction
cursor or change the saved sync schedule. Responses contain no access tokens;
overlarge or inconsistent provider results fail rather than silently truncating.

## Operation documentation

The packaged [operations.json](src/whab_plugin_plaid/operations.json) documents this plugin's public operations, input/output schemas, fictional examples, effects, access, scope, errors and retries. Read it offline with `whab-cli describe --plugin plaid`, add `--json` for structured metadata, or use operation-specific `--help` for public CLI commands. Local-only settings and SDK callbacks are explicitly labeled and are not made remotely callable by documentation. Delegated ledger operations are described by `whab-cli describe --plugin core`.


## Hourly checks without a visual interface

Plaid 1.5 declares `BankAdapter.sync_interval_seconds=3600`. WHAB 2.56's shared
job runner calls the existing incremental synchronization operation, preserving
provider ownership, annotations and atomic transaction/cursor saves. It records
last attempt/success and sanitized failures and coalesces missed intervals.

Use a local CLI profile to run `whab-cli default.budget sync plaid`, inspect
`jobs status`, and register macOS wakeups with `jobs enable`. Web UI is optional.
A running host checks due jobs too; a stopped host can be started by the OS job.
No new bank connection or forced `/transactions/refresh` is created by scheduling.
Institution freshness remains provider-dependent. See [Plaid transaction updates](https://plaid.com/docs/transactions/#transactions-updates).


## Account destinations and transaction history

Plaid 1.6 requires WHAB 2.59+. After connecting a new institution, select **Choose destinations** beside the bank in Plaid settings before the first import. Reviewing accounts fetches provider data without importing transactions or advancing the saved cursor. For each account, select an existing WHAB account in the same currency, create a new account, or choose **Do not import**. Choose a start date explicitly (inclusive), or explicitly select **All available history**. No date is selected automatically. **Save and sync now** saves the selection and imports only the selected accounts.

Expand **WHAB destinations** under a connected bank to review its mappings, last four digits and history choices. **Edit destinations** reopens setup in the same window. The picker lists existing WHAB accounts first; unavailable entries show the reason, including unclassified accounts that need an account type in **Accounts**. Linking an auto-created bank account to an existing account joins its existing transaction references while preserving transaction IDs, notes, reviews, categories and receipts. Old history is retained, including rows before the new cutoff; overlapping YNAB/manual history is not automatically matched. Once an explicit destination is saved, changing it to a different account is refused. Moving a cutoff earlier resets the cursor and replays stable provider transaction IDs without duplicating those bank records. Provider availability still bounds the history returned.

## Manual and regular updates

**Regular updates** offers Manual only, every hour, every 3 hours and every 6 hours, per budget/provider. Save the frequency explicitly. Manual only suppresses periodic and webhook-triggered imports; **Sync now** remains available. Scheduled checks honor the same account links and dates as manual checks. Existing connections retain their previous hourly policy until changed. New connections wait for account setup before importing.

Checks run while the host is running. On macOS, **Enable background checks** registers the existing hourly launchd wakeup; each budget’s saved cadence decides which checks are due. **Disable background checks** unregisters those device/environment wakeups. On other platforms, schedule `python -m whab --home /absolute/runtime --environment production --run-jobs` with cron; hourly wakeups support the saved 1/3/6-hour policies and coalesce missed checks.

Both console entry points use the same host controls:

```sh
whab Household bank connections plaid
whab Household bank preview plaid --item CONNECTION_ID
whab Household bank configure plaid --item CONNECTION_ID --accounts '[{"bankAccountId":"BANK_ACCOUNT_ID","accountId":"WHAB_ACCOUNT_ID","since":"2026-11-01"}]'
whab Household bank sync plaid --item CONNECTION_ID
whab Household bank schedule plaid --every-hours 3
whab Household bank schedule plaid --manual
whab Household bank background plaid --enable
```

Use JSON `null` for a new destination account or an explicitly chosen all-history cutoff. Omit bank accounts you do not want to import. The advanced form is `whab-cli default.budget bank ...`; read `bank --help` or `describe --plugin plaid --operation bank.configure` offline. These are local host controls, not remote ledger commands; connecting a bank still requires its interactive consent flow.

## Shared connections and several Plaid accounts

Plaid 1.8 requires WHAB 2.63+. **Use existing connection** lists connections on
this device in the selected environment, their provider account profile, stable
connection ID, safe account names/masks, owner and budget attachments. Choose
**Use this connection**, then **Choose destinations** to select this budget's destinations
and an explicit inclusive date or **All available history**. Attachment performs
no Link flow, import or ledger copy. Classify unclassified YNAB destinations
first; WHAB never infers account types from names. Overlapping YNAB and Plaid
transactions are not automatically reconciled.

Each budget saves its own mappings, dates, cursor and manual/periodic policy.
Syncing one does not consume another's updates. **Detach from this budget** keeps
history, mappings and progress and leaves the bank and other budgets connected.
**Disconnect from bank for all budgets** lists the affected budgets and revokes
the underlying Item. Disabled plugins retain attachments; disabling never removes
the provider Item. Separate connections at the same institution remain distinct.

Several Plaid API accounts can coexist as named profiles. The existing credentials
remain the **default** profile; add a separate account in the same settings page,
then select its profile before **Connect account**. Existing connections always
use their saved profile. Profile names identify accounts, not Sandbox versus
production; environments remain separate. A profile's account identity cannot be
changed in place; add a new profile for a different Plaid account. Saving credentials
creates no bank Item. Bank consent and reauthentication still use interactive Link.
A consumer Plaid Portal login alone does not supply API credentials for WHAB;
profiles represent API-enabled Plaid accounts in either supported environment.

CLI equivalents:

```sh
whab BUDGET bank profiles plaid
whab BUDGET bank profile-save plaid --profile second-account --credentials-file /private/plaid-account.json
whab BUDGET bank shared plaid
whab BUDGET bank attach plaid --item CONNECTION_ID
whab BUDGET bank preview plaid --item CONNECTION_ID
whab BUDGET bank configure plaid --item CONNECTION_ID --accounts '[{"bankAccountId":"PROVIDER_ACCOUNT_ID","accountId":"CLASSIFIED_WHAB_ACCOUNT_ID","since":"2026-11-01"}]'
whab BUDGET bank sync plaid --item CONNECTION_ID
whab BUDGET bank detach plaid --item CONNECTION_ID
```

The private profile file is a JSON object of provider configuration strings,
including `PLAID_CLIENT_ID`, `PLAID_SECRET` and, for deliberate live access,
`ALLOW_LIVE_PLAID`. Use `--credentials-file -` for stdin. Never put secrets directly
in command arguments or commit that file. Optional `PLAID_WEBHOOK_URL` and
`PLAID_REDIRECT_URI` configure that profile's interactive flow. Named-profile
webhook URLs automatically retain a `profile` query parameter; signature
verification still precedes all effects. Profiles and Item tokens are encrypted
with the existing host key and are not exported in portable budgets.

After reviewing `bank shared`, global revocation requires its exact sorted set of
attached/pending budget IDs, for example with fictional IDs:

```sh
whab BUDGET bank disconnect plaid --item CONNECTION_ID --affected-budgets '["budget-a","budget-b"]'
```

Unavailable saved budgets are listed and block global revocation until their
attachments can be checked. A failed or uncertain revocation requires inspecting
the existing continuation; do not reconnect or repeat provider removal. Back up
all runtime data and keys together. Reopening retries only recoverable local
registration/attachment writes; original connection ownership and native progress
are retained. See the [ownership decision](../../../../docs/architecture.md#shared-bank-connection-ownership).

## Closed accounts and CDs

Plaid depository/CD records already normalize to the core asset classification
`deposit` / `cd`, off budget. CLI uses those same spellings. With WHAB 2.64 and
lifecycle-capable storage, user-closed accounts are archives: bank refreshes cannot
reopen them, replace their saved classification/balances, add/modify/remove their
transactions or mark their snapshots stale as part of an ordinary sync. Open
accounts on the same shared connection and other budgets continue normally. An
all-closed Item is skipped before provider access; mixed Items can still be fetched
for open accounts, with archived records excluded on atomic application. Existing
mappings/history choices remain. New destination pickers exclude closed accounts;
reopen before linking a new destination. Reopening replays this budget's saved
checkpoint at the next due/explicit sync, subject to provider history availability,
without starting sync itself or changing the connection/schedule.
