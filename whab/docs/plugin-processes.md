# Independent plugin processes

From WHAB 2.54, the host discovers and executes every installed plugin in owned
child processes. It never imports installed plugin entry-point modules. This
includes restart-scoped interfaces, settings routes, budget binders, saved-state
upgraders, reporting, imports, categorization, artwork and visual rendering.
Explicitly injected descriptors/backends remain available for host test doubles.

The host retains the financial domain, budget identities, HTTPS listener,
authorization and local-access checks, operation admission, request revisions,
retry receipts, resource ownership and recovery decisions. Host-owned proxies and
declarative transport adapters contain no concrete plugin implementation code.
The CLI remains an independently launched HTTPS client.

## Local transport and public capabilities

Installed workers use their host's Python environment and private stdin/stdout
pipes. No network listener is required for plugin IPC. Approved immutable code
upgrades retain their selected versioned environments and existing protocol/host
compatibility fences; separate processes alone do not create separate dependency
environments or make every lifecycle live-replaceable.

The version-1 `WHPR` protocol bounds metadata to 32 MiB, binary data to 256 MiB,
nesting to 40 levels, value nodes to two million and concurrent calls to 128.
Binary ranges are contiguous and individually hash checked. Messages contain
strict data, an explicit allowlist of public SDK records and opaque scoped
capability IDs. Receivers never import a type named in a message, deserialize
Python code or use pickle. Callables and service objects remain in their owning
process; IDs address only the advertised public operations. Private attributes
and the ledger backend are unavailable through the interface service.

The bridge supports the SDK's synchronous hooks, cooperative engine iterators,
provider methods, namespaced file/state callbacks and existing public local
feature collaboration contracts. Callbacks in a request chain return to its
waiting host thread, preserving reentrant financial lock ownership. Independent
background callers use bounded thread-affine dispatch. Local service and
namespace callbacks acquire budget admission and expire with their process.
Feature registration is restricted to the owning plugin; cross-plugin feature
access requires an explicit installed dependency.

Local HTTP interfaces build and run their routers in the child. The host proxies
only declared paths through its existing guards. It sends bounded request data
and content-negotiation headers without host application objects or cookies.
Local settings messages omit bearer credentials; legacy remote interface factories
receive their required Authorization header. Declarative remote adapters keep
authorization in the host. Local settings paths remain under their owning plugin's
namespace. Child OpenAPI data is included in operation discovery with namespaced
component names. Visual workers render from captured version-coherent resource
bytes, including explicitly declared additional workspace pages.

## Financial outcomes and lifecycle

Current native SQLite, JSON and compatible storage plugins retain the separate
[storage worker protocol](storage-workers.md), durable intent journals, native
receipts, writer ownership and explicit unknown-write recovery. Provider and
categorization workers retain their specialized [worker adapters](plugin-workers.md).
An SDK-only legacy storage backend can use the public-method bridge: an uncertain
write fences that proxy and requires reopening/reconciliation; no durable native
receipt is invented for such a backend. No failed financial operation is retried
automatically.

Reopening an already restored native budget first validates its saved payload in
an exact-code readonly worker. A successful validation changes no native generation
or receipts, preserving separate financial and plugin-namespace watermarks. A
payload needing an actual restore still uses the durable native command path.

Every discovery child is owned before startup can fail. Active process resources
follow the budget generation and close after their dependent services. Shutdown,
retirement and failed startup stop/reap only owned process groups. Failed cleanup
remains observable. `/api/plugins/runtime` includes installed `processes` with
plugin ID, PID and health, alongside the existing approved `workers` list.
Dedicated native/provider/session workers may coexist with a plugin's discovery
and settings worker; each retains its own cleanup and recovery owner.

Stdout is reserved before importing plugin code, so plugin prints and native
stdout writes cannot corrupt protocol frames or expose diagnostics. Hook errors
use fixed messages; public authored domain rejections retain their safe messages.
Processes provide failure containment and remain trusted executable code with
the user's filesystem/network access. They are not an OS security sandbox.

## Explicit process configuration

The ordinary safe child environment remains the default. A packaged `plugin.json`
may additionally declare `process_config`, a bounded list of environment-variable
names its public implementation requires. The loader includes declarations from
installed named dependencies so their public APIs use the same device-local
configuration. It never copies all caller settings, and excludes reserved host,
Python/pip configuration, the runtime encryption key and the configured budget
password alias. Runtime/provider credentials continue through their scoped SDK
contexts. Treat declarations as part of reviewing trusted executable plugins.

AI credentials 1.1 declares `AI_CREDENTIALS_DIR` and the legacy
`LLM_CATEGORIZATION_CREDENTIALS_DIR`. Existing credential files, selected paths
and budget-local opaque references remain in place. No credential files are
copied into source, portable budgets or plugin messages.
