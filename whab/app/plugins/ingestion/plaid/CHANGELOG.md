# Plaid bank ingestion plugin changelog

## 1.1.1 — 2026-10-16

PR: [#36](https://github.com/Fillll/whab/pull/36)

- Keep browser connection, synchronization and disconnect requests scoped to the budget in the current canonical address, including OAuth continuation. Require WHAB 2.5 or newer for budget-aware browser routing.

## 1.1.0 — 2026-10-16

PR: [#31](https://github.com/Fillll/whab/pull/31)

- Classify imported accounts using WHAB's core budget scope and asset types. Checking, savings and credit default on-budget; investments, retirement, loans and CDs default off-budget.
- Retain supported account subtypes, including IRA, Roth IRA, 401(k), mortgage, auto loan and student loan. Unrecognized provider types remain unclassified off-budget accounts.
- Require WHAB 2.1 or newer for the core account classification contract.

## 1.0.0 — 2026-10-15

PR: [#30](https://github.com/Fillll/whab/pull/30)

- Introduce independent packaging and version ownership for the existing `plaid` plugin, compatible with WHAB 2.x. Earlier changes shipped inside the application distribution and were not independent plugin releases.
- Preserve its plugin ID, Python package and entry point.
- Reject plaintext OAuth redirect URLs in Sandbox as well as production; redirect URLs must use HTTPS.
