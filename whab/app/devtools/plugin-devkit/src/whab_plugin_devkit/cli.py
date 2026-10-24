"""Author-controlled project generation and exact-artifact configuration replay."""

import argparse
import json
import sys
from importlib import metadata
from pathlib import Path

from packaging.version import Version

from .artifacts import installed_artifacts
from .configurations import _parse, replay_configuration, sample_configurations, validate_manifest
from .generator import FAMILIES, generate_project


def _selected(plugin_id, wheelhouse, *, runtime=False):
    entries = metadata.entry_points(group="whab.plugins")
    selected = []
    required = ["whab", "whab_plugin_devkit"]
    if runtime and Version(metadata.version("whab")) >= Version("2.32.0"):
        required.extend(["whab_plugin_json", "whab_devkit_runtime_peers"])
    for name in (plugin_id, "sqlite"):
        matches = [entry for entry in entries if entry.name == name]
        if len(matches) != 1:
            raise ValueError("Install exactly one candidate and the reviewed SQLite reference before checking.")
        selected.append(matches[0])
        required.append(matches[0].dist.metadata["Name"])
    artifacts = installed_artifacts(wheelhouse, required)
    # Only these two explicit registrations execute, after installed bytes match.
    return selected[0].load(), selected[1].load(), artifacts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("new", help="Create a complete public-SDK teaching project.")
    new.add_argument("plugin_id")
    new.add_argument("--capability", choices=FAMILIES, required=True)
    new.add_argument("--directory", type=Path)
    new.add_argument("--ci-ref", help="Add reusable CI pinned to this published full WHAB source commit.")
    check = commands.add_parser("check", help="Run distinct constrained resolver/hook configurations.")
    check.add_argument("--plugin", required=True)
    check.add_argument("--combinations", type=int, default=10)
    check.add_argument("--seed", type=int, default=42)
    check.add_argument("--wheelhouse", type=Path, required=True)
    check.add_argument("--output", type=Path, default=Path("whab-plugin-check"))
    check.add_argument("--runtime", action="store_true", help="Also run the versioned native HTTPS lifecycle/migration/copy profile where supported.")
    check.add_argument("--bank-fixture", choices=("fictional-v1",), help="Explicitly authorize this reviewed offline bank fixture; never supply a real provider.")
    replay = commands.add_parser("replay", help="Replay one manifest against exact installed approved artifacts.")
    replay.add_argument("manifest", type=Path)
    replay.add_argument("--wheelhouse", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "new":
            project = generate_project(arguments.plugin_id, arguments.capability, arguments.directory or Path(arguments.plugin_id), ci_ref=arguments.ci_ref)
            print(f"Created {project}")
            return 0
        if arguments.command == "check":
            if arguments.bank_fixture is not None and not arguments.runtime:
                raise ValueError("A bank fixture requires the explicit native runtime profile.")
            if arguments.runtime:
                from .runtime_profiles import (
                    replay_runtime_configuration,
                    sample_runtime_configurations,
                )

                candidate, reference, artifacts = _selected(arguments.plugin, arguments.wheelhouse, runtime=True)
                sample, execute = sample_runtime_configurations, replay_runtime_configuration
            else:
                candidate, reference, artifacts = _selected(arguments.plugin, arguments.wheelhouse)
                sample, execute = sample_configurations, replay_configuration
            manifests = sample(candidate, reference, artifacts,
                               combinations_count=arguments.combinations, seed=arguments.seed,
                               **({"bank_fixture": arguments.bank_fixture} if arguments.runtime else {}))
            arguments.output.mkdir(parents=True, exist_ok=False)
            results = []
            for manifest in manifests:
                path = arguments.output / f"configuration-{manifest['index']:02d}.json"
                path.write_text(json.dumps(manifest, indent=2) + "\n")
                results.append(execute(manifest, candidate, reference, **({"wheelhouse": arguments.wheelhouse} if arguments.runtime else {})))
            (arguments.output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            runtime_passes = sum(result.get("runtime", {}).get("status") == "passed" for result in results)
            print(f"Passed {len(results)} distinct resolver/public-hook configurations; {runtime_passes} native runtime profiles passed.")
            return 0
        with arguments.manifest.open("rb") as source:
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("The configuration manifest exceeds its bound.")
        value = _parse(raw)
        if type(value) is dict and value.get("formatVersion") == 2:
            from .runtime_profiles import replay_runtime_configuration, validate_runtime_manifest

            manifest, execute = validate_runtime_manifest(value), replay_runtime_configuration
            candidate, reference, artifacts = _selected(manifest["candidate"], arguments.wheelhouse, runtime=True)
        else:
            manifest, execute = validate_manifest(value), replay_configuration
            candidate, reference, artifacts = _selected(manifest["candidate"], arguments.wheelhouse)
        if artifacts != manifest["artifacts"]:
            raise ValueError("Replay requires the exact recorded installed wheels and hashes.")
        print(json.dumps(execute(manifest, candidate, reference, **({"wheelhouse": arguments.wheelhouse} if manifest["formatVersion"] == 2 else {})), indent=2))
        return 0
    except Exception:
        print("Plugin check failed. Preserve its manifest, verify the approved installed artifacts and run the project's fictional regression tests. No private exception payload is displayed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
