# Changelog

## 1.0.4 — 2026-11-13

PR: [#199](https://github.com/Fillll/whab/pull/199)

- Advertise optional classified-review support backed by the public shared financial helpers; keep native persistence contracts and historical host compatibility.

## 1.0.3 — 2026-11-09

PR: [#177](https://github.com/Fillll/whab/pull/177)

- Require Python 3.15.x only in package metadata and current guidance; retain the existing public behavior and data contracts.

## 1.0.2 — 2026-11-01

PR: [#142](https://github.com/Fillll/whab/pull/142)

- Package required public operation schemas, examples, access/effect/scope/error/retry documentation for offline CLI discovery.

## 1.0.1 — 2026-10-25

PR: [#113](https://github.com/Fillll/whab/pull/113)

- Ship a packaged JSON manifest declaring the primary category and named plugin dependencies.

## Unreleased

## 1.0.0 — 2026-10-23

PR: [#107](https://github.com/Fillll/whab/pull/107)

- Add an independent persistent POSIX SQLite snapshot adapter using the public WHAB 2.41 storage model, with complete ledger/provider behavior, receipts, neutral saved payloads, native authority, durable commands and compatible storage-worker readiness.
- Preserve acknowledged data and import identities across reopen, refuse unsupported or corrupted formats without reset, fence observed ownership/schema changes, and retain uncertain commit outcomes for explicit receipt-based recovery. Same-device transfer and replicas are not advertised.
