# Plaid bank plugin

Distributed independently as `whab_plugin_plaid`. Its version and dependency compatibility range are defined in [pyproject.toml](pyproject.toml); its dated history belongs in [CHANGELOG.md](CHANGELOG.md). Material changes confined to this plugin update its version and changelog, without an app version or app changelog change.

`src/whab_plugin_plaid/` owns the descriptor, payload normalization, encrypted tokens, Link, synchronization, signed webhooks, templates and connection script. Keep supporting modules, assets and mocked provider tests in this folder.

WHAB requires HTTPS for local pages, bank endpoints, OAuth redirects in every environment and webhook delivery. Registered webhook paths bypass localhost Host/origin checks, but must pass Plaid signature/timestamp/body-hash verification; TLS encryption does not replace provider authentication. See [TLS setup](../../../../docs/usage.md#local-https).

Tests use fictional credentials, in-memory storage, temporary homes and HTTP mock transports. Preserve encrypted-token compatibility, pending-to-posted annotations and atomic imports/cursors.

Browser controls bind once per element on startup and on `whab:page-ready`, so inserted Accounts views remain usable. A cancelable `whab:before-navigate` event vetoes partial replacement while Link, synchronization or disconnection is active, including asynchronous exchange/sync after Link succeeds. The module remains independent of the selected visual plugin and preserves its original budget API scope.

Synchronous storage operations and synchronization normalization run in the event loop's bounded executor so file writes and ledger locks do not block unrelated network requests. A cancelled request waits for already-started storage work to finish before releasing its per-connection lock; saves and cursor updates retain their synchronous, atomic storage contract. Request context propagates into storage workers.

Imported checking, savings and credit accounts default on-budget. Investment, retirement, loan and CD accounts default off-budget, so their balances and transactions do not fund envelopes. Supported provider subtypes identify brokerage, IRA, Roth IRA, 401(k), 403(b), 457(b), pension, mortgage, auto loan, student loan and CD accounts. Unrecognized subtypes remain unspecified; unrecognized account types become unclassified off-budget accounts. Saved user account classifications survive subsequent bank refreshes.

The existing Plaid plugin also displays merchant artwork when a transaction supplies a supported top-level `logo_url`. Normalization retains only an opaque artwork key for canonical HTTPS PNGs on `plaid-merchant-logos.plaid.com` or `plaid-counterparty-logos.plaid.com`; absent or unsupported artwork keeps the interface fallback. It does not infer a merchant, search counterparties, or request a separate enrichment product.

Artwork downloads happen only through a local image request, separately from synchronization and page rendering. They use public Plaid CDN URLs without banking credentials, accept at most 128 KiB of PNG data, and reject redirects. The host caches successful artwork for 24 hours and unavailable artwork for 5 minutes in memory. No new token or secret is required. Previously saved transactions receive artwork only when Plaid supplies them again in an added or modified sync record.

From the application directory, run `.venv/bin/python -m pytest plugins/ingestion/plaid/tests`. See [plugin contribution rules](../../README.md) for all checks and packaging.
