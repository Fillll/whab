"""Build generated public-SDK projects and replay ten exact-artifact configurations."""

import argparse
import configparser
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MINIMUM_HOST_VERSION = "2.22.0"
MINIMUM_HOST_COMMIT = "bac249ca0e62638a40a6effc44af741c283e83d7"
FAMILIES = ("reporting", "branding", "file-import", "categorization")
BUILD_REQUIREMENTS = ("setuptools==84.2.1",)


@contextmanager
def artifact_workspace(destination=None):
    """Preserve reviewed wheels/replay evidence on success and test failure."""
    if destination is not None:
        destination = Path(destination).resolve()
        destination.mkdir(parents=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="whab-generated-plugins-") as temporary:
        base = Path(temporary)
        try:
            yield base
        finally:
            if destination is not None:
                wheels = base / "wheels"
                if wheels.is_dir():
                    shutil.copytree(wheels, destination / "wheels")
                for configuration in sorted(base.glob("external-*/configurations")):
                    shutil.copytree(configuration, destination / configuration.parent.name / "configurations")

VERIFY_PROJECT = """
import importlib, importlib.metadata as metadata, json, pathlib, sys
from whab_plugin_devkit.artifacts import installed_artifacts, normalized
expected, identifier, distribution, module, wheelhouse, destination = sys.argv[1:]
expected = json.loads(expected)
root = pathlib.Path(sys.prefix).resolve()
artifacts = installed_artifacts(wheelhouse, list(expected))
for name, version in expected.items():
    assert metadata.version(name) == version, name
for name in ('whab', 'whab_plugin_devkit', 'whab_plugin_sqlite', module):
    assert pathlib.Path(importlib.import_module(name).__file__).resolve().is_relative_to(root), name
entries = tuple(metadata.entry_points(group='whab.plugins'))
selected = {'sqlite', identifier}
if 'whab_plugin_json' in expected:
    selected |= {'json', 'synthetic-report-a', 'synthetic-report-b', 'synthetic-brand-a', 'synthetic-brand-b', 'synthetic-import-a', 'synthetic-import-b'}
    for name in ('whab_plugin_json', 'whab_devkit_runtime_peers'):
        assert pathlib.Path(importlib.import_module(name).__file__).resolve().is_relative_to(root), name
assert len(entries) == len(selected) and {entry.name for entry in entries} == selected
candidate, = (entry for entry in entries if entry.name == identifier)
assert normalized(candidate.dist.metadata['Name']) == normalized(distribution)
assert candidate.load().id == identifier
console, = metadata.distribution('whab_plugin_devkit').entry_points
assert (console.group, console.name, console.value) == ('console_scripts', 'whab-plugin', 'whab_plugin_devkit.cli:main')
files = [str(pathlib.Path(metadata.distribution(distribution).locate_file(path)).resolve())
         for path in metadata.files(distribution) if str(path).endswith('.py')]
assert files and all(pathlib.Path(path).is_relative_to(root) for path in files)
pathlib.Path(destination).write_text(json.dumps({'artifacts': artifacts, 'sources': files}))
"""


def candidate_registration(wheel, identifier):
    """Inspect one approved wheel registration before installing candidate code."""
    with zipfile.ZipFile(wheel) as archive:
        from email.parser import BytesParser

        from packaging.requirements import Requirement

        metadata_name, = (name for name in archive.namelist() if name.count("/") == 1 and name.endswith(".dist-info/METADATA"))
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        for text in metadata.get_all("Requires-Dist", []):
            requirement = Requirement(text)
            if requirement.url or requirement.extras:
                raise ValueError("Supply plain offline-resolvable candidate dependencies; URL/extra requirements are unsupported.")
        name, = (name for name in archive.namelist() if name.count("/") == 1 and name.endswith(".dist-info/entry_points.txt"))
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(archive.read(name).decode())
    if set(parser["whab.plugins"]) != {identifier}:
        raise ValueError("The project wheel must register exactly the requested plugin ID.")
    target = parser["whab.plugins"][identifier]
    if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", target) is None:
        raise ValueError("The project needs a plain module:descriptor entry point.")
    return target.split(":")[0].split(".")[0]


def approved_extra_wheels(source, destination):
    if source is None:
        return
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Supply a local directory of reviewed dependency wheels.")
    wheels = tuple(source.iterdir())
    if len(wheels) > 128:
        raise ValueError("The approved dependency wheelhouse exceeds its bound.")
    for path in wheels:
        if path.is_symlink() or not path.is_file() or path.suffix != ".whl" or path.stat().st_size > 100 * 1024 * 1024:
            raise ValueError("The approved dependency directory must contain only bounded regular wheels.")
        target = destination / path.name
        if target.exists() and target.read_bytes() != path.read_bytes():
            raise ValueError("Conflicting dependency wheel bytes require explicit review.")
        shutil.copy2(path, target)


