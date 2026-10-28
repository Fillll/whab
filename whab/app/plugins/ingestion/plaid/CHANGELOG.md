# Plaid bank ingestion plugin changelog

## 1.4.0 — 2026-10-27

PR: [#124](https://github.com/Fillll/whab/pull/124)

- Present bank connection, management, sync and disconnect controls in Plaid plugin settings. Keep disconnect confirmation inline, suspend/restore visual settings around Link, and return completed operations to Plaid settings while retaining original OAuth scope checks.

## 1.3.1 — 2026-10-25

PR: [#113](https://github.com/Fillll/whab/pull/113)

- Ship a packaged JSON manifest declaring the primary category and named plugin dependencies.

## Unreleased

## 1.3.0 — 2026-10-23

PR: [#101](https://github.com/Fillll/whab/pull/101)

- Support the typed live bank-provider adapter on WHAB 2.38+, retaining host-owned encryption, durable native application, signed-webhook checks and independently leased background synchronization.
- Preserve budget and OAuth identity through explicit connection continuations, status checks, cancellation and update completion. Keep unknown exchanges blocked for reconciliation and retain existing Item identities, annotations and checkpoints across supported replacement and reopen.

## 1.2.0 — 2026-10-23

PR: [#84](https://github.com/Fillll/whab/pull/84)

- Hold an independent host runtime lease through each coalesced background synchronization, including writes and cleanup, so storage handovers drain active ingestion safely. Observe background failures with sanitized diagnostics and require WHAB 2.27+.

## 1.1.4 — 2026-10-18

PR: [#66](https://github.com/Fillll/whab/pull/66)

- Bind controls idempotently when an Accounts view is inserted without reloading the document. Block partial navigation while Link, synchronization or disconnection is active, including asynchronous success persistence, while preserving budget scope and OAuth behavior.

## 1.1.3 — 2026-10-18

PR: [#59](https://github.com/Fillll/whab/pull/59)

- Move synchronous storage reads, normalization and durable writes off the async network loop, keeping other requests responsive during bank synchronization.
- Preserve per-connection synchronization locks until started storage work finishes, including repeated cancellation and shutdown; retain atomic cursor updates, encrypted-token handling and safe errors.

## 1.1.2 — 2026-10-16

PR: [#40](https://github.com/Fillll/whab/pull/40)

- Rename the independent distribution and Python package to `whab_plugin_plaid` under the shared `whab_plugin_<name>` convention. Preserve the plugin ID and its existing saved data and settings.

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
