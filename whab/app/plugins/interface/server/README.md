# Remote budget server

Distributed independently as `whab_plugin_server`. Its version and dependency compatibility range are defined in [pyproject.toml](pyproject.toml); its dated history belongs in [CHANGELOG.md](CHANGELOG.md). Material changes confined to this plugin update its version and changelog, without an app version or app changelog change.

The `server` interface plugin exposes a budget's public ledger operations at
`/remote/v1/budgets/{budget_id}`. Enable it with the budget's optional plugins and
set `SERVER_TOKEN` to a secret of at least 32 characters on the server machine.
Clients use that budget's stable ID and the matching token. Every request requires
HTTPS and bearer authentication. The host uses native TLS and rejects plaintext for
all routes, including localhost; it does not trust forwarded headers as proof of
HTTPS. Provision a certificate valid for the client's server origin and preserve
loopback restrictions on ordinary visual/private banking routes. See
[local TLS setup and trust](../../../../docs/usage.md#local-https).

This trusted plugin receives the budget's command service, not bank credentials.
It exposes accounts, categories, transactions, budget projections and portable
ledger snapshots. Bank connections and token operations execute on the server
and are never available through this protocol. The existing local browser routes
retain their local access restrictions.

Commands require an opaque expected revision and a request ID. A stale revision
returns a conflict. Repeating the same request returns the original result;
reusing its ID for a different command returns a conflict. The latest 1,024
receipts are retained in memory. Restarting the service rotates its revision;
clients must refresh and inspect uncertain changes after restart. This rejects
ambiguous retries rather than accepting duplicate writes.

The machine/server token authorizes budgets configured with this server plugin;
multi-user roles, public account registration and public certificate
provisioning are outside this plugin contract. Host setup can create a local CA
and localhost certificate, but trust requires explicit user action. Server configuration stays on the machine.
A client can deliberately export its remote access credential in a
password-protected container; bank-provider tokens are never carried.

## Development checks

From `whab/app/`, use `.venv/bin/python -m pytest plugins/interface/server/tests` or `.venv/bin/python scripts/check.py --quick` during iteration, then `.venv/bin/python scripts/check.py` before submitting. Documentation-only edits can use `--docs`. Changes to budget targeting, authorization or command identity also need the [two-budget isolation checks](../../../../docs/architecture.md#budget-isolation-verification). See [plugin contribution rules](../../README.md#test-ownership) for the complete check contract.
