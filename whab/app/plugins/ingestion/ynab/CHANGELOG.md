# Local YNAB ZIP import plugin changelog

## 1.5.0 — 2026-11-13

PR: [#199](https://github.com/Fillll/whab/pull/199)

- Import clearing status as reviewed only when a category is present, retaining original clearing/flag metadata in memos and all financial identities/assignments.

## 1.4.7 — 2026-11-09

PR: [#177](https://github.com/Fillll/whab/pull/177)

- Require Python 3.15.x only in package metadata and current guidance; retain the existing public behavior and data contracts.

## 1.4.6 — 2026-11-08

PR: [#175](https://github.com/Fillll/whab/pull/175)

- Extend the packaged migration guide with explicit open/closed decisions and short commands for an off-budget closed CD. Keep archives out of new bank destinations and distinguish reopening from classification, scope and synchronization.

## 1.4.5 — 2026-11-08

PR: [#174](https://github.com/Fillll/whab/pull/174)

- Add one packaged eight-stage CLI migration guide covering permanent budget selection, reviewed USD/fingerprint import, confirmed account classification, optional shared Plaid attachment, explicit account mapping/history choices, reviewed sync, financial verification limits and six-hour updates. Separate optional device/environment background wakeups and preserve the SDK-only parser boundary.

## 1.4.4 — 2026-11-06

PR: [#156](https://github.com/Fillll/whab/pull/156)

- Losslessly normalize complete UTF-8 surrogate pairs emitted by some YNAB exports, preserving text and the same deterministic transaction IDs as standard UTF-8. Continue to reject lone surrogates and invalid bytes without guessing a legacy encoding or dropping characters.
- Give encoding failures a decoder/byte-offset diagnostic and re-export guidance without exposing table contents. Distinguish malformed CSV/TSV and unsupported ZIP compression errors while preserving validation and archive limits.

## 1.4.3 — 2026-11-06

PR: [#152](https://github.com/Fillll/whab/pull/152)

- Name the plugin **YNAB ZIP importer** in listings and operation documentation, and document the explicit `ynab-zip-importer` CLI identifier.

## 1.4.2 — 2026-11-01

PR: [#142](https://github.com/Fillll/whab/pull/142)

- Package required public operation schemas, examples, access/effect/scope/error/retry documentation for offline CLI discovery.

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
