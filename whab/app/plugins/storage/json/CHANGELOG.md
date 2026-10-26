# Changelog

## 1.3.1 — 2026-10-25

PR: [#113](https://github.com/Fillll/whab/pull/113)

- Ship a packaged JSON manifest declaring the primary category and named plugin dependencies.

## Unreleased

## 1.3.0 — 2026-10-23

PR: [#102](https://github.com/Fillll/whab/pull/102)

- Declare compatible local storage workers on genuine WHAB 2.37+. Support complete read-only readiness without taking the live writer lease or creating replacement native data.
- Preserve the stable native writer lease through drained compatible code handover, bounded cleanup and explicit uncertain-outcome recovery; retain native state, receipts and ownership with complete owning statement/branch coverage.

## 1.2.0 — 2026-10-23

PR: [#87](https://github.com/Fillll/whab/pull/87)

- Add native schema 3 with atomic replica watermarks and promotion identity. Upgrade schemas 1/2 explicitly while retaining exact private recovery images; reject stale jobs and ordinary copy writes.
- Preserve complete financial, encrypted provider and receipt state through promotion and restart, requiring WHAB 2.30+. Verify interrupted commits, both copy directions and public conformance at 100% statement and branch coverage.

## 1.1.0 — 2026-10-23

PR: [#84](https://github.com/Fillll/whab/pull/84)

- Add native schema 2 with durable local authority roles, typed-operation receipts and exact same-device financial/protected transfer. Preserve an fsynced schema-1 recovery image before explicit native adoption and fence uncertain writes.
- Preserve encrypted checkpoints, account metadata, receipts and import identities in both workspaces; declare exact portable payload ownership and require WHAB 2.27+.

## 1.0.0 — 2026-10-23

PR: [#79](https://github.com/Fillll/whab/pull/79)

- Add independently packaged durable JSON storage with strict native/payload schemas, one backend data file, embedded receipts and encrypted provider checkpoints while runtime keys remain outside it.
- Serialize detached candidate commits through a stable POSIX writer lock, flushed atomic replacement, external-change fencing and durable operation/generation evidence. Preserve unknown optional host-owned state and sanitize portable exports.
- Validate public storage contracts, synthetic encrypted synchronization, corruption/disk faults, competing writers, process interruption and installed native-HTTPS save/export/restore/reopen with SQLite imports prohibited. Document measured full-file costs and data bounds.
