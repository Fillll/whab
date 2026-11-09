# Changelog

## 1.8.0 — 2026-11-08

PR: [#169](https://github.com/Fillll/whab/pull/169)

- Save append-only financial history in the same atomic JSON publication as each change, including explicit actors/batches, guarded replay and preservation across reopen and exports.

## 1.7.0 — 2026-11-07

PR: [#167](https://github.com/Fillll/whab/pull/167)

- Advertise durable category-agent support and preserve explicit attribution through atomic edits, review, provider refreshes, checkpoints, native reopen and portable saves. Keep legacy unlabelled history compatible.

## 1.6.0 — 2026-11-07

PR: [#160](https://github.com/Fillll/whab/pull/160)

- Persist and validate bank account mappings and history cutoffs in native JSON state and protected transfers, with atomic setup and unchanged transaction identities and annotations.

## 1.5.1 — 2026-11-01

PR: [#142](https://github.com/Fillll/whab/pull/142)

- Package required public operation schemas, examples, access/effect/scope/error/retry documentation for offline CLI discovery.

## 1.5.0 — 2026-10-28

PR: [#130](https://github.com/Fillll/whab/pull/130)

- Persist core-validated transaction detail corrections and their optional original-field annotations through atomic commits, synchronization, portable export and reopening. Require WHAB 2.47 or newer.

## 1.4.0 — 2026-10-27

PR: [#126](https://github.com/Fillll/whab/pull/126)

- Use the public host/SDK receipt validator for JPEG, PNG and PDF attachments, retaining legacy WebP compatibility and byte-for-byte receipt persistence across native reopening. Require WHAB 2.45 or newer.

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
