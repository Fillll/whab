# Teaching plugins

These independently packaged examples use only the public `whab.plugins` SDK. They live beside the maintained product plugins, with their own source, tests, versions and changelogs.

| Plugin | Teaches |
| --- | --- |
| [`example`](example/README.md) | Reporting and CSV export from an immutable snapshot |
| [`example-branding`](example-branding/README.md) | Offline fictional institution lookup and logo bytes |
| [`example-file-import`](example-file-import/README.md) | Bounded file parsing with host-owned import validation |
| [`example-categorization`](example-categorization/README.md) | Training, per-budget model persistence and currency-scoped suggestions |
| [`example-api`](example-api/README.md) | A WHAB 2.34+ declarative API interface with host-owned HTTPS, bearer auth and financial dispatch |
| [`example-ui`](example-ui/README.md) | A WHAB 2.36+ participating read-only visual interface with coherent templates/assets and browser handover |

Run `python scripts/install.py --dev --example` from `whab/app/` to install all six. Activate only the examples needed, with `WHAB_PLUGINS=example-branding` or another explicit optional selection; select the visual example separately with `WHAB_UI=example-ui`. Installation alone does not enable a plugin. These teaching registrations are hidden from ordinary Settings, including Options, by the current host. Hosts predating a newly introduced example’s registration metadata may list it as an extension; install and select teaching packages explicitly. They are excluded from the supported `bundled` extra.

The three small capability examples advertise restart lifecycle support. The reporting example separately demonstrates the host's live report lifecycle; the API and UI examples declare their supported live data-only adapters. These examples use fictional records and make no bank connections. Installed Python plugins remain trusted executable code.

Each example can be built and tested outside this repository with only the host, toolkit and required test packages; no maintained provider implementation is imported. The [plugin developer kit](../../devtools/plugin-devkit/README.md) also generates complete standalone projects for these six families. Extend each example's capability-specific tests before using it as a production integration.

See the [activation instructions](../../../docs/plugins.md#install-the-working-example) and [plugin contribution rules](../README.md).
