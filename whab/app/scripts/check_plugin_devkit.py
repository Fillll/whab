"""Validate independent toolkit wheels against minimum and candidate host artifacts."""

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import xml.etree.ElementTree as ET
import zipfile
from email.parser import BytesParser
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
MINIMUM_HOST_VERSION = "2.22.0"
MINIMUM_HOST_COMMIT = "bac249ca0e62638a40a6effc44af741c283e83d7"
MINIMUM_TEST_COUNT = 92
TEST_REQUIREMENTS = ("pytest==9.1.1", "pytest-cov==7.1.0", "coverage==7.16.2")

VERIFY_INSTALLATION = """
import importlib
import importlib.metadata as metadata
import json
import sys
from pathlib import Path

environment, repository, expected, destination = sys.argv[1:]
environment, repository = Path(environment).resolve(), Path(repository).resolve()
expected = json.loads(expected)
assert Path(sys.prefix).resolve() == environment
result = {"versions": {}, "toolkit_source_files": []}
for name, version in expected.items():
    module = importlib.import_module(name)
    module_path = Path(module.__file__).resolve()
    assert module_path.is_relative_to(environment), name
    assert not module_path.is_relative_to(repository), name
    assert metadata.version(name) == version, name
    result["versions"][name] = metadata.version(name)
    if name == "whab_plugin_devkit":
        result["toolkit_source_files"] = [str(path.resolve()) for path in module_path.parent.rglob("*.py")]
assert {(entry.group, entry.name, entry.value) for entry in metadata.distribution("whab_plugin_devkit").entry_points} == {
    ("console_scripts", "whab-plugin", "whab_plugin_devkit.cli:main"),
}
entries = tuple(metadata.entry_points(group="whab.plugins"))
selected = {"sqlite"}
if "whab_plugin_json" in expected:
    selected |= {"json", "synthetic-report-a", "synthetic-report-b", "synthetic-brand-a", "synthetic-brand-b", "synthetic-import-a", "synthetic-import-b"}
assert len(entries) == len(selected) and {entry.name for entry in entries} == selected
reference, = (entry for entry in entries if entry.name == "sqlite")
assert reference.dist.metadata["Name"] == "whab_plugin_sqlite"
assert reference.load().storage is not None
for entry in sys.path:
    assert not Path(entry).resolve().is_relative_to(repository), entry
Path(destination).write_text(json.dumps(result))
"""


def isolated_environment(home):
    """Standalone checks also exclude configured budgets and source import paths."""
    password_variable = os.environ.get("WHAB_BUDGET_PASSWORD_ENV")
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("WHAB_", "PLAID_", "SERVER_", "CLIENT_", "PYTHON"))
        and key not in {"TOKEN_ENCRYPTION_KEY", "ALLOW_LIVE_PLAID", "PORT", "VIRTUAL_ENV"}
        and key != password_variable
    }
    environment.update(WHAB_HOME=str(home), WHAB_ENV="sandbox", PLAID_ENV="sandbox", ALLOW_LIVE_PLAID="false")
    return environment


def run(command, *, cwd, env, **kwargs):
    return subprocess.run(command, cwd=cwd, env=env, check=True, **kwargs)


