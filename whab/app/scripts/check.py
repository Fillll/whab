"""Run the same contribution checks locally and in CI, without installing tools."""

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
MIN_NODE_MAJOR = 22


def check_environment(home: Path | None = None) -> dict[str, str]:
    """Do not pass a configured household runtime or credentials to check children."""
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("WHAB_", "PLAID_", "SERVER_", "CLIENT_"))
        and key not in {"TOKEN_ENCRYPTION_KEY", "ALLOW_LIVE_PLAID", "PORT", "PYTHONPATH"}
    }
    env.update(WHAB_ENV="sandbox", PLAID_ENV="sandbox", ALLOW_LIVE_PLAID="false")
    if home is not None:
        env["WHAB_HOME"] = str(home)
    return env


def preflight(*, docs_only: bool, quick: bool, env: dict[str, str] | None = None) -> str | None:
    """Fail before running checks when their required tools are unavailable."""
    if sys.version_info < (3, 14):
        raise ValueError("Python 3.14 or newer is required; use the development environment's Python.")
    if not shutil.which("git"):
        raise ValueError("Git is required to discover source documentation and changelogs.")
    if docs_only:
        return None
    modules = ("ruff",) if quick else ("ruff", "pytest", "pytest_cov", "coverage", "pip")
    missing = [name for name in modules if importlib.util.find_spec(name) is None]
    if missing:
        raise ValueError(
            f"Missing Python tools: {', '.join(missing)}. "
            "Run .venv/bin/python scripts/install.py --dev --example first."
        )
    node = shutil.which("node")
    if not node:
        raise ValueError("Node.js 22 or newer is required for browser tests; install Node.js and retry.")
    result = subprocess.run(
        [node, "--version"], capture_output=True, text=True, check=False,
        env=env if env is not None else check_environment(),
    )
    version = result.stdout.strip().removeprefix("v")
    if result.returncode or not version.split(".")[0].isdigit() or int(version.split(".")[0]) < MIN_NODE_MAJOR:
        raise ValueError("Node.js 22 or newer is required for browser tests; update Node.js and retry.")
    return node


def commands(*, docs_only: bool, quick: bool, node: str | None) -> list[tuple[str, list[str]]]:
    """Keep the quick path free of pytest, wheel builds and dependency downloads."""
    checks = [("Documentation", [sys.executable, "scripts/check_docs.py"])]
    if docs_only:
        return checks
    tests = sorted(str(path.relative_to(ROOT)) for path in (ROOT / "plugins").glob("*/*/tests/*.test.cjs"))
    if not tests:
        raise ValueError("No plugin browser tests were found; check the source checkout.")
    checks = [
        ("Python lint", [sys.executable, "-m", "ruff", "check", "src", "scripts", "tests", "plugins", "conftest.py"]),
        ("Changelog provenance", [sys.executable, "scripts/check_changelog_prs.py"]),
        *checks,
        ("Browser JavaScript", [node, "--test", *tests]),
    ]
    if not quick:
        checks.extend([
            ("Python tests and plugin coverage", [sys.executable, "scripts/check_plugin_coverage.py"]),
            ("Installed wheels", [sys.executable, "scripts/check_distribution.py"]),
        ])
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--quick", action="store_true", help="Lint, changelogs, documentation and Node browser tests only.")
    mode.add_argument("--docs", action="store_true", help="Check documentation only; no Node or development packages required.")
    args = parser.parse_args(argv)
    try:
        with TemporaryDirectory(prefix="whab-contribution-checks-") as temporary:
            env = check_environment(Path(temporary).resolve())
            node = preflight(docs_only=args.docs, quick=args.quick, env=env)
            checks = commands(docs_only=args.docs, quick=args.quick, node=node)
            for name, command in checks:
                print(f"\n{name}", flush=True)
                result = subprocess.run(command, cwd=ROOT, env=env, check=False)
                if result.returncode:
                    print(f"{name} failed (exit {result.returncode}).", file=sys.stderr)
                    return result.returncode if result.returncode > 0 else 1
    except (OSError, ValueError) as error:
        print(f"Contribution checks could not run: {error}", file=sys.stderr)
        return 1
    print("\nContribution checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
