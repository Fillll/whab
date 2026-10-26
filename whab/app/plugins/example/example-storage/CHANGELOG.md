# Changelog

## 1.0.1 — 2026-10-25

PR: [#113](https://github.com/Fillll/whab/pull/113)

- Ship a packaged JSON manifest declaring the primary category and named plugin dependencies.

## Unreleased

## 1.0.0 — 2026-10-23

PR: [#107](https://github.com/Fillll/whab/pull/107)

- Add an independent persistent POSIX SQLite snapshot adapter using the public WHAB 2.41 storage model, with complete ledger/provider behavior, receipts, neutral saved payloads, native authority, durable commands and compatible storage-worker readiness.
- Preserve acknowledged data and import identities across reopen, refuse unsupported or corrupted formats without reset, fence observed ownership/schema changes, and retain uncertain commit outcomes for explicit receipt-based recovery. Same-device transfer and replicas are not advertised.
