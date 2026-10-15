"""Run local/shared tests and require every plugin's Python source to be covered."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate_report(report: dict, sources: tuple[Path, ...]) -> None:
    """Reject unmeasured source, missed statements and missed branches independently."""
    if report.get("meta", {}).get("branch_coverage") is not True:
        raise ValueError("The plugin report must measure branches as well as statements.")
    expected = {path.resolve() for source in sources for path in source.rglob("*.py")}
    if not expected:
        raise ValueError("No plugin Python source was found.")
    measured = {(ROOT / name).resolve(): data for name, data in report["files"].items()}
    missing = expected - measured.keys()
    if missing:
        names = ", ".join(str(path.relative_to(ROOT)) for path in sorted(missing))
        raise ValueError(f"Plugin source omitted from coverage: {names}")
    incomplete = [
        str(path.relative_to(ROOT))
        for path in sorted(expected)
        if measured[path]["summary"]["missing_lines"]
        or measured[path]["summary"]["missing_branches"]
    ]
    if incomplete:
        raise ValueError("Plugin source below 100% coverage: " + ", ".join(incomplete))


def main() -> int:
    sources = {path.parent.name: path for path in sorted((ROOT / "plugins").glob("*/src"))}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", choices=tuple(sources), help="Measure only this plugin's source.")
    arguments = parser.parse_args()
    selected = (sources[arguments.plugin],) if arguments.plugin else tuple(sources.values())
    with tempfile.TemporaryDirectory(prefix="whab-plugin-coverage-") as temporary:
        report = Path(temporary) / "coverage.json"
        env = {**os.environ, "COVERAGE_FILE": str(Path(temporary) / ".coverage")}
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                *(f"--cov={path}" for path in selected),
                "--cov-config=pyproject.toml", "--cov-branch", "--cov-fail-under=100",
                "--cov-report=term-missing", f"--cov-report=json:{report}",
            ],
            cwd=ROOT,
            env=env,
            check=False,
        )
        if result.returncode:
            return result.returncode
        try:
            validate_report(json.loads(report.read_text()), selected)
        except (ValueError, KeyError, OSError) as error:
            print(f"Plugin coverage check failed: {error}", file=sys.stderr)
            return 1
    print(f"All {len(selected)} plugins have 100% Python statement and branch coverage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
