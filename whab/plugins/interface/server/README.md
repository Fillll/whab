# Remote budget server

The `server` interface plugin exposes a budget's public ledger operations at
`/remote/v1/budgets/{budget_id}`. Enable it with the budget's optional plugins and
set `SERVER_TOKEN` to a secret of at least 32 characters on the server machine.
Clients use that budget's stable ID and the matching token. Every request requires
HTTPS and bearer authentication. Configure TLS termination before remote access.

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

Tokens and HTTPS provide access to one configured budget; multi-user roles,
public account registration and automatic certificate provisioning are outside
this plugin's contract. Keep the token in machine configuration, not the portable
budget bundle.
