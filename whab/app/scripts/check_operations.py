"""Require complete packaged operation contracts across maintained/teaching plugins."""

import argparse
import ast
import importlib.util
import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def schema_module():
    spec = importlib.util.spec_from_file_location("whab_operation_schema", ROOT / "src/whab/plugins/operation_schema.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(root=ROOT):
    schema = schema_module()
    paths = [root / "src/whab/operations.json"]
    errors = []
    for project in sorted((root / "plugins").glob("*/*/pyproject.toml")):
        metadata = tomllib.loads(project.read_text())
        identifier, entry = next(iter(metadata["project"]["entry-points"]["whab.plugins"].items()))
        package = entry.split(":")[0].split(".")[0]
        path = project.parent / "src" / package / "operations.json"
        paths.append(path)
        try:
            if path.is_symlink() or path.stat().st_size > 512 * 1024:
                raise ValueError("Documentation must be a bounded regular source resource.")
            document = schema.validate_document(json.loads(path.read_bytes()), identifier)
            if "operations.json" not in metadata["tool"]["setuptools"]["package-data"][package]:
                raise ValueError("Package data must include operations.json for offline help.")
            readme = (project.parent / "README.md").read_text()
            if "## Operation documentation" not in readme or f"](src/{package}/operations.json)" not in readme:
                raise ValueError("README must explain and link the packaged operation contract.")
            # Local action discriminators must cover every declared request action.
            documented = {row["name"] for row in document["operations"] if row["kind"] == "local"}
            feature_names = {(row["kind"], row["name"]) for row in document["operations"] if row["kind"].startswith("feature-")}
            for source in path.parent.glob("*.py"):
                tree = ast.parse(source.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "FeatureOperations":
                        for keyword in node.keywords:
                            if keyword.arg in {"queries", "commands"}:
                                kind = "feature-query" if keyword.arg == "queries" else "feature-command"
                                if {(kind, name) for name in ast.literal_eval(keyword.value)} - feature_names:
                                    raise ValueError("Registered feature operations lack documentation.")
                    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "action" and isinstance(node.annotation, ast.Subscript):
                        values = node.annotation.slice
                        if isinstance(values, ast.Tuple) and all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in values.elts):
                            missing = {item.value for item in values.elts} - documented
                            if missing:
                                raise ValueError("Local settings actions lack operation documentation: " + ", ".join(sorted(missing)))
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f"{project.relative_to(root)}: {error}")
    try:
        document = schema.validate_document(json.loads(paths[0].read_bytes()), "core")
        if any(row["kind"] == "command" and ("required" not in row["input"] or "properties" not in row["input"])
               for row in document["operations"]):
            raise ValueError("Core command inputs must declare properties and required fields.")
        ledger = ast.parse((root / "src/whab/services/ledger.py").read_text())
        service = next(node for node in ledger.body if isinstance(node, ast.ClassDef) and node.name == "LedgerService")
        for method, kind in (("query", "query"), ("command", "command")):
            function = next(node for node in service.body if isinstance(node, ast.FunctionDef) and node.name == method)
            names = set()
            for node in ast.walk(function):
                if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "operation":
                    for value in node.comparators:
                        values = value.elts if isinstance(value, (ast.Set, ast.Tuple, ast.List)) else [value]
                        names.update(item.value for item in values if isinstance(item, ast.Constant) and isinstance(item.value, str))
            if kind == "command":
                names.add("assign")  # The final dispatch branch uses the assign backend port.
            if names != {row["name"] for row in document["operations"] if row["kind"] == kind}:
                raise ValueError("Core dispatch and documented operation names differ.")
    except (OSError, ValueError) as error:
        errors.append("Core operation documentation: " + str(error))
    canonical = root / "src/whab/plugins/operation_schema.py"
    toolkit = root / "devtools/plugin-devkit/src/whab_plugin_devkit/operation_schema.py"
    if not toolkit.is_file() or canonical.read_bytes() != toolkit.read_bytes():
        errors.append("Toolkit documentation validator is stale; run scripts/check_operations.py --write")
    return paths, errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Refresh the toolkit's standalone validator for older hosts")
    args = parser.parse_args(argv)
    if args.write:
        (ROOT / "devtools/plugin-devkit/src/whab_plugin_devkit/operation_schema.py").write_bytes((ROOT / "src/whab/plugins/operation_schema.py").read_bytes())
    paths, errors = check()
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"All {len(paths)} packaged core/plugin operation contracts and examples passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
