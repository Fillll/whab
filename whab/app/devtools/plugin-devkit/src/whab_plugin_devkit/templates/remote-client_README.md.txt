# __PLUGIN_ID__

This fictional declaration requires Python 3.14+ and WHAB 2.40+ within major 2.
The public RemoteLedgerClient ABI begins in 2.39; this project's actual 2.40 floor
also requires custom configuration to be excluded from offline provisioning.
The host owns HTTPS, bearer authorization, remote budget identity, financial
validation, revisions and durable unresolved intent. Workers receive only public
protocol/key names. Trusted Python workers are not an operating-system sandbox.

Privately configure __URL_SETTING__, __TOKEN_SETTING__ and __BUDGET_SETTING__ on
the host, then select WHAB_STORAGE=__PLUGIN_ID__. Never put their values in source,
wheels, worker messages, replay manifests or reports. Bank ingestion belongs on
the remote authority. Local migration/copies are not applicable to this client.

Run owning tests outside the checkout with reviewed host/toolkit/SQLite/server
wheels. They create two fictional native HTTPS listeners and use explicit runtime
CA verification. The reviewed toolkit launcher supplies the public RemoteStore
transport, then invokes the official CLI; this does not establish stock CLI
private-CA configuration. No provider or household data is used. Tests preserve
an acknowledged lost result as unknown across client restart, then explicitly
Finish only its exact original request against the same live server. This is not
receipt lookup, automatic reconciliation or durable retry proof across a server
restart. Failed/unknown completion must never create another operation or clear
the retained intent.

```sh
python -m pytest
whab-plugin check --plugin __PLUGIN_ID__ --runtime --combinations 10 --seed 2026 \
  --wheelhouse APPROVED_WHEELHOUSE --output conformance
whab-plugin replay conformance/configuration-00.json --wheelhouse APPROVED_WHEELHOUSE
```

The versioned remote profile records exact approved artifacts, distinct peer
sets/order, same-binding selection sequences, controlled public fault points and
cleanup. A single supplied candidate wheel makes no distinct-version upgrade
claim. All dependency resolution occurs separately from runtime capabilities.
