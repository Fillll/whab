# HTTPS remote budget client plugin changelog

## 1.3.0 — 2026-10-18

PR: [#51](https://github.com/Fillll/whab/pull/51)

- Forward optional memo and account metadata edits through the existing revision and idempotency protocol. Preserve omitted notes, support explicit clearing and report unavailable receipt storage without creating local shadow data.

## 1.2.0 — 2026-10-17

PR: [#41](https://github.com/Fillll/whab/pull/41)

- Forward next-month funding and monthly rollover choices through the existing revision-checked remote command protocol, preserving retry identity and reporting unsupported or rejected writes clearly. Require WHAB 2.7 for the new commands.

## 1.1.1 — 2026-10-16

PR: [#40](https://github.com/Fillll/whab/pull/40)

- Rename the independent distribution and Python package to `whab_plugin_client` under the shared `whab_plugin_<name>` convention. Preserve the plugin ID and its existing saved data and settings.

## 1.1.0 — 2026-10-16

PR: [#31](https://github.com/Fillll/whab/pull/31)

- Forward account budget scope, asset/liability types and subtype changes through the revision-checked remote command protocol.
- Require WHAB 2.1 for the account classification command and core account fields.

## 1.0.0 — 2026-10-15

PR: [#30](https://github.com/Fillll/whab/pull/30)

- Introduce independent packaging and version ownership for the existing `client` plugin, compatible with WHAB 2.x. Earlier changes shipped inside the application distribution and were not independent plugin releases.
- Preserve its plugin ID, Python package, entry point and existing behavior.
