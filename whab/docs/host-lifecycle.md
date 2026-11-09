# CLI-first host lifecycle

## Accepted design

Financial rules remain in the domain. The shared host owns process coordination,
authenticated local HTTPS, admission, plugin ownership and scheduled-job execution.
The CLI owns command parsing and connection profiles. Plaid owns provider transport
and declares its hourly synchronization policy. Platform scheduling starts the
shared job runner; it does not implement banking or access budget files directly.

New runtime homes select SQLite and no visual or bank plugins. Existing runtime
homes retain their saved selections and legacy defaults. Enabling a visual
interface keeps the host running independently of open browser tabs. Explicit `python -m whab` service operation also keeps it running. Otherwise CLI requests and scheduled jobs
acquire temporary host leases; the host exits after its clients and work finish.

Idle accounting begins when the server becomes ready, excluding worker and ASGI
lifespan startup. A fresh transient host allows up to the CLI's 90-second startup
window to acquire its first authenticated lease; this also bounds an abandoned
startup. After accepting a client, the usual three-second idle window applies
when no clients or persistent interfaces remain. A slow startup or first TLS
connection must not preemptively mark the host stopping before lease admission.

A private runtime-home rendezvous file publishes the local endpoint, verified CA
and an ephemeral credential. A process lock serializes host startup, and existing
storage authority locks remain the final writer fence. A CLI reuses a live host;
it never starts a second writer as a fallback for a failed or uncertain command.
Local control is authenticated before addressed budget activation. Remote profiles
continue to use the explicitly enabled API interface plugin and its credentials.

Optional workers are acquired when their capabilities are used. Resources with
live state remain owned until their operation/session finishes; storage stays open
for the host's lifetime. Worker transport remains authenticated loopback HTTPS.
No financial mutation is automatically replayed after a transport failure.

Hourly bank jobs share the same public host operation as manual synchronization.
They serialize per connection, retain atomic transaction/cursor saves, record
success and sanitized errors, and catch up once after missed intervals. An OS
schedule can start a temporary host without a visual interface. A running host
also executes due work. Provider webhooks require a reachable persistent receiver
and are optional alongside polling.

Periodic attempts use the next local calendar boundary, matching the OS hourly
tick. An initial/manual sync at 10:17 is followed by the 11:00 check, then 12:00;
it cannot postpone the 11:00 tick until 12:00. Older relative due records are
projected forward to that boundary without changing native financial state.

CLI help and packaged operation documentation describe local profiles, plugin
management, synchronization and scheduling. Runtime configuration, credentials,
job state and platform task files remain outside Git.