def project_environment(helper, home):
    """Caller settings cannot change test selection, coverage, indexes or budgets."""
    environment = {key: value for key, value in helper.isolated_environment(home).items()
                   if not key.startswith(("PIP_", "PYTEST_", "COVERAGE_"))}
    environment["PIP_INDEX_URL"] = "https://pypi.org/simple"
    return environment


def controlled_builder(helper, base, wheels, env):
    helper.run([sys.executable, "-I", "-m", "pip", "--isolated", "download", "--index-url", "https://pypi.org/simple", "--disable-pip-version-check", "--only-binary=:all:",
                "--dest", str(wheels), *BUILD_REQUIREMENTS, "packaging==26.3"], cwd=base, env=env)
    virtual = base / "build-environment"
    helper.run([sys.executable, "-I", "-m", "venv", str(virtual)], cwd=base, env=env)
    python = virtual / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    helper.run([str(python), "-I", "-m", "pip", "install", "--no-index", "--find-links", str(wheels),
                *BUILD_REQUIREMENTS, "packaging==26.3"], cwd=base, env=env)
    return python


def build_project(helper, python, project, wheels, base, env):
    # This executes only caller-authorized project build code. Its declared
    # build dependencies must already be in the controlled local wheelhouse.
    from packaging.requirements import Requirement

    metadata = tomllib.loads((project / "pyproject.toml").read_text())
    requirements = metadata["build-system"]["requires"]
    if type(requirements) is not list or len(requirements) > 50:
        raise ValueError("The project needs bounded, explicit build requirements.")
    for text in requirements:
        requirement = Requirement(text)
        if requirement.url or requirement.extras:
            raise ValueError("URL and extra build requirements require a separate reviewed build recipe.")
    helper.run([str(python), "-I", "-m", "pip", "install", "--no-index", "--find-links", str(wheels), *requirements], cwd=base, env=env)
    before = set(wheels.glob("*.whl"))
    helper.run([str(python), "-I", "-m", "pip", "wheel", "--no-index", "--no-build-isolation", "--no-deps",
                "--wheel-dir", str(wheels), str(project)], cwd=base, env=env)
    built = set(wheels.glob("*.whl")) - before
    if len(built) != 1:
        raise ValueError("A project must produce exactly one new wheel identity.")
    return built.pop()


def configuration_manifests(output):
    manifests = [json.loads(path.read_text()) for path in sorted(output.glob("configuration-*.json"))]
    if len(manifests) != 10 or len({frozenset(manifest["peers"]) for manifest in manifests}) != 10:
        raise ValueError("Expected ten distinct selected peer configurations.")
    return manifests


