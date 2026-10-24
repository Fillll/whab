# Remote budget server

Distributed independently as `whab_plugin_server`. Its version and dependency compatibility range are defined in [pyproject.toml](pyproject.toml); its dated history belongs in [CHANGELOG.md](CHANGELOG.md). Material changes confined to this plugin update its version and changelog, without an app version or app changelog change.

The `server` interface plugin exposes a budget's public ledger operations at
`/remote/v1/budgets/{budget_id}`. Enable it with the budget's optional plugins and
set `SERVER_TOKEN` to a secret of 32 through 4,096 characters on the server machine.
Clients use that budget's stable ID and the matching token. Every request requires
HTTPS and bearer authentication. The host uses native TLS and rejects plaintext for
all routes, including localhost; it does not trust forwarded headers as proof of
HTTPS. Provision a certificate valid for the client's server origin and preserve
loopback restrictions on ordinary visual/private banking routes. See
[local TLS setup and trust](../../../../docs/usage.md#local-https).

The plugin declares `Interface(adapter=RemoteLedgerInterface())` with an explicit
live lifecycle. The host owns its routes, bearer comparison, request validation
and ledger dispatch. A worker receives only the protocol declaration, including
the configuration key's name; the token value and command service stay in the
host. It exposes accounts, categories, transactions, budget projections and portable
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

## Selection and upgrades

Use the host's local `POST /api/plugins/management/transition` with the complete
optional selection to enable, disable or replace this adapter while WHAB runs.
Poll `GET /api/plugins/runtime` for completion. The `/remote/v1` adapter supports
one route owner per budget; a second declarative interface or a mixture with a
legacy factory is rejected before binding configuration. `Interface(factory)`
remains available to legacy plugins with restart-required selection semantics.

Each admitted request keeps its original authorization and dispatch binding
through its complete response. Replacement waits for admitted work to drain,
preserves the ledger revision and retry receipts, then retires the old binding.
Failed preparation preserves the active binding. Disabled remote paths return
404 locally and retain ordinary local Host/origin restrictions; HTTPS remains
required even while a transition gates admission. Other budgets keep their own
selection and authorization scope.

Compatible code upgrades use the [approved immutable worker workflow](../../../../docs/plugin-workers.md).
Supply reviewed local wheels and the complete current-host dependency closure;
the runtime performs no downloads. The host probes the new declaration and its
private token configuration before activation. Saved references restore the exact
approved environment on reopen. A different host version requires a compatible
environment prepared for that host. Workers contain failures and remain trusted
Python code with the current user's operating-system access.

## Development checks

From `whab/app/`, use `.venv/bin/python -m pytest plugins/interface/server/tests` or `.venv/bin/python scripts/check.py --quick` during iteration, then `.venv/bin/python scripts/check.py` before submitting. Documentation-only edits can use `--docs`. Changes to budget targeting, authorization or command identity also need the [two-budget isolation checks](../../../../docs/architecture.md#budget-isolation-verification). See [plugin contribution rules](../../README.md#test-ownership) for the complete check contract.
