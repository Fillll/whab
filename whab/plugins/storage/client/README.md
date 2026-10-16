# Remote budget client

Select `client` as the storage plugin to use a budget served by the `server`
interface plugin. Configure these values on the client machine:

```dotenv
WHAB_STORAGE=client
CLIENT_URL=https://your-budget-server.example
CLIENT_BUDGET_ID=your-budget-id
CLIENT_TOKEN=your-private-token-with-at-least-32-characters
```

`CLIENT_URL` is an HTTPS origin without credentials, a path, query or fragment.
TLS certificates are verified, redirects are refused and environment proxies are
disabled. The token stays in machine configuration and is sent only to the
configured origin. Client startup must disable bank ingestion plugins: banks
remain connected on the server, while this plugin exposes public ledger data,
manual commands and credential-free portable exports.

A lost command response leaves its request ID, payload and original revision
pending. Retry the same change to confirm its original result. A different change
is blocked until that request is resolved. Conflicts require refreshing the
budget and reviewing the desired change. Pending requests are held in memory;
after restarting a client, inspect the saved budget before repeating any change
whose result was unknown. There is no automatic offline write queue or background
conflict resolution.
