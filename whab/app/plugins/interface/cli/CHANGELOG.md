# Changelog

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
