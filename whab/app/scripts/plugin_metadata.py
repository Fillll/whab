"""Generate curated plugin metadata from source data without importing plugins."""

import argparse
import ast
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MAX_SOURCE_BYTES = 2 * 1024 * 1024
_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*\Z")
_MODULE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z")


def normalized(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def source_text(path, root):
    """Read bounded source files only; reject links and private/runtime paths."""
    root, path = Path(root).resolve(), Path(path).absolute()
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink() or part in {"data", "budgets", "tls", ".venv", "work", "cache", "node_modules"}:
            raise ValueError("Static metadata must use repository source without symlink components or private/runtime paths.")
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Static metadata source exceeds its supported bound.")
    return path.read_text(encoding="utf-8")


def _assignment(tree, name):
    matches = [node.value for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
    if len(matches) != 1:
        raise ValueError("Static metadata requires one explicit source declaration.")
    return matches[0]


def _literal(node, bindings=None):
    if isinstance(node, ast.Name) and bindings and node.id in bindings:
        return bindings[node.id]
    return ast.literal_eval(node)


def _constructor(node, name, fields, *, bindings=None):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != name or len(node.args) > len(fields) or any(keyword.arg is None for keyword in node.keywords):
        raise ValueError("Use explicit supported descriptor metadata for static inspection.")
    result = dict(zip(fields, node.args))
    for keyword in node.keywords:
        if keyword.arg in result:
            raise ValueError("Static descriptor fields cannot be repeated.")
        result[keyword.arg] = keyword.value
    return result


def _capability(node, bindings=None):
    values = _constructor(node, "Capability", ("name", "version", "composition"))
    return {"name": _literal(values["name"], bindings),
            "version": _literal(values["version"], bindings) if "version" in values else 1,
            "composition": _literal(values["composition"], bindings) if "composition" in values else "additive"}


def _capabilities(node):
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_capability(item) for item in node.elts]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tuple" and len(node.args) == 1 and not node.keywords:
        generator = node.args[0]
        if isinstance(generator, ast.GeneratorExp) and len(generator.generators) == 1:
            loop = generator.generators[0]
            if isinstance(loop.target, ast.Name) and not loop.ifs and not loop.is_async:
                return [_capability(generator.elt, {loop.target.id: value}) for value in _literal(loop.iter)]
    raise ValueError("Static capabilities must be a literal sequence of SDK Capability declarations.")


def _requirements(node):
    if not isinstance(node, (ast.Tuple, ast.List)):
        raise ValueError("Static requirements must use a literal SDK Requirement sequence.")
    records = []
    for item in node.elts:
        values = _constructor(item, "Requirement", ("name", "min_version", "max_version"))
        record = {"name": _literal(values["name"]),
                  "min_version": _literal(values["min_version"]) if "min_version" in values else 1}
        if "max_version" in values and _literal(values["max_version"]) is not None:
            record["max_version"] = _literal(values["max_version"])
        records.append(record)
    return records


def _hook_capabilities(root):
    """Read the SDK's canonical hook map rather than maintain another inventory."""
    tree = ast.parse(source_text(Path(root) / "whab/app/src/whab/plugins/contracts.py", root))
    function, = (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "contract_for")
    loop, = (node for node in function.body if isinstance(node, ast.For) and isinstance(node.target, ast.Tuple)
             and [part.id for part in node.target.elts if isinstance(part, ast.Name)] == ["attribute", "name", "composition"])
    return ast.literal_eval(loop.iter)


def _interface_adapter(node):
    """Recognize the literal SDK adapter without executing its module."""
    try:
        interface = _constructor(node, "Interface", ("factory", "adapter"))
        if set(interface) != {"adapter"}:
            return False
        values = {name: _literal(value) for name, value in _constructor(
            interface["adapter"], "RemoteLedgerInterface",
            ("protocol_version", "token_setting", "queries", "commands")).items()}
        fields = {"protocol_version": 1, "token_setting": "SERVER_TOKEN", "queries": True, "commands": True}
        if values.keys() - fields.keys():
            return False
        fields.update(values)
        return (type(fields["protocol_version"]) is int and fields["protocol_version"] == 1
                and type(fields["token_setting"]) is str and len(fields["token_setting"]) <= 100
                and re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*", fields["token_setting"]) is not None
                and type(fields["queries"]) is bool and type(fields["commands"]) is bool
                and (fields["queries"] or fields["commands"]))
    except (ValueError, TypeError):
        return False


def _visual_adapter(node):
    try:
        names = ("templates", "static", "build_context", "budget_months", "lightweight_transactions",
                 "lightweight_accounts", "transaction_page_size", "resources", "render", "view_protocol")
        fields = _constructor(node, "UserInterface", names)
        if fields.keys() - set(names) or "build_context" not in fields:
            return False
        protocol = _literal(fields["view_protocol"]) if "view_protocol" in fields else 0
        if type(protocol) is not int or protocol != 1 or "render" in fields and _literal(fields["render"]) is not None:
            return False
        for name, default, lower, upper in (("budget_months", 1, 1, 12), ("transaction_page_size", 0, 0, 1000)):
            value = _literal(fields[name]) if name in fields else default
            if type(value) is not int or not lower <= value <= upper:
                return False
        return all(name not in fields or type(_literal(fields[name])) is bool
                   for name in ("lightweight_transactions", "lightweight_accounts"))
    except (ValueError, TypeError, KeyError):
        return False


def _remote_client_adapter(node, identifier):
    """Recognize only public literal configuration-key metadata, never values."""
    try:
        fields = {"protocol_version": 1, "url_setting": "CLIENT_URL", "token_setting": "CLIENT_TOKEN",
                  "budget_setting": "CLIENT_BUDGET_ID"}
        values = {name: _literal(value) for name, value in _constructor(
            node, "RemoteLedgerClient", tuple(fields)).items()}
        if values.keys() - fields.keys():
            return False
        fields.update(values)
        names = [fields[name] for name in ("url_setting", "token_setting", "budget_setting")]
        prefix = identifier.upper().replace("-", "_") + "_"
        return (type(fields["protocol_version"]) is int and fields["protocol_version"] == 1
                and all(type(name) is str and len(name) <= 100 and name.startswith(prefix)
                        and re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*", name) is not None for name in names)
                and len(set(names)) == 3)
    except (ValueError, TypeError):
        return False


def descriptor_metadata(module_path, attribute, root):
    tree = ast.parse(source_text(module_path, root))
    descriptor = _constructor(_assignment(tree, attribute), "Plugin", ("id", "display_name", "api_version"))
    identifier, display = _literal(descriptor["id"]), _literal(descriptor["display_name"])
    present = {name for name, value in descriptor.items() if not isinstance(value, ast.Constant) or value.value is not None}
    contract_node = descriptor.get("contract")
    legacy = contract_node is None or isinstance(contract_node, ast.Constant) and contract_node.value is None
    contract = {} if legacy else _constructor(contract_node, "PluginContract", ())
    authority = _literal(contract["authority"]) if "authority" in contract else (
        ("remote" if "budget_connection" in present else "unknown") if legacy else "local"
    )
    capabilities = {name: {"name": name, "version": 1, "composition": composition}
                    for hook, name, composition in _hook_capabilities(root) if hook in present}
    if "storage" in present and authority != "remote":
        capabilities.update({name: {"name": name, "version": 1, "composition": "singleton"}
                             for name in ("provider.state", "provider.sync")})
    for declared in _capabilities(contract["provides"]) if "provides" in contract else ():
        if declared["name"] in capabilities and declared["composition"] != capabilities[declared["name"]]["composition"]:
            raise ValueError("Static capability composition conflicts with the SDK hook contract.")
        capabilities[declared["name"]] = declared
    lifecycle = _literal(contract["lifecycle"]) if "lifecycle" in contract else "restart"
    state_version = _literal(contract["state_version"]) if "state_version" in contract else 1
    upgrades = _literal(contract["state_upgrades"]) if "state_upgrades" in contract else ()
    unsupported = present - {"id", "display_name", "api_version", "contract", "insights", "export"}
    live = (not legacy and lifecycle == "live" and bool({"insights", "export"} & present)
            and not unsupported and state_version == 1 and not upgrades
            and set(capabilities) <= {"reporting.insights", "reporting.export"}
            and all(row["version"] == 1 for row in capabilities.values()))
    optional_hooks = {"insights", "export", "bank_logos", "merchant_logos", "file_import", "settings", "branding_factory", "interface"}
    optional = (
        not legacy and lifecycle == "live" and state_version == 1 and not upgrades
        and bool(capabilities) and set(capabilities) <= {"reporting.insights", "reporting.export", "branding.bank", "branding.merchant", "ingestion.file", "interface.api"}
        and all(row["version"] == 1 for row in capabilities.values())
        and not (present - {"id", "display_name", "api_version", "contract"} - optional_hooks)
        and ("branding_factory" not in present or set(capabilities) <= {"branding.bank", "branding.merchant"})
        and ("settings" not in present or "file_import" not in present and bool({"bank_logos", "merchant_logos"} & present))
        and ("interface" not in present or _interface_adapter(descriptor["interface"]))
    )
    categorization = (
        not legacy and lifecycle == "live" and "categorization" in present
        and set(capabilities) == {"categorization"} and state_version == 1 and not upgrades
        and all(row["version"] == 1 for row in capabilities.values())
        and not (present - {"id", "display_name", "api_version", "contract", "categorization"})
    )
    visual = (
        not legacy and lifecycle == "live" and "ui" in present
        and set(capabilities) == {"interface.ui"} and state_version == 1 and not upgrades
        and all(row["version"] == 1 for row in capabilities.values())
        and not (present - {"id", "display_name", "api_version", "contract", "ui"})
        and _visual_adapter(descriptor["ui"])
    )
    storage = (
        not legacy and lifecycle == "live" and authority == "local" and "storage" in present
        and state_version == 1 and not upgrades
        and {"storage.worker", "storage.authority", "storage.commands"} <= set(capabilities) <= {
            "ledger.read", "ledger.mutate", "provider.state", "provider.sync", "receipts",
            "transfer.neutral", "transfer.prepare", "transfer.local", "storage.authority",
            "storage.commands", "storage.replica", "storage.worker",
        }
        and all(row["version"] == 1 for row in capabilities.values())
        and not (present - {"id", "display_name", "api_version", "contract", "storage", "storage_payload_paths"})
    )
    remote_client = (
        not legacy and lifecycle == "live" and authority == "remote" and state_version == 1 and not upgrades
        and "storage" in present and not (present - {"id", "display_name", "api_version", "contract", "storage"})
        and set(capabilities) == {"ledger.read", "ledger.mutate"}
        and all(row["version"] == 1 and row["composition"] == "singleton" for row in capabilities.values())
        and _remote_client_adapter(descriptor["storage"], identifier)
    )
    if lifecycle == "live" and not (live or optional or categorization or visual or storage or remote_client):
        raise ValueError("Live metadata requires an explicit supported capability adapter.")
    return {"id": identifier, "display_name": display, "api_version": _literal(descriptor["api_version"]) if "api_version" in descriptor else 1,
            "capabilities": [capabilities[name] for name in sorted(capabilities)],
            "requires": _requirements(contract["requires"]) if "requires" in contract else [],
            "conflicts": list(_literal(contract["conflicts"])) if "conflicts" in contract else [],
            "lifecycle": lifecycle, "legacy": legacy, "authority": authority,
            "state_version": state_version, "state_upgrades": [list(edge) for edge in upgrades],
            "live_support": ("declarative-remote-client-worker" if remote_client else
                             "declarative-api-worker" if optional and "interface" in present else
                             "stateless-report-worker" if live else "stateless-optional-worker" if optional
                             else "categorization-worker" if categorization else "visual-ui-worker" if visual else "local-storage-worker" if storage else "restart-required")}


def source_inventory(root=ROOT):
    """One canonical inventory for docs, generated catalogs and source checks."""
    root = Path(root)
    application = root / "whab/app"
    host = tomllib.loads(source_text(application / "pyproject.toml", root))["project"]
    bundled = {normalized(re.split(r"[\s<>=!~;\[]", value, maxsplit=1)[0]) for value in host["optional-dependencies"]["bundled"]}
    packages, distributions = [], set()
    for path in sorted((application / "plugins").glob("*/*/pyproject.toml")):
        metadata = tomllib.loads(source_text(path, root))
        project = metadata["project"]
        name = normalized(project["name"])
        entry_points = project.get("entry-points", {}).get("whab.plugins", {})
        if len(entry_points) != 1 or name in distributions:
            raise ValueError("Every curated distribution must have one unique plugin entry point.")
        identifier, entry = next(iter(entry_points.items()))
        if type(identifier) is not str or _IDENTIFIER.fullmatch(identifier) is None or type(entry) is not str or entry.count(":") != 1:
            raise ValueError("Invalid static plugin identity.")
        module, attribute = entry.split(":")
        if not _MODULE.fullmatch(module) or not _MODULE.fullmatch(attribute) or "." in attribute:
            raise ValueError("Static entry points must name one explicit module attribute.")
        module_path = path.parent / "src" / Path(*module.split("."))
        module_path = module_path.with_suffix(".py") if module_path.with_suffix(".py").exists() else module_path / "__init__.py"
        descriptor = descriptor_metadata(module_path, attribute, root)
        declared = metadata.get("tool", {}).get("whab", {}).get("plugin")
        expected = {key.replace("_", "-"): descriptor[key] for key in (
            "api_version", "capabilities", "requires", "conflicts", "lifecycle", "legacy", "authority", "state_version", "state_upgrades",
        )}
        if type(declared) is not dict or declared.keys() != expected.keys() | {"support"} or any(json.dumps(declared[key], sort_keys=True) != json.dumps(value, sort_keys=True) for key, value in expected.items()):
            raise ValueError(f"{path.relative_to(root)}: static declarations differ from the source descriptor.")
        support = "maintained" if name in bundled else "teaching"
        if declared["support"] != support or descriptor["id"] != identifier:
            raise ValueError("Static support/entry-point identity differs from the canonical supported set.")
        requirements = [value for value in project.get("dependencies", ()) if re.match(r"(?i)^whab(?:\s|[<>=!~;\[]|$)", value)]
        if len(requirements) != 1:
            raise ValueError("Curated plugin packages require one explicit WHAB compatibility declaration.")
        distributions.add(name)
        packages.append({**descriptor, "distribution": project["name"], "version": project["version"],
                         "entry_point": {"group": "whab.plugins", "name": identifier, "value": entry},
                         "requires_python": project["requires-python"], "requires_host": requirements[0],
                         "support": support, "source": path.parent.relative_to(root).as_posix()})
    if len({row["id"] for row in packages}) != len(packages) or {normalized(row["distribution"]) for row in packages if row["support"] == "maintained"} != bundled:
        raise ValueError("Curated source metadata differs from the supported bundled distributions.")
    return tuple(sorted(packages, key=lambda row: row["id"]))


def host_version(root):
    tree = ast.parse(source_text(Path(root) / "whab/app/src/whab/__init__.py", root))
    return _literal(_assignment(tree, "__version__"))


def build_catalog(root=ROOT):
    root = Path(root)
    configuration = tomllib.loads(source_text(root / "whab/app/plugins/catalog.toml", root))
    publisher = configuration["publisher"]
    verifications = configuration.get("verification", [])
    records = []
    for row in source_inventory(root):
        record = dict(row)
        record.update(maintainer=publisher["maintainer"], repository=publisher["repository"], security=publisher["security"],
                      documentation=f'{publisher["repository"]}/blob/{publisher["documentation_ref"]}/{row["source"]}/README.md')
        record["verifications"] = [item["id"] for item in verifications
                                   if item["artifacts"].get(row["distribution"]) == row["version"]]
        records.append(record)
    return {"format_version": 1, "host_version": host_version(root), "publisher": publisher,
            "plugins": records, "verifications": verifications}


def catalog_markdown(catalog):
    lines = ["# Curated plugin catalog", "", "Generated from package metadata, explicit static declarations and reviewed verification evidence.",
             "Catalog inspection imports no plugin and installs nothing. Installed plugins remain trusted Python code; workers contain failures without providing a security sandbox.", "",
             "Inspect the packaged data through `GET /api/plugins/catalog` or `whab.plugins.catalog.catalog_snapshot()`. The endpoint retains the host's HTTPS and local-access guards and remains available while a budget transition is gated. A missing or damaged catalog returns a fixed diagnostic; discovery and installation are never fallback actions.", "",
             "See [the canonical supported set](../app/plugins/README.md#supported-plugins) and [security reporting](../../SECURITY.md).", "",
             "| Plugin | Distribution version | Host requirement | Lifecycle | Support |", "| --- | --- | --- | --- | --- |"]
    for row in catalog["plugins"]:
        lifecycle = "Legacy; restart required" if row["legacy"] else row["live_support"]
        lines.append(f'| [{row["id"]}]({row["documentation"]}) | `{row["distribution"]} {row["version"]}` | `{row["requires_host"]}` | {lifecycle} | {row["support"]} |')
    lines.extend(["", "Explicit stateless report, branding, file-parser, declarative API interface, pure categorization and participating visual descriptors advertise their supported live-worker adapters. Declarative remote-client workers return metadata only; the host retains their exact HTTPS authority, credentials, transport and financial intent. SQLite and JSON advertise compatible local storage workers; backend migration and copies remain explicit authority operations. Other entries require restart; activation is not inferred from package installation.", "",
                  "## Recorded verification", "", "Evidence applies only to its exact host, artifacts and selection. Installed-only artifacts are not proof that their capabilities executed. Draft PR/source verification is not a release or deployment claim.", ""])
    for item in catalog["verifications"]:
        lines.extend([f'### {item["id"]}', "", f'Verified {item["date"]}; host `{item["host_version"]}`. Selected: ' + ", ".join(f'`{value}`' for value in item["selection"]) + ".", "", item["scope"], "",
                      "Tests: " + ", ".join(f'`{value}`' for value in item["tests"]) + ".", "",
                      "Review: " + ", ".join(f'[PR #{value.rsplit("/", 1)[-1]}]({value})' for value in item["pull_requests"]) + ".", ""])
    return "\n".join(lines)


def generated_outputs(root=ROOT):
    root = Path(root)
    catalog = build_catalog(root)
    return {root / "whab/app/src/whab/plugins/catalog.json": json.dumps(catalog, sort_keys=True, indent=2) + "\n",
            root / "whab/docs/plugin-catalog.md": catalog_markdown(catalog)}


def validate_generated(root=ROOT):
    return [f"{path.relative_to(root)}: generated static plugin metadata is stale; run scripts/plugin_metadata.py --write"
            for path, expected in generated_outputs(root).items()
            if not path.is_file() or source_text(path, root) != expected]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Regenerate the curated JSON resource and compatibility documentation.")
    arguments = parser.parse_args(argv)
    if arguments.write:
        for path, expected in generated_outputs().items():
            path.write_text(expected)
        return 0
    errors = validate_generated()
    if errors:
        parser.exit(1, "\n".join(errors) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
