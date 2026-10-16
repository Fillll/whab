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
disabled. A remote budget file contains only `connection.json`: its version,
server origin and remote budget ID. Saving, opening and downloading that file
does not fetch the ledger. Reads and changes go to the live server; its data can
advance independently of the connection file. Explicit legacy snapshot exports
remain available through `export_budget()`.

Credentials normally stay on this machine. A machine token is used only with
the matching device-configured `CLIENT_URL` and `CLIENT_BUDGET_ID`; opening an
imported connection cannot redirect that token. The host may also supply
credentials explicitly unlocked from an optional password-encrypted budget
package bound to this exact origin and budget. `connection.json` never contains
a token, and the client adapter never changes a credential's target from file
contents. Client startup must disable bank ingestion plugins: bank connections
remain on the server.

A lost command response leaves its request ID, payload and original revision
pending. Retry the same change to confirm its original result. A different change
is blocked until that request is resolved. Conflicts require refreshing the
budget and reviewing the desired change. Pending requests are held in memory;
after restarting a client, inspect the saved budget before repeating any change
whose result was unknown. There is no automatic offline write queue or background
conflict resolution.
