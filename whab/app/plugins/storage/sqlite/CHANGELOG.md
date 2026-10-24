# SQLite budget storage plugin changelog

## 1.11.0 — 2026-10-23

PR: [#87](https://github.com/Fillll/whab/pull/87)

- Atomically persist fenced replica data, complete encrypted provider/receipt state and source/namespace watermarks. Reject stale jobs and ordinary copy writes; bind explicit promotion before fresh-epoch activation.
- Preserve existing ledgers through additive replica metadata and require WHAB 2.30+. Verify transactional failures, process termination, promotion/reopen and public conformance at 100% statement and branch coverage.

## 1.10.0 — 2026-10-23

PR: [#84](https://github.com/Fillll/whab/pull/84)

- Persist local instance/epoch/generation authority, durable typed-operation receipts and role fencing atomically with financial changes. Adopt authority metadata transactionally and retain existing ledger semantics.
- Transfer both workspaces, receipt history/import identities and protected encrypted provider checkpoints through the public same-device contract. Declare exact portable payload ownership, omit runtime authority from sanitized exports and require WHAB 2.27+.

## 1.9.0 — 2026-10-18

PR: [#65](https://github.com/Fillll/whab/pull/65)

- Return receipt markers for one to 100 selected transactions using existing keys, without loading images or full transaction history. Match the full index's active-row and workspace semantics with no schema or format migration.

## 1.8.0 — 2026-10-18

PR: [#63](https://github.com/Fillll/whab/pull/63)

- Provide optional atomic review batches: validate every selected transaction with core rules, update only review flags, and commit the batch as one tracked mutation. Preserve all other ledger fields, receipts and rollback on any invalid target or write failure; no index or format migration is required.

## 1.7.0 — 2026-10-18

PR: [#62](https://github.com/Fillll/whab/pull/62)

- Provide an indexed, workspace-scoped active transaction lookup for receipt access. Exclude deleted rows while allowing pending transactions, preserve workspace errors, and retain the receipt operations' own transaction checks against concurrent deletion. No new index, storage migration or receipt format is required.

## 1.6.3 — 2026-10-18

PR: [#61](https://github.com/Fillll/whab/pull/61)

- Count unselected-account registers by selecting the currency's account IDs once, avoiding a repeated account lookup for each transaction. Keep explicit account selections on their existing query path, with unchanged snapshots, filters, counts and selected rows.

## 1.6.2 — 2026-10-18

PR: [#60](https://github.com/Fillll/whab/pull/60)

- Read complete financial/reference history for core budget calculations while loading full display text and review metadata only for the viewed months. Preserve coherent snapshots, exact results/errors, historical validation, and complete private-state/export/mutation APIs.

## 1.6.1 — 2026-10-18

PR: [#59](https://github.com/Fillll/whab/pull/59)

- Read portable export records directly from allowlisted SQL columns without constructing a second full domain ledger. Validate every record and preserve coherent transaction, metadata and receipt snapshots.
- Omit the three optional runtime indexes from fresh sanitized portable images, reducing rebuild, compression and saved-file size. Keep runtime indexes and exact portable-schema validation.
- Share the core's request-local history indexes across consecutive month projections; require WHAB 2.14.1 or newer.

## 1.6.0 — 2026-10-18

PR: [#58](https://github.com/Fillll/whab/pull/58)

- Count and page transaction selections in one snapshot, with literal Unicode search, stable ordering and additive indexes; materialize only the requested page.
- Read account totals/review counts and account history metadata without loading or exporting the ledger. Read consecutive budget months from one ledger snapshot.
- Save transaction category, memo and review edits through the existing core command in one targeted SQL update, preserving receipts, provider fields, account balances and rollback behavior.
- Avoid validating an already validated export twice while constructing its sanitized fresh database; preserve older portable schemas without the new optional indexes.

## 1.5.0 — 2026-10-18

PR: [#55](https://github.com/Fillll/whab/pull/55)

- Filter register transactions by multiple accounts and inclusive date bounds in SQL within one coherent snapshot. Preserve the existing month/account projection and portable database contracts.

## 1.4.0 — 2026-10-18

PR: [#52](https://github.com/Fillll/whab/pull/52)

- Read register metadata and selected account/month/currency transactions in one coherent snapshot, without loading envelope history or private provider state. Keep existing workspace, public-row and portable-image contracts unchanged.

## 1.3.0 — 2026-10-18

PR: [#51](https://github.com/Fillll/whab/pull/51)

- Persist bounded receipt images in an additive table, retain them in sanitized portable images and move them with matched pending transactions. Roll back uploads that would exceed the portable-image size bound.
- Preserve account names, public bank websites and explicit memo edits through synchronization and portable roundtrips. Keep all historical amounts bound to their original currency.

## 1.2.0 — 2026-10-17

PR: [#41](https://github.com/Fillll/whab/pull/41)

- Persist currency-scoped next-month funding and monthly rollover choices atomically in additive tables, and retain them through reopening, neutral snapshots and sanitized SQLite portable exports. Preserve older database and portable-image schemas.
- Supply the current month to core historical projections and funding validation. Reject replacement imports when a workspace already contains saved funding or rollover settings, and require WHAB 2.7 for core validation.

## 1.1.1 — 2026-10-16

PR: [#40](https://github.com/Fillll/whab/pull/40)

- Rename the independent distribution and Python package to `whab_plugin_sqlite` under the shared `whab_plugin_<name>` convention. Preserve the plugin ID and its existing saved data and settings.

## 1.1.0 — 2026-10-16

PR: [#31](https://github.com/Fillll/whab/pull/31)

- Persist core on-budget/off-budget scope and account subtypes, including retirement, investments, property, loans and restricted deposits.
- Upgrade existing ledgers additively, commit each migration with its backfill atomically and preserve saved account classifications through provider refreshes.
- Include classifications in portable exports and continue reading earlier SQLite budget images with inferred scope defaults.

## 1.0.0 — 2026-10-15

PR: [#30](https://github.com/Fillll/whab/pull/30)

- Introduce independent packaging and version ownership for the existing `sqlite` plugin, compatible with WHAB 2.x. Earlier changes shipped inside the application distribution and were not independent plugin releases.
- Preserve its plugin ID, Python package, entry point and existing behavior.
