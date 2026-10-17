# Local YNAB ZIP import plugin changelog

## 1.1.1 — 2026-10-16

- Scope browser preview and import requests to the budget in the current canonical address, retaining the core's empty-budget and atomic-import checks. Require WHAB 2.5 or newer for budget-aware browser routing.

## 1.1.0 — 2026-10-16

- Emit explicit core account scope and subtype fields. YNAB export accounts remain unclassified off-budget accounts because the archive omits account types and budget scope.
- Document account reclassification after import and preserve the unavailable-balance funding safeguard.
- Require WHAB 2.1 or newer for the core account classification contract.

## 1.0.0 — 2026-10-15

- Introduce independent packaging and version ownership for the existing `ynab` plugin, compatible with WHAB 2.x. Earlier changes shipped inside the application distribution and were not independent plugin releases.
- Preserve its plugin ID, Python package, entry point and existing behavior.
