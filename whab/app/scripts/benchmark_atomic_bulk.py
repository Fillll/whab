"""Fresh-process atomic bulk benchmarks using fictional portable budgets only.

Run: .venv/bin/python scripts/benchmark_atomic_bulk.py --runs 100 --transactions 100000
Writes reproducible raw JSON outside the checkout. Each command/size has 100 fresh
processes; equivalent separate-command workflows run separately. Cold activation,
first mutation, provider exclusion and instrumentation overhead are reported.
"""

import argparse
import json
import math
import os
import platform
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import monotonic, perf_counter, sleep
from unittest.mock import patch

from benchmark_cli_matrix import fixture

from whab.budgets.container import decode_container
from whab.initialization import initialize_home
from whab.plugins.local import local_connection


def host(home, metrics):
    """Measure successful checkpoint calls and native generations, never records."""
    from whab.budgets.session import BudgetSession
    from whab.config import Settings
    from whab.hosting import run_host

    original = BudgetSession.checkpoint
    counts = {"checkpoints": 0, "generation": 0}

    def checkpoint(session):
        result = original(session)
        if session.store is not None:
            counts["checkpoints"] += 1
            counts["generation"] = session.store.commit_evidence().generation
            Path(metrics).write_text(json.dumps(counts))
        return result

    BudgetSession.checkpoint = checkpoint
    run_host(Settings.load(), transient=True)


def percentile(values, fraction):
    return sorted(values)[math.ceil(len(values) * fraction) - 1]


def summarize(values):
    return {"p50_ms": median(values), "p95_ms": percentile(values, 0.95)}


def verify(path, total, expected, history_count):
    from whab_plugin_sqlite import Store

    container = decode_container(Path(path).read_bytes())
    files = {
        name.removeprefix("plugins/sqlite/"): raw
        for name, raw in container.files.items()
        if name.startswith("plugins/sqlite/")
    }
    reader = Store(":memory:", _authority=False)
    try:
        reader.restore_budget_files("personal", files)
        saved = reader.export_budget("personal")
        assert len(saved["transactions"]) == total
        accounts = {row['id']: row for row in saved['accounts']}
        rows = {row['id']: {**row, 'currency': accounts[row['accountId']]['currency']} for row in saved['transactions']}
        rows.update(accounts)
        for identifier, wanted in expected.items():
            assert all(rows[identifier].get(key) == value for key, value in wanted.items())
        assert len(saved["history"]["entries"]) == history_count
        return {
            "restored_actual_checkpoint": True,
            "transactions": total,
            "history_entries": history_count,
            "expanded_payload_bytes": sum(map(len, files.values())),
            "saved_bytes": Path(path).stat().st_size,
        }
    finally:
        reader.close()


