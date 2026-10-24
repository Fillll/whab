# Storage plugins

This category owns persistence backends and their compatible migrations. Supported local backends are the default [SQLite](sqlite/README.md) ledger and independent [JSON](json/README.md) single-file ledger. The [remote budget client](client/README.md) supplies remote storage; its connection container leaves the financial ledger on the server. Select each budget's backend explicitly; changing a selection does not migrate data.

Keep each plugin's implementation, assets, tests and documentation in its own folder. See [plugin contribution rules](../README.md) for naming, SDK contracts and checks.
