# macOS launcher changelog

## 1.1.0 — 2026-10-17

PR: [#42](https://github.com/Fillll/whab/pull/42)

- Add an opt-in Alpha — latest main channel that downloads the current main
  commit's source snapshot without waiting for CI or fetching Git history.
  Preserve tested-main and stable channel requirements, local installation
  checks, isolated Sandbox startup validation and private runtime backups.
- Show the running source revision and installed channel, provide explicit
  update-check feedback, and keep cached update targets and CI provenance
  accurate when switching channels or reusing the same installed revision.
- Verify the complete bundle against the canonical demo budget address.

## 1.0.0 — 2026-10-16

PR: [#33](https://github.com/Fillll/whab/pull/33)

- Add a native WHAB.app with menu bar controls for start, open, stop, restart,
  tested-main and stable updates, runtime adoption, logs and certificate setup.
- Package a private Python 3.14 runtime, source seed and offline dependency wheels.
- Keep application versions and recoverable updates separate from private runtime data.
