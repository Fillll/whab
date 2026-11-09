# Changelog

## 1.3.2 — 2026-11-09

PR: [#177](https://github.com/Fillll/whab/pull/177)

- Require Python 3.15.x only in package metadata and current guidance; retain the existing public behavior and data contracts.

## 1.3.1 — 2026-11-01

PR: [#142](https://github.com/Fillll/whab/pull/142)

- Package required public operation schemas, examples, access/effect/scope/error/retry documentation for offline CLI discovery.

## 1.3.0 — 2026-11-01

PR: [#141](https://github.com/Fillll/whab/pull/141)

- Remove required AI dependencies; expose manual labels/reports and deterministic reuse for future matching payments.

## 1.2.0 — 2026-11-01

PR: [#138](https://github.com/Fillll/whab/pull/138)

- Report total and rolling-12-month classified charges per plan, the reference/window dates and direct annual equivalents. Calculate annual estimates from actual charges to avoid monthly rounding drift; preserve account/currency isolation and historical-view cutoffs.

## 1.1.0 — 2026-10-31

PR: [#137](https://github.com/Fillll/whab/pull/137)

- Run explicit classification scans in the background with saved progress, an idempotent Start and an explicit Stop after the current batch. Resume unchecked payments after a stop or interruption while retaining completed labels and cached decisions.
- Keep progress responsive during provider calls, skip materially changed payments and prevent late results from writing to a retired budget. Reopened unfinished jobs require an explicit resume.

## 1.0.0 — 2026-10-31

PR: [#135](https://github.com/Fillll/whab/pull/135)

- Add independent AI subscription labels, positive/negative repeated-payment caching, persistent corrections and per-currency monthly estimates, actual charge history and cost breakdowns.
