"""Build generated public-SDK projects and replay ten exact-artifact configurations."""

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MINIMUM_HOST_VERSION = "2.22.0"
MINIMUM_HOST_COMMIT = "bac249ca0e62638a40a6effc44af741c283e83d7"
FAMILIES = ("reporting", "branding", "file-import", "categorization")


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

VERIFY = """
import importlib, importlib.metadata as metadata, json, pathlib, sys
root = pathlib.Path(sys.prefix).resolve()
expected = json.loads(sys.argv[1])
for name, version in expected.items():
    module = importlib.import_module(name)
    assert pathlib.Path(module.__file__).resolve().is_relative_to(root), name
    assert metadata.version(name) == version, name
assert {entry.name for entry in metadata.entry_points(group='whab.plugins')} == {'sqlite',sys.argv[2]}
entry, = metadata.distribution('whab_plugin_devkit').entry_points
assert (entry.group,entry.name,entry.value) == ('console_scripts','whab-plugin','whab_plugin_devkit.cli:main')
"""


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
    args = parser.parse_args(argv)
    try:
        helper = helpers()
        with artifact_workspace(args.artifacts_directory) as base:
            home = base / "runtime"
            home.mkdir()
            env = helper.isolated_environment(home)
            projects = [helper.copy_project(ROOT, base / "host"),
                        helper.copy_project(ROOT / "devtools/plugin-devkit", base / "toolkit"),
                        helper.copy_project(ROOT / "plugins/storage/sqlite", base / "sqlite")]
            candidate_version = helper.host_version(projects[0])
            host_versions = [candidate_version]
            minimum = projects[0]
            if candidate_version != MINIMUM_HOST_VERSION:
                minimum = helper.minimum_host_source(base, env)
                projects.append(minimum)
                host_versions.append(MINIMUM_HOST_VERSION)
            references = helper.reference_projects(projects[2], minimum)
            projects.extend(project for project in dict.fromkeys(references.values()) if project not in projects)
            wheels = base / "wheels"
            for project in projects:
                helper.run([sys.executable, "-I", "-m", "pip", "wheel", "--disable-pip-version-check", "--no-deps", "--wheel-dir", str(wheels),
                            str(project)], cwd=base, env=env)
            all_wheels = [helper.wheel_record(path) for path in wheels.glob("*.whl")]
            toolkit_version = next(record["version"] for record in all_wheels if record["distribution"] == "whab_plugin_devkit")
            toolkit = helper.wheel_for(wheels, "whab_plugin_devkit", toolkit_version)
            reference_versions = {label: helper.tomllib.loads((project / "pyproject.toml").read_text())["project"]["version"]
                                  for label, project in references.items()}
            sqlite_wheels = {version: helper.wheel_for(wheels, "whab_plugin_sqlite",
                              reference_versions["minimum" if version == MINIMUM_HOST_VERSION else "candidate"])
                             for version in host_versions}
            host_wheels = {version: helper.wheel_for(wheels, "whab", version) for version in host_versions}
            for version, host in host_wheels.items():
                helper.run([sys.executable, "-I", "-m", "pip", "download", "--disable-pip-version-check", "--only-binary=:all:", "--dest", str(wheels),
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
            for family in FAMILIES:
                identifier = "generated-" + family
                project = base / identifier
                subprocess.run([str(generator_python), "-I", "-m", "whab_plugin_devkit.cli", "new", identifier, "--capability", family,
                                "--directory", str(project)], cwd=base, env=env, check=True)
                helper.run([sys.executable, "-I", "-m", "pip", "wheel", "--disable-pip-version-check", "--no-deps", "--wheel-dir", str(wheels),
                            str(project)], cwd=base, env=env)
                generated.append((family, identifier, project))
            evidence = {"minimum_host_commit": MINIMUM_HOST_COMMIT, "host_versions": host_versions,
                        "wheels": [helper.wheel_record(path) for path in sorted(wheels.glob("*.whl"))], "projects": []}
            for version, host in host_wheels.items():
                sqlite = sqlite_wheels[version]
                sqlite_version = helper.wheel_record(sqlite)["version"]
                for family, identifier, project in generated:
                    work = base / f"external-{version}-{family}"
                    work.mkdir()
                    shutil.copytree(project / "tests", work / "tests")
                    python_root = work / "environment"
                    helper.run([sys.executable, "-I", "-m", "venv", str(python_root)], cwd=work, env=env)
                    python = python_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                    module = "whab_plugin_" + identifier.replace("-", "_")
                    candidate = helper.wheel_for(wheels, module, "0.1.0")
                    command = [str(python), "-I", "-m", "pip", "install", "--no-index", "--find-links", str(wheels),
                               str(host), str(toolkit), str(sqlite), str(candidate), *helper.TEST_REQUIREMENTS]
                    subprocess.run(command, cwd=work, env=env, check=True)
                    expected = {"whab": version, "whab_plugin_devkit": toolkit_version,
                                "whab_plugin_sqlite": sqlite_version, module: "0.1.0"}
                    subprocess.run([str(python), "-I", "-c", VERIFY, json.dumps(expected), identifier], cwd=work, env=env, check=True)
                    subprocess.run([str(python), "-I", "-m", "pytest", "tests", "--import-mode=importlib", "-q",
                                    f"--cov={module}", "--cov-branch", "--cov-fail-under=100", "--cov-report=term-missing"],
                                   cwd=work, env=env, check=True)
                    output = work / "configurations"
                    subprocess.run([str(python), "-I", "-m", "whab_plugin_devkit.cli", "check", "--plugin", identifier,
                                    "--combinations", "10", "--seed", "2026", "--wheelhouse", str(wheels), "--output", str(output)],
                                   cwd=work, env=env, check=True)
                    manifests = configuration_manifests(output)
                    for path in sorted(output.glob("configuration-*.json")):
                        subprocess.run([str(python), "-I", "-m", "whab_plugin_devkit.cli", "replay", str(path), "--wheelhouse", str(wheels)],
                                       cwd=work, env=env, check=True, stdout=subprocess.DEVNULL)
                    evidence["projects"].append({"family": family, "host": version, "candidate": helper.wheel_record(candidate),
                                                 "manifests": manifests, "results": json.loads((output / "results.json").read_text())})
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(evidence, indent=2) + "\n")
            if args.artifacts_directory:
                (args.artifacts_directory / "report.json").write_text(json.dumps(evidence, indent=2) + "\n")
            print(f"Generated {len(generated)} independent plugin families; each passed installed tests, 100% plugin coverage, ten distinct configurations and exact-artifact replay on {len(host_versions)} host artifact(s).")
    except Exception as error:
        print(f"Generated plugin validation failed ({type(error).__name__}); review the fictional test failure and preserve its replay manifests.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
