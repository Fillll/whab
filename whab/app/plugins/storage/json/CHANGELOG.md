# Changelog

## Unreleased

## 1.0.0 — 2026-10-23

PR: [#79](https://github.com/Fillll/whab/pull/79)

- Add independently packaged durable JSON storage with strict native/payload schemas, one backend data file, embedded receipts and encrypted provider checkpoints while runtime keys remain outside it.
- Serialize detached candidate commits through a stable POSIX writer lock, flushed atomic replacement, external-change fencing and durable operation/generation evidence. Preserve unknown optional host-owned state and sanitize portable exports.
- Validate public storage contracts, synthetic encrypted synchronization, corruption/disk faults, competing writers, process interruption and installed native-HTTPS save/export/restore/reopen with SQLite imports prohibited. Document measured full-file costs and data bounds.