def host_version(source):
    for node in ast.parse((source / "src/whab/__init__.py").read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("The host source has no literal version declaration.")


def copy_project(source, destination):
    destination.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info")
    shutil.copytree(source / "src", destination / "src", ignore=ignore)
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copy2(source / name, destination / name)
    return destination


def minimum_host_source(base, environment):
    repository = ROOT.parents[1]
    available = subprocess.run(
        ["git", "cat-file", "-e", f"{MINIMUM_HOST_COMMIT}^{{commit}}"],
        cwd=repository, env=environment, capture_output=True, check=False,
    )
    if available.returncode:
        raise ValueError("The pinned minimum-host commit is unavailable. Fetch full repository history before running toolkit compatibility checks.")
    archive = base / "minimum-host.tar"
    with archive.open("wb") as output:
        run(["git", "archive", "--format=tar", MINIMUM_HOST_COMMIT, "--",
             "whab/app/src", "whab/app/pyproject.toml", "whab/app/README.md", "whab/app/LICENSE",
             "whab/app/plugins/storage/sqlite/src", "whab/app/plugins/storage/sqlite/pyproject.toml",
             "whab/app/plugins/storage/sqlite/README.md", "whab/app/plugins/storage/sqlite/LICENSE"],
            cwd=repository, env=environment, stdout=output)
    destination = base / "minimum-source"
    with tarfile.open(archive) as source:
        source.extractall(destination, filter="data")
    project = destination / "whab/app"
    if host_version(project) != MINIMUM_HOST_VERSION:
        raise ValueError("The pinned source does not match the advertised minimum host version.")
    return project


def source_at_revision(base, environment, revision):
    """Build candidate source from an exact local Git object, never an index host."""
    if type(revision) is not str or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Choose a full lowercase 40-character candidate source commit.")
    archive = base / "candidate-host.tar"
    with archive.open("wb") as output:
        run(["git", "archive", "--format=tar", revision, "--",
             "whab/app/src", "whab/app/pyproject.toml", "whab/app/README.md", "whab/app/LICENSE",
             "whab/app/plugins/storage"],
            cwd=ROOT.parents[1], env=environment, stdout=output)
    with tarfile.open(archive) as source:
        source.extractall(base / "candidate-source", filter="data")
    project = base / "candidate-source/whab/app"
    host_version(project)
    return project


def reference_projects(candidate, minimum, *, candidate_host=None):
    """Pair each host with a genuine compatible reference from its source artifact."""
    def supports(project, version):
        metadata = tomllib.loads((project / "pyproject.toml").read_text())["project"]
        requirements = [Requirement(value) for value in metadata.get("dependencies", ())]
        hosts = [value for value in requirements if canonicalize_name(value.name) == "whab"]
        if len(hosts) != 1 or hosts[0].marker or hosts[0].extras or hosts[0].url:
            raise ValueError("The reference backend must declare one unconditional host compatibility range.")
        return version in hosts[0].specifier

    if not supports(candidate, host_version(candidate_host if candidate_host is not None else ROOT)):
        raise ValueError("The candidate reference backend does not support the candidate host.")
    selected = candidate if supports(candidate, MINIMUM_HOST_VERSION) else minimum / "plugins/storage/sqlite"
    if not supports(selected, MINIMUM_HOST_VERSION):
        raise ValueError("The pinned reference backend does not support the minimum host.")
    candidate_version = tomllib.loads((candidate / "pyproject.toml").read_text())["project"]["version"]
    selected_version = tomllib.loads((selected / "pyproject.toml").read_text())["project"]["version"]
    if selected != candidate and selected_version == candidate_version:
        raise ValueError("Distinct reference backend source artifacts must have distinct versions.")
    return {"candidate": candidate, "minimum": selected}


def wheel_record(path):
    with zipfile.ZipFile(path) as wheel:
        metadata_name, = (name for name in wheel.namelist() if name.count("/") == 1 and name.endswith(".dist-info/METADATA"))
        metadata = BytesParser().parsebytes(wheel.read(metadata_name))
    return {"filename": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "distribution": metadata["Name"], "version": metadata["Version"]}


def wheel_for(wheels, distribution, version):
    matches = [path for path in wheels.glob("*.whl")
               if (record := wheel_record(path))["distribution"] == distribution and record["version"] == version]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one built wheel for {distribution} {version}.")
    return matches[0]


def validate_test_results(path, *, minimum_count=None):
    suites = tuple(ET.parse(path).getroot().iter("testsuite"))
    counts = {name: sum(int(suite.get(name, 0)) for suite in suites)
              for name in ("tests", "failures", "errors", "skipped")}
    minimum = MINIMUM_TEST_COUNT if minimum_count is None else minimum_count
    if counts["tests"] < minimum or any(counts[name] for name in ("failures", "errors", "skipped")):
        raise ValueError("The external toolkit suite must run completely with no failed, errored, or skipped tests.")
    return counts


def validate_toolkit_coverage(report, expected_sources, cwd, *, label="Toolkit"):
    if report.get("meta", {}).get("branch_coverage") is not True:
        raise ValueError(f"{label} coverage must measure branches.")
    measured = {(cwd / name).resolve(): record for name, record in report["files"].items()}
    expected = {Path(name).resolve() for name in expected_sources}
    if not expected or expected - measured.keys():
        raise ValueError(f"{label} source was omitted from installed-artifact coverage.")
    if any(measured[path]["summary"][name] for path in expected for name in ("missing_lines", "missing_branches")):
        raise ValueError(f"{label} source requires 100% statement and branch coverage.")


def check_host(label, host_wheel, toolkit_wheel, sqlite_wheel, wheelhouse, tests, base, environment, extra_wheels=()):
    project = base / f"external-{label}"
    project.mkdir()
    shutil.copytree(tests, project / "tests", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    home = project / "runtime"
    home.mkdir()
    environment = {**environment, "WHAB_HOME": str(home), "COVERAGE_FILE": str(project / ".coverage")}
    virtual = project / "environment"
    run([sys.executable, "-I", "-m", "venv", str(virtual)], cwd=project, env=environment)
    python = virtual / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run([str(python), "-I", "-m", "pip", "install", "--disable-pip-version-check", "--no-index",
         "--find-links", str(wheelhouse), str(host_wheel), str(toolkit_wheel), str(sqlite_wheel), *TEST_REQUIREMENTS,
         *(str(wheel) for wheel in extra_wheels)],
        cwd=project, env=environment)
    expected = {record["distribution"]: record["version"] for record in map(wheel_record, (host_wheel, toolkit_wheel, sqlite_wheel, *extra_wheels))}
    installation_path = project / "installation.json"
    run([str(python), "-I", "-c", VERIFY_INSTALLATION, str(virtual), str(ROOT.parents[1]),
         json.dumps(expected), str(installation_path)], cwd=project, env=environment)
    installation = json.loads(installation_path.read_text())
    command = [str(python), "-I", "-m", "pytest", "tests", "--import-mode=importlib", "-q",
               f"--junitxml={project / 'tests.xml'}"]
    if label == "candidate":
        configuration = project / "coverage.toml"
        configuration.write_text('[tool.coverage.run]\nbranch = true\n[tool.coverage.report]\nfail_under = 100\nexclude_lines = []\npartial_branches = []\n')
        command.extend(["--cov=whab_plugin_devkit", "--cov-branch", f"--cov-config={configuration}",
                        "--cov-fail-under=100", "--cov-report=term-missing", f"--cov-report=json:{project / 'coverage.json'}"])
    run(command, cwd=project, env=environment)
    counts = validate_test_results(project / "tests.xml")
    if label == "candidate":
        validate_toolkit_coverage(json.loads((project / "coverage.json").read_text()),
                                 installation["toolkit_source_files"], project)
    return {"host": wheel_record(host_wheel), "tests": counts, "installed_versions": installation["versions"],
            "toolkit_coverage": "100% statements and branches" if label == "candidate" else "candidate gate only"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Write artifact versions, hashes and test evidence to this JSON path.")
    args = parser.parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="whab-devkit-wheels-") as temporary:
            base = Path(temporary)
            home = base / "runtime"
            home.mkdir()
            environment = isolated_environment(home)
            toolkit = ROOT / "devtools/plugin-devkit"
            toolkit_version = tomllib.loads((toolkit / "pyproject.toml").read_text())["project"]["version"]
            sqlite = ROOT / "plugins/storage/sqlite"
            minimum = minimum_host_source(base, environment)
            candidate = copy_project(ROOT, base / "candidate-source")
            candidate_version = host_version(candidate)
            if candidate_version == MINIMUM_HOST_VERSION:
                raise ValueError("Minimum and candidate host artifacts must represent distinct supported versions.")
            references = reference_projects(copy_project(sqlite, base / "sqlite-source"), minimum)
            from packaging.version import Version

            extras = [copy_project(ROOT / "plugins/storage/json", base / "json-source"),
                      copy_project(toolkit / "fixtures/runtime-peers", base / "runtime-peers-source")] if Version(candidate_version) >= Version("2.32.0") else []
            projects = (minimum, candidate, copy_project(toolkit, base / "toolkit-source"),
                        *dict.fromkeys(references.values()), *extras)
            wheels = base / "wheelhouse"
            for project in projects:
                run([sys.executable, "-I", "-m", "pip", "wheel", "--disable-pip-version-check", "--no-deps",
                     "--wheel-dir", str(wheels), str(project)], cwd=base, env=environment)
            toolkit_wheel = wheel_for(wheels, "whab_plugin_devkit", toolkit_version)
            extra_wheels = [wheel_for(wheels, tomllib.loads((project / "pyproject.toml").read_text())["project"]["name"],
                                     tomllib.loads((project / "pyproject.toml").read_text())["project"]["version"]) for project in extras]
            sqlite_wheels = {label: wheel_for(wheels, "whab_plugin_sqlite",
                            tomllib.loads((project / "pyproject.toml").read_text())["project"]["version"])
                            for label, project in references.items()}
            hosts = {"minimum": wheel_for(wheels, "whab", MINIMUM_HOST_VERSION),
                     "candidate": wheel_for(wheels, "whab", candidate_version)}
            for label, host_wheel in hosts.items():
                run([sys.executable, "-I", "-m", "pip", "download", "--disable-pip-version-check", "--only-binary=:all:",
                     "--dest", str(wheels), str(host_wheel), str(toolkit_wheel), str(sqlite_wheels[label]), *TEST_REQUIREMENTS],
                    cwd=base, env=environment)
            revision = run(["git", "rev-parse", "HEAD"], cwd=ROOT, env=environment, capture_output=True, text=True).stdout.strip()
            source_dirty = bool(run(["git", "diff", "--name-only", "HEAD", "--", "src", "pyproject.toml", "README.md", "LICENSE"],
                                    cwd=ROOT, env=environment, capture_output=True, text=True).stdout.strip())
            report = {"minimum_host_source_commit": MINIMUM_HOST_COMMIT,
                      "candidate_host_source_commit": revision, "candidate_host_source_dirty": source_dirty,
                      "wheels": [wheel_record(path) for path in sorted(wheels.glob("*.whl"))], "hosts": {}}
            for label in ("candidate", "minimum"):
                report["hosts"][label] = check_host(label, hosts[label], toolkit_wheel, sqlite_wheels[label],
                                                   wheels, toolkit / "tests", base, environment,
                                                   extra_wheels if label == "candidate" else ())
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Toolkit wheel checks failed: {error}", file=sys.stderr)
        return 1
    print("Toolkit wheels passed minimum/candidate host compatibility with no skipped reference tests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
