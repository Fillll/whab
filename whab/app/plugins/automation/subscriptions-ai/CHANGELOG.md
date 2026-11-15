# Changelog

## 1.0.3 — 2026-11-14

PR: [#204](https://github.com/Fillll/whab/pull/204)

- Carry explicit category kinds into background scans so Opening Balance entries cannot reach AI requests or reuse subscription labels. Require WHAB 2.79+ and Subscriptions 1.4+.

## 1.0.2 — 2026-11-09

PR: [#177](https://github.com/Fillll/whab/pull/177)

- Require Python 3.15.x only in package metadata and current guidance; retain the existing public behavior and data contracts.

## 1.0.1 — 2026-11-01

PR: [#142](https://github.com/Fillll/whab/pull/142)

- Package required public operation schemas, examples, access/effect/scope/error/retry documentation for offline CLI discovery.

## 1.0.0 — 2026-11-01

PR: [#141](https://github.com/Fillll/whab/pull/141)

- Extract optional subscription inference and background scans, preserving the existing saved namespace through a public integration API.
