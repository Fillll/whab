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
preserves known durable or uncertain work for the original Accounts page.
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
