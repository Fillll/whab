# HTTPS remote budget client plugin changelog

## 1.1.0 — 2026-10-16

PR: [#31](https://github.com/Fillll/whab/pull/31)

- Forward account budget scope, asset/liability types and subtype changes through the revision-checked remote command protocol.
- Require WHAB 2.1 for the account classification command and core account fields.

## 1.0.0 — 2026-10-15

PR: [#30](https://github.com/Fillll/whab/pull/30)

- Introduce independent packaging and version ownership for the existing `client` plugin, compatible with WHAB 2.x. Earlier changes shipped inside the application distribution and were not independent plugin releases.
- Preserve its plugin ID, Python package, entry point and existing behavior.
