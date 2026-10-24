# Changelog

## Unreleased

## 1.0.0 — 2026-10-23

PR: [#105](https://github.com/Fillll/whab/pull/105)

- Introduce a standalone fictional BankAdapter provider for WHAB 2.38+ with an offline transport, deterministic pending/posted/deleted transactions, cursor checks, update flows and retryable cleanup.
- Package public native HTTPS behavioral tests using toolkit 0.8, including explicit scoped continuation completion, synchronization, reopening and disconnection without external provider traffic.
