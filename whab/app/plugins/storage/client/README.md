# Remote budget client

Distributed independently as `whab_plugin_client`. Its version and dependency compatibility range are defined in [pyproject.toml](pyproject.toml); its dated history belongs in [CHANGELOG.md](CHANGELOG.md). Material changes confined to this plugin update its version and changelog, without an app version or app changelog change.

For the original workspace or initial CLI creation, select `client` as the storage plugin to use a budget served by the `server`
interface plugin. Configure these values on the client machine:

```dotenv
WHAB_STORAGE=client
# Bank ingestion stays on the server. Saved selections must exclude bank plugins too.
WHAB_PLUGINS=
CLIENT_URL=https://your-budget-server.example
CLIENT_BUDGET_ID=your-budget-id
CLIENT_TOKEN=your-private-token-with-at-least-32-characters
```

An existing portable budget chooses storage through its manifest rather than `WHAB_STORAGE`; Settings' new-budget action creates SQLite budgets. The installed client is not automatically enabled by installing the supported set.

`CLIENT_URL` is an HTTPS origin without credentials, a path, query or fragment.
TLS certificates are verified, redirects are refused and environment proxies are
disabled. The client's contribution is `plugins/client/connection.json`: its payload version,
server origin and remote budget ID. The complete `.whab` container also has a required
manifest and namespaced plugin settings/state or other retained payloads. Saving, opening and downloading that file
does not fetch the ledger. Reads and changes go to the live server; its data can
advance independently of the connection file. Explicit legacy snapshot exports
remain available through `export_budget()`.

Account creation and `update_account()` forward core on-budget/off-budget scope,
account types and optional subtypes to WHAB 2.1 or newer. Classification changes
use the same revision checks and retry identity as other ledger commands; the
server validates their financial rules and preserves saved classification during
bank refreshes.

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

WHAB 2.7 and client plugin 1.2 forward `assign_next_month(workspace, source_month, currency, assigned)` through the same revision and idempotency boundary as category assignments. The server owns persistence, release and funding validation. Updated server projections advertise `nextMonthFundingEditable: true`; older servers leave the row read-only. Unsupported or rejected funding commands return a clear error without making up a local allocation. Connection files continue to store only the remote reference; legacy neutral snapshots preserve the server's `futureFunding` records.

The adapter also forwards `set_rollover_mode(workspace, source_month, currency, mode)` through the public command protocol. Server projections advertise `rolloverModeEditable` and per-currency modes; the server owns carryover computation and historical date context. The request identity, conflict handling and unknown-result retry protections are shared with funding changes. Older servers reject unsupported writes clearly and leave the control read-only. Connection containers remain references; neutral snapshots retain `rolloverModes` from the server.