def helpers():
    spec = importlib.util.spec_from_file_location("whab_devkit_wheel_checks", ROOT / "scripts/check_plugin_devkit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.MINIMUM_HOST_VERSION, module.MINIMUM_HOST_COMMIT = MINIMUM_HOST_VERSION, MINIMUM_HOST_COMMIT
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Write exact artifacts and complete replay manifests to this JSON file.")
    parser.add_argument("--artifacts-directory", type=Path, help="Preserve approved wheels and replay files in a new directory, including on failure.")
    parser.add_argument("--project", type=Path, help="Build this caller-reviewed external teaching project instead of generating the four projects.")
    parser.add_argument("--plugin", help="The external project's single public plugin entry-point ID.")
    parser.add_argument("--candidate-ref", help="Full source commit for the external CI candidate host; never downloads a host wheel from an index.")
    parser.add_argument("--extra-wheelhouse", type=Path, help="Additional caller-approved dependency wheels; no candidate dependencies are downloaded.")
    args = parser.parse_args(argv)
    try:
        if bool(args.project) != bool(args.plugin) or (args.project is not None and (len(args.plugin) > 64 or re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", args.plugin) is None)):
            raise ValueError("Supply both --project and a valid --plugin ID.")
        if args.candidate_ref is not None and args.project is None:
            raise ValueError("--candidate-ref is for the external project check.")
        helper = helpers()
        with artifact_workspace(args.artifacts_directory) as base:
            home = base / "runtime"
            home.mkdir()
            env = project_environment(helper, home)
            candidate_source = helper.source_at_revision(base, env, args.candidate_ref) if args.candidate_ref else ROOT
            projects = [helper.copy_project(candidate_source, base / "host"),
                        helper.copy_project(ROOT / "devtools/plugin-devkit", base / "toolkit"),
                        helper.copy_project(candidate_source / "plugins/storage/sqlite", base / "sqlite")]
            candidate_version = helper.host_version(projects[0])
            from packaging.version import Version

            runtime_projects = []
            if Version(candidate_version) >= Version("2.32.0"):
                runtime_projects = [helper.copy_project(candidate_source / "plugins/storage/json", base / "json"),
                                    helper.copy_project(ROOT / "devtools/plugin-devkit/fixtures/runtime-peers", base / "runtime-peers")]
                projects.extend(runtime_projects)
            if args.project is not None and candidate_version == MINIMUM_HOST_VERSION:
                raise ValueError("External CI requires distinct genuine minimum and candidate host artifacts.")
            host_versions = [candidate_version]
            minimum = projects[0]
            if candidate_version != MINIMUM_HOST_VERSION:
                minimum = helper.minimum_host_source(base, env)
                projects.append(minimum)
                host_versions.append(MINIMUM_HOST_VERSION)
            references = helper.reference_projects(projects[2], minimum, candidate_host=projects[0])
            projects.extend(project for project in dict.fromkeys(references.values()) if project not in projects)
            wheels = base / "wheels"
            wheels.mkdir()
            approved_extra_wheels(args.extra_wheelhouse, wheels)
            builder = controlled_builder(helper, base, wheels, env)
            for project in projects:
                build_project(helper, builder, project, wheels, base, env)
            all_wheels = [helper.wheel_record(path) for path in wheels.glob("*.whl")]
            toolkit_version = next(record["version"] for record in all_wheels if record["distribution"] == "whab_plugin_devkit")
            toolkit = helper.wheel_for(wheels, "whab_plugin_devkit", toolkit_version)
            reference_versions = {label: helper.tomllib.loads((project / "pyproject.toml").read_text())["project"]["version"]
                                  for label, project in references.items()}
            sqlite_wheels = {version: helper.wheel_for(wheels, "whab_plugin_sqlite",
                              reference_versions["minimum" if version == MINIMUM_HOST_VERSION else "candidate"])
                             for version in host_versions}
            host_wheels = {version: helper.wheel_for(wheels, "whab", version) for version in host_versions}
            runtime_wheels = [helper.wheel_for(wheels, helper.tomllib.loads((project / "pyproject.toml").read_text())["project"]["name"],
                                               helper.tomllib.loads((project / "pyproject.toml").read_text())["project"]["version"])
                              for project in runtime_projects]
            for version, host in host_wheels.items():
                helper.run([sys.executable, "-I", "-m", "pip", "--isolated", "download", "--index-url", "https://pypi.org/simple", "--disable-pip-version-check", "--only-binary=:all:", "--dest", str(wheels),
                            str(host), str(toolkit), str(sqlite_wheels[version]), *helper.TEST_REQUIREMENTS], cwd=base, env=env)
            bootstrap = base / "generator-environment"
            helper.run([sys.executable, "-I", "-m", "venv", str(bootstrap)], cwd=base, env=env)
            generator_python = bootstrap / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            subprocess.run([str(generator_python), "-I", "-m", "pip", "install", "--no-index", "--find-links", str(wheels),
                            str(host_wheels[candidate_version]), str(toolkit)], cwd=base, env=env, check=True)
            subprocess.run([str(generator_python), "-I", "-c",
                            "import whab_plugin_devkit,pathlib,sys; assert pathlib.Path(whab_plugin_devkit.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve())"],
                           cwd=base, env=env, check=True)
            generated = []
            for family in (() if args.project is not None else FAMILIES):
                identifier = "generated-" + family
                project = base / identifier
                subprocess.run([str(generator_python), "-I", "-m", "whab_plugin_devkit.cli", "new", identifier, "--capability", family,
                                "--directory", str(project)], cwd=base, env=env, check=True)
                candidate = build_project(helper, builder, project, wheels, base, env)
                generated.append((family, identifier, project, candidate))
            if args.project is not None:
                project = args.project.resolve()
                if not (project / "tests").is_dir():
                    raise ValueError("The external project needs its own fictional behavioral tests directory.")
                generated.append(("external", args.plugin, project, build_project(helper, builder, project, wheels, base, env)))
            revision = helper.run(["git", "rev-parse", "HEAD"], cwd=ROOT, env=env, capture_output=True, text=True).stdout.strip()
            dirty = bool(helper.run(["git", "status", "--porcelain", "--untracked-files=all", "--",
                                     "devtools/plugin-devkit", "scripts/check_plugin_generation.py", "scripts/check_plugin_devkit.py"],
                                    cwd=ROOT, env=env, capture_output=True, text=True).stdout.strip())
            evidence = {"minimum_host_commit": MINIMUM_HOST_COMMIT, "host_versions": host_versions,
                        "candidate_host_commit": args.candidate_ref or revision, "tooling_source_commit": revision,
                        "tooling_source_dirty": dirty,
                        "wheels": [helper.wheel_record(path) for path in sorted(wheels.glob("*.whl"))], "projects": []}
            for version, host in host_wheels.items():
                sqlite = sqlite_wheels[version]
                sqlite_version = helper.wheel_record(sqlite)["version"]
                for family, identifier, project, candidate in generated:
                    work = base / f"external-{version}-{family}"
                    work.mkdir()
                    shutil.copytree(project / "tests", work / "tests")
                    python_root = work / "environment"
                    helper.run([sys.executable, "-I", "-m", "venv", str(python_root)], cwd=work, env=env)
                    python = python_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                    module = candidate_registration(candidate, identifier)
                    candidate_record = helper.wheel_record(candidate)
                    command = [str(python), "-I", "-m", "pip", "install", "--no-index", "--find-links", str(wheels),
                               str(host), str(toolkit), str(sqlite), str(candidate) + "[test]", *helper.TEST_REQUIREMENTS,
                               *(str(wheel) for wheel in runtime_wheels if Version(version) >= Version("2.32.0"))]
                    subprocess.run(command, cwd=work, env=env, check=True)
                    subprocess.run([str(python), "-I", "-m", "pip", "check"], cwd=work, env=env, check=True)
                    expected = {"whab": version, "whab_plugin_devkit": toolkit_version,
                                "whab_plugin_sqlite": sqlite_version, candidate_record["distribution"]: candidate_record["version"]}
                    if Version(version) >= Version("2.32.0"):
                        expected.update({record["distribution"]: record["version"] for record in map(helper.wheel_record, runtime_wheels)})
                    installation = work / "installation.json"
                    subprocess.run([str(python), "-I", "-c", VERIFY_PROJECT, json.dumps(expected), identifier,
                                    candidate_record["distribution"], module, str(wheels), str(installation)], cwd=work, env=env, check=True)
                    coverage_config = work / "coverage.toml"
                    coverage_config.write_text('[tool.coverage.run]\nbranch = true\n[tool.coverage.report]\nfail_under = 100\nexclude_lines = []\npartial_branches = []\n')
                    pytest_config = work / "pytest.ini"
                    pytest_config.write_text("[pytest]\n")
                    subprocess.run([str(python), "-I", "-m", "pytest", "tests", "--import-mode=importlib", "-q", "-c", str(pytest_config),
                                    f"--junitxml={work / 'tests.xml'}", f"--cov={module}", "--cov-branch", "--cov-fail-under=100",
                                    f"--cov-config={coverage_config}", "--cov-report=term-missing", f"--cov-report=json:{work / 'coverage.json'}"],
                                   cwd=work, env=env, check=True)
                    counts = helper.validate_test_results(work / "tests.xml", minimum_count=1)
                    installed = json.loads(installation.read_text())
                    helper.validate_toolkit_coverage(json.loads((work / "coverage.json").read_text()), installed["sources"], work, label="Candidate plugin")
                    output = work / "configurations"
                    subprocess.run([str(python), "-I", "-m", "whab_plugin_devkit.cli", "check", "--plugin", identifier,
                                    "--runtime", "--combinations", "10", "--seed", "2026", "--wheelhouse", str(wheels), "--output", str(output)],
                                   cwd=work, env=env, check=True)
                    manifests = configuration_manifests(output)
                    for path in sorted(output.glob("configuration-*.json")):
                        subprocess.run([str(python), "-I", "-m", "whab_plugin_devkit.cli", "replay", str(path), "--wheelhouse", str(wheels)],
                                       cwd=work, env=env, check=True, stdout=subprocess.DEVNULL)
                    evidence["projects"].append({"family": family, "host": version, "candidate": helper.wheel_record(candidate),
                                                 "tests": counts, "coverage": "100% statements and branches",
                                                 "manifests": manifests, "results": json.loads((output / "results.json").read_text())})
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(evidence, indent=2) + "\n")
            if args.artifacts_directory:
                (args.artifacts_directory / "report.json").write_text(json.dumps(evidence, indent=2) + "\n")
            print(f"Validated {len(generated)} independent plugin project(s); each passed installed behavioral tests, 100% plugin coverage, ten distinct configurations and exact-artifact replay on {len(host_versions)} host artifact(s).")
    except Exception as error:
        print(f"Plugin validation failed ({type(error).__name__}); ensure every required runtime/test/build wheel is present in the approved wheelhouse, review the fictional test failure and preserve its replay manifests.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
