# Local YNAB ZIP import plugin changelog

## 1.1.2 — 2026-10-16

PR: [#40](https://github.com/Fillll/whab/pull/40)

- Rename the independent distribution and Python package to `whab_plugin_ynab` under the shared `whab_plugin_<name>` convention. Preserve the plugin ID and its existing saved data and settings.

## 1.1.1 — 2026-10-16

PR: [#36](https://github.com/Fillll/whab/pull/36)

- Scope browser preview and import requests to the budget in the current canonical address, retaining the core's empty-budget and atomic-import checks. Require WHAB 2.5 or newer for budget-aware browser routing.

## 1.1.0 — 2026-10-16

PR: [#31](https://github.com/Fillll/whab/pull/31)

- Emit explicit core account scope and subtype fields. YNAB export accounts remain unclassified off-budget accounts because the archive omits account types and budget scope.
- Document account reclassification after import and preserve the unavailable-balance funding safeguard.
- Require WHAB 2.1 or newer for the core account classification contract.

## 1.0.0 — 2026-10-15

PR: [#30](https://github.com/Fillll/whab/pull/30)

- Introduce independent packaging and version ownership for the existing `ynab` plugin, compatible with WHAB 2.x. Earlier changes shipped inside the application distribution and were not independent plugin releases.
- Preserve its plugin ID, Python package, entry point and existing behavior.
