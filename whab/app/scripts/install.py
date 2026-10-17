"""Install the source host and maintained plugins together without index lookups for plugins."""

import argparse
import importlib.metadata as metadata
import re
import shutil
import subprocess
import sys
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGACY_PLUGIN_DISTRIBUTIONS = frozenset(
    f"whab-{plugin_id}-plugin"
    for plugin_id in ("bank-logos", "client", "example", "plaid", "server", "sqlite", "web", "ynab")
)


def installed_legacy_plugins():
    """Find only official distribution names superseded by whab_plugin_* packages."""
    installed = {
        re.sub(r"[-_.]+", "-", distribution.metadata.get("Name") or "").lower()
        for distribution in metadata.distributions()
    }
    return sorted(installed & LEGACY_PLUGIN_DISTRIBUTIONS)


def remove_generated_metadata(legacy, expected_name):
    """Remove generated metadata only when its unlinked header identifies the owner."""
    metadata = legacy / "PKG-INFO"
    if legacy.is_symlink() or not legacy.is_dir() or metadata.is_symlink():
        return False
    try:
        with metadata.open("rb") as source:
            headers = BytesParser().parsebytes(source.read(64 * 1024), headersonly=True)
    except OSError:
        return False
    if headers.get_all("Name") != [expected_name]:
        return False
    shutil.rmtree(legacy)
    return True


def remove_legacy_metadata(root):
    """Remove only obsolete, generated application metadata at the old source root."""
    return remove_generated_metadata(root / "whab.egg-info", "whab")


def remove_legacy_plugin_metadata(root):
    """Remove legacy egg-info that new editable paths would otherwise keep visible."""
    plugins = root / "plugins"
    if plugins.is_symlink():
        return 0
    removed = 0
    for project in plugins.glob("*/*"):
        legacy_name = f"whab-{project.name}-plugin"
        if legacy_name not in LEGACY_PLUGIN_DISTRIBUTIONS:
            continue
        source = project / "src"
        if project.parent.is_symlink() or project.is_symlink() or source.is_symlink():
            continue
        legacy = source / (legacy_name.replace("-", "_") + ".egg-info")
        removed += remove_generated_metadata(legacy, legacy_name)
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true", help="Include the host's development tools.")
    parser.add_argument("--example", action="store_true", help="Also install teaching example plugins.")
    args = parser.parse_args()
    legacy_plugins = installed_legacy_plugins()
    include_examples = args.example or "whab-example-plugin" in legacy_plugins
    projects = sorted(
        project.parent
        for project in (ROOT / "plugins").glob("*/*/pyproject.toml")
        if include_examples or project.parent.parent.name != "example"
    )
    command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    command.extend(("-e", str(ROOT) + ("[dev]" if args.dev else "")))
    for project in projects:
        command.extend(("-e", str(project)))
    subprocess.run(command, cwd=ROOT, check=True)
    if legacy_plugins:
        subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "--disable-pip-version-check", "--yes", *legacy_plugins],
            cwd=ROOT,
            check=True,
        )
        print("Removed superseded plugin distributions after installing their replacements.")
    if remove_legacy_plugin_metadata(ROOT):
        print("Removed obsolete plugin packaging metadata from the source projects.")
    if remove_legacy_metadata(ROOT):
        print("Removed obsolete application packaging metadata from the old source root.")


if __name__ == "__main__":
    main()
