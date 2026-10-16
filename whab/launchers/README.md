# WHAB launchers

Platform launchers install and manage the local [WHAB application](../app/README.md), open its browser interface, and provide start, stop, restart, update and diagnostic controls. Budgeting rules, data access and plugin behavior stay in the application.

| Platform | Implementation |
| --- | --- |
| [macOS](macos/README.md) | Native Swift companion with a bundled Python lifecycle manager |

Each platform companion owns its source, build instructions, tests and version history in its platform directory. Future Windows and Linux implementations can share the same application CLI and HTTPS health contract.

Application installations, private runtime homes and backups stay separate from repository source and native build outputs. Updates prepare a separate installation and retain existing data, keys, certificate material and plugin selections. Certificate trust remains an explicit user action. Existing development checkouts can [retain their previous runtime home](../README.md#existing-development-checkouts) when adopting the product layout.

See [product ownership](../AGENTS.md), [runtime and backup guidance](../docs/usage.md) and the [application architecture](../docs/architecture.md).
