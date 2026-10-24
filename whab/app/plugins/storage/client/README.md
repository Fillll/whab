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

Client 1.6 requires WHAB 2.39 or newer. The bundled descriptor uses the data-only `RemoteLedgerClient` adapter and
`PluginContract(authority="remote", lifecycle="live")`. The host owns HTTPS,
credentials, remote requests, financial validation and the retained connection.
An isolated descriptor worker returns only protocol/configuration-key metadata;
it receives no endpoint, bearer token, remote budget ID or financial records.
Independently named remote backends receive the same restriction on local bank
ingestion and model training. Legacy callable factories remain restart-required;
`create_storage`, `RemoteStore` and `connection_configuration` remain available
as compatibility exports.

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

The host provides these local management controls:

- `GET /api/storage/remote/status` reports the exact selected descriptor/version,
  enabled state, decision revision and unresolved remote outcome.
- `POST /api/storage/remote/selection` takes `pluginId`, `enabled` and
  `expectedRevision`. Pausing drains admitted work, retires its descriptor worker
  and prevents new ledger requests. Local management remains available. Enabling
  starts the retained approved version; it does not reconnect a bank.
- Compatible approved wheel upgrades use the existing plugin-management upgrade
  action. Preparation checks the same normalized HTTPS origin, remote budget and
  credential binding before publishing one durable decision. Failed preparation
  preserves the active transport, local revision/retry state and original intent.
  Code upgrades cannot change authority or rotate credentials implicitly.
- `POST /api/storage/remote/recover` with `authority` set to `active` or `candidate`
  validates an uncertain local decision. It sends no financial command.

A remote command first records bounded private intent metadata durably: its
request ID, canonical command fingerprint, original server revision and scoped
binding hash. Credentials and the financial body are excluded. A lost result
blocks new financial changes after code replacement **and after client restart**;
reads remain available while enabled. Reads, conflicts and restarts never clear
that marker. The affected budget's status instructs the operator to preserve the
original change; do not reconnect, discard it, refresh its revision or submit a
new request identity.

`POST /api/storage/remote/finish` accepts the original `operation`, `workspace`
and `payload`. The host validates them with its ordinary financial validation and
requires their canonical form to match the retained fingerprint exactly. It may
explicitly submit only that original authorized request with its original ID and
CAS revision. This can finish an originally unexecuted request under an unchanged
revision; it is not a receipt lookup or automatic reconciliation. A correlated
successful response durably acknowledges completion. Repeating an acknowledged
Finish returns completion status, without inventing an original financial result.
An already captured response with a failed local acknowledgment is flushed
without another HTTP command. A `409`, including after server restart, retains
the unknown outcome and requires independent evidence at the authority. There is
no discard-unknown action, public receipt-proof endpoint, automatic offline write
queue or background conflict resolution.

Private decisions and outcome metadata remain under the runtime budget's
`remote-client/` directory, outside portable connection containers. Preserve that
runtime home and encryption key. A saved descriptor environment from another host
SDK is refused before executing it; the stopped host helper
`replace_saved_remote_worker` can prepare an explicitly approved current-host,
same-binding descriptor using a decision CAS. Exact repair retries acknowledge
their original receipt without executing old code. Retained immutable environments
are preserved. Worker processes contain failures and are trusted executable code;
they are not a security sandbox.

WHAB 2.7 and client plugin 1.2 forward `assign_next_month(workspace, source_month, currency, assigned)` through the same revision and idempotency boundary as category assignments. The server owns persistence and funding validation. Core 2.9 projections advertise `monthlyFundingFlow: true` alongside `nextMonthFundingEditable: true`, exposing independent source-month set-asides and assignable carry-in. Interfaces leave older funding-floor servers read-only until upgraded. Unsupported or rejected commands return a clear error without making up a local allocation. Connection files continue to store only the remote reference; neutral snapshots preserve the server's target-month `futureFunding` records.

The adapter also forwards `set_rollover_mode(workspace, source_month, currency, mode)` through the public command protocol. Server projections advertise `rolloverModeEditable` and per-currency modes; the server owns carryover computation and historical date context. The request identity, conflict handling and unknown-result retry protections are shared with funding changes. Older servers reject unsupported writes clearly and leave the control read-only. Connection containers remain references; neutral snapshots retain `rolloverModes` from the server.

Transaction memo edits forward through the existing revision and idempotency boundary. Omitted notes remain unchanged, empty notes clear the memo, and unsupported remote commands fail clearly. Account metadata edits use the same command path. The adapter does not advertise `ReceiptStorage`; attachment controls remain unavailable rather than saving an unrelated local copy.

Category suggestions and model settings are forwarded through public ledger operations to a WHAB 2.19+ budget host. Training and inference execute only at that authority. Reviewing a suggestion forwards its displayed identity together with the selected category and review flag.

## Development checks

From `whab/app/`, use `.venv/bin/python -m pytest plugins/storage/client/tests` or `.venv/bin/python scripts/check.py --quick` during iteration, then `.venv/bin/python scripts/check.py` before submitting. Documentation-only edits can use `--docs`. Follow the [two-budget isolation recipe](../../../../docs/architecture.md#budget-isolation-verification) for changes to remote targets, contexts, retry state or plugin files. See [plugin contribution rules](../../README.md#test-ownership) for the complete check contract.
