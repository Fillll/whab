"""Keep core independent and each contributor plugin behind public contracts."""

import ast
import json
import tomllib
from importlib.util import resolve_name
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "whab"
PLUGIN_PACKAGES = tuple(
    path
    for path in sorted((ROOT / "plugins").glob("*/*/src/*"))
    if (path / "__init__.py").is_file()
)
PLUGIN_NAMES = tuple(path.name for path in PLUGIN_PACKAGES)
# Reviewed public peer contracts. Private implementation modules remain forbidden.
PUBLIC_PLUGIN_APIS = {"whab_plugin_ai_credentials": ("api",), "whab_plugin_subscriptions": ("api",)}


def public_dependency_import(package, module):
    manifest = json.loads((package / "plugin.json").read_text())
    dependencies = {row["id"] for row in manifest["dependencies"]}
    requirements = tomllib.loads((package.parents[1] / "pyproject.toml").read_text())["project"]["dependencies"]
    for peer, modules in PUBLIC_PLUGIN_APIS.items():
        plugin_id = peer.removeprefix("whab_plugin_").replace("_", "-")
        if plugin_id in dependencies and any(value.startswith(peer + ">=") for value in requirements):
            if module == peer or any(module == f"{peer}.{name}" or module.startswith(f"{peer}.{name}.") for name in modules):
                return True
    return False


def imported_modules(path):
    source = next(parent for parent in path.parents if parent.name == "src")
    relative = path.relative_to(source).with_suffix("")
    package = ".".join(relative.parts[:-1])
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            module = resolve_name(module, package) if node.level else module
            yield module
            # Also catch package-level forms such as `from whab import storage`.
            yield from (f"{module}.{alias.name}" for alias in node.names if alias.name != "*")


@pytest.mark.parametrize(
    ("owner", "forbidden"),
    [
        (
            "domain",
            ("web", "storage", "integrations", "interfaces", "plugins", "config", "app"),
        ),
        ("storage/bank.py", ("web", "interfaces", "integrations", "config", "app")),
        ("web", ("integrations", "interfaces")),
        ("app.py", ("integrations", "interfaces")),
        ("plugins", ("integrations", "interfaces")),
    ],
)
def test_core_modules_do_not_import_higher_layers_or_specific_providers(owner, forbidden):
    location = PACKAGE / owner
    paths = [location] if location.is_file() else sorted(location.rglob("*.py"))
    assert paths, f"The {owner} package must exist"
    prefixes = tuple(f"whab.{name}" for name in forbidden) + PLUGIN_NAMES + (
        "whab.storage.sqlite", "sqlite3",
    )
    violations = [
        f"{path.relative_to(ROOT)} imports {module}"
        for path in paths
        for module in imported_modules(path)
        if any(module == prefix or module.startswith(prefix + ".") for prefix in prefixes)
        or (
            owner in {"domain", "web", "app.py", "plugins", "storage/bank.py"}
            and (
                module.startswith("whab.storage.")
                and not (module == "whab.storage.bank" or module.startswith("whab.storage.bank."))
            )
        )
    ]
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("package", PLUGIN_PACKAGES, ids=lambda path: path.name)
def test_plugins_use_public_host_and_declared_public_peer_contracts(package):
    allowed = ("whab.domain", "whab.plugins", "whab.__version__")
    other_plugins = tuple(name for name in PLUGIN_NAMES if name != package.name)
    violations = [
        f"{path.relative_to(ROOT)} imports {module}"
        for path in sorted(package.rglob("*.py"))
        for module in imported_modules(path)
        if (
            module.startswith("whab.")
            and not any(module == prefix or module.startswith(prefix + ".") for prefix in allowed)
        )
        or (any(module == name or module.startswith(name + ".") for name in other_plugins)
            and not public_dependency_import(package, module))
    ]
    assert not violations, "\n".join(violations)


def test_peer_contract_exception_never_permits_private_modules_or_undeclared_dependencies():
    category = next(path for path in PLUGIN_PACKAGES if path.name == "whab_plugin_llm_categorization")
    web = next(path for path in PLUGIN_PACKAGES if path.name == "whab_plugin_web")
    assert public_dependency_import(category, "whab_plugin_ai_credentials.api")
    assert not public_dependency_import(category, "whab_plugin_ai_credentials.private")
    assert not public_dependency_import(category, "whab_plugin_ai_credentials.providers")
    assert not public_dependency_import(web, "whab_plugin_ai_credentials.api")


@pytest.mark.parametrize("module", ["store", "schema", "seeding"])
def test_sqlite_ledger_modules_remain_independent_of_host_and_sdk(module):
    path = ROOT / "plugins" / "storage" / "sqlite" / "src" / "whab_plugin_sqlite" / f"{module}.py"
    assert path.is_file()
    assert not any(
        name == "whab.plugins" or name.startswith("whab.plugins.")
        for name in imported_modules(path)
    )
