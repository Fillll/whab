# Offline fictional bank

This independently packaged teaching plugin requires Python 3.14+ and WHAB 2.38+ within major 2. Enable `__PLUGIN_ID__` explicitly in a disposable Sandbox home. It advertises the typed live bank lifecycle. Production is unconfigured and refuses connection, exchange, synchronization and removal requests.

`FictionalProvider` receives only public `BankProviderContext` data and delegates to an injectable asynchronous transport. The default `OfflineTransport` has no HTTP client, URLs or external service. Its only accepted public token is `fictional-public-token`; all returned tokens and account records are fictional. One first synchronization adds a pending USD 7.25 grocery transaction. The next synchronization posts it using `pendingTransactionId`; subsequent calls return no changes at the retained cursor. Both budgets deliberately receive the same raw identifiers, so the host remains responsible for isolation and stable scoping.

The host owns continuation identity, encrypted credential/response capture, native command receipts, atomic financial validation and cursor commits. The provider never receives a backend, encryption key, callback or router. Link update mode keeps the existing item; it does not exchange another public token. Webhooks always fail closed with 401. There is no provider UI or external bank connection. A trusted Python worker provides failure containment; it is not a network sandbox.

Build and run project tests from this folder or a copy outside the checkout. The complete test extra requires toolkit 0.8+ and the reviewed SQLite and JSON distributions in the approved environment:

```sh
python -m pip wheel --no-deps . --wheel-dir /tmp/fictional-bank-wheels
python -m pip install --no-index --find-links /approved/local/wheels '.[test]'
python -m pytest tests --cov=__MODULE__ --cov-branch --cov-fail-under=100
```

The project suite includes actual native HTTPS behavioral tests for both local backends. With this plugin and toolkit 0.8+ installed, the additive public helper can also run directly:

```python
from whab_plugin_devkit.bank_conformance import BankRuntime, BankScenario, exercise_bank_runtime

scenario = BankScenario("__PLUGIN_ID__", "fictional-item", "Fictional checking",
                        "Fictional grocer", 725, "fictional-public-token", "fictional-link-token")
for storage in ("sqlite", "json"):
    with BankRuntime(storage, ("__PLUGIN_ID__",)) as runtime:
        print(exercise_bank_runtime(runtime, scenario))
```

The helper creates a fresh private home and host key, explicitly trusts that home's CA, uses the installed WHAB CLI and removes its fictional home on exit. It covers begin retry, cancellation, completion, pending-to-posted annotations, demo isolation, saved connection/cursor reopening, update and idempotent disconnect through public HTTPS. `bank_applicability(host_version)` explicitly reports this profile as not applicable below host 2.38; `BankRuntime` refuses to launch it there. It does not certify a real provider's authentication, webhook signatures, network failure behavior or compatible code upgrades. The toolkit's explicit `native-bank-v1` sampler runs ten distinct native configurations with `whab-plugin check --plugin __PLUGIN_ID__ --runtime --bank-fixture fictional-v1 --combinations 10 --wheelhouse /approved/local/wheels --output /tmp/__PLUGIN_ID__-check`. Its five flow variants across both local backends retain the approved exact artifact, public lifecycle, copy/promotion/migration and reopened cursor checks. Supply only the reviewed offline fixture; this is not a test recipe for a real bank connection.
