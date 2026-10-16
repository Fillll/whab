"""Install the source host and maintained plugins together without index lookups for plugins."""

import argparse
import shutil
import subprocess
import sys
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def remove_legacy_metadata(root):
    """Remove only obsolete, generated application metadata at the old source root."""
    legacy = root / "whab.egg-info"
    metadata = legacy / "PKG-INFO"
    if legacy.is_symlink() or not legacy.is_dir() or metadata.is_symlink():
        return False
    try:
        with metadata.open("rb") as source:
            headers = BytesParser().parsebytes(source.read(64 * 1024), headersonly=True)
    except OSError:
        return False
    if headers.get_all("Name") != ["whab"]:
        return False
    shutil.rmtree(legacy)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true", help="Include the host's development tools.")
    parser.add_argument("--example", action="store_true", help="Also install teaching example plugins.")
    args = parser.parse_args()
    projects = sorted(
        project.parent
        for project in (ROOT / "plugins").glob("*/*/pyproject.toml")
        if args.example or project.parent.parent.name != "example"
    )
    command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    command.extend(("-e", str(ROOT) + ("[dev]" if args.dev else "")))
    for project in projects:
        command.extend(("-e", str(project)))
    subprocess.run(command, cwd=ROOT, check=True)
    if remove_legacy_metadata(ROOT):
        print("Removed obsolete application packaging metadata from the old source root.")


if __name__ == "__main__":
    main()
