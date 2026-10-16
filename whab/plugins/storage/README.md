# Storage plugins

This category owns persistence backends and their compatible migrations. Its independently packaged supported plugins are the default [SQLite](sqlite/README.md) ledger backend and [remote budget client](client/README.md). The client is a remote storage capability; its connection container leaves the financial ledger on the server.

Keep each plugin's implementation, assets, tests and documentation in its own folder. See [plugin contribution rules](../README.md) for naming, SDK contracts and checks.
