# SQLite budget storage plugin changelog

## 1.1.0 — 2026-10-16

- Persist core on-budget/off-budget scope and account subtypes, including retirement, investments, property, loans and restricted deposits.
- Upgrade existing ledgers additively, commit each migration with its backfill atomically and preserve saved account classifications through provider refreshes.
- Include classifications in portable exports and continue reading earlier SQLite budget images with inferred scope defaults.

## 1.0.0 — 2026-10-15

- Introduce independent packaging and version ownership for the existing `sqlite` plugin, compatible with WHAB 2.x. Earlier changes shipped inside the application distribution and were not independent plugin releases.
- Preserve its plugin ID, Python package, entry point and existing behavior.
