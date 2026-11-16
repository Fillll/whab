# Atomic transfer benchmark

Run from `whab/app` in the installed development environment:

```sh
.venv/bin/python plugins/interface/cli/benchmarks/atomic_transfers.py \
  --runs 25 --transactions 100000 \
  --output /tmp/whab-transfer-benchmark.json
```

This measures 25 fresh `whab` processes at each of 1, 10, 50 and 100 pairs:
100 primary bulk invocations total. Each invocation performs its normal preview,
revision/request guards, native commit, synchronous portable checkpoint and JSON
receipt. One equivalent complete workflow of separate `match-transfer` commands
is measured per size; use `--separate-runs` for additional baseline samples.
Each separate workflow follows the bulk samples in its first fixture segment,
uses the same number of fresh unmatched pairs, and retains the bounded history
created by those samples. A one-sample baseline is an observed workflow duration,
not a robust latency distribution. `--separate-runs 0` reruns only the primary samples.

Fixtures contain 100,000 fictional transactions and 100 accounts. Every measured
intent uses previously unmatched pairs; no benchmark repeatedly replays a receipt.
Segments bound measured bulk work to 1,000 pairs, plus the separately reported
cold mutation and baseline workflows, then start from a fresh equivalent fixture. No production home, provider or bank data
is opened. No storage limits are raised and no audit history is pruned.

JSON records total-batch p50/p95, per-pair/per-leg time, measured workflow savings,
every raw sample, load averages, native commit/checkpoint counts, cold activation
and first-mutation time separately. Provider latency is excluded. Instrumentation
writes one small local metrics file after each successful checkpoint.
`native_commits` is the delta in the public authority commit-evidence generation;
`checkpoints` counts successful durable checkpoint calls. This is not a trace of
internal SQLite bookkeeping statements. The complete
native source digest before/after makes concurrent implementation changes visible;
do not call a mixed-source run an exact-head measurement.

The run asserts one native commit and one checkpoint per bulk invocation, versus
one of each per separate pair. It restores the actual final saved `.whab` artifact
into an empty store and verifies transaction count, every affected record and
history count. Timeouts, refused operations, count mismatches and restore failures
stop the run instead of producing misleading success measurements.

The warm p95 target is 250 ms. The result marks misses explicitly; this is not a
promise for cold work, arbitrary hardware or maximum record payloads. Full
separate-command baselines can take substantially longer than bulk sampling.

## Historical measurements before the portable compatibility fence

[Raw earlier samples](results-2026-11-15-before-portable-fence.json) contain 25 fresh CLI
processes per size, 100 total. These precede the required portable compatibility
fence and are historical diagnostic evidence, not final delivery measurements.
They ran on Python 3.15.1/macOS arm64 with 100,000 fictional
transactions and 100 accounts. Times include normal preview, command startup,
guards, save and verified JSON receipt. These shared-computer measurements
overlapped separate-command baseline work and, briefly, a separate diagnostic.
The recorded load averages provide context; they do not isolate scheduling cost.

| Pairs / transaction legs | Batch p50 | Batch p95 | Per-leg p50 | Commit/checkpoint counts per batch |
| --- | ---: | ---: | ---: | ---: |
| 1 / 2 | 474.53 ms | 684.32 ms | 237.27 ms | 1 / 1 |
| 10 / 20 | 518.44 ms | 1,292.70 ms | 25.92 ms | 1 / 1 |
| 50 / 100 | 710.33 ms | 3,386.47 ms | 7.10 ms | 1 / 1 |
| 100 / 200 | 770.94 ms | 3,013.96 ms | 3.85 ms | 1 / 1 |

**Every size missed the 250 ms warm p95 target.** All seven bounded segments
restored the actual saved artifact into an empty store and verified the 100,000
records, changed fields and coherent history. The largest portable payload was
48,451,584 bytes, below the unchanged 52,428,800-byte storage limit. Cold activation
and first-mutation measurements are separate in the raw evidence; there were no
provider calls.

The raw before/after source digests differ. During this run the CLI help wording
was corrected and guarded history reversal validation was tightened; native
matching and durable checkpoint execution were unchanged. These results are
identified by both digests, not described as an immutable exact-head run. Replacing
only `src/whab/domain/history.py` and the CLI `transfer_commands.py` with their
pre-edit contents exactly reproduces the starting digest. The history edit was
written at `2026-11-16T00:09:05.505Z`; final result/digest capture completed at
`2026-11-16T00:09:16.968Z`. This establishes a source change during the captured
interval, without assuming that a particular warm process loaded the new guard.

- Before: `e30cd71fa720624c50f0945fd6a66be71025d6236a5b93d523f091cc729b7648`
- After: `aa2f55f6b7b73a6f81c5422fcf8c2ce92b018bf861a788d46515b5c3f3c8efee`

## Audit reserve diagnostic

The separate [native diagnostic samples](results-2026-11-15-history-profile.json)
record 20 warm operations of 50 pairs after one cold operation. Reproduce with:

```sh
.venv/bin/python plugins/interface/cli/benchmarks/profile_transfer_history.py \
  --transactions 100000 --pairs 50 --runs 20 \
  --output /tmp/whab-transfer-history-profile.json
```

This temporary-process instrumentation wraps history writes and sanitized image
preparation, and writes a cProfile file whenever the audit reserve grows. It
measures native operations/image export with profiling overhead, excluding the
CLI/HTTP and the host's final durable-container checkpoint. It does not replace
the fresh-process benchmark.

At warm batch 16, the old audit JSON ended at byte 31,418,589 with capacity
31,425,402. Appending the next batch required rewriting the JSON baseline and
extending capacity to 32,536,738. Native history writing took 355.26 ms; the
sanitized image's corresponding history write took 233.56 ms, within 618.77 ms
of image preparation. That instrumented operation took 1,483.89 ms; nearby
operations took 269–393 ms. The portable image grew from 47,276,032 to
48,377,856 bytes, below the size limit.

This demonstrates periodic full audit-JSON reserve refill cost. It does not prove
that the entire 7,671.99 ms fresh-process outlier came from this event, nor that
every warm batch serializes all 100,000 transaction rows. Shared-computer load,
transport and durable-container work remain distinct from this diagnostic. No
production optimization, storage-limit increase or history pruning was made.
