# Changelog

## Unreleased

## 1.1.0 — 2026-10-23

PR: [#84](https://github.com/Fillll/whab/pull/84)

- Add native schema 2 with durable local authority roles, typed-operation receipts and exact same-device financial/protected transfer. Preserve an fsynced schema-1 recovery image before explicit native adoption and fence uncertain writes.
- Preserve encrypted checkpoints, account metadata, receipts and import identities in both workspaces; declare exact portable payload ownership and require WHAB 2.27+.

## 1.0.0 — 2026-10-23

PR: [#79](https://github.com/Fillll/whab/pull/79)

- Add independently packaged durable JSON storage with strict native/payload schemas, one backend data file, embedded receipts and encrypted provider checkpoints while runtime keys remain outside it.
- Serialize detached candidate commits through a stable POSIX writer lock, flushed atomic replacement, external-change fencing and durable operation/generation evidence. Preserve unknown optional host-owned state and sanitize portable exports.
- Validate public storage contracts, synthetic encrypted synchronization, corruption/disk faults, competing writers, process interruption and installed native-HTTPS save/export/restore/reopen with SQLite imports prohibited. Document measured full-file costs and data bounds.
