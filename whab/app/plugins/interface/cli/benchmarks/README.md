# Atomic transfer benchmark

The final compatible-source run used 100,000 fictional transactions, 100 accounts
and **100 fresh CLI processes**: 25 each at 1, 10, 50 and 100 pairs. It includes
portable version 2 and explicit existing zero-pair support; measured pairs were
nonzero. Python was 3.15.1 on macOS 26.5.2 arm64. Each invocation included normal
preview, guards, native commit, synchronous durable checkpoint and verified JSON
receipt. [Complete raw primary samples](results-2026-11-15-primary.json).
The separate 100-command comparison is still running and is not claimed complete
or included in measured workflow savings below; it does not gate deployment.

| Pairs / transaction legs | Batch p50 | Batch p95 | Per-leg p50 | Commits / checkpoints per batch |
| --- | ---: | ---: | ---: | ---: |
| 1 / 2 | 629.12 ms | 1,424.92 ms | 314.56 ms | 1 / 1 |
| 10 / 20 | 476.27 ms | 756.67 ms | 23.81 ms | 1 / 1 |
| 50 / 100 | 664.82 ms | 1,107.51 ms | 6.65 ms | 1 / 1 |
| 100 / 200 | 992.28 ms | 3,861.97 ms | 4.96 ms | 1 / 1 |

**Every size missed the 250 ms warm p95 target.** These shared-computer
measurements overlapped the separate-command baseline. Raw samples retain load
averages; this evidence does not isolate scheduling cost. Provider latency is
excluded: there were no provider calls. Cold activation (36.90–48.88 s) and first
mutation (13.95–23.80 s) are recorded separately and excluded from the warm table.

All seven bounded segments restored the actual final saved `.whab` artifact into
an empty store and verified 100,000 records, affected fields and coherent history.
The largest portable payload was 48,451,584 bytes, below the unchanged
52,428,800-byte storage limit. No history was pruned and no limits were increased.
The portable compatibility fence preserves pairless version 1 files; paired data
or reversible paired history requires version 2. Compatibility validation confirms
WHAB 2.80 refuses these newer files before changing destination data, instead of
applying its older transfer-funding rules.

The primary source digest was identical before and after measurement:
`46b0a649aca2035ad108369fe46eecd4476e7df6f689d15f81301537fe5d962b`.
It covers core, SQLite, JSON and CLI production Python plus the benchmark script.

After that complete run, four CLI help strings were corrected to distinguish
clearing an optional Transfer label from removing the last eligible classification:
`transaction_changes.py`, `transaction_commands.py`, `review_commands.py` and
`category_classification.py`. Matching, storage and audit execution did not change.
The post-help source digest is
`7bc744c5694759f5bdf9d4aef296c966fd3cd3625364e004490ad5b2822c3773`.
The still-running separate comparison will retain its own before/after identities.

## Reproduction and measurement

Run from `whab/app` in the installed development environment:

```sh
.venv/bin/python plugins/interface/cli/benchmarks/atomic_transfers.py \
  --runs 25 --transactions 100000 \
  --output /tmp/whab-transfer-benchmark.json
```

This runs 100 primary bulk processes and one equivalent complete workflow of
separate `match-transfer` commands per size. Use `--separate-runs` for additional
baseline samples, or `--separate-runs 0` for only the primary measurements. Each
separate workflow follows the bulk samples in its first fixture segment and uses
the same number of fresh unmatched pairs, retaining that bounded history. One
baseline sample is an observed workflow duration, not a robust latency distribution.

Segments bound measured bulk work to 1,000 pairs, plus the separately reported
cold mutation and baseline workflow, then start with an equivalent fresh fixture.
Every measured intent uses previously unmatched pairs; this is not repeated receipt
replay. No production home, provider or bank data is opened.

`native_commits` measures the public authority commit-evidence generation delta;
`checkpoints` counts successful durable checkpoint calls. Instrumentation writes
one small metrics file after each checkpoint; it is not an internal SQLite statement
trace. Assertions require exactly one of each per bulk invocation and one per
separate pair. Timeouts, refused operations, count mismatches or restore failures
stop the run instead of producing a successful result.

## Earlier complete separate-command comparisons

[Raw earlier baselines](results-2026-11-15-before-fence-baselines.json) preserve
one complete measured workflow at each size below. Those separate commands used
the prior portable format and nonzero semantics; raw source changes are recorded.
These are historical comparisons, not final compatible-source primary runs.

| Separate pair commands | Complete workflow | Time saved vs final bulk p50 | Commits / checkpoints |
| --- | ---: | ---: | ---: |
| 1 | 25,767.65 ms | 25,138.53 ms (97.56%) | 1 / 1 |
| 10 | 273,143.32 ms | 272,667.05 ms (99.83%) | 10 / 10 |
| 50 | 1,480,705.90 ms | 1,480,041.08 ms (99.96%) | 50 / 50 |

All saved artifacts passed restoration verification. This run was intentionally
stopped after its complete 50-pair case, before any 100-pair measurements, so the
next case could use the compatible reader throughout. No partial workflow is
substituted for a complete measured result. Comparing these prior-format baselines
to the final primary run does not isolate the effect of the compatibility fence.

## Periodic audit reserve cost

The [native diagnostic samples](results-2026-11-15-history-profile.json) record
20 warm operations of 50 pairs after one cold operation, before the portable fence.
The same mechanism can be inspected against the checked-out implementation:

```sh
.venv/bin/python plugins/interface/cli/benchmarks/profile_transfer_history.py \
  --transactions 100000 --pairs 50 --runs 20 \
  --output /tmp/whab-transfer-history-profile.json
```

This separate process instruments history writes and sanitized-image preparation,
with cProfile output when the audit reserve grows. It measures native operations
and image export, with profiling overhead; it excludes CLI/HTTP and the host's
final durable-container checkpoint and does not replace the fresh-process results.

At warm batch 16, audit JSON ended at byte 31,418,589 with capacity 31,425,402.
The next batch rewrote the JSON baseline and enlarged capacity to 32,536,738.
Native history writing took 355.26 ms; the sanitized copy's history write took
233.56 ms within 618.77 ms of image preparation. The instrumented operation took
1,483.89 ms, versus 269–393 ms nearby. The portable image grew from 47,276,032 to
48,377,856 bytes, below its size limit.

This proves periodic audit-JSON reserve refill cost. It does not attribute every
observed p95/outlier to that event, or mean every warm batch exports all 100,000
transaction rows. Shared load, transport and durable-container work remain distinct.
No production optimization, storage-limit increase or history pruning was made.
