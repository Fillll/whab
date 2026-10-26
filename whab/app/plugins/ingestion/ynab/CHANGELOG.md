# Local YNAB ZIP import plugin changelog

## 1.4.1 — 2026-10-25

PR: [#113](https://github.com/Fillll/whab/pull/113)

- Ship a packaged JSON manifest declaring the primary category and named plugin dependencies.

## 1.4.0 — 2026-10-23

PR: [#88](https://github.com/Fillll/whab/pull/88)

- Declare live file-parser support and pass preview parser identity with commit requests. A retired or missing identity is rejected before parsing with replacement code; exact file fingerprints/import retry semantics remain host-owned.
- Require WHAB 2.31+ and retain coherent generation-specific templates/assets across live replacement. Verify parsing/UI contracts and 100% statement/branch coverage.

## 1.3.1 — 2026-10-18

PR: [#72](https://github.com/Fillll/whab/pull/72)

- Select the new budget for the browser before redirecting after import. Keep selection failures retryable with the same saved budget, and prevent reopening the previous budget's models through a stale selection.

## 1.3.0 — 2026-10-18

PR: [#71](https://github.com/Fillll/whab/pull/71)

- Ask for a budget name before importing, with an editable suggestion from the ZIP filename. Validate names before preview, send Unicode names to WHAB 2.21 or newer, and preserve the name and operation for commit retries.
- Open the returned readable Accounts URL after import instead of rejecting budget slugs as failed creation.

## 1.2.1 — 2026-10-18

PR: [#66](https://github.com/Fillll/whab/pull/66)

- Bind import panels idempotently after partial view navigation and prevent navigation while preview or commit is active. Preserve selected files, import operation identity and cross-budget completion redirects.

## 1.2.0 — 2026-10-18

PR: [#50](https://github.com/Fillll/whab/pull/50)

- Move ZIP import into the plugin's Options dialog. Explain that each import creates and opens a new, separate budget, with a read-only preview first.
- Use the host's isolated-budget import API and retain the import operation on a failed commit so retries open the same budget.
- Require WHAB 2.10 or newer for the new-budget import workflow.

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
