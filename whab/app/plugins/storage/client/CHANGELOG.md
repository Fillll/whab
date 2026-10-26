# HTTPS remote budget client plugin changelog

## 1.6.1 — 2026-10-25

PR: [#113](https://github.com/Fillll/whab/pull/113)

- Ship a packaged JSON manifest declaring the primary category and named plugin dependencies.

## Unreleased

## 1.6.0 — 2026-10-23

PR: [#100](https://github.com/Fillll/whab/pull/100)

- Require WHAB 2.39 and use its declarative live remote-client adapter. Keep endpoint, token, remote budget and transport in the host while approved descriptor workers return bounded metadata only.
- Retain exact authority, pending command identity and revision through pause/resume, compatible code replacement and saved-version reopen. Preserve unknown outcomes across restart and expose explicit original-request completion without automatic reconciliation or discard.
- Keep legacy factory and transport exports available with accurate restart-only compatibility.

## 1.5.0 — 2026-10-23

PR: [#76](https://github.com/Fillll/whab/pull/76)

- Declare remote authority through the versioned SDK contract, requiring WHAB 2.22 or newer. Host policy no longer depends on this plugin's implementation name; lifecycle remains restart-only.

## 1.4.0 — 2026-10-18

PR: [#68](https://github.com/Fillll/whab/pull/68)

- Forward model settings actions and suggestion queries to the authoritative budget host. Preserve displayed suggestion identities with atomic category/review edits and keep existing register browsing usable against older hosts.

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