def case(command, size, args):
    executable = str(Path(sys.executable).parent / "whab")
    samples, separate, cold, expected = [], [], {}, {}
    with TemporaryDirectory(prefix="whab-atomic-benchmark-") as directory:
        root = Path(directory)
        home = root / "runtime"
        metrics = root / "counts.json"
        env = {
            key: os.environ[key]
            for key in ("PATH", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT")
            if key in os.environ
        }
        env.update(
            HOME=directory,
            WHAB_HOME=str(home),
            WHAB_ENV="sandbox",
            WHAB_UI="",
            WHAB_PLUGINS="",
            WHAB_STORAGE="sqlite",
            ALLOW_LIVE_PLAID="false",
        )

        def counts():
            return json.loads(metrics.read_text())

        def cli(*parts, stdin=None):
            started = perf_counter()
            result = subprocess.run(
                [executable, "--home", str(home), "--environment", "sandbox", *parts],
                input=stdin,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            elapsed = (perf_counter() - started) * 1000
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout), elapsed

        def changes(iteration):
            if command == "classify":
                return [
                    {
                        "accountId": f"account-{i}",
                        "type": "credit" if iteration % 2 else "depository",
                        "onBudget": True,
                    }
                    for i in range(size)
                ]
            return [
                {
                    "transactionId": f"tx-{i:06}",
                    "categoryId": "category-56" if iteration % 2 else "category-55",
                    "categoryAgent": "Benchmark",
                    **(
                        {"note": f"Fictional memo {iteration % 2}", "reviewed": bool(iteration % 2)}
                        if command == "edit-many"
                        else {}
                    ),
                }
                for i in range(size)
            ]

        def bulk(edits):
            if command == "classify":
                return cli(
                    target,
                    "account",
                    "classify",
                    *[row["accountId"] for row in edits],
                    "--type",
                    edits[0]["type"],
                    "--on-budget",
                    "--atomic",
                    "--agent",
                    "Benchmark",
                )
            if command == "categorize":
                return cli(
                    target,
                    "trnx",
                    "categorize",
                    *[row["transactionId"] for row in edits],
                    "--category",
                    edits[0]["categoryId"],
                    "--agent",
                    "Benchmark",
                    "--atomic",
                )
            return cli(
                target,
                "trnx",
                "edit-many",
                "--file",
                "-",
                "--agent",
                "Benchmark",
                stdin=json.dumps({"edits": edits}),
            )

        with patch.dict(os.environ, env, clear=True):
            initialize_home(home)
            budget = fixture(root, args.transactions, 100)
            target = budget["name"]
            log = (root / "host.log").open("w")
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--host", str(home), str(metrics)],
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                started = perf_counter()
                deadline = monotonic() + 120
                while not (home / 'run/sandbox/host.json').exists():
                    assert process.poll() is None, 'Instrumented fictional host exited before readiness'
                    assert monotonic() < deadline, 'Instrumented host did not become ready'
                    sleep(.05)
                with local_connection(home, "sandbox", timeout=120) as connection:
                    opened = connection.post(
                        "/api/budget/open",
                        content=Path(budget["path"]).read_bytes(),
                        headers={"Content-Type": "application/zip"},
                    )
                    opened.raise_for_status()
                    cold["host_and_fixture_activation_ms"] = (perf_counter() - started) * 1000
                    listed, _ = cli("budget", "list")
                    assert any(row['id'] == budget['id'] for row in listed['budgets'])
                    # Host-owned upload location; inspect its actual saved artifact,
                    # never rewrite a registry or runtime metadata during setup.
                    path = home / 'budgets' / budget['id'] / 'sandbox' / 'opened.whab' 
                    # Cold native/portable cache construction is separate, never hidden.
                    result, elapsed = bulk(changes(0))
                    cold["first_mutation_ms"] = elapsed
                    history_count = 1
                    for i in range(args.runs):
                        edits = changes(i + 1)
                        before = counts()
                        result, elapsed = bulk(edits)
                        after = counts()
                        sample = {
                            "wall_ms": elapsed,
                            "checkpoints": after["checkpoints"] - before["checkpoints"],
                            "native_commits": after["generation"] - before["generation"],
                        }
                        assert sample["checkpoints"] == sample["native_commits"] == 1
                        assert result["data"]["count"] == size
                        samples.append(sample)
                        for row in result["data"]["changes"]:
                            expected[row["id"]] = row["after"]
                        history_count += 1
                    # Identical annotations and target order; separate commits deliberately.
                    for i in range(args.separate_runs):
                        edits = changes(args.runs + i + 1)
                        before = counts()
                        elapsed = 0
                        for edit in edits:
                            if command == "classify":
                                _, duration = cli(
                                    target,
                                    "account",
                                    "classify",
                                    edit["accountId"],
                                    "--type",
                                    edit["type"],
                                    "--on-budget",
                                )
                            elif command == "categorize":
                                _, duration = cli(
                                    target,
                                    "trnx",
                                    "categorize",
                                    edit["transactionId"],
                                    "--category",
                                    edit["categoryId"],
                                    "--agent",
                                    "Benchmark",
                                )
                            else:
                                categories, read_duration = cli(target, "trnx", "categories")
                                _, duration = cli(
                                    "api",
                                    "--budget",
                                    budget["id"],
                                    "command",
                                    "edit_transaction",
                                    "--parameters",
                                    json.dumps(edit),
                                    "--revision",
                                    categories["revision"],
                                    "--request-id",
                                    f"separate-{i}-{edit['transactionId']}",
                                )
                                # Include the ordinary read/revision workflow in elapsed below.
                                duration += read_duration
                            elapsed += duration
                            identifier = edit.get("transactionId", edit.get("accountId"))
                            expected[identifier].update(
                                {
                                    k: v
                                    for k, v in edit.items()
                                    if k not in {"transactionId", "accountId"}
                                }
                            )
                        after = counts()
                        sample = {
                            "wall_ms": elapsed,
                            "checkpoints": after["checkpoints"] - before["checkpoints"],
                            "native_commits": after["generation"] - before["generation"],
                        }
                        assert sample["checkpoints"] == sample["native_commits"] == size
                        separate.append(sample)
                        history_count += size
                    restored = verify(path, args.transactions, expected, history_count)
            finally:
                process.terminate()
                process.wait(timeout=60)
                log.close()
        summary = summarize([row["wall_ms"] for row in samples])
        baseline = summarize([row["wall_ms"] for row in separate])
        return {
            "command": command,
            "batch_size": size,
            "bulk_runs": args.runs,
            "separate_workflow_runs": args.separate_runs,
            "bulk": summary,
            "per_record_p50_ms": summary["p50_ms"] / size,
            "separate": baseline,
            "workflow_saved_p50_ms": baseline["p50_ms"] - summary["p50_ms"],
            "workflow_saved_p50_percent": 100 * (1 - summary["p50_ms"] / baseline["p50_ms"]),
            "target_p95_ms": 250,
            "target_met": summary["p95_ms"] < 250,
            "cold": cold,
            "saved_verification": restored,
            "bulk_samples": samples,
            "separate_samples": separate,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs",
        type=int,
        default=100,
        help="Fresh bulk processes per command AND size (default 100)",
    )
    parser.add_argument(
        "--transactions",
        type=int,
        default=100000,
        help="Fictional populated rows per fresh case (default 100000)",
    )
    parser.add_argument(
        "--separate-runs",
        type=int,
        default=3,
        help="Complete equivalent separate-command workflows per case (default 3; p95 has limited statistical power)",
    )
    parser.add_argument("--sizes", default="1,10,50,100", help="Comma-separated batch sizes, 1–100")
    parser.add_argument(
        "--commands", default="categorize,edit-many,classify", help="Primary commands to benchmark"
    )
    parser.add_argument(
        "--output", required=True, help="Raw JSON evidence path outside source/runtime data"
    )
    args = parser.parse_args()
    sizes = [int(value) for value in args.sizes.split(",")]
    commands = args.commands.split(",")
    if (
        not 1 <= args.runs <= 100
        or not 1 <= args.separate_runs <= 5
        or not 1000 <= args.transactions <= 100000
        or any(not 1 <= size <= 100 for size in sizes)
        or any(command not in {"categorize", "edit-many", "classify"} for command in commands)
    ):
        parser.error(
            "Use 1–100 bulk runs, 1–5 separate runs, 1000–100000 rows, sizes 1–100 and supported commands. Full evidence requires >=100 runs."
        )
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "host": version("whab"),
        "cli": version("whab_plugin_cli"),
        "transactions": args.transactions,
        "notes": "End-to-end fresh processes, verified HTTPS and synchronous save. Native evidence query plus a small count-file write after each checkpoint are included instrumentation overhead. Provider latency excluded (no calls). Cold first mutation excluded from warm samples. Fresh fixture per command/size bounds retained history below native/portable limits. Separate p95 uses only the stated number of workflow repetitions.",
        "cases": [],
    }
    for command in commands:
        for size in sizes:
            result["cases"].append(case(command, size, args))
            Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
            print(
                json.dumps(
                    {
                        k: result["cases"][-1][k]
                        for k in ("command", "batch_size", "bulk", "separate", "target_met")
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--host":
        host(sys.argv[2], sys.argv[3])
    else:
        main()
