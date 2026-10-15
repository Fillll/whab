# Plaid bank plugin

`src/whab_plaid/` owns the descriptor, payload normalization, encrypted tokens, Link, synchronization, signed webhooks, templates and connection script. Keep supporting modules, assets and mocked provider tests in this folder.

Tests use fictional credentials, in-memory storage, temporary homes and HTTP mock transports. Preserve encrypted-token compatibility, pending-to-posted annotations and atomic imports/cursors.

The existing Plaid plugin also displays merchant artwork when a transaction supplies a supported top-level `logo_url`. Normalization retains only an opaque artwork key for canonical HTTPS PNGs on `plaid-merchant-logos.plaid.com` or `plaid-counterparty-logos.plaid.com`; absent or unsupported artwork keeps the interface fallback. It does not infer a merchant, search counterparties, or request a separate enrichment product.

Artwork downloads happen only through a local image request, separately from synchronization and page rendering. They use public Plaid CDN URLs without banking credentials, accept at most 128 KiB of PNG data, and reject redirects. The host caches successful artwork for 24 hours and unavailable artwork for 5 minutes in memory. No new token or secret is required. Previously saved transactions receive artwork only when Plaid supplies them again in an added or modified sync record.

From the application directory, run `.venv/bin/python -m pytest plugins/ingestion/plaid/tests`. See [plugin contribution rules](../../README.md) for all checks and packaging.
