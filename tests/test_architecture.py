"""Prevent core dependency boundaries from eroding as contributors add modules."""

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "whab"


def imported_modules(path):
    relative = path.relative_to(PACKAGE.parent).with_suffix("")
    package = ".".join(relative.parts[:-1])
    for node in ast.walk(ast.parse(path.read_text())):
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
        (
            "storage/sqlite/store.py",
            ("web", "interfaces", "integrations", "plugins", "config", "app"),
        ),
        (
            "storage/sqlite/schema.py",
            ("web", "interfaces", "integrations", "plugins", "config", "app"),
        ),
        (
            "storage/sqlite/seeding.py",
            ("web", "interfaces", "integrations", "plugins", "config", "app"),
        ),
        ("storage/bank.py", ("web", "interfaces", "integrations", "config", "app")),
        ("web", ("integrations", "interfaces")),
        ("app.py", ("integrations", "interfaces")),
        ("plugins", ("integrations", "interfaces")),
        ("interfaces/web", ("web", "storage", "integrations", "config", "app")),
    ],
)
def test_core_modules_do_not_import_higher_layers_or_specific_providers(owner, forbidden):
    location = PACKAGE / owner
    paths = [location] if location.is_file() else sorted(location.rglob("*.py"))
    assert paths, f"The {owner} package must exist"
    prefixes = tuple(f"whab.{name}" for name in forbidden)
    violations = [
        f"{path.relative_to(PACKAGE)} imports {module}"
        for path in paths
        for module in imported_modules(path)
        if any(module == prefix or module.startswith(prefix + ".") for prefix in prefixes)
        or (
            owner in {"domain", "web", "app.py", "plugins", "storage/bank.py"}
            and (
                module == "sqlite3"
                or module.startswith("sqlite3.")
                or (
                    module.startswith("whab.storage.")
                    and not module.startswith("whab.storage.bank")
                )
            )
        )
    ]
    assert not violations, "\n".join(violations)
