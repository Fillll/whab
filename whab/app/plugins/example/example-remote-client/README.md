# example-remote-client

This independently packaged fictional remote client requires Python 3.14+ and
WHAB 2.40+ within major 2. Its public `RemoteLedgerClient` declaration gives a
worker only protocol and host setting names. The host owns HTTPS, bearer values,
remote budget identity, financial validation, transport, revisions and durable
unknown-write intent. Installed Python remains trusted code; a worker is not an
OS sandbox.

The descriptor ABI first exists in WHAB 2.39. This example's genuine 2.40 floor
also requires the provisioning allowlist: the offline installer and artifact
verification subprocesses do not inherit custom endpoint or credential settings.

Install the wheel explicitly and select `WHAB_STORAGE=example-remote-client`.
Privately configure `EXAMPLE_REMOTE_CLIENT_URL` with the approved HTTPS origin,
`EXAMPLE_REMOTE_CLIENT_TOKEN` with its random 32–4096-character bearer token, and
`EXAMPLE_REMOTE_CLIENT_BUDGET_ID` with the addressed budget. The URL accepts one
HTTPS origin, with no path, query or embedded credentials. Verify that server's
CA explicitly. Do not put these values in source, wheels, worker frames, plugin
state, replay manifests or reports. Keep the runtime encryption key unchanged.
Bank ingestion belongs on the remote authority and is refused on the client.

Pausing drains admitted work and prevents new remote financial admission while
keeping local management controls available. Same-binding code replacement must
preserve the exact authority, credential binding and pending original request.
Unknown writes remain blocked across restart. Reads do not prove a write's
outcome; no receipt lookup or automatic reconciliation is advertised. Explicit
Finish may submit only the exact authorized original request, identity and CAS
revision; a conflict retains unknown status. Never discard an unknown outcome,
create a replacement operation or redirect it to another server.

Build and install outside the WHAB checkout using reviewed local host, toolkit,
SQLite and API server wheels. The owning behavioral test creates two disposable
fictional native HTTPS listeners, explicitly verifies their runtime CAs, writes a
manual account through this client, exercises pause/enable and reopens the exact
authority. Both processes and temporary homes are removed. No provider connection,
household runtime, OS trust change or community plugin is involved. Backend
migration and copies are not applicable to this remote authority.

The client test uses the reviewed toolkit `remote_launcher` overlay: it supplies
an explicitly CA-verified transport through the public host-owned `RemoteStore`
constructor, then runs the official WHAB CLI. The server uses the ordinary CLI.
This verifies the public client/services with that transport; it does not claim
stock CLI support for private-CA configuration. Ambient TLS/proxy settings stay
disabled, and certificate or hostname failures remain errors.
