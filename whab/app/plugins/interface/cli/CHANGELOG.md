# Changelog

## 2.20.0 — 2026-11-13

PR: [#199](https://github.com/Fillll/whab/pull/199)

- Add explicit atomic trnx review and complete read-only review-audit commands, Transfer category creation/configuration, detailed offline help/examples and exact-intent recovery.

## 2.19.0 — 2026-11-13

PR: [#198](https://github.com/Fillll/whab/pull/198)

- Add name-addressed Income creation/classification, historical dry-run preview, verified guarded results, audit attribution and exact-intent recovery with complete offline root/group/command help and examples.

## 2.18.0 — 2026-11-13

PR: [#193](https://github.com/Fillll/whab/pull/193)

- Add name-addressed transfer proposals, exact preview and guarded match/unmatch commands, complete offline help, audit attribution and original-intent recovery.

## 2.17.0 — 2026-11-11

PRs: [#184](https://github.com/Fillll/whab/pull/184), [#188](https://github.com/Fillll/whab/pull/188)

- Integrate command-specific lazy startup and coherent storage-filtered reads from PR #184. [#184](https://github.com/Fillll/whab/pull/184)

- Add multi-ID categorize/classify and mixed edit-many JSON/stdin workflows, exact read-only previews, optional single-target --atomic and complete prominent offline root/group/command help. Preserve default single-record output. Return original resolved intent for safe recovery; never loop, split, truncate or retry uncertain writes automatically. [#188](https://github.com/Fillll/whab/pull/188)

## 2.16.0 — 2026-11-11

PR: [#187](https://github.com/Fillll/whab/pull/187)

- Add name/ID/path-addressed category rename and stable-ID transaction memo append, with automatic revisions/request IDs, agent/batch audit labels, verified saved records, JSON/table output and detailed offline help. Preserve imported notes and merchant whitespace; recover exact successful intents after host restart without appending twice. Refuse ambiguous/duplicate names, stale or invalid edits, and never replay uncertain writes with a new intent.

## 2.15.0 — 2026-11-09

PR: [#182](https://github.com/Fillll/whab/pull/182)

- Document zero-only row deletion and expose consistent deletable status with nonzero blocker counts/months. Refresh detailed offline help, version requirements and guarded workflow examples.

## 2.14.0 — 2026-11-09

PR: [#180](https://github.com/Fillll/whab/pull/180)

- Add exact name/ID category show with complete deletion blocker counts/months and updated offline help. Report definitive authored refusals separately from uncertain writes, and surface history query errors.

## 2.13.0 — 2026-11-09

PR: [#179](https://github.com/Fillll/whab/pull/179)

- Add exact name/ID-addressed category delete and category group delete, with qualified Unicode child paths, internally guarded single intents, whole-hierarchy saved-result verification, clear deleted JSON/table output and detailed offline help. Refuse ambiguous/wrong-kind/nonempty targets and never replay uncertain writes.

## 2.12.0 — 2026-11-09

PR: [#178](https://github.com/Fillll/whab/pull/178)

- Add complete name/ID-addressed `trnx list` (`transactions list`) with inclusive date bounds, literal case-insensitive merchant substring matching and OR filters. Return every nondeleted match across review/category states, pending entries, refunds and accounts, with stable IDs, saved annotations, account/category names and explicit completeness counts in JSON or readable tables.
- Package detailed offline help and schemas/examples; reject inconsistent/oversized reads without partial output. Keep existing guarded categorization separate, preserve financial fields/history and make no future-rule or lender-alias inference.

## 2.11.1 — 2026-11-09

PR: [#177](https://github.com/Fillll/whab/pull/177)

- Require Python 3.15.x only in package metadata and current guidance; retain the existing public behavior and data contracts.

## 2.11.0 — 2026-11-08

PR: [#176](https://github.com/Fillll/whab/pull/176)

- Add name/ID-addressed `category list`, `category group create`, `category create` and `category move`, with JSON/table output, internally guarded intents, saved-result verification and one packaged offline help workflow.

## 2.10.0 — 2026-11-08

PR: [#175](https://github.com/Fillll/whab/pull/175)

- Add name-addressed account close/reopen, independent lifecycle state in JSON/tables, active/closed list filters and detailed offline help. Expose the existing deposit/cd classification and all canonical types; keep scope explicit, preserve lifecycle when classifying, and verify each guarded saved result without uncertain-write replay.

## 2.9.1 — 2026-11-08

PR: [#174](https://github.com/Fillll/whab/pull/174)

- Render importer-owned packaged workflow topics in import/preview/commit help and discover them from generic import help. Keep offline plugin discovery and SDK callbacks accurately labeled, prefer the simple account commands in classification help, and document simple name-addressed workflows with detailed help as a contribution requirement.

## 2.9.0 — 2026-11-08

PR: [#173](https://github.com/Fillll/whab/pull/173)

- Add name-addressed account list/show/classify commands with exact ID/unique Unicode name resolution, unclassified filtering, JSON/table output, explicit or preserved budget scope, domain subtype validation and detailed offline help. Reuse guarded public ledger updates, generate intent/revision metadata automatically and verify saved results without replaying stale or uncertain mutations. Preserve imported balance uncertainty and financial history; never request bank synchronization.

## 2.8.0 — 2026-11-08

PR: [#171](https://github.com/Fillll/whab/pull/171)

- Add shared connection/attachment inspection, attach/detach, reviewed global disconnect, account-profile listing and encrypted credential-file/stdin profile setup to both console entry points. Include provider-specific action help with runnable examples; recognize bank commands before optional profile filenames.

## 2.7.1 — 2026-11-08

PR: [#170](https://github.com/Fillll/whab/pull/170)

- Explain agent scope, discovery, category attribution, minor units, revisions, exact retries and history recovery in root help and the CLI guide. Add missing argument help, public invocation templates and human-readable schema bounds across installed operation contracts.
- Expand bank mapping, scheduling and investment help; clarify history pagination, preview versus immediate checkpoint writes and financial snapshot exports. Preserve history/history_preview JSON query parameters through both local and remote CLI transports instead of silently discarding them.

## 2.7.0 — 2026-11-08

PR: [#169](https://github.com/Fillll/whab/pull/169)

- Add name-addressed history list/show/export, snapshot and previewed undo/redo/restore commands, explicit category --batch labels, guarded fingerprint application and retained exact-intent retry diagnostics.

## 2.6.0 — 2026-11-08

PR: [#168](https://github.com/Fillll/whab/pull/168)

- Add name-addressed category listing and transaction categorization with exact IDs or unique/qualified names, explicit agent attribution, optional Needs review, manual clearing and automatic guarded intent/revision handling. Retain explicit exact-intent retries without automatic write replay.

## 2.5.0 — 2026-11-07

PR: [#167](https://github.com/Fillll/whab/pull/167)

- Add explicit --agent LABEL on edit_transaction through whab api and whab-cli. Require a category in the same write, reject conflicting labels and document self-declared provenance, review choices and manual replacement.

## 2.4.0 — 2026-11-07

PR: [#165](https://github.com/Fillll/whab/pull/165)

- Add name-addressed uncategorized transaction reads to whab with full-history counts, account names, sampling, optional month/posted/transfer filters and currency-aware table output. Preserve review/category independence, budget/revision guards and complete JSON records without changing the ledger.

## 2.3.0 — 2026-11-07

PR: [#164](https://github.com/Fillll/whab/pull/164)

- Add bank investments through both whab and whab-cli, with explicit connection, optional provider-account filter and inclusive activity dates. Document financial units, basis coverage, current-snapshot semantics and unavailable acquisition/realized history.

## 2.2.1 — 2026-11-07

PR: [#163](https://github.com/Fillll/whab/pull/163)

- Show the core account-classification guide in both console help entry points and expose detailed field meanings and an explicit on-budget credit-card edit example through operation help and offline discovery.

## 2.2.0 — 2026-11-07

PR: [#160](https://github.com/Fillll/whab/pull/160)

- Add matching bank connections, preview, configure, sync, schedule and background commands to the name-addressed console and advanced local CLI.

## 2.1.2 — 2026-11-07

PR: [#157](https://github.com/Fillll/whab/pull/157)

- Explain management-tool and required-storage controls in CLI help and documentation. Report protected toggle failures with nonzero exits and fixed diagnostics in both consoles, without echoing server records.

## 2.1.1 — 2026-11-06

PR: [#156](https://github.com/Fillll/whab/pull/156)

- Preserve bounded authored import validation errors for HTTP 400/409/413 so terminal users can diagnose rejected archives and parser conflicts. Keep malformed responses, server failures and transport diagnostics sanitized; never automatically replay a request.

## 2.1.0 — 2026-11-06

PR: [#155](https://github.com/Fillll/whab/pull/155)

- Add discoverable `whab BUDGET import PLUGIN preview|commit FILE --currency USD` commands using the existing guarded host import API. Preview reports counts, warnings and eligibility; commit requires the reviewed SHA-256, checks bounded file bytes before connecting and obtains a fresh parser identity within the same host lease. Preserve empty-budget safeguards, atomic receipts, exact-file retries and isolation without direct database access or automatic replay.

## 2.0.0 — 2026-11-06

PR: [#153](https://github.com/Fillll/whab/pull/153)

- Add the `whab` console with name-addressed budgets, create/list, per-budget plugin controls, core summaries and unreviewed transaction reads. Support directory/file paths and quoted `~`, default local profiles, nested offline help and advanced `whab api` discovery.
- **Breaking default change:** CLI commands now select production when neither an option nor profile specifies an environment. Explicit sandbox options/profiles remain supported; the host SDK and developer fixtures retain sandbox defaults. Requires WHAB 2.57 or newer.

## 1.5.0 — 2026-11-06

PR: [#152](https://github.com/Fillll/whab/pull/152)

- Use `ynab-zip-importer` as the canonical CLI identifier in enable/disable, help, discovery and plugin listings. Translate at the existing host boundary to preserve saved budgets and accept the old spelling in scripts; reject reserved-identifier collisions before submission. Show packaged plugin purposes in top-level help.

## 1.4.1 — 2026-11-04

PR: [#151](https://github.com/Fillll/whab/pull/151)

- Document restart-required plugin enable/disable results: the host saves the requested selection and reports `restart_required`, while supported changes continue to apply live.

## 1.4.0 — 2026-11-04

PR: [#149](https://github.com/Fillll/whab/pull/149)

- Add local JSON profiles with home/environment, automatic authenticated host acquisition, plugin management, bank sync and scheduled-job commands. Preserve remote profiles, offline discovery and explicit financial intent validation.

## 1.3.0 — 2026-11-01

PR: [#148](https://github.com/Fillll/whab/pull/148)

- Read JSON connection files with `url`, `budget`, `ca`, `workspace` and `token_env` fields, retaining command-line overrides, relative CA paths and offline help. Document JSON in help and examples, reject unknown/duplicate keys and invalid values, and preserve compatibility with existing flag files. Keep access tokens in environment variables.

## 1.2.0 — 2026-11-01

PR: [#147](https://github.com/Fillll/whab/pull/147)

- Accept an optional leading connection config file, so `whab-cli default.budget --help` and ordinary commands share saved URL, budget and CA defaults. Support quoted values, comments and backslash continuations, explicit command-line overrides and CA paths relative to the config directory. Keep access tokens in environment variables.

## 1.1.0 — 2026-11-01

PR: [#142](https://github.com/Fillll/whab/pull/142)

- Generate full operation-specific CLI help and offline/authority machine-readable descriptions from installed contracts.

## 1.0.0 — 2026-11-01

PR: [#141](https://github.com/Fillll/whab/pull/141)

- Add an independent JSON CLI Interface plugin for public ledger and discoverable feature operations without AI.
