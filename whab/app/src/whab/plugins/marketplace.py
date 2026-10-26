"""Curated package installation, separate from budget activation and financial rules."""

import hashlib
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from importlib import metadata, resources
from pathlib import Path
from threading import Lock
from uuid import uuid4

from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

from whab import __version__

from .catalog import catalog_snapshot
from .manifests import validate_manifest
from .process_environment import process_environment
from .runtime import PluginError

_LOCK = Lock()  # One installation per interpreter, including across open budgets.


def _freeze():
    return {canonicalize_name(dist.metadata["Name"]): dist.version for dist in metadata.distributions() if dist.metadata.get("Name")}


def _run(arguments):
    try:
        subprocess.run([sys.executable, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", *arguments],
                       check=True, timeout=300, capture_output=True, env=process_environment(), cwd=tempfile.gettempdir())
    except (OSError, subprocess.SubprocessError):
        raise PluginError("Package preparation or installation failed. Check network access, package compatibility and this environment's write permissions, then review again.", code="installation_failed") from None


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class Marketplace:
    def __init__(self):
        self._plans = {}

    def _data(self):
        catalog = catalog_snapshot()
        data = json.loads(resources.files("whab.plugins").joinpath("manifests.json").read_text())
        manifests = {row["id"]: validate_manifest(row) for row in data["plugins"]}
        rows = [{**row, **manifests[row["id"]]} for row in catalog["plugins"] if row["support"] == "maintained"]
        return rows, data["source_ref"]

    def projection(self):
        rows, _ = self._data()
        installed = _freeze()
        return {"plugins": [{"id": row["id"], "display_name": row["display_name"], "category": row["category"],
                             "version": row["version"], "dependencies": row["dependencies"],
                             "installed_version": installed.get(canonicalize_name(row["distribution"])),
                             "compatible": Version(__version__) in SpecifierSet(row["requires_host"].removeprefix("whab"))
                             and Version(".".join(map(str, sys.version_info[:3]))) in SpecifierSet(row["requires_python"])} for row in rows]}

    def _closure(self, identifier, rows):
        catalog = {row["id"]: row for row in rows}
        ordered, visiting, done = [], set(), set()

        def visit(name, version=""):
            row = catalog.get(name)
            if row is None or Version(row["version"]) not in SpecifierSet(version):
                raise PluginError("A required plugin is unavailable in a compatible catalog version.", code="plugin_dependency", plugin_id=name)
            if name in visiting:
                raise PluginError("Plugin dependencies contain a cycle.", code="dependency_cycle", plugin_id=name)
            if name in done:
                return
            if Version(__version__) not in SpecifierSet(row["requires_host"].removeprefix("whab")) or Version(".".join(map(str, sys.version_info[:3]))) not in SpecifierSet(row["requires_python"]):
                raise PluginError("This plugin requires a different WHAB or Python version.", code="incompatible_host", plugin_id=name)
            visiting.add(name)
            for dependency in row["dependencies"]:
                visit(dependency["id"], dependency["version"])
            visiting.remove(name)
            done.add(name)
            ordered.append(row)

        visit(identifier)
        return ordered

    def review(self, identifier):
        with _LOCK:
            # Bounded, expiring reviews; abandoned downloads never accumulate.
            for key, plan in list(self._plans.items()):
                if time.monotonic() - plan["created"] > 900 or len(self._plans) >= 4:
                    plan["directory"].cleanup()
                    del self._plans[key]
            rows, source_ref = self._data()
            closure = self._closure(identifier, rows)
            installed = _freeze()
            missing = []
            for row in closure:
                version = installed.get(canonicalize_name(row["distribution"]))
                if version and version != row["version"]:
                    raise PluginError("An installed plugin has a different version. Use the WHAB updater before installing this dependency set.", code="installed_version_conflict", plugin_id=row["id"])
                if not version:
                    missing.append(row)
            if not missing:
                raise PluginError("This plugin and its dependencies are already installed.", code="already_installed", plugin_id=identifier)
            directory = tempfile.TemporaryDirectory(prefix="whab-marketplace-")
            path = Path(directory.name)
            try:
                constraints = path / "constraints.txt"
                constraints.write_text("\n".join(f"{name}=={version}" for name, version in sorted(installed.items())))
                app = Path(__file__).resolve().parents[3]
                sources = []
                for row in missing:
                    local = app / Path(row["source"]).relative_to("whab/app")
                    if (local / "pyproject.toml").is_file():
                        # Build from a detached copy so metadata writes cannot dirty source.
                        target = path / "sources" / row["id"]
                        shutil.copytree(local, target, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "build", ".venv"))
                        sources.append(str(target))
                    else:
                        if len(source_ref) != 40 or any(character not in "0123456789abcdef" for character in source_ref):
                            raise PluginError("The marketplace source revision is invalid.", code="invalid_catalog")
                        sources.append(f"git+https://github.com/Fillll/whab.git@{source_ref}#subdirectory={row['source']}")
                wheels = path / "wheels"
                wheels.mkdir()
                _run(["wheel", "--no-deps", "--wheel-dir", str(wheels), *sources])
                requested = sorted(wheels.glob("*.whl"))
                report = path / "report.json"
                _run(["install", "--dry-run", "--only-binary=:all:", "--report", str(report), "--constraint", str(constraints), *map(str, requested)])
                prepared = json.loads(report.read_text())["install"]
                packages = []
                for item in prepared:
                    name, version = item["metadata"]["name"], item["metadata"]["version"]
                    if canonicalize_name(name) in installed:
                        raise PluginError("Installation would replace an existing package. Update WHAB first.", code="installed_version_conflict")
                    packages.append({"name": name, "version": version})
                    url = item["download_info"]["url"]
                    if not url.startswith("file:"):
                        checksum = item["download_info"].get("archive_info", {}).get("hashes", {}).get("sha256")
                        if not checksum or not url.startswith("https://"):
                            raise PluginError("A dependency has no verified HTTPS wheel.", code="invalid_artifact")
                        _run(["download", "--no-deps", "--only-binary=:all:", "--dest", str(wheels), url + "#sha256=" + checksum])
                # Check exact requested distribution identities and versions as well as Python deps.
                resolved = {canonicalize_name(item["name"]): item["version"] for item in packages}
                if any(resolved.get(canonicalize_name(row["distribution"])) != row["version"] for row in missing):
                    raise PluginError("Prepared plugins differ from the reviewed catalog.", code="invalid_artifact")
                token = uuid4().hex
                artifacts = {str(wheel): _digest(wheel) for wheel in wheels.glob("*.whl")}
                self._plans[token] = {"directory": directory, "created": time.monotonic(), "installed": installed,
                                      "artifacts": artifacts, "expected": resolved, "plugins": [row["id"] for row in closure]}
                return {"token": token, "plugins": [{"id": row["id"], "display_name": row["display_name"], "version": row["version"],
                                                       "installed": canonicalize_name(row["distribution"]) in installed} for row in closure],
                        "packages": packages, "expires_in": 900}
            except Exception:
                directory.cleanup()
                raise

    def install(self, token):
        with _LOCK:
            plan = self._plans.pop(token, None)
            if plan is None:
                raise PluginError("Review this installation again.", code="stale_installation")
            try:
                if time.monotonic() - plan["created"] > 900 or _freeze() != plan["installed"]:
                    raise PluginError("The installation review expired or installed packages changed. Review again.", code="stale_installation")
                if any(not Path(path).is_file() or _digest(Path(path)) != digest for path, digest in plan["artifacts"].items()):
                    raise PluginError("Prepared packages changed. Review again.", code="invalid_artifact")
                _run(["install", "--no-index", "--no-deps", *plan["artifacts"]])
                importlib.invalidate_caches()
                installed = _freeze()
                if any(installed.get(name) != version for name, version in plan["expected"].items()):
                    raise PluginError("Installed packages could not be confirmed. Refresh the plugin list and review again.", code="installation_unconfirmed")
                return {"installed": plan["plugins"], "message": "Installed. Refresh the plugin list to enable plugins for this budget."}
            finally:
                plan["directory"].cleanup()


marketplace = Marketplace()
