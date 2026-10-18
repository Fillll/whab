# SQLite budget storage plugin changelog

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
