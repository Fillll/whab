# Changelog

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
