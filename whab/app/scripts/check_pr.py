"""Fast PR checks; complete source regressions, independent shards and strict coverage."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

from check import ROOT, check_environment
from check_plugin_coverage import plugin_sources, validate_report

REPOSITORY = ROOT.parents[1]
SHARDS = 8


def run(command, *, env, **kwargs):
    started = time.monotonic()
    subprocess.run(command, cwd=ROOT, env=env, check=True, **kwargs)
    print(f"Completed in {time.monotonic() - started:.2f}s: {' '.join(map(str, command[:4]))}", flush=True)


def change_plan(paths):
    """Only known documentation/launcher boundaries skip application regressions."""
    def docs(name):
        return name.endswith(".md") or name in {"LICENSE", "whab/app/LICENSE"}

    def launcher(name):
        return name.startswith("whab/launchers/")
    application = not paths or any(not docs(name) and not launcher(name) for name in paths)
    native = any(launcher(name) or name in {
        "whab/app/src/whab/cli.py", "whab/app/src/whab/__main__.py",
        "whab/app/scripts/install.py", "whab/app/pyproject.toml", ".github/workflows/ci.yml",
    } for name in paths)
    return {"application": application, "launcher": native}


def changed_paths(base):
    # Treat refs only as Git revision arguments, never as shell text or options.
    resolved = subprocess.check_output(["git", "rev-parse", "--verify", f"{base}^{{commit}}"], cwd=ROOT, text=True).strip()
    output = subprocess.check_output(["git", "diff", "--name-only", "--no-renames", "-z", f"{resolved}...HEAD", "--"], cwd=ROOT)
    return [name for name in output.decode().split("\0") if name]


def source_identity():
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    diff = subprocess.check_output(["git", "diff", "--binary", "HEAD", "--"], cwd=ROOT)
    return {"revision": revision, "diff_sha256": hashlib.sha256(diff).hexdigest(),
            "python": list(sys.version_info[:2])}


def test_counts(path):
    suites = ElementTree.parse(path).getroot().iter("testsuite")
    counts = {name: 0 for name in ("tests", "failures", "errors", "skipped")}
    for suite in suites:
        for name in counts:
            counts[name] += int(suite.get(name, "0"))
    if counts["failures"] or counts["errors"] or counts["skipped"]:
        raise ValueError(f"PR regressions must pass without omitted tests: {counts}")
    return counts["tests"]


def verify_test_ids(path, selected):
    """JUnit identities must match selected IDs, even if total counts agree."""
    expected = []
    for nodeid in selected:
        address, bracket, parameters = nodeid.partition("[")
        parts = address.split("::")
        parts[0] = parts[0].replace("/", ".").removesuffix(".py")
        parts[-1] += bracket + parameters
        expected.append((".".join(parts[:-1]), parts[-1]))
    actual = [(case.get("classname"), case.get("name"))
              for case in ElementTree.parse(path).getroot().iter("testcase")]
    if Counter(actual) != Counter(expected):
        raise ValueError("Executed test identities differ from the selected source tests.")


def verify_partition(manifests, count, identity):
    from pr_shard import partition

    if len(manifests) != count or {row["index"] for row in manifests} != set(range(count)):
        raise ValueError("Missing or duplicate source test shards.")
    collected = manifests[0]["collected"]
    if not collected or len(set(collected)) != len(collected):
        raise ValueError("A nonempty unique complete test collection is required.")
    for row in manifests:
        if row["count"] != count or row["identity"] != identity or row["collected"] != collected:
            raise ValueError("Source, Python version or test collection differs across shards.")
        expected = partition(collected, row["index"], count)
        if row["selected"] != expected or row["executed"] != len(expected):
            raise ValueError("A shard omitted, repeated or selected unexpected tests.")
    return len(collected)


def shard(index, count, workers, output, env):
    output.mkdir(parents=True, exist_ok=False)
    collection = output / "collection"
    sources = tuple(plugin_sources().values())
    env = {**env, "COVERAGE_FILE": str(output / ".coverage")}
    command = [sys.executable, "-m", "pytest", "-p", "scripts.pr_shard",
               "--pr-shard-index", str(index), "--pr-shard-count", str(count),
               "--pr-collection-dir", str(collection), "-n", str(workers), "--dist", "worksteal",
               *(f"--cov={source}" for source in sources), "--cov-config=pyproject.toml",
               "--cov-branch", "--cov-fail-under=0", "--cov-report=", "--durations=15",
               f"--junitxml={output / 'tests.xml'}"]
    before = source_identity()
    run(command, env=env)
    if source_identity() != before:
        raise ValueError("Source changed during PR validation; rerun on a stable checkout.")
    rows = [json.loads(path.read_text()) for path in sorted(collection.glob("*.json"))]
    if not rows or any(row != rows[0] for row in rows):
        raise ValueError("Workers did not collect the same complete test suite.")
    manifest = {**rows[0], "identity": before, "executed": test_counts(output / "tests.xml")}
    verify_test_ids(output / "tests.xml", manifest["selected"])
    if manifest["executed"] != len(manifest["selected"]):
        raise ValueError("Selected test IDs and executed test count differ.")
    (output / "manifest.json").write_text(json.dumps(manifest) + "\n")


def aggregate(directory, count, env):
    paths = sorted(directory.glob("*/manifest.json"))
    total = verify_partition([json.loads(path.read_text()) for path in paths], count, source_identity())
    # Each artifact must carry actual coverage, not just a passing test manifest.
    for path in paths:
        if not (path.parent / ".coverage").is_file():
            raise ValueError("A source shard has no coverage data.")
        row = json.loads(path.read_text())
        if test_counts(path.parent / "tests.xml") != row["executed"]:
            raise ValueError("Executed test counts differ from the source manifest.")
        verify_test_ids(path.parent / "tests.xml", row["selected"])
    with TemporaryDirectory(prefix="whab-pr-coverage-") as temporary:
        env = {**env, "COVERAGE_FILE": str(Path(temporary) / ".coverage")}
        run([sys.executable, "-m", "coverage", "combine", "--keep",
             *(str(path.parent / ".coverage") for path in paths)], env=env)
        report = Path(temporary) / "coverage.json"
        run([sys.executable, "-m", "coverage", "json", "--fail-under=100", "-o", str(report)], env=env)
        validate_report(json.loads(report.read_text()), tuple(plugin_sources().values()))
    print(f"All {total} source tests passed exactly once; all plugins have 100% statement/branch coverage.")


def focused_paths(paths):
    """Local iteration only; the authoritative CI gate still runs every source test."""
    selected = {"tests/domain", "tests/test_architecture.py", "tests/test_ledger_service.py"}
    for name in paths:
        parts = Path(name).parts
        if parts[:3] == ("whab", "app", "plugins") and len(parts) >= 5:
            selected.add(str(Path(*parts[2:5]) / "tests"))
            selected.add("tests/web")
        elif name.startswith("whab/app/tests/"):
            path = ROOT / Path(*parts[2:])
            if path.exists():
                selected.add(str(path.relative_to(ROOT)))
        elif name.startswith("whab/app/scripts/"):
            selected.update({"tests/test_contribution_checks.py", "tests/test_pr_checks.py"})
        elif name.startswith("whab/app/src/whab/domain/"):
            selected.add("tests/test_portable.py")
        elif name.startswith("whab/app/src/whab/"):
            relative = Path(*parts[4:])
            candidate = Path("tests") / relative.parent / f"test_{relative.stem}.py"
            if (ROOT / candidate).is_file():
                selected.add(str(candidate))
    return sorted(name for name in selected if (ROOT / name).exists())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "shard", "aggregate", "local", "distribution"))
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--count", type=int, default=SHARDS)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.mode == "plan":
            plan = change_plan(changed_paths(args.base))
            print(json.dumps(plan))
            if os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a") as output:
                    output.writelines(f"{name}={str(value).lower()}\n" for name, value in plan.items())
            return 0
        with TemporaryDirectory(prefix="whab-pr-home-") as home:
            env = check_environment(Path(home))
            if args.mode == "shard":
                from pr_shard import partition

                partition([], args.index, args.count)
                if args.workers < 1 or args.output is None:
                    raise ValueError("A positive worker count and new output directory are required.")
                shard(args.index, args.count, args.workers, args.output.resolve(), env)
            elif args.mode == "aggregate":
                if args.output is None:
                    raise ValueError("Coverage artifact directory is required.")
                aggregate(args.output.resolve(), args.count, env)
            elif args.mode == "distribution":
                run([sys.executable, "scripts/check_distribution.py"], env=env)
            else:
                paths = changed_paths(args.base)
                run([sys.executable, "scripts/check.py", "--quick"], env=env)
                if change_plan(paths)["application"]:
                    run([sys.executable, "-m", "pytest", "-n", str(args.workers), "--dist", "worksteal",
                         "--durations=10", *focused_paths(paths)], env=env)
                print("Focused local checks passed. Complete source coverage is verified by required PR CI.")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"PR checks failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
