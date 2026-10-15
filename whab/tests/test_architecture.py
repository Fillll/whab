"""Keep core independent and each contributor plugin behind public contracts."""

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "whab"
PLUGIN_PACKAGES = tuple(
    path
    for path in sorted((ROOT / "plugins").glob("*/src/*"))
    if (path / "__init__.py").is_file()
)
PLUGIN_NAMES = tuple(path.name for path in PLUGIN_PACKAGES)


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
def test_plugins_use_public_host_contracts_and_do_not_import_each_other(package):
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
        or any(module == name or module.startswith(name + ".") for name in other_plugins)
    ]
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("module", ["store", "schema", "seeding"])
def test_sqlite_ledger_modules_remain_independent_of_host_and_sdk(module):
    path = ROOT / "plugins" / "sqlite" / "src" / "whab_sqlite" / f"{module}.py"
    assert path.is_file()
    assert not any(
        name == "whab.plugins" or name.startswith("whab.plugins.")
        for name in imported_modules(path)
    )
