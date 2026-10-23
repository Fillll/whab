# Plugin startup recovery

Start the minimal recovery listener when an optional plugin, a saved selection or
a report-worker reference prevents the full application from starting:

```sh
python -m whab --recover-plugins
python -m whab --recover-plugins --budget /path/to/Fictional.whab
```

Use the same runtime home, environment, encryption key and TLS configuration as
the affected installation. The listener binds `127.0.0.1` and requires the usual
trusted HTTPS certificate. Host, Origin, request-size and content-type guards
remain active; forwarded headers cannot bypass them. `--create`, `--copy` and
`--reload` cannot be combined with recovery mode. Recovery never installs packages,
changes financial authority or selects another storage backend.

Open the listener's root page. Initially it reads installed entry-point/package
metadata and validated saved configuration. It does not load plugin entry-point
targets, open a storage backend, start workers, fetch artwork or connect to a bank.
Disabled or unavailable names remain visible. Storage and the selected interface
remain required until an explicit recovery decision changes the visual selection.
Repair unavailable storage packages separately.
The catalog cannot establish a disabled plugin's runtime capability compatibility.

Select optional plugins and choose **Disable checked plugins** to remove them from
the next startup selection. The operation cannot add a plugin or remove storage or
the selected interface. Portable budget namespaces, settings, state versions and
opaque files remain intact, including unavailable and disabled plugins. Global
runtime state, financial records, provider connections and encryption keys remain
in place.

An invalid global `plugins.json` requires **Archive and clear invalid selection**.
That explicit action preserves the exact bounded original bytes in the runtime
home's `plugin-recovery-archive/`, flushes the archive, then writes an empty valid
optional selection. It does not guess repairs to an invalid budget manifest.
Unreadable files, oversized records and unsafe symbolic-link paths require local
file repair after preserving the affected runtime.

**Archive worker reference** preserves the exact device-local active reference in
`plugin-workers/recovery-archive/` before removing that reference. Prepared
environment directories and package wheels are retained. This removes the saved
worker-version decision; a later successful startup uses the reviewed descriptor
installed in the host environment. It does not choose or download a replacement.
Worker reference inspection does not execute an environment interpreter or claim
that an environment is healthy.

The separate saved visual decision appears with its selected plugin, resource
generation and approved worker identity. **Select headless startup** preserves its
exact prior decision in `visual-ui/history/` and explicitly selects no visual
interface for the next full startup. Retained worker references, resources and
opaque plugin state survive. This action never executes old UI code, opens a
backend or chooses another interface. It requires the current recovery revision
and refuses a running visual owner, invalid scope or unreadable journal. To
replace a stopped visual worker with approved current-host wheels, use the
[stopped visual repair helpers](plugin-workers.md#visual-interface-workers).

Choose **Retry full startup** after making the intended repairs. WHAB explicitly
constructs the full application and enters its complete startup lifespan. Only a
successful candidate receives subsequent requests on the same HTTPS listener.
A failed candidate leaves the recovery page available with a fixed, plugin-
attributed diagnostic; private exception text is omitted. Closing the scheduling
HTTP request does not abandon an already-started candidate. Listener shutdown
waits for its owned startup task and closes an activated candidate. Arbitrary
trusted plugin code can still hang or produce its own logs; this is recovery
coordination rather than a security sandbox.

## Metadata-only budget selection journal

An unopened portable file must retain its original financial payload until the
selected backend actually restores it. A recovery selection save with no open
backend uses an explicit version-2 `source.json` record. The record atomically
binds the original container content fingerprint to the complete validated
manifest, `selectionOnly: true` and a boolean `restoreRequired`. A new unopened
container requires restoration; an existing durable native recovery record can
establish that restoration was already completed. Prior pending fingerprints are
retained when applicable.

The embedded manifest avoids a two-file partial selection decision. A version-2
record never makes the presence of cached metadata stand in for a restored
ledger. An interrupted first restore retains the original source fingerprint and
the repaired selection. The next open can retry the same nonempty payload or
recognize an already-completed restore. A normal successful checkpoint replaces
the temporary selection journal with the existing complete recovery record.
Exact older unversioned records remain readable. Unknown versions or contradictory
fields fail validation without rewriting the original budget or journal.

## Local recovery API

`GET /api/recovery` returns the static catalog, configured optional IDs, safe worker
reference status, a revision token, startup status and fixed diagnostics. It
excludes settings/state payloads, financial records, credentials, module targets,
filesystem paths and raw exceptions. All mutations use JSON and the latest exact
revision token:

| Operation | Body |
| --- | --- |
| `POST /api/recovery/disable` | `{"revision": "…", "plugins": ["fictional-report"]}` |
| `POST /api/recovery/selection/clear-invalid` | `{"revision": "…"}` |
| `POST /api/recovery/workers/fictional-report/reset` | `{"revision": "…"}` |
| `POST /api/recovery/visual/disable` | `{"revision": "…"}` |
| `POST /api/recovery/retry` | `{"revision": "…"}` |

Stale revisions reject with HTTP 409. Recovery decisions use a local POSIX file
lock and flushed metadata/archive writes. Once the full candidate activates,
queued recovery controls cannot change its startup selection; use the full
application's management controls thereafter. The minimal API is no longer routed
after activation.
