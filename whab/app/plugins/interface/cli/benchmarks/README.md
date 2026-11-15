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
A one-sample baseline is an observed workflow duration, not a robust latency
distribution. `--separate-runs 0` reruns only the primary samples.

Fixtures contain 100,000 fictional transactions and 100 accounts. Every measured
intent uses previously unmatched pairs; no benchmark repeatedly replays a receipt.
Segments bound measured bulk work to 1,000 pairs, plus the separately reported
cold mutation and baseline workflows, then start from a fresh equivalent fixture. No production home, provider or bank data
is opened. No storage limits are raised and no audit history is pruned.

JSON records total-batch p50/p95, per-pair/per-leg time, measured workflow savings,
every raw sample, load averages, native commit/checkpoint counts, cold activation
and first-mutation time separately. Provider latency is excluded. Instrumentation
writes one small local metrics file after each successful checkpoint. The complete
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
